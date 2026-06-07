# 08 — Project-Based Extraction Workflow Foundation

## Problem / Purpose

Phase 1 produced an analysis-only console: submit a URL, get back a list of
suggested CSS selectors and field names, then nothing. The product vision
requires the full loop:

```text
Analyze → Field Selection → Preview → Extract → Results
```

This phase builds the backend contracts, database tables, services, and
frontend pages for that loop. The execution is still seed/sample-level (the AI's
sample values become the extracted records), not a real multi-page crawler.
The point is to make every step of the product journey testable before investing
in full crawler execution in Phase 2.

The old `jobs` table and `/jobs` API are kept as compatibility wrappers so
existing tests, bookmarks, and integrations don't break.

---

## What Changed

### Database

Migration `007_project_workflow.py`:

1. **Adds new `ProjectState` enum values** to the existing `job_state` PostgreSQL
   type using `ALTER TYPE job_state ADD VALUE IF NOT EXISTS`. These run inside
   `op.get_context().autocommit_block()` — PostgreSQL forbids `ADD VALUE` inside
   a transaction and will raise an error without this wrapper.
   New values: `PREVIEWING`, `PREVIEW_READY`, `DISCOVERING`, `EXTRACTING`,
   `EXPORTING`, `COMPLETED`, `PAUSED`.

2. **Renames `jobs` → `projects`** and renames the three covering indexes.
   The PostgreSQL type name `job_state` is intentionally kept — see Design
   Decisions below.

3. **Creates five new tables** with foreign-key cascades to `projects`:
   - `extraction_specs` — user's field/mode selection, separate from raw AI output
   - `preview_results` — persisted sample rows so refresh doesn't lose preview
   - `crawl_pages` — one row per discovered URL, with lease and retry fields
   - `extracted_records` — raw and normalized data per extracted item
   - `exports` — export metadata (format, record count, spec hash)

4. **Backfills `extraction_specs`** for all existing projects: reads each
   project's `analysis` JSONB, converts `candidate_fields` (STRUCTURED) or
   `metadata_fields` (CONTENT) into the `fields` array format, and inserts
   one spec row per project. The offline migration path emits a structural
   `INSERT … SELECT` instead.

### Backend

**`app/models/job.py`** is the most changed file:

- `JobState` → `ProjectState` with the 7 new states above.
- `Job` class → `Project` class; `__tablename__ = "projects"`.
- New ORM models: `ExtractionSpec`, `PreviewResult`, `CrawlPage`,
  `ExtractedRecord`, `Export`.
- `AnalysisCache` is unchanged.
- **Compatibility aliases at the bottom** (`Job = Project`, `JobState = ProjectState`,
  etc.) keep the existing `/jobs` endpoints and tests working without changes.

**New backend files:**

| File | Purpose |
| ---- | ------- |
| `app/api/v1/endpoints/projects.py` | Full project CRUD + workflow endpoints |
| `app/schemas/project.py` | Pydantic request/response models |
| `app/services/project_status.py` | Maps `ProjectState` → product-facing labels and tones |
| `app/services/project_preview.py` | Builds sample records from field `sample_values`; persists `PreviewResult` |
| `app/services/project_extraction.py` | Materializes preview records into `ExtractedRecord` rows |
| `app/services/extraction_spec_service.py` | Create, fetch, and update `ExtractionSpec`; lazy-create from analysis on first open |

**`app/services/readiness.py`** extended: probes now cover all 6 new tables
(total 12 `LIMIT 0` probes, up from 7).

### Frontend

- `App.tsx`: routes changed from `/jobs/*` to `/projects/*`; `/jobs/:id`
  redirects via `LegacyJobRedirect` (uses `useParams`, not
  `window.location.pathname`).
- `AppShell.tsx`: nav items updated; health indicator switched to
  `/health/ready` so "Backend ready" reflects DB connectivity, not just
  process liveness.
- `DashboardPage.tsx`: `AnalysisJobsSection` → `ProjectsSection`; active count
  uses `ACTIVE_PROJECT_STATES.has()` from `projectPolling.ts` (covers all active
  states including `PAUSED`).
- New pages: `NewProjectPage`, `ProjectsPage`, `ProjectDetailPage`.
- New lib: `projectPolling.ts` — `ACTIVE_PROJECT_STATES`, `TERMINAL_PROJECT_STATES`,
  `shouldPollProject`, `projectTone`.

---

## Invariants

1. **Raw AI analysis is immutable.** `Project.analysis` is written once by the
   executor and never touched again. Field customisation writes to
   `ExtractionSpec.fields`, not back to `analysis`.

