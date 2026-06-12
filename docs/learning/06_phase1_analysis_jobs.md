# 06 — Phase 1: Analysis Jobs Pipeline

## Problem / Purpose

Phase 1 introduces the *Analysis Jobs* pipeline — the first step in ScrapeGPT's redesigned BYOK extraction workflow. Before Phase 1, the platform had a single-purpose scrape pipeline (scrape URL → call LLM → return raw text summary). Phase 1 replaces that with an intelligent analysis system:

1. Validate and safely fetch a URL
2. Check `robots.txt` consent
3. Fetch page HTML (static or browser)
4. Build a DOM summary (noise-stripped, token-efficient)
5. Call the user's configured AI provider to analyze structure
6. Return structured extraction recommendations (field selectors, confidence, warnings)

The result is stored as a Job that transitions through a state machine from `QUEUED` to either `ANALYSIS_READY` (high confidence, no warnings) or `AWAITING_SETUP` (analysis complete, but the future extraction setup/review phase is still required). Phase 2 will consume the `AWAITING_SETUP` output to start the actual extraction crawl.

---

## Invariants

1. **Every job always reaches a terminal state.** `execute_job_pipeline` wraps the entire pipeline in a try/except that calls `transition_job_to_failed` as a final catch. A job never stays in `ANALYZING` forever.

2. **SSRF-safe by default.** `url_validator.validate_url` blocks loopback, private, link-local, multicast, reserved, and cloud metadata IPs. Metadata IPs (`169.254.169.254`, etc.) are always blocked even when `ALLOW_PRIVATE_NETWORK_URLS=True`. Every redirect hop is validated before following.

3. **`robots.txt` respected by default.** `robots_service.check_robots` parses and enforces robots.txt with the User-Agent from `settings.USER_AGENT`. On fetch failure, behavior is determined by `ROBOTS_FAILURE_POLICY` (`deny` by default; configurable to `allow`). A job blocked by robots fails with `error_code="ROBOTS_BLOCKED"`.

4. **Provider API key never exposed in job responses.** `JobResponse` holds `provider_config_id` (an integer FK) — never the key or decrypted secret.

5. **Analysis cache prevents redundant LLM calls.** If the same `(content_hash, extraction_mode, provider, model, ANALYZER_VERSION)` tuple has been analyzed before, the cached result is returned without calling the LLM provider. Cache is stored in `analysis_cache` and keyed by SHA-256 of the raw HTML bytes.

6. **Active job limit enforced at admission time.** `admit_job` holds a PostgreSQL advisory lock (`pg_advisory_xact_lock`) while counting active jobs, preventing races where two concurrent admissions both observe count=0 and both succeed.

7. **Per-transition sessions.** Each `transition_job_to_*` function opens its own `AsyncSession` via `async_session_factory()`. Passing a live session into a transition function would trigger SQLAlchemy's `InvalidRequestError` because `db.begin()` cannot be called twice on the same session.

8. **Terminal-only deletion.** `DELETE /jobs/{id}` returns 400 if `job.state not in DELETABLE_JOB_STATES`. Active jobs cannot be deleted because the background worker holds a reference to them.

---

## Design Decisions, Rejected Alternatives, Trade-offs

### SSRF validation: DNS-time, not connect-time

The validator resolves the hostname via `socket.getaddrinfo` at validation time and checks all returned A/AAAA records against the blocked-IP logic. This means a hostname that sometimes resolves to a public IP and sometimes to a private IP will be blocked if any resolution returns a private address.

**Rejected:** Connect-time validation (letting the network library connect, then inspecting the final destination). This is more accurate but much harder to implement reliably for all redirect hops and all libraries.

**Trade-off:** A small false-positive rate on dual-homed hosts; no false-negatives on any DNS-based SSRF attack.

### robots.txt: In-memory TTL cache, not persistent

The robots cache is a module-level dict keyed by origin (`scheme://host:port`) with a 5-minute TTL. It resets on process restart.

