# ScrapGPT Status

Last verified: June 7, 2026.

## Implemented

- Phase 0 security fixes:
  - Rate-limit keying verifies JWT signatures.
  - Refresh-token endpoint is rate limited.
  - Watchdog transitions guard expected states.
  - Ownership mismatches do not mutate another user's task.
- Phase 0.5 provider foundation:
  - Old credit columns and `system_state` were removed.
  - BYOK provider configs are stored per user.
  - Provider API keys are Fernet-encrypted at rest.
  - Normal provider responses never return keys.
  - Explicit key reveal requires password confirmation.
- Frontend v0:
  - React/Vite app with auth, protected routes, provider management, health, legacy scrape, dashboard, jobs, and new analysis screens.
  - Access tokens are in memory; refresh tokens are stored locally.
  - Provider key reveal is password-confirmed and not cached client-side.
  - Fluid grid layout with correct boundary constraints for form fields and scrolling dialogs.
- Phase 1 analysis jobs:
  - Project-based workflow foundation with `projects` replacing the old `jobs` table.
  - `/jobs` remains as a temporary compatibility API over the same project rows.
  - `analysis_cache` remains unchanged.
  - SSRF-safe URL validation.
  - `robots.txt` checks with TTL cache and configurable failure policy.
  - Static fetcher with per-redirect validation.
  - Optional Playwright browser rendering, including Windows Uvicorn selector-loop handling.
  - DOM summary builder.
  - Cached LLM analysis for structured datasets and content/RAG-style extraction.
  - Job admission with provider preflight, active-job limit, and per-user advisory lock.
  - Job executor with always-finalize failure handling.
  - Compatibility Jobs API: create, list, detail, cancel, delete.
  - Project API:
    - `POST /projects/analyze`
    - `GET /projects`
    - `GET /projects/{id}`
    - `PATCH /projects/{id}/spec`
    - `POST /projects/{id}/preview`
    - `POST /projects/{id}/extract`
    - `GET /projects/{id}/records`
    - `GET /projects/{id}/export`
    - `POST /projects/{id}/cancel`
    - `DELETE /projects/{id}`
  - Project workflow tables:
    - `extraction_specs`
    - `preview_results`
    - `crawl_pages`
    - `extracted_records`
    - `exports`
  - Frontend project workflow:
    - Projects list.
    - New Extraction URL-first form.
    - Advanced drawer for mode/render/provider overrides.
    - Project workspace with Overview, Fields, Preview, Extraction, Results, and Advanced sections.
    - `/jobs` frontend routes redirect to `/projects`.

## Current Primary Workflow

1. Start backend and frontend.
2. Register or log in.
3. Add a provider in Providers.
4. Submit a URL from New Extraction.
5. Watch the project move through analysis.
6. Open the project workspace when it is ready.
7. Select fields and edit user-facing field labels.
8. Run Preview to inspect sample rows.
9. Run Extract to persist seed/sample records.
10. Inspect Results and download CSV/JSON through the export endpoint.

The older Legacy Scrape page still exists for the `/scrape` pipeline, but it is no longer the primary product flow.

## Not Implemented Yet

- Visual field selection.
- Full multi-page crawl execution.
- Template routing and selector repair.
- Background extraction workers beyond the current seed/sample extraction path.
- File-backed export storage beyond streamed CSV/JSON responses.
- Authenticated-content browser sessions.
- CAPTCHA solving, stealth browser patches, proxy evasion, or challenge bypass.

## Verification Snapshot

Commands last run successfully:

```bash
venv\Scripts\python.exe -m pytest -q
cd frontend
npm test
npm run typecheck
npm run lint
npm run build
```

Results:

- Backend: 159 passed.
- Frontend tests: 31 passed.
- Frontend typecheck, lint, and production build: passed.
- Browser render smoke with Windows selector event loop: passed for `https://example.com`.
