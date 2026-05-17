# Current Status — Known Issues

Last updated: 2026-05-17

For the full roadmap and what to build next, see [plan/ROADMAP.md](plan/ROADMAP.md).
For architecture details, see [architecture.md](architecture.md).

---

## Status Board

| Area                                 | Status                        |
| ------------------------------------ | ----------------------------- |
| FastAPI bootstrap, routing, lifespan | ✅ Done                       |
| Auth (register / login / refresh)    | ✅ Done                       |
| User + ScrapeTask data model         | ✅ Done                       |
| State machine + transitions          | ✅ Done                       |
| Admission (credits + one active)     | ✅ Done                       |
| Async pipeline orchestration         | ✅ Done                       |
| Scraper (httpx + BeautifulSoup)      | ✅ Done                       |
| Health / readiness                   | ✅ Done                       |
| Daily credit reset (multi-instance)  | ✅ Done                       |
| Watchdog (stuck-task cleanup)        | ⚠️ Has NULL-skip bug          |
| `POST /scrape/start` rate limiting   | 🔴 Broken (SlowAPI collision) |
| `/scrape/tasks/current` routing      | 🔴 Shadowed by `{task_id}`    |
| LLM integration                      | 🟡 Stub only                  |
| Frontend                             | 🟡 Not started                |
| Test suite                           | 🟡 Skeleton only              |

---

## Bugs to Fix (Phase 0)

These must be fixed before building new features.

### 1. SlowAPI parameter collision 🔴

**File:** `app/api/v1/endpoints/scrape.py:64-69`

The `request` param is the Pydantic body model, but SlowAPI expects `starlette.requests.Request`. Rename body to `payload`, put `request: Request` first.

### 2. Route shadowing 🔴

**File:** `app/api/v1/endpoints/scrape.py:124 vs 153`

`/tasks/{task_id}` is declared before `/tasks/current`. Move the static route above the dynamic one.

### 3. Watchdog NULL-skip 🔴

**File:** `app/services/watchdog.py:44`

`updated_at` is nullable with no insert default. Use `COALESCE(updated_at, created_at)` in the filter.

### 4. Migration enum drift 🟠

Old enum values (`FINALIZED`, `LLM_ANALYZED`, `OUTPUT_GENERATION`) exist in migration 002 but aren't used by the current model. Squash migrations to a clean baseline since there's no production data.

### 5. JWT `int()` cast can 500 🟠

**File:** `app/api/deps.py:88`

`int(payload.sub)` raises `ValueError` for malformed tokens. Wrap in try/except → 401.

### 6. Per-user rate limiting is not actually per-user 🟠

**File:** `app/core/rate_limit.py`

The rate-limit key function checks `request.state.user`, but no middleware or dependency sets it. Authenticated routes currently fall back to IP-based limits.

### 7. Auth rate limit constant is unused 🟡

**File:** `app/core/rate_limit.py`, `app/api/v1/endpoints/auth.py`

`AUTH_RATE_LIMIT` exists, but auth endpoints are not decorated with it.

### 8. Config is not fully enforced 🟡

Examples: `SCRAPE_CREDIT_COST`, `LLM_TIMEOUT`, and `MAX_CONCURRENT_JOBS` are declared but not consistently enforced by runtime paths.

### 9. `/scrape/tasks/current` response contract mismatch 🟡

The response model allows `None`, but the implementation raises 404 when no task exists. Pick one behavior and make schema/docs match.

---

## What's Next

See [plan/ROADMAP.md](plan/ROADMAP.md) — Phase 0 (bug fixes) then Phase 1 (URL validation/fetching and Gemini integration).