**Rejected:** Database caching. For a self-hosted single-instance deployment, a process-local cache is simpler, sufficient, and avoids a DB round-trip on every request.

**Trade-off:** Cache doesn't survive restarts; robots.txt fetch overhead is paid once per 5 minutes per origin. For production multi-worker, each worker gets its own cache — this is fine since robots.txt is fetched per origin, and the worst case is redundant fetches.

### Static fetch first, optional browser fallback

`fetch_url(render_mode="AUTO")` fetches statically first. If the resulting HTML has fewer than 500 non-whitespace characters, it retries with Playwright (browser). `render_mode="STATIC"` skips the fallback; `render_mode="BROWSER"` skips the static attempt. Browser mode raises `FetchError(error_code="BROWSER_UNAVAILABLE")` if Playwright is not installed. On Windows Uvicorn selector loops, browser rendering runs through the worker-thread sync Playwright path described below.

**Rejected:** Always-browser. Too slow (~5s per page), too much memory, and most sites render fine statically.

**Trade-off:** AUTO mode makes two requests for JS-heavy sites (static + browser). This is intentional and acceptable for an analysis-only phase.

### DOM summary, not raw HTML, to the LLM

`build_dom_summary` strips noise tags (scripts, styles, ads, navbars, footers), then extracts title, meta description, headings H1–H3, JSON-LD schema.org data, repeated CSS class patterns, pagination indicators, and up to 20 important links. The result is capped at 4000 characters.

**Why:** Raw HTML for an average product listing page is 100–500 KB. LLM context windows are not free; sending raw HTML wastes tokens on irrelevant markup, hurts schema compliance on weaker models, and slows response time.

**Trade-off:** The summary is lossy. If the site embeds critical field information only in deeply nested or visually-rendered elements, the summary may miss it. The confidence score returned by the LLM reflects this uncertainty.

### Two fixed analysis schemas, not a generic LLM response

`StructuredAnalysis` and `ContentAnalysis` are locked Pydantic models. `call_json_model` validates LLM output against the appropriate schema (up to 3 retries with a clarifying prompt on validation failure).

**Rejected:** Accepting any JSON from the LLM and storing it as a blob. This causes frontend rendering to be indeterminate and makes it impossible to write deterministic tests for the analysis output.

**Trade-off:** If a site type doesn't fit "structured" or "content", the LLM may fill in fields with low confidence or warnings. That's the correct behavior — surface uncertainty explicitly rather than silently returning garbage.

### Job admission: advisory lock + count check

`admit_job` calls `SELECT pg_advisory_xact_lock(hash(user_id))` inside a transaction, then counts active jobs for that user. The lock is released on transaction commit/rollback.

**Rejected:** PostgreSQL partial unique index (the old Phase 0 approach). The partial unique index was dropped in migration 005 because it didn't support a configurable limit > 1. Count-based checking with an advisory lock gives the same race-free guarantee while allowing `MAX_CONCURRENT_JOBS_PER_USER` to be any positive integer.

**Rejected:** Optimistic lock (count then insert without locking). Race condition: two concurrent admissions both observe count=0 and both create jobs.

### FAST+high_confidence+no_warnings → ANALYSIS_READY; otherwise → AWAITING_SETUP

At the end of the pipeline, `execute_job_pipeline` decides the final state:
- `workflow_mode == FAST` AND `confidence >= ANALYSIS_CONFIDENCE_FAST_THRESHOLD` (default 0.75) AND `len(warnings) == 0` → `ANALYSIS_READY`
- Otherwise → `AWAITING_SETUP`

`AWAITING_SETUP` is the future human setup gate. Phase 1 does not implement the review/approve UI yet, so the frontend labels this as "Analysis complete" rather than "Needs review." Phase 2 will add a `POST /jobs/{id}/start` endpoint for the user to review and confirm field configuration from `AWAITING_SETUP`.

---

## Code Walkthrough

### Migration: `alembic/versions/006_analysis_jobs.py`

