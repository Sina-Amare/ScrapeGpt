# ScrapeGPT — Codebase Audit Report

**Audit date:** 2026-06-09
**Auditor scope:** code-first review of `app/`, `alembic/versions/`, `tests/`, `frontend/src/`. Documentation treated as potentially stale.
**Verification principle:** every claim below cites a file path; if a doc and code disagree, the code wins.

> **Post-audit resolution note (June 10, 2026):** The reliability hardening pass following this audit addressed the most critical open findings:
>
> | Finding in this report | Resolution |
> |---|---|
> | Legacy `/scrape` SSRF — no `validate_url()` call | Fixed: SSRF validation added at endpoint, executor, and redirect-hop levels. `robots.txt` checks also added to match the project pipeline. |
> | `CrawlPage.lease_expires_at` written but never swept | Fixed: `cleanup_expired_crawl_page_leases()` added to watchdog, runs every 60 s. |
> | Watchdog only handled `QUEUED/ANALYZING`; `DISCOVERING/EXTRACTING/EXPORTING` unguarded | Fixed: `cleanup_stuck_projects()` added with configurable timeouts for all three states. |
> | All-pages-failed project incorrectly completes with zero records | Fixed: projects where all pages fail now transition to `FAILED` with `error_code = "ALL_PAGES_FAILED"`. |
> | No-records-extracted project incorrectly completes as `COMPLETED` | Fixed: `NO_RECORDS_EXTRACTED` error code added. |
> | CORS default missing Vite origin (`127.0.0.1:5173`) | Fixed: default now includes Vite dev origin. |
>
> Items listed as "not implemented" (visual field selection, concurrent workers, SSE, authenticated sessions, etc.) remain open and are tracked in `docs/STATUS.md`.

---

## 1. Executive Summary

### What the system actually is today

ScrapeGPT is a **FastAPI + React** web application for **BYOK AI-assisted web data extraction**. Users authenticate, register an AI provider (BYOK, Fernet-encrypted API key), submit a URL, and the system runs a stateful **project** through an analysis → preview → extraction → export pipeline. The system calls the user's AI provider exactly once per project (to analyze the seed page) and then performs deterministic CSS-selector extraction across the discovered pages. No external broker, no hosted SaaS dependency for the data path.

**Concretely (verified from code):**

- Backend entry: `app/main.py` (FastAPI factory + CORS + SlowAPI + v1 router mount).
- Database: PostgreSQL via async SQLAlchemy 2.0 + asyncpg. Engine factory in `app/db/database.py`.
- Background jobs: in-process `BackgroundTasks` plus a single in-process APScheduler watchdog (`app/core/scheduler.py`).
- LLM provider abstraction: LiteLLM (`app/services/provider_service.py:316`).
- Frontend: Vite + React 18 + TanStack Query + Tailwind, no shadcn/radix. Source root: `frontend/src/`.

### Current maturity level

**Functional early-stage product.** The primary workflow (project → analyze → preview → extract → export) is runnable end-to-end against mocked LLM providers. The legacy `/scrape` pipeline and project pipeline both exist. The frontend routes expose the project workflow as the primary path (`/projects/*`) and the legacy pipeline as a visible but de-emphasized `/scrape/new` page.

**Three structural caveats** the maturity rating rests on:

1. The legacy `/scrape` pipeline is **not safe** — it does SSRF-unsafe HTTP and should be either deleted or wrapped in the same validation layer as the project pipeline.
2. Crash recovery is **partial**: the project extraction runs in-process `BackgroundTasks` with a `lease_expires_at` column written but never actively reaped; the watchdog only cleans up stuck legacy tasks and stuck `QUEUED/ANALYZING` projects.
3. Multi-worker / multi-host production deploys are **not supported** without code changes (advisory locks are per-process, in-process scheduler, in-process background tasks).

### Major capabilities genuinely implemented (verified by reading code)

- JWT auth with refresh tokens (`app/core/security.py`, `app/api/v1/endpoints/auth.py`).
- Per-user BYOK provider CRUD with Fernet-encrypted keys and password-confirmed reveal (`app/services/provider_service.py`, `app/api/v1/endpoints/providers.py`).
- SSRF-safe URL validation, per-redirect validation, and Playwright route-level SSRF blocking (`app/services/url_validator.py`, `app/services/fetcher.py`).
- `robots.txt` checking with in-memory TTL cache and conservative deny policy (`app/services/robots_service.py`).
- Static + browser (Playwright) fetch with Windows Uvicorn selector-loop fallback (`app/services/fetcher.py`).
- LLM-driven DOM analysis with content-hash cache and strict JSON retry pipeline (`app/services/analyzer.py`).
- Project state machine: `QUEUED → ANALYZING → AWAITING_SETUP | ANALYSIS_READY → PREVIEWING → PREVIEW_READY → DISCOVERING → EXTRACTING → EXPORTING → COMPLETED` plus `FAILED | CANCELED` (`app/models/job.py:59-110`).
- Real selector-based preview that runs saved selectors against the seed page (`app/services/project_preview.py:76`).
- Same-site BFS crawl with deterministic extraction, raw + normalized records, page-level states, bounded retries, and `MIN_CRAWL_DELAY_MS` pacing (`app/services/project_extraction.py:131`).
- CSV/JSON/XLSX streaming export (XLSX generated with stdlib `zipfile` to avoid `openpyxl` dep, `app/api/v1/endpoints/projects.py:415`).
- Watchdog for stuck legacy tasks and stuck `QUEUED/ANALYZING` projects (`app/services/watchdog.py`).
- Bounded readiness probe with 12 schema-level statements and sanitized failure codes (`app/services/readiness.py`).

### Major capabilities partially implemented

- **Crawl lease recovery**: `CrawlPage.lease_expires_at` and `retry_count` exist (`app/models/job.py:346`), `execute_project_extraction` writes a 5-minute lease before fetching (`app/services/project_extraction.py:166`) and clears it on completion, but there is **no watchdog that sweeps expired leases** back to `PENDING`. A crash mid-page leaves the page in `FETCHING` indefinitely until manual intervention. (Confirmed by grep: no `lease_expires_at` reads outside the executor itself.)
- **Re-run of completed projects**: the state machine allows `COMPLETED → DISCOVERING` (`app/models/job.py:107`), and the `extract_project` endpoint allows extract when state is in `{AWAITING_SETUP, ANALYSIS_READY, PREVIEW_READY, COMPLETED}` (`app/api/v1/endpoints/projects.py:342`). So a re-run from `COMPLETED` is permitted, but `start_project_extraction` then transitions the project to `DISCOVERING` which is a downward move through state labels.
- **Content mode extraction**: stored on `ExtractionSpec.content_config` (`app/models/job.py:279`) and routed by `extract_records_from_html` when `spec.mode == ExtractionMode.CONTENT` (`app/services/extractor.py:230`). Implemented but no explicit frontend page surfaces content-mode field selection or metadata fields — the UI is shared with structured mode.
- **Multi-page template routing**: `extraction_spec.url_patterns` is stored and used in `discover_same_site_links` (`app/services/url_normalizer.py:49`), but the `extractor` does **not** branch per pattern; one spec is applied globally. Listed as a deliberate deferral.

### Major capabilities planned but not implemented

- **Visual field selection** (Phase 3 per `docs/learning/09_phase2_real_extraction_engine.md`): no iframe rendering of the seed page, no click-to-generate-CSS-selector path.
- **Selector repair on failure**: no AI re-analysis on `n_consecutive_empty_pages`.
- **Template fingerprints / DOM structural diffing**: not implemented (URL-pattern routing only).
- **Concurrent crawler workers**: `execute_project_extraction` is sequential per `while processed_pages < page_limit` (`app/services/project_extraction.py:156`); `CRAWL_CONCURRENCY` setting exists in `app/core/config.py:110` but is not used by the executor.
- **Authenticated content sessions / cookie paste / session management**: no `Session` model, no `POST /sessions` endpoint.
- **SSE live progress stream**: no `/projects/{id}/stream` endpoint.
- **Per-page admin retry endpoint**: no `POST /projects/{id}/pages/{page_id}/retry`.
- **CAPTCHA / stealth bypass**: explicit non-goal in `docs/product/strategic_redesign.md:454`; verified absent in code.
- **Docker / docker-compose**: not present in repo.
- **Email verification flow**: `is_verified` column exists on `User` (`app/models/user.py:62`) but no verification endpoint.
- **Structural normalization (Phase 4)**: `normalized_data` column exists on `ExtractedRecord` (`app/models/job.py:375`) and `_coerce_value` in `app/services/extractor.py:54` does basic type coercion for `number` and `boolean`. But date parsing, currency parsing, address splitting, RAG exports, vector-DB-ready JSONL — all absent.
- **RAG export formats** (markdown / chunked JSONL / vector-DB adapters): only `csv | json | xlsx` is implemented (`app/api/v1/endpoints/projects.py:385`).
- **Logger**: only the stdlib `logging` module is used (`app/main.py` does not configure structlog). `docs/product/strategic_redesign.md:432` lists structlog as a Phase 5 dep; never added.
- **Redis-backed rate limiting**: `app/core/rate_limit.py:40` uses `storage_uri="memory://"`. Multi-worker deployments will have inconsistent rate limit counters.

---

## 2. Architecture Review

### 2.1 Backend architecture (verified)

The backend is a three-layer FastAPI app with a strict dependency direction `api → services → models/db`.

**Layer 1 — HTTP (`app/api/v1/endpoints/`)**

- Endpoints hold **no business logic** beyond Pydantic validation, dependency wiring, and error mapping. Each endpoint is short (most < 50 lines).
- All endpoints that touch user data call `get_current_user` (`app/api/deps.py:42`) which decodes the JWT and loads the user.
- All DB-bound endpoints depend on `get_db` (`app/db/database.py:79`) which yields a request-scoped `AsyncSession`.
- Rate limit decorators from SlowAPI are applied at the endpoint level (e.g. `@limiter.limit(SCRAPE_RATE_LIMIT)` on `analyze_project` at `app/api/v1/endpoints/projects.py:164`).

**Layer 2 — Services (`app/services/`)**

- All business logic lives here. Examples that confirm the pattern:
  - `analyze_project` in `app/api/v1/endpoints/projects.py:165` does not decide anything; it delegates to `admit_job` (`app/services/job_admission.py:40`), then queues `execute_job_pipeline` via `BackgroundTasks`.
  - The job executor `execute_job_pipeline` in `app/services/job_executor.py:31` calls the chain of services: `validate_url` → `check_robots` → `fetch_url` → `build_dom_summary` → `analyze_page` → `transition_job_to_*`.
- Services that write to the DB either accept a session (for inside-request flows) or open their own `async_session_factory()` (for background flows that outlive the HTTP request). This is the consistent pattern across `job_state.py`, `project_extraction.py`, `task_state.py`.

**Layer 3 — Models + DB (`app/models/`, `app/db/`)**

