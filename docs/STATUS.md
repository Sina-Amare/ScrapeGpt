# ScrapGPT Status

Last verified: June 11, 2026 (Phase 2.5+ hardening: export cap, cache TTL, state machine fix, timeout reduction).

## Implemented

- **Phase 0 — Security fixes:**
  - Rate-limit keying verifies JWT signatures.
  - Refresh-token endpoint is rate limited.
  - Watchdog transitions guard expected states.
  - Ownership mismatches do not mutate another user's task.

- **Phase 0.5 — BYOK provider foundation:**
  - Old credit columns and `system_state` were removed.
  - BYOK provider configs are stored per user with Fernet-encrypted API keys.
  - Normal provider responses never return keys; reveal requires password confirmation.

- **Frontend v0:**
  - React/Vite app with auth, protected routes, provider management, health, legacy scrape, dashboard, jobs, and project screens.
  - Access tokens are in memory; refresh tokens are stored locally.
  - Provider key reveal is password-confirmed and not cached client-side.

- **Phase 1 — Analysis jobs:**
  - Project-based workflow with `projects` as the primary entity. `/jobs` is a thin compat API.
  - SSRF-safe URL validation with per-redirect checking.
  - `robots.txt` checks with TTL cache and configurable failure policy.
  - Static fetcher (httpx) + optional Playwright browser rendering, including Windows Uvicorn selector-loop handling.
  - DOM summary builder (10,000-character cap with repeated container samples, table samples, `data-*` attributes).
  - Cached LLM analysis for structured datasets and content/RAG extraction.
  - Job admission with provider preflight, active-job limit, and per-user advisory lock.
  - Project API: analyze, list, detail, spec patch, preview, extract, records, export, cancel, delete.
  - Project workflow tables: `extraction_specs`, `preview_results`, `crawl_pages`, `extracted_records`, `exports`.

- **Phase 2 — Real extraction engine:**
  - Preview executes saved CSS selectors against the seed page (real HTTP, not AI sample values).
  - Same-site BFS crawl with per-page state persistence and bounded retries.
  - Deterministic CSS extraction groups records by `repeated_item_selector` with index-based fallback.
  - Content extraction stores selected primary content text plus selected metadata fields.
  - Results exported as CSV, JSON, or XLSX.
  - Page-state progress counts visible in project workspace.

- **Phase 2.5 — Crawl scope, frontier preview, and extraction trust:**
  - **Crawl scope** (`CrawlScope` JSONB on `ExtractionSpec`) — four modes: `CURRENT_PAGE`, `PAGINATION`, `DATASET`, `FULL_SITE`.
  - **Scope confirmation gate** — non-`CURRENT_PAGE` scopes require `status = USER_CONFIRMED` before extraction; HTTP 409 `SCOPE_NOT_CONFIRMED` otherwise.
  - **Frontier preview** — `POST /projects/{id}/frontier-preview` classifies seed-page links by scope mode; shows included/excluded URLs with reason codes; preview and extraction share the same classifier.
  - **Extraction quality** — per-field success/missing rates, warning codes, and overall quality label (`good`/`needs_review`/`risky`) persisted as `quality_summary` on the spec.
  - **Server-side paginated results** — `GET /projects/{id}/records-page` with `total`, `has_more`, `next_skip`, `columns`; max 500 records/page.
  - **Frontend UX layer**: `ScopeSelector`, `FrontierPreviewPanel`, `TrustSummaryPanel`, `PaginatedResultsTable`; scope confirmation flow; 409 error handling; safety limit rename; export format moved to Results.
  - All 8 E2E validation scenarios passing (see `docs/reviews/03_phase25_validation.md`).