PostgreSQL `ADD VALUE` cannot run inside a transaction. The migration uses `op.get_context().autocommit_block()` to issue a `COMMIT` before each `ALTER TYPE ... ADD VALUE`, then `BEGIN` after to re-enter transactional context. This pattern is the same as migration 005 for the provider-related enums.

Four new enums: `job_state`, `extraction_mode`, `workflow_mode`, `render_mode`.

Two new tables:
- `jobs` — one row per analysis job, with all state machine fields and the `analysis JSONB` column
- `analysis_cache` — one row per unique analysis result, keyed by `(content_hash, extraction_mode, provider, model, analyzer_version)` unique index

### Models: `app/models/job.py`

`Job` and `AnalysisCache` are SQLAlchemy 2.0 `DeclarativeBase` models. Key model-level logic:

```python
@property
def is_terminal(self) -> bool:
    return self.state in TERMINAL_JOB_STATES

@property
def is_active(self) -> bool:
    return self.state in ACTIVE_JOB_STATES

def can_transition_to(self, target: JobState) -> bool:
    if self.is_terminal:
        return False
    return True  # executor checks specific expected_states where needed
```

`TERMINAL_JOB_STATES = {AWAITING_SETUP, ANALYSIS_READY, FAILED, CANCELED}` — all four terminal states are set-type checked for O(1) membership.

### Services

#### `url_validator.py`

Entry points: `validate_url(url)` and `validate_redirect_target(location, original_url)`.

Validation order:
1. Parse with `urlparse`
2. Check scheme is present (raises `INVALID_URL`)
3. Check scheme is in `_ALLOWED_SCHEMES` (raises `SCHEME_NOT_ALLOWED`) — **scheme is checked before netloc** so `file:///path` gets `SCHEME_NOT_ALLOWED` not `INVALID_URL`
4. Check netloc is present (raises `INVALID_URL`)
5. Try to parse hostname as raw IP → call `_check_ip`
6. If not a raw IP, resolve via `socket.getaddrinfo` → check each resolved IP

`_check_ip` checks:
- In `_BLOCKED_IPS` (metadata endpoints) → always block regardless of `ALLOW_PRIVATE_NETWORK_URLS`
- If `ALLOW_PRIVATE_NETWORK_URLS=False`: loopback, link-local, multicast, private, reserved

#### `robots_service.py`

Cache key: `f"{scheme}://{host}:{port}"` (origin). Cached value: `(expiry_timestamp, RobotFileParser | None)`.

On 404: assume no restrictions → parser is `None`, treated as ALLOWED.
On 3xx (redirect): **not followed**. `follow_redirects=False` is set on the client. If `resp.is_redirect` is true, return `None` (UNAVAILABLE) and apply `ROBOTS_FAILURE_POLICY`. This prevents SSRF via a robots.txt redirect to an internal metadata endpoint.
On fetch exception: apply `ROBOTS_FAILURE_POLICY`.

`check_robots` returns `RobotsCheck(result: RobotsResult, reason: str)`. The executor checks `result == RobotsResult.BLOCKED` → fail with `ROBOTS_BLOCKED`. `result == RobotsResult.UNAVAILABLE` with `ROBOTS_FAILURE_POLICY=deny` → fail with `ROBOTS_UNAVAILABLE`.

#### `fetcher.py`

`_static_fetch` uses `httpx.AsyncClient` with `follow_redirects=False`. It implements the redirect loop manually, calling `validate_redirect_target` on each `Location` header before following. This gives per-hop SSRF protection. A single client instance covers the full redirect chain, avoiding a new TLS handshake per hop.

Truncation: `body = await resp.aread()` then `body = body[:settings.MAX_FETCH_BYTES]` if over limit. `FetchResult.fetch_metadata` carries three explicit fields:

- `original_bytes`: size of the response before truncation
- `analyzed_bytes`: size passed to analysis (≤ `MAX_FETCH_BYTES`)
- `truncated`: `True` if the two differ