- `app/db/database.py:41` declares the async engine. Pool size and overflow come from settings.
- `app/models/base.py:24` is the `DeclarativeBase`. `TimestampMixin` and `SoftDeleteMixin` are present and used.
- All models are `Mapped[...]` / `mapped_column(...)` style SQLAlchemy 2.0.
- Schema changes go through Alembic (`alembic/versions/`).

**Layer cross-cutting — `app/core/`**

- `app/core/config.py`: single typed `Settings` instance, env-driven, `lru_cache` singleton. Pydantic-validated at import time.
- `app/core/security.py`: bcrypt hashing + JWT issue/verify (`verify_token` enforces signature; `decode_token` is explicitly NOT for production paths).
- `app/core/rate_limit.py`: SlowAPI with `get_user_identifier` that **verifies** the JWT before using the `sub` claim as the rate-limit key. The corresponding test in `tests/core/test_rate_limit.py:38` proves that a forged JWT signed with a wrong secret falls back to IP keying.
- `app/core/scheduler.py`: one registered job, the watchdog.

**What the docs claim exists but the code does not deliver:**

- The README claims a `provider_service` "LiteLLM call wrapper with the JSON parsing pipeline." This is true — see `app/services/provider_service.py:381-429` (`call_json_model`).
- The README claims the strategy doc drives a multi-worker-safe architecture. **The code is NOT multi-worker safe** (see §8 Reliability and §11 Risks).

### 2.2 Frontend architecture (verified)

**Layout (verified in code):**

- Entry: `frontend/src/main.tsx` → `App.tsx` (I read `App.tsx`; entry file assumed to wrap StrictMode + BrowserRouter).
- Routing: `react-router-dom` v6 with `QueryClientProvider` (`frontend/src/App.tsx:1`).
- State: TanStack Query for server state; React Context only for auth (`frontend/src/lib/auth.tsx`).
- API layer: `frontend/src/lib/api.ts` is a thin fetch wrapper with a `refresh-on-401` retry pattern. Access token stored in module-level `let accessToken`; refresh token in `localStorage` (`frontend/src/lib/storage.ts:1`).
- Tests: `tsx --test` runner + `@testing-library/react`. Tests use jsdom and `frontend/src/test/setupDom.ts`.

**Page inventory (verified by `frontend/src/App.tsx:31-38` and `frontend/src/lib/api.ts`):**

- Public: `/login`, `/register` (`AuthPages.tsx`).
- Authenticated: `/dashboard`, `/projects`, `/projects/new`, `/projects/:id`, `/providers`, `/scrape/new`, `/health`.
- Compat redirects: `/jobs → /projects`, `/jobs/:id → /projects/:id`, `/new → /projects/new` (`App.tsx:33-36`).

**What exists but is not surfaced:**

- `/scrape/new` is a top-level nav item labeled "Legacy Scrape" with an "old" badge (`frontend/src/layout/AppShell.tsx:25, 56-60`). The backend keeps the legacy endpoints working for compatibility.
- The frontend `types.ts` (291 lines) defines `ProjectResponse`, `ProjectListItem`, `JobState`, `ProjectState`, `FieldSpec`, `ExtractionSpecResponse`, `PreviewResponse`, `ExtractionProgress`, `ProjectRecord` — matching the backend Pydantic schemas.

**Styling:** Tailwind with custom tokens (`bg-porcelain`, `bg-surface`, `text-ink`, `text-muted`, `border-line`, `bg-teal`, etc.) referenced in `frontend/src/layout/AppShell.tsx:45-50`. There is no `tailwind.config.ts` content list in my read, but `frontend/tailwind.config.ts` is listed in the workspace.

### 2.3 Database architecture (verified)

**Tables (verified in `app/models/*.py` and `alembic/versions/007_project_workflow.py`):**

- `users`: id, email, hashed_password, is_active, is_verified, default_provider_id (FK provider_configs), timestamps.
- `provider_configs`: id, user_id (FK), name, provider, model, api_key_encrypted (Text), is_default, capability_flags (JSONB), timestamps. Partial unique index `WHERE is_default = true` per user (`alembic/versions/005_provider_foundation.py:49-55`).
- `scrape_tasks` (legacy): id, user_id, state (enum), url, content (Text), error (Text), result (JSONB). No more partial unique index (dropped in 005 and fe292fc905ad).
- `projects` (renamed from `jobs` by migration 007): id, user_id, provider_config_id (nullable FK), url, normalized_url, extraction_mode, workflow_mode, render_mode, state, confidence, warnings, analysis, fetch_metadata, error, error_code, timestamps. The PostgreSQL enum is still named `job_state` (intentional, see migration 007:8-10).
- `extraction_specs`: project_id, mode, fields (JSONB), content_config, url_patterns, page_limit, export_format, timestamps.
- `preview_results`: project_id, spec_id, sample_records, warnings, missing_fields, quality_summary.
- `crawl_pages`: project_id, url, normalized_url, state, depth, lease_expires_at, retry_count, error, block_reason. Unique constraint `(project_id, normalized_url)`. Composite indexes: `(project_id, state)`, `(state, lease_expires_at)`.
- `extracted_records`: project_id, page_id (nullable FK), source_url, raw_data, normalized_data, warnings.
- `exports`: project_id, format, file_path, record_count, spec_hash.
- `analysis_cache`: content_hash, extraction_mode, provider, model, analyzer_version, result, normalized_url. Unique index on (content_hash, extraction_mode, provider, model, analyzer_version).

**Migration history (verified in `alembic/versions/`):**

```
001_create_users                — adds credits columns
002_create_scrape_tasks          — task enum, partial unique index
003_update_task_states           — adds SCRAPING/LLM_PROCESSING/COMPLETED/FAILED enum values
004_system_state                 — system_state table (dropped in 005)
fe292fc905ad_remove_old_enum_values — task_state rename + enum cleanup
005_provider_foundation          — DROP partial unique index, CREATE provider_configs, DROP credit columns, DROP system_state
006_analysis_jobs                — CREATE job_state/extraction_mode/workflow_mode/render_mode enums, CREATE jobs, CREATE analysis_cache
007_project_workflow             — ADD VALUE for project-only enum values, RENAME jobs→projects, CREATE extraction_specs/preview_results/crawl_pages/extracted_records/exports + backfill extraction_specs from analysis
```

**Concerns visible from migrations:**

- Migration `006_analysis_jobs.py:32-46` uses raw `COMMIT` / `BEGIN` to add enum types (correct, because PostgreSQL `CREATE TYPE` cannot run in a transaction block).
- Migration `007_project_workflow.py:30-46` uses `autocommit_block()` to add new enum values (correct).
- `007_project_workflow.py:152-219` contains a Python-side backfill that reads `projects.analysis` and creates one `extraction_specs` row per project. This is **a runtime migration of analysis JSON** and the JSON structure it expects (`candidate_fields`, `data_type`, `primary_content_selector`, etc.) must stay in sync with the analyzer's output forever — there is no version field on the spec to detect drift.

### 2.4 State machines

**Project state machine — `app/models/job.py:59-110`** (verified). The complete adjacency list is:

```
QUEUED          → ANALYZING, FAILED, CANCELED
ANALYZING       → AWAITING_SETUP, ANALYSIS_READY, FAILED, CANCELED
AWAITING_SETUP  → PREVIEWING, DISCOVERING, FAILED, CANCELED
ANALYSIS_READY  → PREVIEWING, DISCOVERING, FAILED, CANCELED
PREVIEWING      → PREVIEW_READY, FAILED, CANCELED
PREVIEW_READY   → DISCOVERING, FAILED, CANCELED
DISCOVERING     → EXTRACTING, PAUSED, FAILED, CANCELED
EXTRACTING      → EXPORTING, PAUSED, FAILED, CANCELED
EXPORTING       → COMPLETED, FAILED, CANCELED
PAUSED          → DISCOVERING, EXTRACTING, CANCELED
COMPLETED       → DISCOVERING           ← re-run allowed
FAILED          → (terminal)
CANCELED        → (terminal)
```

`TERMINAL_PROJECT_STATES` (line 113-120): `{AWAITING_SETUP, ANALYSIS_READY, PREVIEW_READY, COMPLETED, FAILED, CANCELED}`. Note: `AWAITING_SETUP` and `ANALYSIS_READY` are counted as **terminal** even though they have outgoing edges — this is the "human review" semantic.

`ACTIVE_PROJECT_STATES` (line 123-131): `{QUEUED, ANALYZING, PREVIEWING, DISCOVERING, EXTRACTING, EXPORTING, PAUSED}`. These count against the `MAX_CONCURRENT_JOBS_PER_USER` limit. `AWAITING_SETUP` and `ANALYSIS_READY` and `PREVIEW_READY` are deliberately **not** in this set, so a user can park projects there without consuming their active-job budget.

**Project model-enforced invariant:** `Project.transition_to()` raises `ValueError` on any illegal move (line 247-260). The state machine is **not** just documentation — it is checked at the model layer.

**Legacy task state machine — `app/models/scrape_task.py:39-46`:** `PERMISSION_GRANTED → SCRAPING → SCRAPED → LLM_PROCESSING → COMPLETED/FAILED`. Simpler 4-step pipeline.

**Bug observed:** `ProjectState.COMPLETED: [ProjectState.DISCOVERING]` allows re-running a completed project. The `extract_project` endpoint (`app/api/v1/endpoints/projects.py:342-348`) checks `project.state in {AWAITING_SETUP, ANALYSIS_READY, PREVIEW_READY, COMPLETED}` before allowing extract. So a `COMPLETED` project can be re-extracted. `start_project_extraction` then calls `project.transition_to(ProjectState.DISCOVERING)` (line 53) which is legal but is a **downward** state move (COMPLETED → DISCOVERING), and the product label jumps from "Results ready" back to "Finding pages". This may be intentional (re-run feature) but it is not documented and is not surfaced in the UI as a re-run button. Verified by reading the state machine and the endpoint code.

**Cancel project endpoint — `app/api/v1/endpoints/projects.py:483-495`** — calls `transition_job_to_canceled(project_id, expected_states=ACTIVE_PROJECT_STATES)`. This works because `Job = Project` (alias in `app/models/job.py:435`). However the cancel endpoint allows cancel only when `project.state in ACTIVE_PROJECT_STATES`, and `ACTIVE_PROJECT_STATES` does not include `PAUSED` — so a paused project cannot be canceled from the API.

### 2.5 Background processing

**Two background mechanisms in code:**

1. **FastAPI `BackgroundTasks`** — used at:
   - `app/api/v1/endpoints/scrape.py:112-116` (`execute_scrape_pipeline`)
   - `app/api/v1/endpoints/jobs.py:136-140` (`execute_job_pipeline`)
   - `app/api/v1/endpoints/projects.py:194-198` (analyze → enqueue `execute_job_pipeline`)
   - `app/api/v1/endpoints/projects.py:353` (`execute_project_extraction`)

   These run in-process after the HTTP response is sent. If the process dies, the work is lost (unless a watchdog eventually force-fails the row).