- **Logging and observability:**
  - Structured logging with stdlib `logging` + JSON formatter + `contextvars` correlation.
  - `app/core/logging_config.py` — `configure_logging()`, `DevFormatter`, `JsonFormatter`, `ContextInjectingFilter`, `SecretRedactingFilter` (with URL sanitization and exception traceback redaction).
  - `app/core/log_context.py` — `request_id`, `user_id`, `project_id`, `page_id` context vars; binding helpers for HTTP middleware and background tasks.
  - Auth event logging (`auth.register_*`, `auth.login_*`, `auth.token_refresh_*`).
  - Provider key reveal audit trail (`security.key_revealed`, `security.key_reveal_failed`).
  - Extraction pipeline events: scope classification, frontier preview, per-page, quality, export.
  - Watchdog and scheduler job timing events.
  - `LOG_FORMAT=text` (dev) / `LOG_FORMAT=json` (Docker/prod); `LOG_LEVEL` gates all output.
  - See `docs/learning/11_logging_observability.md` for architecture, invariants, and full event catalog.

- **Reliability hardening (Phase 2.5 closeout):**
  - Legacy `/scrape` pipeline now has SSRF-safe URL validation at the endpoint (immediate 400 feedback), executor (defense-in-depth), and redirect-hop levels, plus `robots.txt` checks mirroring the project pipeline.
  - CrawlPage lease reaper: `cleanup_expired_crawl_page_leases()` resets FETCHING pages with expired leases back to PENDING, only within active projects. Runs every 60 seconds via the watchdog scheduler.
  - Stuck-project watchdog: `cleanup_stuck_projects()` fails projects stuck in DISCOVERING/EXTRACTING/EXPORTING beyond configurable timeouts. Uses atomic UPDATE with WHERE-clause state guards for concurrency safety.
  - Extraction completion semantics: projects where all pages fail or are blocked now transition to FAILED with `error_code = "ALL_PAGES_FAILED"` instead of COMPLETED with zero records. Partial success (some pages extracted) still completes normally with quality assessment.
  - Anti-bot challenge pages (Cloudflare/captcha markers) are classified as blocked extraction input, not extracted content.
  - Structured extraction with fetched pages but zero extracted rows now fails with `error_code = "NO_RECORDS_EXTRACTED"` instead of producing a misleading "Results ready" empty export.
  - CORS default now includes `http://127.0.0.1:5173` (Vite dev server origin).
  - `CRAWL_CONCURRENCY` setting description clarified as "Reserved for future use" since the executor is sequential.
  - See `docs/learning/12_reliability_hardening.md` for decision log.

- **Phase 2.5+ hardening (June 11, 2026):**
  - **Export cap removed:** The export endpoint previously had a silent 5000-record hard cap (`list_records(db, project_id, 0, 5000)`). Now loads all records in 1000-record chunks with no truncation. Large exports (>10,000 records) log a warning for operator visibility. The `export.completed` log event now includes both `record_count` (exported) and `total_records` (DB count) to make any discrepancy observable.
  - **Per-page extraction limit made configurable:** `MAX_RECORDS_PER_PAGE` env var (default 1000, range 1–10000) controls the maximum records extracted from a single page. Previously hardcoded at 1000 with no documentation. Now passed explicitly from `settings.MAX_RECORDS_PER_PAGE` in the extraction loop. Operator-only setting, not surfaced to users.
  - **State machine bypass fixed:** `PAUSED → FAILED` transition added to `VALID_PROJECT_TRANSITIONS`. `_mark_project_failed()` now uses `transition_to()` when possible, with a logged fallback for impossible transitions (should not occur with current transition table). The direct-assignment bypass that previously violated the state-machine invariant is now defensive-only with an explicit error log.
  - **Analysis cache TTL:** `expires_at` column added to `analysis_cache` table (Alembic migration 009). `ANALYSIS_CACHE_TTL_DAYS` env var (default 30, range 0–365; 0 = no expiry). Cache lookups filter out expired entries when TTL > 0. Cache writes set `expires_at` when TTL > 0. Watchdog purges expired entries on each 60-second sweep via `cleanup_expired_analysis_cache()`.
  - **Crash recovery timeout reduction:** `WATCHDOG_PROJECT_EXTRACTING_TIMEOUT_MINUTES` default reduced from 60 to 10 minutes. Since in-process BackgroundTasks cannot survive a server restart, a shorter timeout surfaces crashed extractions faster. The project is still hard-failed (no resume), but the failure is detected sooner.