`FetchResult.content_hash` is `hashlib.sha256(html.encode()).hexdigest()` — this is the cache key for analysis results.

**Browser mode (`_browser_fetch`)**: Playwright is an **optional dependency** — not in `requirements.txt` by default. If it is not installed, the Playwright import raises `ImportError`, caught and re-raised as `FetchError(error_code="BROWSER_UNAVAILABLE")`.

Install separately after the standard requirements:

```bash
pip install playwright
python -m playwright install chromium
```

**Browser SSRF prevention** via `context.route("**", _route_handler)`: every outgoing request fires the handler before the TCP connection is established. For any `http://` or `https://` URL, `validate_url` is called. If it raises `URLValidationError` (private IP, blocked range), the handler calls `route.abort("blockedbyclient")` and appends a `FetchError(error_code="BROWSER_URL_BLOCKED")` to the shared `blocked` list.

`blocked` is initialized **before** the outer `try:` block. This matters because real Playwright throws from `page.goto()` when a route handler aborts the main navigation — the exception propagates out through the `finally` cleanup blocks before the `if blocked: raise blocked[0]` guard inside the try is reached. The `except Exception as exc:` handler checks `blocked` first; if set, it re-raises the `BROWSER_URL_BLOCKED` error rather than wrapping it as the generic `FETCH_FAILED`.

**DNS rebinding (TOCTOU) limitation**: `validate_url` resolves DNS in Python at check time. The browser re-resolves at TCP connect time. An attacker-controlled hostname can return a public IP during the Python check and a private IP for the actual browser connection. This race is not preventable at the application layer. Full mitigation requires an egress firewall or IP-pinned transport.

After navigation, `validate_url` is called again on `page.url` (the post-navigation final URL) as a belt-and-suspenders check for JS-driven redirects that bypass the route handler.

Page content is capped identically to static mode: `html_raw = (await page.content()).encode("utf-8"); html_raw = html_raw[:settings.MAX_FETCH_BYTES]`, with the same `original_bytes` / `analyzed_bytes` / `truncated` metadata fields in `fetch_metadata`.

**Windows Uvicorn fix**: Uvicorn can run under a Windows selector event loop. That loop cannot create the subprocess Playwright needs for Chromium, causing `NotImplementedError`. The fetcher detects this condition and runs browser rendering through Playwright's sync API in a worker thread after switching the thread's event-loop policy to `WindowsProactorEventLoopPolicy`. The running Uvicorn loop is not replaced. This path is covered by tests and was smoke-tested by forcing a Windows selector loop for `https://example.com`.

**Actionable browser errors**: Playwright can raise exceptions with an empty string. `_format_browser_exception` falls back to the exception class name, so jobs no longer store a blank message like `Browser fetch failed: `.

#### `dom_summary.py`

`build_dom_summary` uses BeautifulSoup with the `lxml` parser. Steps:
1. Remove noise tags: `script, style, noscript, iframe, svg, aside, nav, footer, header, form, button`
2. Extract title and meta description
3. Extract H1–H3 headings (up to 10)
4. Extract JSON-LD `application/ld+json` blocks
5. Find repeated class patterns (class appearing 3+ times → likely a repeating item)
6. Find pagination indicators (classes/text matching `next`, `prev`, `page`, `pager`)
7. Extract up to 20 anchor hrefs with text

Output is a plain-text summary string capped at `_MAX_SUMMARY_CHARS = 4000`.

#### `analyzer.py`

Two LLM prompt templates (`_STRUCTURED_PROMPT`, `_CONTENT_PROMPT`) instruct the model to return the exact locked schema JSON.

`analyze_page` flow:
1. Query `analysis_cache` for `(content_hash, extraction_mode, provider, model, ANALYZER_VERSION)`
2. Cache hit → return `result` dict immediately
3. Cache miss → call `call_json_model(provider_config, messages, schema)` from `provider_service.py`
4. Validate result against `StructuredAnalysis` or `ContentAnalysis` Pydantic schema
5. Store in `analysis_cache`
6. Return result as `dict`

