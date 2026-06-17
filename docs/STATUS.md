# ScrapeGPT Status

Last verified: June 17, 2026. Regenerated from HEAD against code (not prior docs).
For the testing approach and exact, current command output see
[`docs/testing_guide.md`](testing_guide.md), which is the authoritative
current-state reference; `docs/product/strategic_redesign.md` is historical
roadmap context only.

## Implemented

- **Phase 0 — Security fixes:**
  - Rate-limit keying verifies JWT signatures; refresh-token endpoint is rate limited.
  - Watchdog transitions guard expected states.
  - Ownership mismatches do not mutate another user's resource (404 on mismatch).

- **Phase 0.5 — BYOK provider foundation:**
  - Old credit columns and `system_state` were removed (no credit system).
  - BYOK provider configs are stored per user with Fernet-encrypted API keys.
  - Normal provider responses never return keys; reveal requires password
    confirmation and emits a `security.key_revealed` audit log.

- **Frontend:**
  - React/Vite app with auth, protected routes, provider management, browser
    sessions, health, dashboard, the project workspace, and a demoted legacy
    scrape page. Access tokens in memory; refresh tokens stored locally.

- **Phase 1 — Analysis engine:**
  - Project-based workflow with `projects` as the primary entity; `/jobs` and
    `/scrape` are thin legacy/compat surfaces.
  - SSRF-safe URL validation with per-redirect checking (`validate_url`).
  - Static fetcher (httpx) + optional browser rendering (Camoufox / Playwright /
    FlareSolverr), including Windows Uvicorn selector-loop handling.
  - DOM summary builder; cached LLM analysis (structured + content modes) keyed
    by content hash, mode, provider, model, and `ANALYZER_VERSION`.
  - LLM selectors are re-validated against fresh HTML after analysis
    (`validate_selectors_against_html`), self-healing over-specified selectors.
  - Job admission with provider preflight, active-job limit, per-user advisory lock.

  > NOTE: robots.txt is **not** enforced. The orphaned `robots_service` module
  > (defined-but-never-called) and its `ROBOTS_FAILURE_POLICY` setting were
  > removed on 2026-06-17. The fetch pipelines do not consult robots.txt.

- **Phase 2 — Real extraction engine:**
  - Preview executes saved CSS selectors against the seed page (real HTTP).
  - Same-site/scope-aware crawl with per-page state persistence and bounded retries.
  - Deterministic CSS extraction (repeated containers → field-index → table
    fallback) with type coercion; content mode stores primary content + metadata.
  - Results exported as CSV, JSON, or XLSX (no record cap).

- **Phase 2.5 — Crawl scope, frontier preview, extraction trust:**
  - **Five** crawl-scope modes: `CURRENT_PAGE`, `PAGINATION`, `COLLECTION`,
    `DATASET`, `FULL_SITE`. COLLECTION is segment-aware and bounded to a positive
    `max_depth` at scope construction (defense-in-depth).
  - Scope confirmation gate — non-`CURRENT_PAGE` scopes require
    `status = USER_CONFIRMED` (HTTP 409 otherwise), enforced in both the sync
    start path and the background executor.
  - Frontier preview shares the same scope classifier as extraction; evidence-based
    scope recommendation with one-click broaden.
  - Extraction quality scoring; server-side paginated results (`/records-page`).
  - Frontend: `ScopeSelector`, `FrontierPreviewPanel`, `TrustSummaryPanel`,
    `PaginatedResultsTable`, `InteractionsPanel`.

- **Run-scoped, non-destructive extraction (migration 013):**
  - Each crawl page, record, and export belongs to an `ExtractionRun`.
  - A retry writes to a NEW run and only promotes it to
    `projects.current_extraction_run_id` on success, so a failed retry never
    destroys prior results.
  - Page fencing (`lease_token`), record idempotency
    (`uq_extracted_records_run_page_ordinal`), and a partial unique index
    enforcing at most one active run per project.

- **Page-variant / interaction extraction (migration 012):**
  - `interaction_profile` on the spec; deterministic in-DOM and interactive
    (browser-click) variants, cross-variant row merge, URL-parameter variants.
  - Detection from static HTML; disabled by default. Owner-checked endpoints.

