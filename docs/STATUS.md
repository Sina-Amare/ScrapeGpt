# Project Status — Where to Continue From

Last updated: 2026-05-10
Source audit: [implementation_audit.md](implementation_audit.md) (2026-02-16)
Architecture: [architecture.md](architecture.md)

This is the single page that answers "what should I work on next?". It groups the remaining work by priority and points at concrete file:line locations.

## TL;DR

The MVP architecture is sound and end-to-end runnable. **Three endpoint-wiring bugs and one migration hygiene issue should be fixed before anything else is built on top.** After that, the highest-leverage work is (a) replacing the LLM stub and (b) building out a real test suite.

## Status board

| Area                                | Status                            |
| ----------------------------------- | --------------------------------- |
| FastAPI bootstrap, routing, lifespan | ✅ Done                           |
| Auth (register / login / refresh)   | ✅ Done (one minor edge case)     |
| User + ScrapeTask data model        | ✅ Done                           |
| State machine + transitions         | ✅ Done                           |
| Admission (credits + one active)    | ✅ Done                           |
| Async pipeline orchestration        | ✅ Done                           |
| Scraper (httpx + BeautifulSoup)     | ✅ Done                           |
| Health / readiness                  | ✅ Done                           |
| Daily credit reset (multi-instance) | ✅ Done                           |
| Watchdog (stuck-task cleanup)       | ⚠️ Done, but has a NULL-skip bug  |
| `POST /scrape/start` rate limiting  | 🔴 Wired but currently broken     |
| `/scrape/tasks/current` routing     | 🔴 Shadowed by `/tasks/{task_id}` |
| Migration enum drift (002 vs model) | 🟠 Functional but risky           |
| Per-user rate-limit key fn          | 🟠 Falls back to IP today         |
| LLM integration                     | 🟡 Stub only                      |
| Auth-endpoint rate limits           | 🟡 Constant exists, not applied   |
| Test suite                          | 🟡 Skeleton only                  |
| Config-driven governance            | 🟡 Several unused settings        |

Legend: ✅ done · ⚠️ done with caveat · 🔴 critical/high bug · 🟠 medium · 🟡 unfinished

---

## Critical & high bugs (fix these first)

### 1. `POST /scrape/start` SlowAPI parameter collision 🔴