2. **One spec per project at a time (for now).** `ensure_default_spec` and
   `latest_spec` return the most recent spec by `created_at DESC, id DESC`.
   `PATCH /spec` updates the fields on the current latest spec, not the project row.

3. **Preview is persisted.** `PreviewResult` rows are never deleted on spec edit.
   The latest preview is fetched by `latest_preview(db, project_id)` ordered the
   same way. Persisting prevents a browser refresh from losing the sample rows
   the user is looking at.

4. **`raw_data` is never modified after insert.** `normalized_data` is additive
   and may be null. If normalization runs later, it writes `normalized_data`;
   raw_data stays unchanged.

5. **Active projects cannot be deleted.** `DELETE /projects/{id}` checks
   `project.state in DELETABLE_PROJECT_STATES` (which equals
   `TERMINAL_PROJECT_STATES`). Active states return 400.

6. **`/jobs` compatibility reads and writes the same rows.** `Job = Project` at
   the model level means the `/jobs` endpoints query the `projects` table
   transparently. Existing test fixtures that build `Job(...)` objects still work.

7. **`ADD VALUE` migrations always use `autocommit_block`.** PostgreSQL raises
   `ERROR: ALTER TYPE ... ADD VALUE cannot run inside a transaction block` if
   this is not respected. The CLAUDE.md explicitly lists this invariant; it
   applies to every future state addition too.

---

## Design Decisions

### Keep the PostgreSQL enum name `job_state`

**Rejected alternative:** `ALTER TYPE job_state RENAME TO project_state`.