`ANALYZER_VERSION = "1"` is a manual bump key. Changing the prompt templates should increment this to invalidate stale cache entries.

#### `job_state.py`

Each `transition_job_to_*` function:
1. Opens a new session via `async_session_factory()`
2. Queries the job by ID (with optional `expected_states` check to prevent race-condition resurrection of a finished job)
3. Sets new state and fields
4. Commits

`expected_states` guard: if the job's current state is not in `expected_states`, return `JobTransitionResult(success=False, job=job, error="...")`. The executor uses this for the failed/canceled transitions to avoid double-failing a job that another path already terminated.

#### `job_admission.py`

```python
async def admit_job(user, url, extraction_mode, workflow_mode, render_mode,
                    provider_config_id, db) -> JobAdmissionSuccess | JobAdmissionError:
```

Step 1: Resolve provider. Priority: explicit `provider_config_id` → user's `default_provider_id` → any owned provider. No provider → `NO_PROVIDER_CONFIGURED`.

Step 2: Advisory lock. `await db.execute(text("SELECT pg_advisory_xact_lock(:h)"), {"h": hash(user.id) % (2**31 - 1)})`. This serializes all concurrent admission attempts for the same user.

Step 3: Count active jobs. `SELECT count(*) FROM jobs WHERE user_id=:uid AND state IN :active_states`. If count >= `MAX_CONCURRENT_JOBS_PER_USER` → `ACTIVE_JOB_LIMIT_REACHED`.

Step 4: Create `Job(state=QUEUED, ...)`, add to session, commit. Return `JobAdmissionSuccess`.

#### `job_executor.py`

```python
async def execute_job_pipeline(job_id: int, provider_config_id: int) -> None:
```

This runs as a FastAPI `BackgroundTask` — in-process, no external queue. The function:

1. `QUEUED → ANALYZING`
2. `validate_url(job.url)` → on `URLValidationError`: fail with `error_code`
3. `check_robots(job.url)` → on BLOCKED/UNAVAILABLE with deny policy: fail
4. `fetch_url(job.url, job.render_mode)` → on `FetchError`: fail
5. `build_dom_summary(result.html, result.final_url)`
6. `analyze_page(provider_config, dom_summary, extraction_mode, content_hash)`
7. Determine final state: FAST+high_confidence+no_warnings → `ANALYSIS_READY`; else → `AWAITING_SETUP`

Outer `try/except Exception`:
- Any uncaught exception in any phase → `transition_job_to_failed("Unexpected error: ...")`
- This guarantees the always-finalize invariant

#### API endpoints: `app/api/v1/endpoints/jobs.py`

| Method | Path | Notes |
|--------|------|-------|
| `POST /jobs` | Create job | Returns 202; enqueues `execute_job_pipeline` as `BackgroundTasks` |
| `GET /jobs` | List jobs | `skip`/`limit` (max 100), ordered by `created_at DESC` |
| `GET /jobs/{id}` | Get job | 404 if not found or not owned |
| `POST /jobs/{id}/cancel` | Cancel | 409 if not in `ACTIVE_JOB_STATES` |
| `DELETE /jobs/{id}` | Delete | 400 if not in `DELETABLE_JOB_STATES`; 204 on success |

`create_job` admission error mapping:
- `NO_PROVIDER_CONFIGURED` → 409 `{"error_code": "NO_PROVIDER_CONFIGURED", "message": "..."}`
- `ACTIVE_JOB_LIMIT_REACHED` → 409 `{"error_code": "ACTIVE_JOB_LIMIT_REACHED", "message": "..."}`

### Watchdog extension: `app/services/watchdog.py`

`cleanup_stuck_jobs()` mirrors `cleanup_stuck_tasks()`. It queries jobs where `state IN (QUEUED, ANALYZING)` and `updated_at < now() - timeout`. Each stuck job is transitioned to FAILED with `error_code="WATCHDOG_TIMEOUT"` and `expected_states={QUEUED, ANALYZING}` — the guard prevents double-failing a job that legitimately just completed.

