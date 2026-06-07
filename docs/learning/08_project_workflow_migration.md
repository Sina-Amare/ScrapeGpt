# 08 — Project-Based Extraction Workflow Foundation

## Purpose

This phase moves ScrapGPT from an analysis-job console to the long-term product shape:

```text
Project -> Analyze -> Field Selection -> Preview -> Extract -> Results
```

`Project` is now the durable user-facing object. The old `/jobs` API remains as a compatibility wrapper, but the frontend uses `/projects`.

## What Changed

- `jobs` table is migrated to `projects`.
- The historical PostgreSQL enum name `job_state` is kept to avoid risky enum churn, but code maps it as `ProjectState`.
- New states were added: `PREVIEWING`, `PREVIEW_READY`, `DISCOVERING`, `EXTRACTING`, `EXPORTING`, `COMPLETED`, `PAUSED`.
- New tables:
  - `extraction_specs`
  - `preview_results`
  - `crawl_pages`
  - `extracted_records`
  - `exports`
- New primary API:
  - `POST /api/v1/projects/analyze`
  - `GET /api/v1/projects`
  - `GET /api/v1/projects/{id}`
  - `PATCH /api/v1/projects/{id}/spec`
  - `POST /api/v1/projects/{id}/preview`
  - `POST /api/v1/projects/{id}/extract`
  - `GET /api/v1/projects/{id}/records`
  - `GET /api/v1/projects/{id}/export`
  - `POST /api/v1/projects/{id}/cancel`
  - `DELETE /api/v1/projects/{id}`
- Frontend routes:
  - `/projects`
  - `/projects/new`
  - `/projects/:id`
  - `/jobs/*` redirects to `/projects/*`

## Invariants

1. Raw AI analysis is immutable user input to setup. Field selection updates `extraction_specs`, not `projects.analysis`.
2. `/jobs` compatibility reads and writes the same project rows.
3. Normal responses do not expose provider secrets.
4. Product status is computed for UI labels; raw enum names are backend system state.
5. Preview is persisted so refresh/navigation does not lose sample rows.
6. Extraction writes `raw_data` to `extracted_records`; `normalized_data` is additive and may be null.
7. Active projects cannot be deleted.

## Current Extraction Scope

This phase implements the end-to-end product contract using seed/sample extraction from the analyzed page and saved spec. It is intentionally not the final multi-page BFS crawler.

The database design already includes page-level rows, leases, retry counts, and exports so the future crawler can extend the system without replacing the project/spec/results model.

## Key Tradeoffs

- Kept DB enum name `job_state`:
  - avoids a risky enum rename in PostgreSQL.
  - code exposes `ProjectState`, so the old name is mostly hidden.
- Kept `/jobs`:
  - preserves old API tests and any bookmarks/integrations.
  - frontend no longer depends on it.
- URL-only default project creation:
  - non-technical users are not forced to choose mode/render/provider.
  - advanced overrides remain available for power users.
- Seed/sample extraction now:
  - makes the complete product journey testable.
  - full crawl execution remains a later phase.

## Verification

Last verification after this phase:

```bash
venv\Scripts\python.exe -m pytest -q
cd frontend
npm run typecheck
npm run lint
npm test -- --run
npm run build
```

Results:

- Backend: 159 passed.
- Frontend tests: 31 passed.
- Frontend typecheck, lint, and production build: passed.