## Current Primary Workflow

1. Start backend and frontend.
2. Register or log in.
3. Add a provider in Providers.
4. Submit a URL from New Extraction. Choose "Rows in a table" or "Content for knowledge base".
5. Watch the project move through analysis.
6. Open the project workspace when it is ready.
7. Choose crawl scope ("This page only", "This list across pages", "This dataset", "The whole site").
8. Generate a frontier preview to see which URLs will be crawled.
9. Confirm scope for any non-current-page mode.
10. Select fields and run Preview to inspect real selector output from the seed page.
11. Run Extract to crawl approved pages, execute saved selectors, and persist records.
12. Inspect Results and download CSV, JSON, or XLSX.

The older Legacy Scrape page still exists for the `/scrape` pipeline, but it is no longer the primary product flow.

## Not Implemented Yet

- Visual field selection (click-to-extract, iframe seed page, CSS path generator).
- SSE live progress stream (`/projects/{id}/stream`).
- Concurrent crawler workers (lease-based crash recovery now implemented for single-instance).
- Template routing, DOM fingerprinting, and selector repair.
- File-backed export storage beyond streamed CSV/JSON/XLSX responses.
- Authenticated-content browser sessions.
- Per-page retry endpoint (`POST /projects/{id}/pages/{page_id}/retry`).
- Rich DOM summary (microdata, full JSON-LD, multi-sample containers) — `ANALYZER_VERSION` still `"1"`.
- Docker/docker-compose one-command setup.
- Selector validation at spec save (smoke-test selectors against seed page HTML).
- Preview-before-extract soft gate (warn if no preview since last spec save).
- CAPTCHA solving, interactive Turnstile/CAPTCHA bypass, proxy evasion (permanent non-goals).
  - Cloudflare JS challenges (`cf-chl-*`, `/cdn-cgi/challenge-platform/`) are now automatically retried with the Playwright browser, which executes the JS challenge. Turnstile (interactive) and hCaptcha/reCAPTCHA are detected and failed cleanly — they require human interaction and are not retried.

## Known Issues

- **DNS rebinding is a known limitation of URL validation.** `validate_url()` blocks private/loopback/metadata IPs at the DNS resolution stage, but a malicious server could rebind DNS after validation passes. This is a known limitation acknowledged in the URL validator comments, not a new fix item.
- **In-process BackgroundTasks cannot survive a server restart.** Analysis and extraction run as FastAPI `BackgroundTasks` in the web process. If the server restarts during extraction, the project is stuck until the watchdog detects it (now 10 minutes for EXTRACTING) and hard-fails it. There is no resume-from-last-page mechanism. The CrawlPage lease reaper recovers individual pages, but nothing automatically re-queues the project. This will be addressed in a future watchdog re-queue mechanism (Option A).
- **AI selectors are unvalidated until preview.** The LLM proposes CSS selectors from a compressed DOM summary; nothing verifies they match real elements before they're saved in the spec. A user can skip preview and go straight to Extract with zero-match selectors. A selector smoke-test and preview-before-extract soft gate are planned (P2.1).
- **`normalized_data` column is never populated.** The raw+normalized dual-layer design exists in the schema, but no normalization logic runs. `normalized_data` is always null. Export uses `normalized_data or raw_data`, so there is no user-visible bug, but the column name implies more than it delivers.

## Verification Snapshot

Commands last run successfully:

```powershell
# Backend
venv\Scripts\python.exe -m pytest -q

# Frontend
cd frontend
npm.cmd test
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
```

Results:

- Backend: **379 passed**, 1 skipped, 47 warnings.
- Frontend tests: **70 passed**.
- Frontend typecheck, lint, and production build: passed (last verified June 10).

E2E validation:

```powershell
venv\Scripts\python.exe tests\validation\run_validation.py
```

Result: **8/8 scenarios PASSED** (see `docs/reviews/03_phase25_validation.md`).