**Where:** [app/api/v1/endpoints/scrape.py:55-70](../app/api/v1/endpoints/scrape.py#L55-L70)

The endpoint signature uses `request: StartScrapeRequest` (the body model) but `@limiter.limit(SCRAPE_RATE_LIMIT)` requires a parameter named `request` of type `starlette.requests.Request`. SlowAPI walks signature args looking for that exact name+type. The current code passes the Pydantic body model and SlowAPI raises at runtime.

**Fix:** Rename the body param and put a real `Request` on `request`:

```python
@limiter.limit(SCRAPE_RATE_LIMIT)
async def start_scrape(
    request: Request,                           # Starlette Request — for SlowAPI
    payload: StartScrapeRequest,                # the body
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    url_str = str(payload.url)
    ...
```

(Drop the unused `fastapi_request: Request = None` param at the end.)

### 2. `/scrape/tasks/current` is shadowed by `/scrape/tasks/{task_id}` 🔴

**Where:** [app/api/v1/endpoints/scrape.py:124](../app/api/v1/endpoints/scrape.py#L124) (declared first) vs [app/api/v1/endpoints/scrape.py:153](../app/api/v1/endpoints/scrape.py#L153)

FastAPI routes are matched in declaration order. A `GET /scrape/tasks/current` is matched by `/tasks/{task_id}` first, where `current` then fails int validation → 422.

**Fix:** Reorder so the static path is declared *before* the dynamic one. Move `get_current_task` above `get_task` in the file. Also: the response model says `TaskResponse | None` but the implementation raises 404 on absence — pick one (recommended: keep 404 and change the response model to plain `TaskResponse`).

### 3. Watchdog skips fresh `PERMISSION_GRANTED` tasks 🔴

**Where:** [app/services/watchdog.py:41-46](../app/services/watchdog.py#L41-L46) and [app/models/scrape_task.py:111-115](../app/models/scrape_task.py#L111-L115)

`updated_at` is nullable with no insert default. The watchdog uses `ScrapeTask.updated_at < cutoff`, but `NULL < anything` is `NULL` (falsy in `WHERE`), so newly-created tasks that never moved past `PERMISSION_GRANTED` are never caught.

**Fix (pick one):**

- **Easiest:** change the filter to `func.coalesce(ScrapeTask.updated_at, ScrapeTask.created_at) < cutoff`.
- **Cleaner:** make `updated_at` non-null with a server default of `now()` and a backfill migration. Then keep the existing filter.

Apply the same pattern to the `SCRAPING` and `LLM_PROCESSING` watchdog queries for consistency, even though those states do update `updated_at`.

### 4. Migration enum drift 🟠

**Where:** [alembic/versions/002_create_scrape_tasks.py:23-29](../alembic/versions/002_create_scrape_tasks.py#L23-L29) vs [app/models/scrape_task.py:30-35](../app/models/scrape_task.py#L30-L35)

Migration 002 introduced enum values `LLM_ANALYZED`, `OUTPUT_GENERATION`, `FINALIZED`. The current model uses `SCRAPING`, `LLM_PROCESSING`, `COMPLETED`, `FAILED`. Migration 003 added the new values and the partial unique index, but the index `WHERE state NOT IN ('COMPLETED', 'FAILED')` would still treat any leftover `FINALIZED` row as active.

**Fix:** Write a migration `005_consolidate_task_states.py` that:

1. Maps any historical `FINALIZED` / `LLM_ANALYZED` / `OUTPUT_GENERATION` rows to `COMPLETED` or `FAILED` (your call which).
2. Drops the unused enum values from the type (`ALTER TYPE … RENAME` + `CREATE TYPE` cycle in PostgreSQL — there's no direct `DROP VALUE`).
3. Document this in [docs/learning/](learning/) so the next person knows why the migration churn happened.

If your local DB has no production data, you can also reset migrations entirely and squash 001-004 into a clean baseline — only do this if you're certain no instance is running on the old schema.

---

## Medium-priority cleanup

### 5. Per-user rate limiting 🟠

**Where:** [app/core/rate_limit.py:23-26](../app/core/rate_limit.py#L23-L26)

The key function reads `request.state.user`, but no middleware sets it. Result: rate limits fall back to IP-based throttling — fine for unauthenticated routes, suboptimal for authenticated ones (multiple users behind one NAT share a budget).

**Fix:** Add a small middleware (or a dependency that runs early) that copies the resolved `User` onto `request.state.user`. Easiest is a dependency that wraps `get_current_user` and stores the result.

### 6. JWT subject `int()` cast can 500 🟠

**Where:** [app/api/deps.py:88](../app/api/deps.py#L88), [app/api/deps.py:123](../app/api/deps.py#L123), [app/api/v1/endpoints/auth.py:204](../app/api/v1/endpoints/auth.py#L204)

`int(payload.sub)` raises `ValueError` for malformed but otherwise-decodable tokens, surfacing as a 500.

**Fix:** Wrap each `int(payload.sub)` in `try/except ValueError → raise HTTPException(401, "Invalid token")`. Or add a Pydantic-validated `TokenPayload` schema.

### 7. `/scrape/tasks/current` response-model mismatch 🟠

Already covered in fix #2 — pick a single behaviour (404 or `null`).

---

## Unfinished features

### 8. Replace the LLM stub 🟡

**Where:** [app/services/llm_processor.py](../app/services/llm_processor.py)

`process_with_llm(content)` currently sleeps 1 s and returns `{"summary": ..., "word_count": ..., "analysis": "stub"}`.

Approach:

1. Add an LLM provider config: `LLM_PROVIDER`, `ANTHROPIC_API_KEY` (or whichever), `LLM_MODEL` to `Settings`.
2. Implement a real call using the provider SDK. For Anthropic: enable prompt caching on the system prompt to make repeat scrapes cheap. (See the Claude API skill for caching patterns.)
3. Respect `LLM_TIMEOUT` (already in config). Wrap the call in `asyncio.wait_for`.
4. Surface partial failures cleanly — the task transitions to `FAILED` with a useful error message; do not retry inside the LLM call (let the watchdog or an explicit retry endpoint handle that later).

Test on a few real URLs before merging.

### 9. Auth-endpoint rate limiting 🟡

**Where:** [app/core/rate_limit.py:42](../app/core/rate_limit.py#L42)

`AUTH_RATE_LIMIT` is defined but no `@limiter.limit(AUTH_RATE_LIMIT)` decorator on `register` / `login` / `refresh`. Add the decorator and a `request: Request` parameter to each — same pattern as fix #1.

### 10. Enforce currently-unused config 🟡

These settings are declared in `app/core/config.py` but not actually consulted at runtime:

- `SCRAPE_CREDIT_COST` — credit deduction is hard-coded to 1 in `transition_to_llm_processing`.
- `LLM_TIMEOUT` — the stub doesn't honour it.
- `MAX_CONCURRENT_JOBS` — no concurrency cap exists in the executor.
- `DEFAULT_DAILY_CREDITS` — only used at user-create time? Verify in `auth.py` register flow.

Either wire them up or delete them. Don't leave dead config.

### 11. Test suite 🟡

**Current state:**

- [tests/api/v1/test_health_readiness.py](../tests/api/v1/test_health_readiness.py) — health probe tests.
- [tests/services/test_readiness.py](../tests/services/test_readiness.py) — readiness service tests.
- Nothing else.

**Highest-value tests to add, in order:**

1. **Auth happy paths + token refresh.** Register, login, refresh, expired token, malformed token. Single integration test file.
2. **Admission service.** Each `AdmissionError` branch + happy path. Fixture-based, no HTTP.
3. **State transitions.** Each `transition_*` function, including the credit-deduction atomicity in `transition_to_llm_processing` (kill the task mid-transition and verify no half-state).
4. **Pipeline end-to-end.** Mock `scrape_url` and `process_with_llm`, run `execute_scrape_pipeline`, assert final state.
5. **Concurrency:** two `POST /scrape/start` from the same user racing — verify only one task is admitted (the partial unique index should make the second one fail).
6. **Watchdog:** create stuck tasks (with various `updated_at` values including NULL), run cleanup, assert correct ones are failed. This will surface fix #3 if you write the test before the fix.

Aim for a real `pytest` setup with an ephemeral test database (transactional rollback per test, or testcontainers-postgres).

---

## README cleanup (already done in this pass)

- ✅ Removed Playwright claim — no Playwright dep exists.
- ✅ Added auth + scrape endpoints to the API table.
- ✅ Linked to architecture.md and STATUS.md.

---

## Suggested next sprint

If you want a concrete two-week plan, work through this order:

1. **Day 1:** Fixes #1, #2, #3 (the three 🔴 bugs). Add a regression test for each as you go — this seeds the test infrastructure.
2. **Day 2:** Fix #4 (migration consolidation). Run `alembic upgrade head` on a fresh DB and an existing dev DB to verify both paths work.
3. **Day 3-4:** Fixes #5, #6, #9 (rate limiting and JWT robustness). Easy wins, small surface area.
4. **Day 5-6:** Test suite scaffolding + tests for auth, admission, state transitions (#11 items 1–3).
5. **Day 7-8:** Pipeline end-to-end test + watchdog test + concurrency test (#11 items 4–6).
6. **Day 9-10:** LLM integration (#8). The hardest piece; do it after the test suite so you can iterate safely.

Defer #10 (unused config) to a final cleanup pass — it's low-stakes.

---

## Out of scope (good to keep on the radar but not blocking MVP)

- Webhook / SSE for task completion (today: client polls).
- Retry policy for transient scrape failures.
- Persistent job queue (Celery / Arq) for horizontal scaling.
- Per-task cost telemetry (which scrapes are expensive?).
- Admin endpoints for ops (force-fail a task, top up credits, list active tasks).
- Frontend.