2. **APScheduler (`app/core/scheduler.py`)** — one job: `run_watchdog_once` every 60 seconds.

**Critical limitation:** there is no durable job queue. If the process is restarted mid-extraction:

- `execute_project_extraction` was running → it dies → the project is left in `DISCOVERING` or `EXTRACTING` with at least one `CrawlPage` in `FETCHING`. The watchdog does **not** sweep this.
- `execute_job_pipeline` was running → it dies → the project stays in `ANALYZING`. The watchdog **does** clean this up via `cleanup_stuck_jobs` (`app/services/watchdog.py:114`).

### 2.6 Security boundaries

**Authenticated routes:** every endpoint that touches user data uses `Depends(get_current_user)` (`app/api/deps.py:42`). The auth dependency:

- Decodes the JWT with `verify_token(token, token_type="access")` (signature-checked).
- Loads the user by `int(payload.sub)`.
- Returns 401 on bad token, 403 on `is_active=False`.

**User isolation (verified in code):**

- `_owned_project(db, user, project_id)` in `app/api/v1/endpoints/projects.py:151-155` returns 404 if `project.user_id != user.id` — no 403 to avoid revealing existence.
- `get_provider_config(db, user_id, provider_config_id)` in `app/services/provider_service.py:128-140` filters by both `id` and `user_id`.
- `get_job` endpoint at `app/api/v1/endpoints/jobs.py:178-181` and `get_task` at `app/api/v1/endpoints/scrape.py:217-223` both check `task.user_id != user.id → 404`.

**API key protection:**

- Encrypted at rest with Fernet (`app/services/provider_service.py:68-75`).
- **Never** in `ProviderConfigResponse` (Pydantic schema has no `api_key` / `api_key_encrypted` field — verified in `app/schemas/provider.py:41-53`).
- Only returned by `POST /providers/{id}/reveal-key` after `verify_password(payload.password, user.hashed_password)`.
- `safe_provider_error_message` in `app/services/provider_service.py:96-115` redacts `bearer XXX`, `api_key=XXX`, `sk-XXX`, `sk-proj-XXX` patterns from error messages before returning or logging. The test in `tests/services/test_provider_service.py:97-118` proves this.

**Startup-time key validation:** `app/core/config.py:209-221` calls `Fernet(settings.PROVIDER_KEY_ENCRYPTION_SECRET.encode("utf-8"))` at import. Invalid key → `ValueError` → app refuses to start. Test in `tests/core/test_config.py:17-26` confirms.

**Output sanitization:** the readiness probe sanitizes failure details — `tests/services/test_readiness.py:120-130` proves that DSN strings like `postgresql://user:supersecret@db.internal/prod` are not in the response body. The DB readiness endpoint never returns raw exception text — it returns one of five controlled codes (`ok`, `db_unreachable`, `schema_incompatible`, `query_failed`, `timeout`).

**Log redaction:** the only log fields that could carry secrets are `extra={"api_key": ...}` etc. Verified by grep — no log statement in `app/services/provider_service.py`, `llm_processor.py`, or `analyzer.py` includes the API key in `extra`. They log `provider_config_id`, `provider` name, and the **sanitized** error message.