Timeout settings:
- `WATCHDOG_JOB_QUEUED_TIMEOUT_MINUTES` (default 3)
- `WATCHDOG_JOB_ANALYZING_TIMEOUT_MINUTES` (default 5)

### Readiness extension: `app/services/readiness.py`

Two new probes added to `check_db_ready`: lightweight `LIMIT 0` SELECTs on `jobs` and `analysis_cache`. These verify the tables exist and the ORM columns are correct without reading real data. The total probe count went from 5 to 7 (accounted for in `test_readiness.py`).

---

## Frontend

### `types.ts`

Added:
- `JobState` union type (all 6 states)
- `ExtractionMode`, `WorkflowMode`, `RenderMode` string unions
- `JobCreateInput`, `JobListItem`, `JobResponse`
- `StructuredAnalysis`, `ContentAnalysis`, `StructuredCandidateField`, `ContentMetadataField`

### `lib/api.ts`

Added `createJob`, `listJobs`, `getJob`, `cancelJob`, `deleteJob` methods.

### `lib/jobPolling.ts`

Analogous to `taskPolling.ts`. Exports:
- `TERMINAL_JOB_STATES`, `ACTIVE_JOB_STATES` — Set constants
- `shouldPollJob(job, consecutiveFailures)` — stops on terminal state or 3 failures
- `jobStateTone(state)` — maps to Badge color: ANALYSIS_READY=success, AWAITING_SETUP=accent, FAILED=danger, CANCELED=neutral, active=warning
- `jobStateLabel(state)` — human-friendly labels ("Analysis complete" for AWAITING_SETUP, because review/approve controls are not implemented yet)

### Pages

**`NewJobPage`**: URL form + 4 mode selectors (extraction, workflow, render, provider). On submit, `POST /jobs` → polls job status at 2s → shows PipelineProgress bar → on terminal state, shows confidence + warnings + link to detail page.

**`JobsPage`**: Full job list with 5s auto-refresh. Table shows mode, state badge with label, confidence %, date. Eye button links to `/jobs/:id`. Delete button disabled for active jobs.

**`JobDetailPage`**: URL-parameterized (`/jobs/:id`). Polls at 2s while active. Two specialized result components:
- `StructuredResult`: page type, estimated pages, confidence bar, repeated item selector, pagination selector, candidate fields table (name/selector/type/confidence/samples)
- `ContentResult`: content type, primary selector, recommended chunking, avg content length, metadata fields table

Both show warnings in an Info Alert. Cancel button visible for active jobs. Fetch metadata shown as key-value tiles.

---

## Runtime Lifecycle

### Success path (GUIDED, static page)

```
POST /jobs  →  admit_job()  →  202  →  BackgroundTask(execute_job_pipeline)

execute_job_pipeline:
  transition QUEUED → ANALYZING
  validate_url() [no-op for public HTTPS]
  check_robots() → ALLOWED (cache miss → fetch /robots.txt)
  fetch_url(render_mode=AUTO) → static fetch, no redirect needed
  build_dom_summary(html) → ~2000 char summary
  analyze_page() → cache miss → call_json_model() → validate schema → store cache
  confidence=0.85, warnings=[]
  workflow=GUIDED → transition ANALYZING → AWAITING_SETUP
```

Frontend polls `/jobs/:id` at 2s. After ~8–15s the state becomes `AWAITING_SETUP`, polling stops, and the UI shows the analysis result as complete. It does not show review/approve controls yet.

### Failure paths