- **Authenticated browser sessions (migration dcbda4fc8a19):**
  - Fernet-encrypted, domain-scoped cookies per user; consumed by the fetcher for
    authenticated scraping and bot-protection bypass via saved cookies.

- **Reliability:**
  - CrawlPage lease reaper (every 60s) resets expired `FETCHING` pages to PENDING.
  - **Watchdog crash-recovery resume (A1, 2026-06-17):** a stalled DISCOVERING/
    EXTRACTING run (in-process worker died, e.g. server restart) is **re-dispatched**
    up to `WATCHDOG_MAX_RESUME_ATTEMPTS` (default 3) times, then hard-failed with
    `EXTRACTION_RESUME_EXHAUSTED`. Liveness is judged by per-run page activity (not
    `Project.updated_at`), and a re-dispatched run is held in an in-process guard so
    a later sweep cannot start a second worker. EXPORTING is still hard-failed.
    `resume_count` added in migration 014.
  - Stuck-job/task watchdogs; all-pages-failed → `FAILED` with
    `ALL_PAGES_FAILED`; structured zero-records → `NO_RECORDS_EXTRACTED`;
    anti-bot challenge pages classified as `BLOCKED`.
  - Startup watchdog sweep recovers orphans left by a prior process death.

- **Observability:**
  - Structured stdlib logging + JSON formatter + `contextvars` correlation;
    `SecretRedactingFilter` with URL sanitization.
  - Prometheus `/metrics`: extraction run/page counters, run-duration histogram,
    provider 429-retry counter. Provider 429 backoff; CPU offload of parsing.

- **Password reset (migration 010)** and **project events / activity log
  (migration 011)** with code-confirmed, enumeration-safe flows.

## Migrations

Schema is at head **014**: 001–007 (users → project workflow), 008 (phase 2.5
foundation), 009 (analysis cache TTL), `dcbda4fc8a19` (browser sessions), 010
(password reset), 011 (project events), 012 (interaction profile), 013
(extraction runs), 014 (extraction-run `resume_count`).

## Not Implemented Yet

- Visual field selection (click-to-extract, iframe seed page, CSS path generator).
- SSE live progress stream (`/projects/{id}/stream`).
- Concurrent crawler workers (`CRAWL_CONCURRENCY` is reserved/unused; the crawl
  loop is sequential, though the lease model would support parallel workers).
- Multi-process deployment: in-memory rate limiting and a per-process scheduler
  mean exactly one process must run. No Redis-backed rate limiting.
- Durable job queue. Extraction runs as in-process `BackgroundTasks`; A1 resume
  recovers a stalled run on the next sweep, but there is no external queue.
- Retiring the legacy `/scrape` + `/jobs` surfaces and duplicate admission
  services.
- SSRF DNS-rebinding mitigation beyond DNS-time validation (TOCTOU; documented).
- Docker / docker-compose one-command setup.
- CAPTCHA solving, Turnstile/hCaptcha bypass, proxy evasion (permanent non-goals).
  Cloudflare JS challenges are retried with the browser backend.

## Known Issues

- **DNS rebinding** is a known limitation of `validate_url()` (validates at DNS
  resolution; the HTTP client re-resolves at connect time).
- **In-process BackgroundTasks do not survive a restart.** A1 re-dispatch now
  resumes a stalled run (bounded) instead of always hard-failing, but recovery
  still waits for the watchdog sweep; there is no live failover.
- **AI selectors are unvalidated until preview against real HTML.** A spec-save
  smoke-test rejects malformed selectors and a `preview_stale` flag warns when the
  spec changed since the last preview, but a syntactically-valid selector that
  matches nothing is still only proven wrong at preview/extract time.
- **`normalized_data` holds type-coerced values** (numbers/booleans coerced from
  the raw strings; equal to `raw_data` for string fields). It is populated, not
  null — exports use `normalized_data` and fall back to `raw_data` only if absent.

## Verification Snapshot

```powershell
venv\Scripts\python.exe -m pytest -q
```

Backend: **559 passed, 10 skipped** (verified 2026-06-17). Frontend test/
typecheck/lint and the Phase 2.5 E2E harness
(`tests\validation\run_validation.py`) are run separately; real-DB behavior for
the run model and watchdog resume is covered by
`tests\manual\verify_extraction_runs.py` and
`tests\manual\verify_watchdog_resume.py` (require Postgres at head).