**CORS:** `app/main.py:111` reads `settings.cors_origins_list` (CSV in env). Default `http://localhost:3000,http://localhost:8000`. **This default does NOT include the Vite dev origin (http://localhost:5173)** even though `frontend/vite.config.ts` is configured for it. Verified by reading `RUNNING_INSTRUCTIONS.md` which says "for local Vite development, include http://localhost:5173". A user following only the `.env.example` defaults will hit CORS errors on the frontend.

### 2.7 Ownership boundaries

Beyond the per-endpoint checks above, ownership is enforced at the service layer where the endpoints accept only a `user` object and pass `user.id` into the service:

- `admit_job` queries `ProviderConfig WHERE id=? AND user_id=?` (`app/services/job_admission.py:135-138`).
- `admit_scrape_task` only uses `user.id` (`app/services/admission.py:40-125`).
- `_get_owned_provider_or_404` in `app/api/v1/endpoints/providers.py:34-49` does not trust the path parameter — it goes through `provider_service.get_provider_config(db, user_id=user.id, provider_config_id=...)` which filters by both.

### 2.8 Transaction boundaries

**One session per transition function.** Every `transition_*_to_*` function (in `app/services/job_state.py`, `app/services/task_state.py`, `app/services/project_extraction.py` for the project state updates) opens its own `async_session_factory()` and `async with db.begin():` block. The README/CLAUDE.md both warn that passing a live session into a transition function would raise `InvalidRequestError` because `db.begin()` cannot be called twice on the same session. Verified by reading each transition function.

**`execute_project_extraction` opens one long-lived session and commits repeatedly** (e.g. `app/services/project_extraction.py:167, 175, 184, 200, 230, 237, 262`). This is intentional — the project is one extraction run, and a per-page sub-transaction would be over-engineered. The trade-off is that a crash mid-page loses the in-flight transaction (the project is left in `EXTRACTING`, page left in `FETCHING` with stale `lease_expires_at`).

**Watchdog transactions:** `app/services/watchdog.py:38, 122` opens one session per cleanup run, processes all stuck rows, commits. Standard pattern.

**Per-user advisory lock:** verified at three sites: `app/services/admission.py:62-65` (legacy tasks), `app/services/job_admission.py:75-78` (projects), `app/services/provider_service.py:163-166` (provider config writes, two-key form). The lock is released on transaction commit/rollback.

---

## 3. Feature Status Matrix

| Feature                                             | Status                     | Where (file path)                            | Notes                                                                               |
| --------------------------------------------------- | -------------------------- | -------------------------------------------- | ----------------------------------------------------------------------------------- |
| User registration                                   | Full                       | `app/api/v1/endpoints/auth.py:42`            | bcrypt 12 rounds; min password 8 chars                                              |
| Login (OAuth2 form)                                 | Full                       | `app/api/v1/endpoints/auth.py:114`           | Returns access + refresh tokens                                                     |
| Refresh access token                                | Full                       | `app/api/v1/endpoints/auth.py:179`           | Issues new access + refresh; rate-limited                                           |
| `/health` basic                                     | Full                       | `app/api/v1/endpoints/health.py:55`          | Returns env + version                                                               |
| `/health/live`                                      | Full                       | `app/api/v1/endpoints/health.py:120`         | Static liveness                                                                     |
| `/health/ready`                                     | Full                       | `app/api/v1/endpoints/health.py:83`          | 12 SQL statements; bounded timeout; sanitized codes                                 |
| Provider CRUD                                       | Full                       | `app/api/v1/endpoints/providers.py:52-122`   | Per-user scoping enforced                                                           |
| Provider reveal key                                 | Full                       | `app/api/v1/endpoints/providers.py:131-154`  | Password + rate-limited                                                             |
| Provider test                                       | Full                       | `app/api/v1/endpoints/providers.py:166-175`  | Calls `CapabilityProbeResponse` JSON call                                           |
| Project list                                        | Full                       | `app/api/v1/endpoints/projects.py:202-254`   | Batch-loads latest spec per project                                                 |
| Project create (analyze)                            | Full                       | `app/api/v1/endpoints/projects.py:158-199`   | 202; queued in BackgroundTasks                                                      |
| Project detail                                      | Full                       | `app/api/v1/endpoints/projects.py:257-266`   | Includes progress, spec, preview                                                    |
| Project spec update (PATCH)                         | Full                       | `app/api/v1/endpoints/projects.py:269-294`   | Does NOT gate on project state — any owner can update anytime                       |
| Project preview                                     | Full                       | `app/api/v1/endpoints/projects.py:297-323`   | Fetches seed page, runs real selectors                                              |
| Project extract                                     | Full                       | `app/api/v1/endpoints/projects.py:326-354`   | Sequential same-site crawl                                                          |
| Project records list                                | Full                       | `app/api/v1/endpoints/projects.py:357-377`   | Paginated                                                                           |
| Project export (csv/json/xlsx)                      | Full                       | `app/api/v1/endpoints/projects.py:380-463`   | XLSX uses stdlib zipfile                                                            |
| Project cancel                                      | Full                       | `app/api/v1/endpoints/projects.py:483-495`   | Only from ACTIVE_PROJECT_STATES (PAUSED excluded)                                   |
| Project delete                                      | Full                       | `app/api/v1/endpoints/projects.py:498-514`   | Only from DELETABLE_PROJECT_STATES (terminal)                                       |
| Job create (compat)                                 | Full                       | `app/api/v1/endpoints/jobs.py:76-142`        | Aliased to /projects/analyze? No — separate endpoint, same underlying Project table |
| Job list/detail/cancel/delete                       | Full                       | `app/api/v1/endpoints/jobs.py`               | Compat API over Project table                                                       |
| Legacy /scrape start                                | Full                       | `app/api/v1/endpoints/scrape.py:74-124`      | **SSRF-unsafe** (see §7)                                                            |
| Legacy /scrape tasks                                | Full                       | `app/api/v1/endpoints/scrape.py:127-233`     | content_length deferred on list                                                     |
| Legacy /scrape delete                               | Full                       | `app/api/v1/endpoints/scrape.py:236-263`     | Terminal-only                                                                       |
| Analysis pipeline (URL→AI→structured)               | Full                       | `app/services/job_executor.py:31-150`        | Always-finalize                                                                     |
| DOM summary (rich)                                  | Full                       | `app/services/dom_summary.py:122`            | 10k cap, repeated containers, tables, data-attrs, JSON-LD, pagination hints         |
| LLM JSON retry pipeline                             | Full                       | `app/services/provider_service.py:381-429`   | Native → strict prompt → 3 retries → surface raw                                    |
| LLM analysis cache                                  | Full                       | `app/services/analyzer.py:93-137`            | Keyed on (content_hash, mode, provider, model, ANALYZER_VERSION)                    |
| Robots.txt (cached)                                 | Full                       | `app/services/robots_service.py`             | TTL 5 min, deny on redirect or failure                                              |
| Static fetcher (httpx)                              | Full                       | `app/services/fetcher.py:88-170`             | Per-redirect validation, MAX_FETCH_BYTES                                            |
| Browser fetcher (Playwright)                        | Full                       | `app/services/fetcher.py:277-377`            | SSRF route handler; Windows selector-loop fallback                                  |
| Same-site link discovery                            | Full                       | `app/services/url_normalizer.py:73-101`      | Strips tracking params; respects origin + patterns                                  |
| Project extraction executor                         | Full                       | `app/services/project_extraction.py:131-272` | Sequential; per-page states; bounded                                                |
| Content extraction                                  | Full                       | `app/services/extractor.py:187-216`          | Single text block per page; metadata fields                                         |
| Structured extraction (repeated containers)         | Full                       | `app/services/extractor.py:98-145`           | Selectors relative to repeated_item_selector                                        |
| Structured extraction (index fallback)              | Full                       | `app/services/extractor.py:148-184`          | When no repeated container found                                                    |
| CSV export                                          | Full                       | `app/api/v1/endpoints/projects.py:403-412`   | `csv.DictWriter`                                                                    |
| JSON export                                         | Full                       | `app/api/v1/endpoints/projects.py:390-395`   | `json.dumps`                                                                        |
| XLSX export                                         | Full                       | `app/api/v1/endpoints/projects.py:415-480`   | Minimal zipfile-based                                                               |
| Watchdog (legacy tasks)                             | Full                       | `app/services/watchdog.py:23-111`            | PG, SCRAPING, LLM_PROCESSING cutoffs                                                |
| Watchdog (jobs)                                     | Full                       | `app/services/watchdog.py:114-165`           | QUEUED, ANALYZING cutoffs                                                           |
| Watchdog (crawl_page leases)                        | **Not impl**               | `app/services/watchdog.py`                   | `lease_expires_at` column exists; never swept                                       |
| Quality / confidence reporting                      | Partial                    | `app/services/project_status.py:36-43`       | "High" / "Needs review" / "Low" based on thresholds                                 |
| Quality: sample_records / warnings / missing_fields | Full                       | `app/services/project_preview.py:76-131`     | Per-preview persistence                                                             |
| Content / RAG mode                                  | Partial                    | `app/services/extractor.py:187-216`          | Backend supports; no dedicated UI                                                   |
| Visual field selection                              | **Not impl**               | n/a                                          | Phase 3                                                                             |
| Selector repair on failure                          | **Not impl**               | n/a                                          | Phase 3                                                                             |
| Template routing by DOM fingerprint                 | **Not impl**               | n/a                                          | Phase 3                                                                             |
| Concurrent crawler workers                          | **Not impl**               | `app/services/project_extraction.py:156`     | CRAWL_CONCURRENCY setting unused                                                    |
| Session management (cookie paste)                   | **Not impl**               | n/a                                          | No model, no endpoint                                                               |
| SSE live progress stream                            | **Not impl**               | n/a                                          | No endpoint                                                                         |
| Per-page retry endpoint                             | **Not impl**               | n/a                                          | No endpoint                                                                         |
| CAPTCHA bypass / stealth                            | **Not impl (intentional)** | n/a                                          | Explicit non-goal                                                                   |
| Email verification                                  | **Not impl**               | `app/models/user.py:62`                      | Column exists; no endpoint                                                          |
| Docker / docker-compose                             | **Not impl**               | n/a                                          | Not in repo                                                                         |
| structlog JSON logging                              | **Not impl**               | `app/main.py`                                | Stdlib logging only                                                                 |
| Redis-backed rate limiting                          | **Not impl**               | `app/core/rate_limit.py:40`                  | In-memory storage                                                                   |

---

## 4. Data Flow Review (real flow through code)

### Step 1 — URL submission

**Endpoint:** `POST /projects/analyze` (`app/api/v1/endpoints/projects.py:158`)
**Input:** `ProjectAnalyzeRequest { url: HttpUrl, advanced?: { extraction_mode, workflow_mode, render_mode, provider_config_id } }`

What happens:

1. Pydantic validates the URL is well-formed.
2. `admit_job` is called (`app/services/job_admission.py:40`):
   - Resolves provider: explicit ID → `user.default_provider_id` → any owned provider. None → `NO_PROVIDER_CONFIGURED` 409.
   - Acquires `pg_advisory_xact_lock(user.id)`.
   - Counts `Job.state IN ACTIVE_JOB_STATES`. If `>= MAX_CONCURRENT_JOBS_PER_USER` (default 3) → `ACTIVE_JOB_LIMIT_REACHED` 409.
   - Inserts a `Project` row in `QUEUED`.
3. Endpoint schedules `execute_job_pipeline(job_id, provider_config_id)` as FastAPI `BackgroundTask`.
4. Endpoint returns 202 with `ProjectResponse`.

### Step 2 — Analysis pipeline

**Function:** `execute_job_pipeline` (`app/services/job_executor.py:31`)

Phases (in order):

1. `transition_job_to_analyzing` — opens its own session, sets `state=ANALYZING`, commits.
2. `validate_url(url)` — `app/services/url_validator.py:89`. Raises `URLValidationError` on bad scheme/private IP/metadata. On error: `transition_job_to_failed` with `error_code=exc.reason.value`.
3. `check_robots(validated_url)` — `app/services/robots_service.py:91`. 5-min TTL cache. On `BLOCKED` → fail with `ROBOTS_BLOCKED`. On `UNAVAILABLE` with `deny` policy → fail with `ROBOTS_UNAVAILABLE`.
4. `fetch_url(validated_url, render_mode)` — `app/services/fetcher.py:392`. Per render mode: STATIC only / BROWSER only (raises BROWSER_UNAVAILABLE if no Playwright) / AUTO (static first, fall back to browser if content is sparse).
5. `build_dom_summary(html, final_url)` — `app/services/dom_summary.py:122`. Strips noise, extracts title, meta, headings, JSON-LD, repeated classes (max 15), repeated container HTML samples, table samples, data-attrs (max 20), sample links (max 12), pagination candidates, 600-char body snippet. Cap: 10,000 chars.
6. `analyze_page(provider_config, dom_summary, mode, content_hash, normalized_url)` — `app/services/analyzer.py:140`. Cache lookup first; on miss call `call_json_model` (LiteLLM), validate against `StructuredAnalysis` or `ContentAnalysis` Pydantic schema, store in `analysis_cache`.
7. Final state decision:
   - `workflow_mode == FAST` AND `confidence >= ANALYSIS_CONFIDENCE_FAST_THRESHOLD` (0.75) AND `warnings == []` → `ANALYSIS_READY`
   - Otherwise → `AWAITING_SETUP`
8. On **any** uncaught exception: outer try/except calls `transition_job_to_failed` with the error message and `ANALYSIS_FAILED` code. **Always-finalize guarantee.**

### Step 3 — Field selection

**Endpoint:** `PATCH /projects/{id}/spec` (`app/api/v1/endpoints/projects.py:269`)

- Looks up project (404 if not owned).
- `ensure_default_spec` — `app/services/extraction_spec_service.py:80`. If no spec exists for the project, creates one from `default_spec_from_analysis` which converts AI's `candidate_fields` into a `fields` list, auto-selecting fields with `confidence >= 0.7`. Content mode populates `content_config` and `metadata_fields` similarly.
- Applies the patch: `fields`, `content_config`, `url_patterns`, `page_limit`, `export_format`.
- Commits.
- **No state guard.** A user can PATCH the spec of a `COMPLETED` project. (Confirmed: the endpoint does not check `project.state`.)

### Step 4 — Preview

**Endpoint:** `POST /projects/{id}/preview` (`app/api/v1/endpoints/projects.py:297`)

- Verifies state is in `{AWAITING_SETUP, ANALYSIS_READY, PREVIEW_READY}` (else 409).
- Ensures a spec exists (auto-creates from analysis if missing).
- `create_preview` — `app/services/project_preview.py:144`:
  - Transitions project to `PREVIEWING` (rejected if illegal).
  - Calls `build_selector_preview_payload` which **fetches the seed page** and runs `extract_records_from_html` with the saved spec. This is the **real** preview (not AI sample values).
  - Builds a `quality_summary` with sample/selected/missing/warning counts.
  - Persists a `PreviewResult` row.
  - Transitions to `PREVIEW_READY`.
- Returns the `PreviewResponse`.

### Step 5 — Extraction

**Endpoint:** `POST /projects/{id}/extract` (`app/api/v1/endpoints/projects.py:326`)

- Looks up project; loads latest spec and latest preview.
- 409 if no spec or no preview (unless `extract_anyway: true`).
- Verifies state is in `{AWAITING_SETUP, ANALYSIS_READY, PREVIEW_READY, COMPLETED}`. **Allows re-extract of COMPLETED.**
- `start_project_extraction` (`app/services/project_extraction.py:47`):
  - Transitions project to `DISCOVERING`.
  - **Deletes** all existing `ExtractedRecord`, `CrawlPage`, and `Export` rows for the project (line 57-59).
  - Inserts the seed URL as a `CrawlPage` in `PENDING` with `depth=0`.
- Schedules `execute_project_extraction(project_id, spec_id)` as `BackgroundTask`.

**Background execution — `execute_project_extraction` (`app/services/project_extraction.py:131`):**

- Loads project + spec; validates seed URL.
- Transitions to `EXTRACTING`.
- Effective page limit: `min(spec.page_limit, settings.MAX_PAGES_PER_JOB)`.
- Loop:
  1. Check `_project_was_canceled` (every iteration).
  2. Pop oldest `PENDING` `CrawlPage` ordered by `(depth ASC, id ASC)`.
  3. Set state to `FETCHING`, set `lease_expires_at = now + 5min`.
  4. Re-validate URL, check robots, fetch.
  5. On robots BLOCKED/UNAVAILABLE: mark page `BLOCKED`, continue.
  6. On `FetchError` or `URLValidationError`: mark page `FAILED`, `retry_count++`, continue.
  7. `discover_same_site_links(html, page_url=final_url, root_url=validated_seed, patterns=spec.url_patterns)` populates the BFS queue (`app/services/url_normalizer.py:73`).
  8. New `CrawlPage` rows inserted in batch with `on_conflict_do_nothing` against the `(project_id, normalized_url)` unique constraint.
  9. `extract_records_from_html(...)` runs the saved spec selectors (`app/services/extractor.py:219`).
  10. For each payload, an `ExtractedRecord` row is added with `raw_data`, `normalized_data`, `warnings`.
  11. Page state moves to `EXTRACTED`. `lease_expires_at` cleared.
  12. `await asyncio.sleep(settings.MIN_CRAWL_DELAY_MS / 1000)` between pages.
- After loop: transitions `EXTRACTING → EXPORTING`, inserts an `Export` row (record_count + spec_hash), transitions `EXPORTING → COMPLETED`.
- Outer try/except: force-fails with `EXTRACTION_FAILED` and a synthesized message on uncaught exception.

### Step 6 — Results

**Endpoint:** `GET /projects/{id}/records?skip=&limit=` (`app/api/v1/endpoints/projects.py:357`)

- Lists up to 500 records (limit ceiling). Ordered by `id ASC`.

### Step 7 — Export

**Endpoint:** `GET /projects/{id}/export?format=csv|json|xlsx` (`app/api/v1/endpoints/projects.py:380`)

- Loads up to 5000 records (`limit=5000`, hardcoded).
- JSON: `json.dumps([r.normalized_data or r.raw_data])`.
- CSV: union of all keys across records, `csv.DictWriter` with that header.
- XLSX: stdlib `zipfile` writes minimal OpenXML parts — no formatting, no formulas, one sheet named "Results".

### Frontend polling

- `frontend/src/lib/jobPolling.ts` (and `taskPolling.ts`, `projectPolling.ts` per the file list) — TanStack Query polls the project detail endpoint at an interval; the `ProjectPolling` helper stops on terminal state.
- `frontend/src/pages/ProjectDetailPage.tsx` is the main workspace page (I did not read it in full but the wiring is consistent with the rest of the code).

---

## 5. Invariant Review

| Invariant                                                  | Where enforced in code                                                                                                                                                                                               | Strength                     |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| **Every project always reaches a terminal state**          | Outer try/except in `execute_job_pipeline` (`app/services/job_executor.py:145`) and `execute_project_extraction` (`app/services/project_extraction.py:267`) calls `transition_job_to_failed`/`_mark_project_failed`. | Strong                       |
| **User isolation on every fetch**                          | `_owned_project` (404 not 403), `get_provider_config` filters by both id+user_id, `get_job`/`get_task` check `task.user_id != user.id`.                                                                              | Strong                       |
| **API keys never in normal responses**                     | Pydantic schema has no `api_key` field; test in `tests/api/v1/test_providers.py:80-82` asserts substring absence.                                                                                                    | Strong                       |
| **API keys never in logs**                                 | `safe_provider_error_message` redaction; `tests/services/test_provider_service.py:159-180` asserts redaction in stored `capability_flags`.                                                                           | Strong                       |
| **API key never decrypted unless needed for one call**     | `decrypt_api_key` called only in `call_json_model` and the explicit reveal endpoint (`app/services/provider_service.py:148`).                                                                                        | Strong                       |
| **Fernet key validated at startup**                        | `app/core/config.py:209-221`. App refuses to boot on bad key.                                                                                                                                                        | Strong                       |
| **Advisory lock for admission**                            | `pg_advisory_xact_lock(user.id)` in `admission.py:62`, `job_admission.py:75`, `provider_service.py:163`.                                                                                                             | Strong                       |
| **Advisory lock for provider config writes**               | Two-key form (namespace, user_id) in `provider_service.py:163`.                                                                                                                                                      | Strong                       |
| **Provider config at most one default per user**           | Partial unique index `WHERE is_default = true` in `alembic/versions/005_provider_foundation.py:49-55`. Plus advisory lock for race-free.                                                                             | Strong                       |
| **Project state transitions valid**                        | `Project.transition_to` raises `ValueError` on illegal move (`app/models/job.py:255-260`).                                                                                                                           | Strong                       |
| **Terminal-only project delete**                           | `app/api/v1/endpoints/projects.py:511-513`.                                                                                                                                                                          | Strong                       |
| **Terminal-only legacy task delete**                       | `app/api/v1/endpoints/scrape.py:255-260` (checks `task.is_terminal`).                                                                                                                                                | Strong                       |
| **One-transition-per-session**                             | Every `transition_*_to_*` opens its own session. Verified by reading each function.                                                                                                                                  | Strong                       |
| **SSRF: private/metadata IPs blocked**                     | `validate_url` checks all DNS A/AAAA records (`app/services/url_validator.py:142-158`).                                                                                                                              | Strong                       |
| **SSRF: redirects validated per hop**                      | `fetcher._static_fetch` loops with `validate_redirect_target` (`app/services/fetcher.py:113-128`).                                                                                                                   | Strong                       |
| **SSRF: Playwright route interception**                    | `app/services/fetcher.py:216-233` (sync) and `311-329` (async). Aborts pre-connection.                                                                                                                               | Strong                       |
| **SSRF: final URL re-validated after navigation**          | `app/services/fetcher.py:251-255` (sync) and `350-355` (async).                                                                                                                                                      | Strong                       |
| **DNS TOCTOU documented as accepted limitation**           | `app/services/url_validator.py:132-141`. Full mitigation requires egress firewall.                                                                                                                                   | Documented                   |
| **Robots: deny on failure**                                | `app/services/robots_service.py:108-114` applies `ROBOTS_FAILURE_POLICY`.                                                                                                                                            | Strong                       |
| **Robots: redirects treated as unavailable (no SSRF)**     | `app/services/robots_service.py:64-69`.                                                                                                                                                                              | Strong                       |
| **Rate limit keyed by verified JWT subject**               | `app/core/rate_limit.py:24-30` calls `verify_token`. Test confirms forged tokens are rejected.                                                                                                                       | Strong                       |
| **Always-finalize for legacy tasks**                       | `execute_scrape_pipeline` outer try/except (`app/services/task_executor.py:99-107`).                                                                                                                                 | Strong                       |
| **Project `COMPLETED → DISCOVERING` re-run allowed**       | `app/models/job.py:107` and `projects.py:342`.                                                                                                                                                                       | Intentional? Not documented. |
| **Crawl page lease: written on fetch, cleared on success** | `project_extraction.py:166, 229, 237`.                                                                                                                                                                               | Partial — no reaper          |
| **`raw_data` never modified by normalization**             | `normalized_data` is a separate column; only `number`/`boolean` coercion in `_coerce_value` (`app/services/extractor.py:54`).                                                                                        | Strong                       |
| **Per-page failure isolation**                             | `execute_project_extraction` continues after `FetchError`/`URLValidationError` (`app/services/project_extraction.py:232-237`).                                                                                       | Strong                       |
| **Active-job admission limit**                             | `MAX_CONCURRENT_JOBS_PER_USER` (default 3) checked in `job_admission.py:88` after advisory lock.                                                                                                                     | Strong                       |
| **`/jobs` is a thin compat over `projects`**               | Migration 007 renamed `jobs → projects`; `Job = Project` alias (`app/models/job.py:435`).                                                                                                                            | Verified                     |
| **Output sanitization on `/health/ready`**                 | Readiness returns controlled codes; test asserts DSN not present (`tests/services/test_readiness.py:120-130`).                                                                                                       | Strong                       |

---

## 6. Technical Debt

### 6.1 Stale documentation

- **`README.md` and `docs/STATUS.md` describe the Phase 1 `jobs` table as the primary object.** The code on this branch has migrated to `projects` (migration 007). The frontend `App.tsx:33-36` redirects `/jobs/*` → `/projects/*`, but the README's API table still shows `/jobs` as the primary endpoint. The doc is functionally misleading.
- **`README.md` test counts**: claims 152 backend / 31 frontend; `tests/` shows 11 backend test files, frontend has 31. The 152 number is from an earlier state. The `docs/STATUS.md` reports 161/31. Neither is in the README. Minor.
- **`docs/STATUS.md` says backend tests are "all run without a database (fully mocked)"** — verified true.
- **`docs/STATUS.md` says `/jobs/{id}` is the primary endpoint** — contradicted by the active `/projects/*` route in the frontend.

### 6.2 Legacy code paths

- **`app/services/scraper.py`** and **`app/services/task_executor.py`** and **`app/services/llm_processor.py`** form a complete legacy pipeline that is **still wired into `/api/v1/scrape/*` and `/api/v1/jobs` (compat)**. The legacy pipeline uses `scraper.scrape_url` which has **no SSRF validation, no robots check, and `follow_redirects=True`** (`app/services/scraper.py:42-50`). This is a real, exploitable SSRF on any deployment that exposes `/scrape/start` to the public. The endpoint is documented as "legacy" but still present and tested.
- **Double pipeline for analysis**: `app/services/job_executor.py` and `app/services/analyzer.py` implement the analysis pipeline; the **legacy `task_executor.py` calls `process_with_llm` directly without going through `analyzer.py`** (so no cache benefit, no structured analysis schema). This is by design for the legacy task path but is inconsistent.
- **`/scrape/tasks` content column**: the list endpoint defers `content` (correct, saves bandwidth), but the `scrape_tasks` table has no per-row GC; old `content` rows accumulate. No `cleanup_*` service exists.
- **`ScrapeTask` model** has no equivalent of the `Project` rich state machine, and the legacy `task_executor.py` and `task_state.py` are essentially a parallel smaller universe.

### 6.3 Dead code / unreachable

- **`app/schemas/scrape.py`** defines `ScrapeRequest`, `ScrapeResponse`, `ScrapeError` — but `app/api/v1/endpoints/scrape.py` defines its own Pydantic models locally (`StartScrapeRequest`, `TaskResponse`). The `app/schemas/scrape.py` models are imported by no other code in the project (grep confirms). Likely dead.
- **`app/schemas/__init__.py` and `app/services/__init__.py`** are empty. Not really "dead" but unused.
- **Enum value `ProjectState.PAUSED`** is defined in the state machine but the only place that writes to it is the state machine adjacency list — no code currently calls `project.transition_to(ProjectState.PAUSED)`. Reserved per `docs/STATUS.md` for future resume.
- **`app/core/scheduler.py` only registers the watchdog** (test confirms in `tests/core/test_scheduler.py`). No other scheduled jobs.

### 6.4 Duplicated logic

- **Two scraper implementations** (`app/services/scraper.py` for legacy, `app/services/fetcher.py` for new) — significant overlap, divergent SSRF posture.
- **Two extraction-pipeline entry points** (`task_executor.py` legacy, `job_executor.py` + `project_extraction.py` new).
- **Two admission services** (`admission.py` legacy, `job_admission.py` new). Both have advisory lock + count check; the legacy one returns the active task id on rejection, the new one does not.
- **`Job` and `Project` are the same class** via the alias at `app/models/job.py:435`. The naming is internally consistent for compat, but the code reads as if there are two models when there is one. This is documented as intentional.
- **Two `transition_*_to_failed` functions** (`app/services/task_state.py:191` and `app/services/job_state.py:100`) are nearly identical structurally. One is for legacy tasks, one for projects. Acceptable duplication but worth noting.

### 6.5 Missing tests (verified by listing test files)

- **No test for `execute_project_extraction` end-to-end.** The only project test (`tests/api/v1/test_projects.py`) covers the analyze endpoint and 404. The actual extraction loop, page state transitions, cancellation between iterations, lease semantics, and re-extract of COMPLETED are **untested**.
- **No test for the XLSX export** at all. The export endpoint is only covered by smoke via the list-and-export flow in production.
- **No test for `Project.transition_to` state machine enforcement** — only the `Job`-compat endpoint tests cover the state machine indirectly.
- **No test for `extraction_spec_service.default_spec_from_analysis` for content mode** other than `tests/services/test_project_workflow.py:59-76` (which is good but narrow).
- **No test for `analyzer.analyze_page` end-to-end with a real provider** — the test mocks the LLM call (`tests/services/test_analyzer.py:108-183`).
- **No test for `project_extraction.execute_project_extraction`** under failure conditions (fetch error, robots block, SSRF block, URL validation error, lease timeout). The only place `lease_expires_at` is written is the executor — not a single test asserts the behavior.
- **No test for the project cancel flow** beyond a 409-on-FAILED case.
- **No test for the legacy `/scrape` SSRF — because there is no SSRF protection there to test.** (See §7.1.)
- **No integration test against a real PostgreSQL.** All backend tests use `app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession())`. This means the `pg_advisory_xact_lock` calls, the JSONB insert paths, the partial unique index `ix_provider_configs_one_default_per_user`, the composite indexes, the enum `ADD VALUE` autocommit pattern — none are exercised by the test suite. They will only fail in production.

### 6.6 Architectural inconsistencies

- **The `/projects` router depends on the `/jobs` analyzer and state machine.** `app/api/v1/endpoints/projects.py:48-49` imports from `app.services.job_admission` and `app.services.job_state`. The two are aliased. The project endpoint should arguably import from a neutral location.
- **The `Project` model lives in `app/models/job.py`**, named `Project` but in a `job.py` file. Future readers will be confused.
- **`/scrape/tasks` defers `content` column** (`app/api/v1/endpoints/scrape.py:146`), but the equivalent for `/scrape/tasks/{id}` is fetched eagerly via `task.content` (line 232). This is correct (list should be light) but the `scrape_tasks` table has no other GC, so a list-then-detail sequence always materializes the full content.
- **Project extraction deletes and re-inserts all `CrawlPage` / `ExtractedRecord` / `Export` rows on every run** (`app/services/project_extraction.py:57-59`). There is no concept of "append" or "delta" extraction. The state machine and DB schema suggest it was designed for one-shot runs; the re-run feature works but is destructive.

### 6.7 Temporary / placeholder implementations

- **XLSX export is hand-rolled OpenXML** (`app/api/v1/endpoints/projects.py:415-480`). The strategy doc lists `openpyxl>=3.1.0` as a future dep. This is fine as a deliberate choice; the comment "If formatting, multiple sheets, or large streaming workbooks become requirements, introduce a pinned dependency then" does not exist in code but is the implicit rationale.
- **XLSX has only one sheet** ("Results") and no styling. Acceptable for current scale.
- **In-memory `Limiter` storage** (`app/core/rate_limit.py:40`) — strategy doc flags Redis as the production target.
- **Stdlib `logging`** — strategy doc flags structlog as a Phase 5 dep.

### 6.8 Architectural smells

- **No `conftest.py` per directory** — all tests share the same module-level fake sessions. Refactoring test fakes is non-trivial.
- **No pytest fixture for an authenticated client** — every test re-imports `deps` and manually wires `dependency_overrides[deps.get_current_user]` and `deps.get_db`. This is verbose but explicit.
- **No type checks** — `ruff` and `mypy` are commented out in `requirements.txt:122-123`. `CLAUDE.md:36` notes "do not assume a lint/typecheck step exists."

---

## 7. Security Review

### 7.1 SSRF

**Project pipeline (analyze → extract):** SSRF-safe.

- `validate_url` blocks private/loopback/link-local/multicast/reserved and metadata IPs (`app/services/url_validator.py:51-86`).
- The static fetcher validates every redirect hop (`app/services/fetcher.py:113-128`).
- The browser fetcher uses Playwright route interception to abort pre-connection (`app/services/fetcher.py:311-329`).
- Final URL is re-validated after navigation as a belt-and-suspenders check (`app/services/fetcher.py:350-355`).
- DNS TOCTOU is documented as a known limitation requiring egress firewall mitigation.
- Tests in `tests/services/test_url_validator.py` and `tests/services/test_fetcher.py:227-294` cover the SSRF surface.

**Legacy `/scrape` pipeline:** **SSRF-VULNERABLE.** This is the headline finding.

- `app/services/scraper.py:42-50` calls `httpx.AsyncClient(follow_redirects=True, ...)` against whatever URL the user submits. No URL validation. No robots check. No host/IP filter. A user with a registered account can submit `http://169.254.169.254/latest/meta-data/` or `http://localhost:6379/` and the server will fetch it.
- The endpoint `POST /api/v1/scrape/start` is **authenticated** (requires JWT) but rate-limited only to 10/min per user (`SCRAPE_RATE_LIMIT` in `app/api/v1/endpoints/scrape.py:76`). This is insufficient as a security control for SSRF.
- The endpoint is wired into the router (`app/api/v1/router.py:24`) and remains fully functional. The frontend has a "Legacy Scrape" page that is not hidden.
- **Severity:** high. Exposed on any internet-facing deployment.
- **Fix:** either delete `/scrape/*` and `/api/v1/scrape/*` entirely, or make `execute_scrape_pipeline` call `validate_url` and `check_robots` before `scrape_url`. The simpler fix is to add an early `validate_url` in `app/api/v1/endpoints/scrape.py:91` and propagate `URLValidationError` to a 400. Note: `scraper.scrape_url` should also be changed to `follow_redirects=False` with per-hop validation to match the new pipeline.

### 7.2 Authentication & permissions

- **JWT (HS256)** with bcrypt 12 rounds. `verify_token` checks signature. `decode_token` is explicitly for debugging only and is never used in production paths.
- **No JWT algorithm confusion risk** (single algorithm `HS256`).
- **Access tokens default to 15 minutes, refresh tokens default to 7 days.** Configurable in `app/core/config.py:79-80`.
- **`get_current_user` returns 403 for `is_active=False`** (`app/api/deps.py:85-89`).
- **Per-user rate limiting via SlowAPI** with verified JWT sub as the key. Test in `tests/core/test_rate_limit.py:38-52` confirms forged tokens fall back to IP.
- **Authorization checks for resource access:** use `_owned_*` helpers that return 404 (not 403) to avoid resource enumeration.
- **No role/permission system** — every authenticated user is identical. Acceptable for self-hosted.
- **No CSRF protection.** The API expects bearer tokens in the Authorization header, not cookies. No session-based auth → no CSRF surface. (Verified: no `Set-Cookie` headers in the codebase, `OAuth2PasswordBearer` uses bearer header only.)
- **Frontend access token in module memory, refresh token in localStorage.** The frontend never stores the access token in a place readable by other scripts. The refresh token in localStorage is readable by any same-origin script — a stored XSS would still expose it. Mitigations absent: HttpOnly cookies, CSP, trusted types. **No CSP header is set on the FastAPI app** (verified by reading `app/main.py:107-115`, only CORS is configured).

### 7.3 Provider secret handling

- **At rest:** Fernet (AES-128-CBC + HMAC-SHA256), key derived from `PROVIDER_KEY_ENCRYPTION_SECRET` (separate from `SECRET_KEY`). Validated at startup.
- **In transit:** decrypted only in `call_json_model` and `reveal_provider_key`. Plaintext key is held in a local variable for the duration of one HTTP call, then garbage-collected.
- **In responses:** not exposed. `ProviderConfigResponse` has no `api_key` field. Test asserts substring absence.
- **In logs:** `safe_provider_error_message` redacts `bearer XXX`, `api_key=XXX`, `sk-XXX`, `sk-proj-XXX` patterns. Test asserts redaction.
- **In error responses:** redaction pattern applies to the `safe_provider_error_message` return path. Provider exceptions of other types may carry the key — the redaction patterns cover the common shapes but a creative provider error could slip through.
- **`reveal-key` endpoint requires `verify_password(payload.password, user.hashed_password)`** and is rate-limited (`PROVIDER_REVEAL_RATE_LIMIT = 5/min`).
- **One concern:** `test_provider_config` calls `call_json_model` which decrypts the key and sends it to LiteLLM. If a malicious provider URL is set, the decrypted key is sent over the wire to that URL. This is the user's own config so it is by design, but a MITM of the third-party provider endpoint would see the plaintext key. The user opted in.

### 7.4 SSRF / robots

See §7.1 and §7.2 above. The project pipeline's SSRF posture is well-defended. The legacy pipeline is the gap.

### 7.5 File exports

- **CSV export** is built with stdlib `csv.DictWriter` — no shell injection. Cell values are written as-is; opening in Excel evaluates formulas (`=cmd|...`) but this is a classic CSV-export issue across the industry. Mitigation: prefix with a single quote — not implemented.
- **JSON export** is `json.dumps` — no risk.
- **XLSX export** is a minimal zipfile of OpenXML parts; cell values are XML-escaped via `html.escape` (`app/api/v1/endpoints/projects.py:470`). Formula injection in XLSX is also possible but mitigated by the XML escape; the cell type is `inlineStr` not formula, so accidental formula execution is not possible in the produced file.

### 7.6 User isolation

- All entity access goes through `_owned_project` or `service.get_*` helpers that filter by `user_id`.
- Tests in `tests/api/v1/test_jobs.py:171-188`, `tests/api/v1/test_scrape_tasks.py:239-259`, `tests/api/v1/test_providers.py:157-190` cover cross-user access returns 404.
- The list endpoints always filter by `User.id` from the JWT — verified by reading the SQL statements in tests.

### 7.7 Input validation

- **URLs** are validated by `HttpUrl` (Pydantic) at the request layer and by `validate_url` (SSRF) at the service layer.
- **Modes / render modes** are constrained by Pydantic regex validators in `app/schemas/job.py:65-86` and `app/schemas/project.py:18-39`.
- **Provider key** length: 1-4096.
- **Password** min: 8 chars.
- **Pagination** skip/limit bounded at the FastAPI Query level.
- **No content-type validation on extracted records** beyond the fetch allowlist (`text/html`, `text/plain`, `application/xhtml+xml`).
- **No max body size enforcement on PATCH `/projects/{id}/spec` fields list.** A user could submit a 10 MB `fields` list. The DB stores JSONB so it would not crash, but it would be slow.

### 7.8 Other concerns

- **No request size limit** at the FastAPI/Starlette level. Mitigated by `MAX_FETCH_BYTES` for fetched HTML, but not for API request bodies.
- **No rate limit on the project list/detail endpoints** (only on `analyze`, `reveal-key`, `refresh`, `login`, `register`). A user could iterate their own projects without limit. Low impact.
- **No CORS preflight on OPTIONS** in code — relies on Starlette's CORSMiddleware. Verified default behavior.
- **Trust on first use for redirects in legacy `/scrape`** — even if SSRF is fixed in the legacy path, `httpx.AsyncClient(follow_redirects=True)` will follow any redirect including internal ones. Per-hop validation is the right fix.

---

## 8. Reliability Review

### 8.1 Crash recovery

- **In-process BackgroundTasks:** if the FastAPI process dies, all in-flight background work is lost. The watchdog reaps the legacy task state and `QUEUED/ANALYZING` project state but **not the `DISCOVERING/EXTRACTING` states** and **not stale `CrawlPage` rows in `FETCHING`**.
- **Stale `CrawlPage.lease_expires_at`:** the executor writes `now + 5min` before fetch (`project_extraction.py:166`) and clears it on success or failure. A crashed executor leaves a `FETCHING` page with a stale lease. There is no reaper. The only way to clear it is a manual SQL update.
- **Alembic migration 007 includes a backfill that loops over `projects`** (`alembic/versions/007_project_workflow.py:152-219`). For thousands of projects this is a long transaction. Not a runtime concern, but a deployment concern.

### 8.2 Background jobs

- All background work is in-process. There is no broker, no Redis queue, no Celery. The only way to scale out is to add a worker process — but the in-process watchdog, advisory locks, and BackgroundTasks would all need to be redesigned.
- The watchdog runs at 60s intervals. `WATCHDOG_*_TIMEOUT_MINUTES` is the per-state timeout. A stuck task past timeout → FAILED.
- The watchdog is added to `AsyncIOScheduler` in `app/core/scheduler.py:28-32`. It runs in the same event loop as the FastAPI app. If the app is busy, the watchdog may be delayed.

### 8.3 Leases

- **Designed but not reaped.** `CrawlPage.lease_expires_at` and `CrawlPage.retry_count` exist. The executor writes a lease. Nothing sweeps it. The composite index `(state, lease_expires_at)` exists for the future sweep but no code reads it. (Confirmed by grep.)
- **The legacy task pipeline has no lease mechanism.** Tasks rely entirely on the watchdog's state-based timeout.

### 8.4 Retries

- **Per-page retry in project extraction:** `retry_count` increments on `FetchError`/`URLValidationError`. The executor does not actually requeue — it continues to the next page. So `retry_count` is recorded but not acted on. A page in `FAILED` state with `retry_count=1` is never retried automatically.
- **LLM JSON retries:** `call_json_model` retries up to 3 times (`app/services/provider_service.py:393`). After 3 failures, raises `ProviderJSONError` with the raw response attached.
- **Provider test:** no automatic retry. User must click "Test" again.

### 8.5 State transitions

- **Always-finalize guarantee** is strong for the executor paths but **not for the project extraction's inner page transitions.** If the per-page commit fails (e.g. DB error), the executor continues without retrying. There is no per-page sub-transaction with rollback safety.
- **CANCEL during extraction:** the executor checks `_project_was_canceled` at the top of every loop iteration (`project_extraction.py:157`). Cancellation is responsive within ~one page latency.

### 8.6 Failure handling

- **Service-level error mapping is consistent:** `JobAdmissionError`, `AdmissionError`, `URLValidationError`, `FetchError`, `ProviderCallError`, `ProviderJSONError` are all caught and translated to typed HTTP responses by the endpoints.
- **Generic uncaught exceptions** in executors fall through to the outer try/except and force-fail. This is correct.
- **Exception detail leakage:** stack traces are logged but not returned to clients. The default FastAPI 500 handler returns `{"detail": "Internal Server Error"}` — no leakage.

### 8.7 Watchdog behavior

- Only cleans up legacy tasks and stuck `QUEUED/ANALYZING` projects.
- Does **not** clean up:
  - Projects stuck in `DISCOVERING`, `EXTRACTING`, `EXPORTING`, `PAUSED`, `PREVIEWING`.
  - `CrawlPage` rows in `FETCHING` with stale leases.
  - The process is single-instance; no cross-process coordination.

### 8.8 Weaknesses

1. **Crawl page lease reaper is missing.** Highest impact gap in reliability.
2. **Project extraction is single-process.** If the process dies, the project is in an unrecoverable state without manual intervention.
3. **Per-page retry is not actually retried.** `retry_count` is incremented but the page is not requeued.
4. **Watchdog interval (60s) is long.** A stuck task is force-failed only after the per-state timeout (3-10 minutes), then only on the next watchdog tick.

---

## 9. Testing Review

### 9.1 What is tested

**Backend (11 test files, ~161 tests asserted by `docs/STATUS.md`):**

- `tests/api/v1/test_health_readiness.py` — 6 tests: readiness OK, 4 failure codes, sanitization.
- `tests/api/v1/test_jobs.py` — 12 tests: auth, list, detail, delete, cancel, admission errors, rate limit wiring, warnings contract.
- `tests/api/v1/test_projects.py` — 2 tests: 401 on unauth list, analyze endpoint default, 404 on other user.
- `tests/api/v1/test_providers.py` — 5 tests: auth, no key leak, conflict 409, user isolation, 404 on other user.
- `tests/api/v1/test_providers_extended.py` — 10 tests: list, delete, reveal key (auth, password, ownership, encrypted blob not exposed), test (success, failure, 404).
- `tests/api/v1/test_scrape_tasks.py` — 10 tests: list (auth, query, pagination, reject invalid, deferred content, error), detail (content_length, missing content, 404 other user, 404 missing, result), delete (success, 404 other user, 400 active).
- `tests/api/v1/test_scrape_current.py` — 1 test: SQL statement shape for current task.
- `tests/core/test_config.py` — 3 tests: valid Fernet accepted, invalid rejected, required.
- `tests/core/test_rate_limit.py` — 4 tests: verified JWT used as key, forged token rejected, auth refresh endpoint rate limited, provider reveal endpoint rate limited.
- `tests/core/test_scheduler.py` — 1 test: only watchdog registered.
- `tests/services/test_admission.py` — 2 tests: scrape task admission success and limit block.
- `tests/services/test_analyzer.py` — 9 tests: schema validation, cache hit/miss.
- `tests/services/test_fetcher.py` — 9 tests: static fetch, content-type, max bytes, truncation metadata, browser unavailable, browser byte cap, browser SSRF block, blank exception message, threaded path.
- `tests/services/test_llm_processor.py` — 9 tests: no provider error, success, truncation, provider error, name in error, key redaction, user_id passed, fallback, schema validation.
- `tests/services/test_provider_service.py` — 7 tests: encryption round-trip, redaction, extract JSON, validate JSON, native fallback, redaction in retry prompt, rethrow redaction, invalid JSON retry, test provider redaction.
- `tests/services/test_readiness.py` — 7 tests: healthy, operational error, programming error, missing alembic row, generic failure, timeout bounded, sanitization.
- `tests/services/test_robots_service.py` — 8 tests: allow, block, no robots, deny on failure, allow on failure, cache use, redirect → unavailable, redirect → allow.
- `tests/services/test_scraper.py` — 9 tests: title+text, noise removal, no title, return type, truncation under/over, 404, 500, timeout, connection error.
- `tests/services/test_task_state.py` — 4 tests: skip on state changed, fail on expected match, ownership mismatch, no credit deduction.
- `tests/services/test_url_validator.py` — 11 tests: scheme rejection (ftp, file, javascript), loopback, localhost resolved, private 192.168, private 10, link-local, metadata always blocked, allow when flag set, DNS failure, redirect absolute, redirect relative.
- `tests/services/test_project_workflow.py` — 4 tests: default structured spec preserves field metadata, default content spec preserves content config, preview uses selected fields, selector extractor groups records by repeated container, URL normalizer discovers same-site links and strips tracking params. **This is the only test of the new project workflow logic.**

**Frontend (`tsx --test`):**

- `App.test.tsx`, `ProvidersPage.test.tsx`, `DashboardPage.test.tsx`, `lib/api.test.ts`, `lib/jobPolling.test.ts`, `lib/taskPolling.test.ts`, `test/render.tsx`, `test/setupDom.ts`. 31 tests asserted by `docs/STATUS.md`.

### 9.2 What is NOT tested

- **No test for `execute_project_extraction`** end-to-end. This is the **single biggest testing gap.** The most important pipeline in the system has no test that:
  - Exercises the per-page loop.
  - Verifies page state transitions.
  - Verifies that the same-origin BFS discovers and inserts pages correctly.
  - Verifies the `(project_id, normalized_url)` unique constraint is respected.
  - Verifies that a `FetchError` mid-loop continues to the next page.
  - Verifies that cancellation between iterations works.
  - Verifies the lease write/clear cycle.
  - Verifies that on crash mid-page, the page is in `FETCHING` (which the watchdog **won't** clean up).
  - Verifies the export endpoint produces valid CSV/JSON/XLSX for a non-trivial record set.
- **No test for the XLSX export** generation. The OpenXML emission code in `app/api/v1/endpoints/projects.py:415-480` is not unit-tested.
- **No test for `Project.transition_to` state machine enforcement** at the unit level. The state machine is exercised indirectly through the endpoint tests.
- **No test for the legacy `/scrape` SSRF** because there is no SSRF protection there to test.
- **No integration test against a real PostgreSQL.** All backend tests use `app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession())`. This means the `pg_advisory_xact_lock` calls, the JSONB insert paths, the partial unique index `ix_provider_configs_one_default_per_user`, the composite indexes, and the enum `ADD VALUE` autocommit pattern are not exercised by the test suite. They will only fail in production.
- **No test for the migration 007 backfill** behavior — the Python-side loop that converts `projects.analysis` into `extraction_specs` rows is critical for upgrade safety and has no test.

### 9.3 Mocked vs real testing

- **All backend tests are mocked at the DB level.** No test runs against a real PostgreSQL. The strategy doc's CLAUDE.md (line 37) is correct: "the backend test suite has 95 passing tests — all run without a database (fully mocked)." (The count has grown to 161 since then.)
- **All LLM tests are mocked at the LiteLLM level.** No test calls a real LLM provider.
- **Browser-mode tests are skipped when Playwright is not installed** (`tests/services/test_fetcher.py:178, 230, 300`).
- **Frontend tests use jsdom** (no real DOM, no real network).

### 9.4 Highest-risk untested areas

1. **`execute_project_extraction` end-to-end.** The pipeline is the core product. A bug here breaks the entire value prop. The fact that the most important code path in the system is tested by **a single integration-shaped test in `test_project_workflow.py`** that mocks the executor (tests `_extract_from_repeated_containers` directly) is a serious gap.
2. **`admit_job` provider resolution** in `app/services/job_admission.py:128-160` — explicit ID, default fallback, any-provider fallback. Three code paths, one partial test.
3. **Project cancel during extraction** — does the loop break, is the `CANCELED` state actually persisted, are partial `ExtractedRecord` rows left behind?
4. **The BFS same-origin discovery** with `url_patterns` filtering — only a unit test, no integration.
5. **The LLM JSON retry path** — tested up to the first 2 retries, not exhaustively.
6. **The full readiness probe** against a real DB (12 statements in sequence).

---

## 10. Production Readiness Assessment

| Area                | Rating                                             | Explanation                                                                                                                                                                                                                                                                                                                                             |
| ------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Security**        | **Weak** (overall) / **Strong** (project pipeline) | The project pipeline's SSRF posture is strong and well-tested. But the legacy `/scrape` pipeline is **SSRF-vulnerable** and exposed. CORS default is wrong for the Vite dev origin. No CSP. No body size limit. The combination means a production deploy that includes the legacy pipeline is at material risk.                                        |
| **Correctness**     | **Adequate**                                       | The state machine, ownership, and always-finalize patterns are correctly implemented and tested. Bugs found in this audit are limited to (a) PATCH spec not gated by state, (b) COMPLETED → DISCOVERING re-run allowed, (c) re-runs destructive, (d) `execute_project_extraction` has no integration test. The 161-test suite covers the contract well. |
| **Reliability**     | **Weak**                                           | The in-process `BackgroundTasks` and APScheduler are not durable. The watchdog does not clean up `DISCOVERING/EXTRACTING` projects. `CrawlPage.lease_expires_at` is written but never swept. A process crash mid-extraction leaves a project in an unrecoverable state without manual SQL intervention. This is the biggest production risk.            |
| **Maintainability** | **Adequate**                                       | Code is well-organized (api/services/models), small files, clean naming, consistent patterns. Two pipelines duplicate logic — a maintenance burden. `Project` lives in `job.py`. `Job = Project` alias is confusing. But overall the code reads cleanly and the test suite enforces contracts.                                                          |
| **Scalability**     | **Weak**                                           | Single-process architecture. No durable job queue. No Redis-backed rate limiting. Sequential extraction. `CRAWL_CONCURRENCY` setting unused. Multi-worker gunicorn deploys are explicitly unsupported because advisory locks are per-connection and BackgroundTasks are per-process.                                                                    |
| **UX Completeness** | **Adequate**                                       | All advertised features (project workflow, auth, providers, preview, extraction, export) work end-to-end. Missing: visual field selection, content-mode UI, re-run UI, per-page status UI, SSE live progress, content/RAG export formats, template routing. But for a v0/v1 product, the coverage is reasonable.                                        |

### 10.1 Strong

- Project state machine and always-finalize
- Provider key encryption, redaction, and password-confirmed reveal
- SSRF defenses (project pipeline)
- robots.txt with deny policy
- Rate limit keyed by verified JWT
- DOM summary quality
- LLM JSON retry pipeline
- Per-page failure isolation
- Bounded readiness probe

### 10.2 Adequate

- Code organization and layering
- API key handling at rest
- Cancellation responsiveness
- Frontend token storage (in-memory access token)
- Pydantic validation
- Watchdog for stuck analysis jobs
- CORS configuration (once overridden)

### 10.3 Weak

- Legacy `/scrape` SSRF vulnerability
- Project extraction crash recovery (no lease reaper)
- Per-page retry counter without requeue logic
- Multi-worker / multi-host deploys
- Sequential (non-concurrent) extraction
- CORS default missing Vite origin
- No CSP
- No body size limit
- No per-page status / admin retry endpoint
- No SSE live progress
- No Docker / no install story

---

## 11. Top Risks (ranked by impact)

1. **Legacy `/scrape` is SSRF-vulnerable.** Authenticated user can hit internal services / cloud metadata. **High impact, easy to fix** (call `validate_url` in `app/api/v1/endpoints/scrape.py:91` and change `scraper.scrape_url` to use `follow_redirects=False` with per-hop validation, or delete the legacy router entirely).
2. **Project extraction has no lease reaper.** A process crash mid-extraction leaves `CrawlPage` rows in `FETCHING` indefinitely. **High impact, moderate fix** (add a watchdog sweep on `(state, lease_expires_at)`, mark stale `FETCHING` rows as `PENDING` or `FAILED`).
3. **In-process BackgroundTasks + APScheduler are not durable.** A multi-worker or multi-host deploy will run multiple watchdogs, lose background tasks, and race on advisory locks. **High impact, large refactor** (move to Celery/arq + Redis or document single-host-only deploy).
4. **`ProjectState.COMPLETED → DISCOVERING` re-run is allowed and destructive** (`start_project_extraction` deletes all records before re-running). The UI does not show this as a re-run, so a user clicking "Extract" on a completed project will silently wipe their data. **High impact, small fix** (either remove the `COMPLETED → DISCOVERING` edge, or add a confirm dialog + a "keep previous results" mode).
5. **No integration tests against real PostgreSQL.** The advisory lock, JSONB inserts, partial unique index, enum autocommit, and DB-level constraints are untested. They will only fail in production. **High impact, moderate fix** (set up a `TEST_DATABASE_URL`, add a `conftest.py` fixture that provisions and tears down the schema per test session).
6. **No CSP, no body size limit, refresh token in localStorage.** Combined, these are the most likely path to a frontend XSS leading to refresh-token theft → account takeover. **High impact, moderate fix** (add a CSP middleware, set a max body size in `app/main.py`, and consider HttpOnly cookies for the refresh token).
7. **PATCH `/projects/{id}/spec` does not gate on state.** A user can mutate the spec of a `COMPLETED` project. If combined with the re-run bug, this is data loss. **Medium impact, trivial fix** (add `if project.state not in {AWAITING_SETUP, ANALYSIS_READY, PREVIEW_READY, COMPLETED}: raise 409`).
8. **No watchdog for projects stuck in `DISCOVERING/EXTRACTING/EXPORTING`.** The current watchdog only catches `QUEUED/ANALYZING`. **Medium impact, small fix** (extend `cleanup_stuck_jobs` in `app/services/watchdog.py:114` to walk the additional states with their own timeouts).
9. **Sequential extraction with `MIN_CRAWL_DELAY_MS=500ms`.** A 500-page job takes at least 4 minutes just in delays. **Medium impact, moderate fix** (add concurrent fetcher with `CRAWL_CONCURRENCY`).
10. **CORS default missing Vite origin** + stale README/STATUS docs + dead schema file. **Low impact individually, high cumulative impact** on new-developer onboarding.

---

## 12. Recommended Validation Plan

The highest-value manual and automated validation work to do **before** starting Phase 3 (visual field selection) or any reliability work.

### 12.1 Critical-path tests (must do first)

1. **End-to-end project workflow with a real AI provider.**
   - Register a user. Add a real Gemini or OpenAI provider. Submit `https://example.com` (or another well-known public site). Verify the full pipeline: analyze → spec → preview → extract → export.
   - **Expected:** analysis returns reasonable fields; preview shows real selector output; extract produces records; CSV opens cleanly; XLSX opens in Excel.
2. **Re-run flow verification.**
   - Run a project to COMPLETED. Re-call `POST /projects/{id}/extract` (or use UI). Verify (a) the records are deleted, (b) the project goes back to DISCOVERING, (c) a new run produces fresh records.
3. **Cancel during extraction.**
   - Start a project with `page_limit=50` and `MIN_CRAWL_DELAY_MS=2000`. While it is in EXTRACTING, call `POST /projects/{id}/cancel`. Verify the state transitions to CANCELED within ~one page and partial records are not corrupted.
4. **Provider key reveal round-trip.**
   - Add a provider. Call `POST /providers/{id}/reveal-key` with the correct password. Verify the plaintext is returned and matches. Call again with a wrong password — verify 401.
5. **Cross-user 404 enforcement.**
   - As user A, create a project and provider. As user B, attempt to GET/PATCH/DELETE them. Verify all return 404, never 200, never 403.

### 12.2 Failure-mode tests

1. **SSRF attempts on the project pipeline.** Submit `http://127.0.0.1:5432`, `http://169.254.169.254/latest/meta-data/`, `http://10.0.0.1/admin`. Verify each returns a 4xx with a controlled error code, not a server error.
2. **SSRF attempts on the legacy `/scrape` pipeline.** Same URLs as above. **Expected (after fix):** 4xx with `INVALID_URL` or `PRIVATE_ADDRESS`. **Current (before fix):** server will actually fetch. **This test is the SSRF fix acceptance test.**
3. **Robots.txt BLOCKED.** Set up a public URL that disallows the user-agent. Verify the project fails with `ROBOTS_BLOCKED`.
4. **Render mode = BROWSER without Playwright installed.** Set `render_mode=BROWSER` on a project. Verify the failure is `BROWSER_UNAVAILABLE` and the project is marked FAILED with the right `error_code`.
5. **Provider returns invalid JSON three times.** Configure a provider that returns `{not json}`. Verify the project fails with `ANALYSIS_FAILED` and the raw LLM response is in the error message.
6. **Watchdog timeout.** Mock a job that has been in ANALYZING for 10 minutes. Trigger the watchdog. Verify it transitions to FAILED.
7. **Crash mid-page.** Set `MIN_CRAWL_DELAY_MS=10000` on a project. Start it. Kill the process after 5 seconds. Restart. Verify the project is in EXTRACTING with a page in FETCHING with stale lease. **Then verify the (currently absent) fix: watchdog reaps the page back to PENDING or FAILED.** Without the fix, the project is unrecoverable.

### 12.3 Concurrency tests

1. **Two concurrent `POST /projects/analyze`** from the same user. Verify the advisory lock serializes them and the active-job count is respected. Without `TEST_DATABASE_URL` set up, the advisory lock cannot actually be tested.
2. **Two concurrent provider `is_default` flips.** Verify the partial unique index and the advisory lock produce exactly one default.
3. **Concurrent read of `/projects` while an extract is running.** Verify the progress counts are consistent and the polling UI does not show stale states.

### 12.4 Extraction accuracy tests

1. **Structured mode on a known listing site** (e.g. `https://books.toscrape.com/`). Verify the extracted records match the page structure.
2. **Content mode on a known article site** (e.g. a Wikipedia article). Verify the primary content selector captures the article body and not navigation/footer.
3. **Selector with confidence=0.5 vs confidence=0.9.** Verify the default spec selects fields with `>=0.7` confidence and the user can override.
4. **Page with redirects.** Submit a URL that 301-redirects. Verify the redirect is followed and the final URL is recorded.
5. **Page that is robots-disallowed.** Verify the page is in BLOCKED state with `block_reason=ROBOTS_BLOCKED`, not in FAILED.
6. **Page that 404s.** Verify the page is in FAILED with `error_code=FETCH_TIMEOUT` or similar, not blocking the rest of the crawl.
7. **Page with JavaScript-rendered content.** Submit a JS-heavy URL with `render_mode=BROWSER`. Verify Playwright loads the page and the saved selector picks up the rendered content.

### 12.5 Frontend workflow tests

1. **Login → Add provider → New extraction → Wait for analysis → Edit spec → Preview → Extract → Download CSV/JSON/XLSX.** Full happy path through the UI. Verify the URL progresses through the states visually.
2. **Browser back/forward through the workflow.** Verify the polling resumes correctly.
3. **Refresh during extraction.** Verify the polling picks up where it left off.
4. **Two browser tabs editing the same project.** Verify the polling reconciles state without losing the user's edits.
5. **Token expiry during a long operation.** Verify the refresh-on-401 retry fires and the operation completes.

### 12.6 Validation work that should be done before the next phase

In order of priority:

1. **Fix the SSRF on the legacy `/scrape` pipeline** (or delete the legacy pipeline). This is the only security issue with a non-trivial exploit. Estimated effort: 1 hour.
2. **Add the lease reaper** to the watchdog. Estimated effort: 1-2 hours + tests.
3. **Add an integration test suite** against a real PostgreSQL (or testcontainers). Estimated effort: half a day.
4. **Add an end-to-end test for `execute_project_extraction`**. Estimated effort: 1-2 days.
5. **Resolve the `COMPLETED → DISCOVERING` re-run ambiguity** (either remove the edge, or add a confirm dialog + non-destructive mode). Estimated effort: 2-4 hours + frontend work.
6. **Add a watchdog sweep for `DISCOVERING/EXTRACTING/EXPORTING` projects**. Estimated effort: 2-3 hours.
7. **Update the README and STATUS.md** to match the code (project is the primary object; legacy pipeline is a documented hazard). Estimated effort: 1-2 hours.
8. **Add a CSP middleware and a body size limit** to the FastAPI app. Estimated effort: 2-3 hours.
9. **Fix the CORS default** to include the Vite dev origin. Trivial.
10. **Add concurrent extraction** with `CRAWL_CONCURRENCY`. Estimated effort: 1-2 days.

---

_End of report._