PostgreSQL has no `ALTER TYPE … RENAME VALUE` command until PG 14 (and even then
it's just for label renames). Renaming the type itself requires `CREATE TYPE …
AS ENUM`, migrating every column that uses it, and dropping the old type — across
`projects.state`, any inherited views, and any code that queries `pg_type` by name.
That's a risky migration for a cosmetic change. Python code uses `ProjectState`;
the DB type name `job_state` is an internal detail that only shows up in
`\d projects` output.

### Keep `/jobs` as a compatibility API

**Rejected alternative:** Remove `/jobs` endpoints entirely.

The compatibility alias costs nothing at runtime. It lets existing tests, any
bookmarks in the browser, and any future integrations that might have stored job
IDs under `/jobs/` continue to work. The frontend no longer uses `/jobs/` at all
(it uses `/projects/`), so this is purely a backward-compat shim with no
maintenance cost.

### `ExtractionSpec` as a separate table, not embedded in `Project.analysis`

**Rejected alternative:** Write selected fields back into `Project.analysis`.

`analysis` is the AI's output. It represents what the AI observed. `ExtractionSpec`
is what the user decided. Mixing them would mean the analysis response changes
when the user edits a field label, making it impossible to tell "what did the AI
originally think?" from "what did the user choose?" The separate table also means
re-running analysis on the same URL (cache hit) returns the same analysis, while
the spec captures the user's evolving selections independently.

### Seed/sample extraction now, real crawl later

**Rejected alternative:** Block the end-to-end journey until CSS extraction is
implemented.

Seed extraction materializes the AI's `sample_values` (the 2–5 example rows
returned by analysis) into `ExtractedRecord` rows. It makes the full product
loop — Analyze → Fields → Preview → Extract → Results → Export — testable today.
Users see the journey works. The database tables and API contracts for real
extraction are already in place (`crawl_pages` with lease fields, `extracted_records`,
`exports`). Phase 2 replaces the seed materialisation with a real CSS executor
without changing the API contracts.

### Lazy spec creation in `ensure_default_spec`

The migration backfill creates specs for all projects that existed before 007.
For new projects, the spec is created lazily when the user opens the workspace:
the `_project_response` helper calls `ensure_default_spec`, which creates the
spec from `project.analysis` if none exists yet. This avoids creating a spec in
the background task (which doesn't know the user's preferences), and avoids
failing the workspace open if the spec hasn't been persisted yet.

On the list endpoint (`GET /projects`), specs are **not** auto-created — the
list only needs `selected_field_count`, which defaults to 0 if no spec exists.
Specs are batch-loaded in a single query (not N+1) and the result is used as-is.

---

## Code Walkthrough

### Project state machine (`app/models/job.py`)

```text
QUEUED
  ↓
ANALYZING
  ↓
AWAITING_SETUP | ANALYSIS_READY   ← AI analysis done; user opens workspace
  ↓
PREVIEWING
  ↓
PREVIEW_READY                     ← Sample rows ready to inspect
  ↓
DISCOVERING                       ← (Phase 2: BFS page discovery)
  ↓
EXTRACTING                        ← (Phase 2: CSS selector execution)
  ↓
EXPORTING
  ↓
COMPLETED

FAILED | CANCELED from any non-terminal state.
PAUSED: reserved for future crawler pause/resume.
```

`TERMINAL_PROJECT_STATES` = `{AWAITING_SETUP, ANALYSIS_READY, PREVIEW_READY,
COMPLETED, FAILED, CANCELED}`.

`ACTIVE_PROJECT_STATES` = everything else including `PAUSED` (crawler paused is
still "in progress" from the user's perspective).

`Project.is_terminal` and `is_active` reference the aliases `TERMINAL_JOB_STATES`
and `ACTIVE_JOB_STATES` (which are set equal to the `_PROJECT_` versions at module
bottom). Python resolves property bodies at call time, not class-definition time,
so these work correctly. They should be updated to reference `_PROJECT_` names
directly when the compatibility aliases are removed in a future cleanup.

### `ensure_default_spec` (`app/services/extraction_spec_service.py`)

1. Query for the latest spec by `created_at DESC, id DESC`.
2. If found, return it immediately.
3. If not found and `project.analysis` is None, return None — analysis hasn't
   run yet or failed; nothing to default from.
4. Otherwise, call `default_spec_from_analysis(project)`: iterates
   `candidate_fields` (STRUCTURED) or `metadata_fields` (CONTENT) and sets
   `selected = True` for fields with confidence ≥ 0.7.
5. Insert, flush, refresh, return.

### `run_seed_extraction` (`app/services/project_extraction.py`)

1. Set project state to `DISCOVERING`, flush.
2. Delete all existing `ExtractedRecord`, `CrawlPage`, and `Export` rows for
   this project (idempotent re-run).
3. Create one `CrawlPage` row for the seed URL, set state `FETCHED`.
4. Set project state to `EXTRACTING`.
5. Use the passed `PreviewResult.sample_records` (or call `build_preview_payload`
   as fallback) to get the record list.
6. Insert one `ExtractedRecord` per record.
7. Set `CrawlPage` state to `EXTRACTED`.
8. Set project state to `EXPORTING`, create `Export` row.
9. Set project state to `COMPLETED`, flush.

The entire function runs inside the caller's transaction (started by the endpoint
or by the background task). If anything raises, the transaction rolls back and
the project lands in `FAILED` via the executor's always-finalize guarantee.

### `_progress` helper (`app/api/v1/endpoints/projects.py`)

Three `SELECT COUNT(*)` queries per project detail response. This is acceptable
for a single-project detail view. It is **not** called in `list_projects` — the
list item shape does not include per-project counts for this reason.

### `list_projects` batch spec loading

After fetching the project page, a single query fetches all specs for those
project IDs:

```python
select(ExtractionSpec)
    .where(ExtractionSpec.project_id.in_([p.id for p in projects]))
    .order_by(ExtractionSpec.created_at.desc(), ExtractionSpec.id.desc())
```

In Python, the first spec encountered per `project_id` (descending order) is
kept in the dict. This is `O(n)` per project and avoids N+1 round-trips.

---

## Runtime Lifecycle

### Success path (new project, STRUCTURED mode)

```text
POST /projects/analyze
  → admit_job(): provider lookup, advisory lock, active-count check, INSERT project(state=QUEUED)
  → BackgroundTask(execute_job_pipeline)
  → 202 with project response

execute_job_pipeline (background):
  transition QUEUED → ANALYZING
  validate_url()
  check_robots()
  fetch_url(render_mode=AUTO) → static fetch
  build_dom_summary(html)
  analyze_page() → LLM call → AWAITING_SETUP or ANALYSIS_READY

User opens workspace (GET /projects/{id}):
  ensure_default_spec() → creates spec from analysis.candidate_fields

PATCH /projects/{id}/spec → user selects 3 fields, edits labels

POST /projects/{id}/preview → build_preview_payload() → PreviewResult inserted
  project: AWAITING_SETUP → PREVIEWING → PREVIEW_READY

POST /projects/{id}/extract:
  run_seed_extraction() → CrawlPage + ExtractedRecord rows + Export row
  project: PREVIEW_READY → DISCOVERING → EXTRACTING → EXPORTING → COMPLETED

GET /projects/{id}/export?format=csv → streams extracted_records as CSV
```

### Failure paths

| Where | Cause | Outcome |
| ----- | ----- | ------- |
| `admit_job` | No provider | 409 `NO_PROVIDER_CONFIGURED` |
| `admit_job` | Too many active | 409 `ACTIVE_JOB_LIMIT_REACHED` |
| `execute_job_pipeline` | Any unhandled exception | project → `FAILED` (always-finalize guarantee) |
| `POST /preview` | Project not in AWAITING_SETUP or ANALYSIS_READY | 409 |
| `POST /extract` | No spec saved | 409 "Select fields before extracting" |
| `POST /extract` | No preview and no analysis | 409 "Run Preview first" |
| Watchdog | Job stuck > timeout | project → `FAILED` with `WATCHDOG_TIMEOUT` |

---

## Concurrency and Crash Analysis

The same per-user advisory lock from `job_admission.py` serialises concurrent
`POST /projects/analyze` calls for the same user. Two concurrent `PATCH /spec`
calls for the same project will both succeed (last-write-wins on the spec row);
this is acceptable at Phase 2 since there's no concurrent editing UI.

`run_seed_extraction` deletes all existing records before inserting new ones. If
the process dies mid-extraction, the project row remains in `EXTRACTING`. The
watchdog will eventually force-fail it. On retry (user calls `POST /extract`
again), the delete step clears the partial state before re-inserting.

If the server dies after `project.state = COMPLETED` is set but before `db.flush()`
in `run_seed_extraction`, the project remains in `EXPORTING`. The watchdog catches
it.

---

## Pitfalls

1. **`ADD VALUE` outside `autocommit_block`** — Any future migration that adds a
   new `ProjectState` value must wrap `ALTER TYPE job_state ADD VALUE` inside
   `op.get_context().autocommit_block()`. Without it, Alembic fails with
   `ERROR: ALTER TYPE ... ADD VALUE cannot run inside a transaction block`.
   Migration 007 demonstrates the correct pattern.

2. **`activeCount` and `PAUSED`** — The dashboard stat tile and the delete-button
   guard both check project state. Always use `ACTIVE_PROJECT_STATES.has()` and
   `TERMINAL_PROJECT_STATES.has()` from `projectPolling.ts`, not hardcoded string
   arrays. Adding a new state to the Sets automatically updates every guard.

3. **`_progress()` is called per detail view, not per list item.** Adding it to
   the list endpoint would cause 3×N extra queries per page. The `list_projects`
   endpoint deliberately omits it.

4. **`ensure_default_spec` must not be called from `list_projects`** — It issues
   a SELECT (and possibly an INSERT + flush) per project, creating an N+1 pattern.
   The list endpoint batch-loads specs instead.

5. **`LegacyJobRedirect` must use `useParams`** — The component is rendered inside
   `<Route path="jobs/:id">` so the id is already in React Router's param map.
   Using `window.location.pathname` breaks under any non-root base URL.

6. **The compatibility aliases at the bottom of `job.py` are load-order sensitive**
   — `Project.is_terminal` references `TERMINAL_JOB_STATES` by name. This name
   doesn't exist until line ~450 of the module, after the class definition. Python
   resolves property bodies at call time (not class-definition time), so it works
   at runtime. But if any code calls `is_terminal` synchronously during module
   import (e.g., in a class-level default), it will `NameError`. Remove the
   aliases and update the property bodies to reference `TERMINAL_PROJECT_STATES`
   directly when `/jobs` compatibility is no longer needed.

---

## Safe Evolution Notes

- **Adding a new `ProjectState`**: migration with `autocommit_block`, add to
  `VALID_PROJECT_TRANSITIONS`, add to the appropriate constant set, add to
  `product_status_for` mapping in `project_status.py`, update `projectPolling.ts`
  Sets in the frontend.

- **Removing `/jobs` compatibility**: delete the alias block at the bottom of
  `job.py`, update `is_terminal` and `is_active` to reference `_PROJECT_` names,
  remove `app/api/v1/endpoints/jobs.py` (or keep it for the cancel/delete subset),
  remove the `/jobs` redirect routes in `App.tsx`.

- **Implementing real CSS extraction (Phase 2)**: `run_seed_extraction` is the only
  function to replace. `crawl_pages`, `extracted_records`, and `exports` tables
  are already structured for real page-level execution. The `lease_expires_at`
  and `retry_count` columns are in place for the watchdog sweep. The API contracts
  (`GET /records`, `GET /export`) don't change.

- **Adding JSONL/XLSX export formats**: `GET /export` currently streams CSV or JSON.
  Add the format to the `ExtractionSpec.export_format` enum and add a branch in
  the export handler. The `Export` row already stores `format` as a string.

- **Multi-page jobs**: `Project.page_limit` and `Project.crawl_concurrency` are
  already configurable via settings. The `CrawlPage` table with unique-on-
  `(project_id, normalized_url)` and BFS-compatible `depth` + `state` columns
  is ready. Phase 2 adds the discovery and extraction executor.