- **Bad scheme**: `validate_url` raises `URLValidationError(SCHEME_NOT_ALLOWED)` → `FAILED(error_code="SCHEME_NOT_ALLOWED")`
- **robots blocked**: `check_robots` returns `BLOCKED` → `FAILED(error_code="ROBOTS_BLOCKED")`
- **Fetch timeout**: httpx `ReadTimeout` → `FetchError(error_code="FETCH_TIMEOUT")` → `FAILED`
- **LLM provider error**: `call_json_model` raises → outer catch → `FAILED(error_code="PROVIDER_ERROR")`
- **Playwright missing**: `FetchError(error_code="BROWSER_UNAVAILABLE")` → `FAILED` immediately if `render_mode=BROWSER`
- **Windows selector loop with Playwright**: fetcher uses the worker-thread sync Playwright path instead of failing with `NotImplementedError`
- **Watchdog**: Job stuck in QUEUED/ANALYZING past timeout → force-failed with `WATCHDOG_TIMEOUT`

---

## Concurrency and Crash Analysis

Background tasks run in-process (same Python process as the FastAPI server). Two jobs running concurrently for the same user are fine: each has its own `job_id` and each transition function opens an independent session and transaction.

**If the server process dies mid-job**: the job row remains in `QUEUED` or `ANALYZING`. On restart, the watchdog APScheduler job (which also starts on process startup) will detect the stuck job and fail it within 3–5 minutes.

**If `commit()` fails after setting `state=ANALYZING`**: the job remains in `QUEUED`. The executor crashes. The watchdog catches it later.

**If `commit()` fails after setting `state=AWAITING_SETUP`**: same job row stays in `ANALYZING`. The executor already completed its work; it just failed to persist the terminal state. Watchdog will eventually force-fail it. This is a known limitation of single-process in-memory background tasks without a persistent job queue — acceptable for Phase 1 single-instance self-hosting.

---

## Pitfalls

1. **`transition_job_to_*` must not receive a live session.** The endpoint calls `admit_job(db=db)` which uses the request-scoped session. The executor calls `transition_*` functions which open new sessions. These must never be mixed.

2. **Monkeypatching in tests: patch the reference site, not the source module.** All three functions imported directly in `jobs.py` (`admit_job`, `execute_job_pipeline`, `transition_job_to_canceled`) must be patched as `app.api.v1.endpoints.jobs.admit_job` etc., not `app.services.job_admission.admit_job`.

3. **`file:///path` hits the INVALID_URL guard, not SCHEME_NOT_ALLOWED, if netloc is checked first.** The scheme check must come before the netloc check (fixed in url_validator.py).

4. **Analysis cache invalidation**: if the LLM prompts change (better instructions, schema field additions), `ANALYZER_VERSION` must be bumped from `"1"` to `"2"`. Old cache entries with version `"1"` will remain but won't be returned for new analysis calls since the version is part of the cache key.

5. **DNS TOCTOU (time-of-check, time-of-use)**: DNS is resolved at validation time. The actual HTTP connection may go to a different IP if DNS TTL has expired. This is an accepted limitation — full SSRF prevention at the network layer requires an egress firewall, which is outside the scope of the application layer.

---

## Safe Evolution Notes

- **Adding a new terminal state** (e.g., `CANCELED_BY_ADMIN`): add to the `job_state` enum in a new migration using `autocommit_block()`, add to `TERMINAL_JOB_STATES` in `job.py`, add to `DELETABLE_JOB_STATES`.
- **Changing the analysis schema**: update the Pydantic models in `schemas/job.py`, bump `ANALYZER_VERSION` in `analyzer.py` to invalidate cache, update the prompt templates, update frontend type definitions in `types.ts`.
- **Adding a new render mode** (e.g., `PLAYWRIGHT_CDP`): add to the `render_mode` enum (migration + `RenderMode` enum in `job.py`), add handling in `fetcher.py`, update `RenderMode` in frontend `types.ts`.
- **Moving to a persistent job queue** (Redis/Celery): `execute_job_pipeline` is already a standalone async function with no FastAPI-specific dependencies. It can be wrapped in a Celery task or arq job with no changes to the service logic. The `BackgroundTasks` wiring in the endpoint is the only thing to replace.
