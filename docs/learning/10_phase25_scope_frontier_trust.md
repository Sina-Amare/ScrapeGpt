# Phase 2.5 — Crawl Scope, Frontier Preview, and Extraction Trust

**Date:** June 9, 2026  
**Branch:** `project-workflow-migration`  
**Status:** Complete and validated (8/8 E2E scenarios passing)

---

## Problem and Purpose

Before Phase 2.5, ScrapeGPT had no first-class representation of crawl intent. A user submitting `https://calories.info/food/potato-products` would silently receive records from the entire site (Pizza, Meat, Beer, Fruit categories) because the crawler treated every same-origin link as a valid target. The user had no way to say "extract only this paginated list." `url_patterns` existed as a low-level glob list but was not connected to any product-level concept.

Three concrete gaps:

1. **Scope** — the system had no model for user intent. Same-origin BFS was the only mode.
2. **Frontier preview** — users could not inspect what the crawler *would* fetch before committing.
3. **Extraction trust** — after extraction, users had no signal about whether the resulting dataset was complete and correct.

Phase 2.5 addresses all three as a prerequisite for Phase 3's visual field selection and SSE live progress.

---

## Invariants Enforced

**Scope confirmation gate** (`app/services/crawl_scope.py`):
- `CURRENT_PAGE` never requires confirmation — it is always safe.
- `PAGINATION`, `DATASET`, `FULL_SITE` require `status = USER_CONFIRMED` before extraction proceeds.
- `assert_scope_confirmed()` raises `ScopeConfirmationError`, which the endpoint maps to HTTP 409 with body `{"detail": {"error_code": "SCOPE_NOT_CONFIRMED", "scope": {...}, "message": "..."}}`.
- The gate runs in `start_project_extraction()` (sync, HTTP path) and as a defensive check in `execute_project_extraction()` (async, background path). The sync path is the primary enforcer; the background check guards against race conditions.

**Preview/extraction classifier parity:**
- Frontier preview calls `classify_links_for_scope()`.
- Project extraction calls `discover_links_for_scope()`, which calls the same classifier.
- What the preview shows for an included URL is exactly what the crawler follows. This guarantee is maintained by both paths routing through the same `crawl_scope.py` module.

**Conservative default scope:**
- New projects created from `default_spec_from_analysis()` receive `CURRENT_PAGE / SYSTEM_DEFAULTED`.
- Legacy projects (backfilled in migration 008) receive `FULL_SITE / SYSTEM_DEFAULTED`, preserving prior behavior.
- `SYSTEM_DEFAULTED` is *not* treated as `USER_CONFIRMED`. Old projects see the scope gate on their next extraction.

**Crawl scope is always present on any spec:**
- `ensure_default_spec()` guarantees a valid scope object when creating or accessing a spec.
- The `PATCH /projects/{id}/spec` endpoint validates the scope payload through the `CrawlScope` Pydantic schema before persisting.

---

## Design Decisions and Trade-offs

### JSONB instead of a separate `crawl_scopes` table

The original review (`docs/reviews/02_product_ux_strategy.md`) recommended a 1:1 FK table. Implementation chose JSONB on `extraction_specs.crawl_scope` instead.

**Why:** The spec and scope change together — they define the extraction contract. A separate table adds join overhead with no clear benefit at current scale. JSONB makes adding new scope fields (e.g., `llms_txt_url`, `template_fingerprint`) non-breaking without a migration.

**Trade-off:** No DB-level shape validation. Correctness is enforced by the Pydantic `CrawlScope` schema and service-layer calls. A raw SQL update can bypass validation. Acceptable for self-hosted.

### `CrawlScopeMode` and `CrawlScopeStatus` as Python enums, not PostgreSQL enums

Phase 0.5 learned that PostgreSQL enum `ADD VALUE` cannot run inside a transaction (requires `autocommit_block()`). Adding new modes mid-product-life would require the same careful migration dance. JSONB string values validated at the service layer is simpler.

### Scope `status` lifecycle

```
AI_SUGGESTED  →  USER_CONFIRMED
SYSTEM_DEFAULTED  →  USER_CONFIRMED
```

`AI_SUGGESTED` is set when `default_spec_from_analysis()` produces a scope recommendation from the analysis result (future: when the analysis includes a scope confidence score). Currently most new projects start as `SYSTEM_DEFAULTED → CURRENT_PAGE`.

`USER_CONFIRMED` is set when the user explicitly patches `status = USER_CONFIRMED` or sends `user_confirmed_at`. The frontend sends this via the "Confirm scope" button in `ScopeSelector.tsx`.

### Frontier preview is persisted, not ephemeral

`create_frontier_preview()` persists a `FrontierPreview` row and returns it. It does not return an in-memory result. This allows:
- The GET endpoint to serve cached previews without re-fetching the seed page.
- The stale-preview detection in the frontend: if `scope_hash` changes, the UI warns that the preview is outdated.

Rejected alternative: return the preview in the POST response only (no persistence). Would require re-fetching the seed page on every project load. Seed pages can be large; caching the classification result is the right trade-off.

### `RecordPageResponse` with server-side pagination

Before Phase 2.5, results were loaded as a flat list via `GET /projects/{id}/records?skip=&limit=`. The max limit was 500. For projects with 1,000–100,000 records, the frontend would need multiple calls with no reliable total count.

Phase 2.5 added `GET /projects/{id}/records-page` with `RecordPageResponse`:
```json
{
  "items": [...],
  "total": 1000,
  "skip": 0,
  "limit": 100,
  "has_more": true,
  "next_skip": 100,
  "columns": ["product_name", "price", "category"]
}
```

`columns` is derived from the union of keys across the current page, biased toward spec field order. It drives the `PaginatedResultsTable` column headers without requiring a separate schema query.

`limit` is capped at 500 in the query validator. `limit=501` → HTTP 422.

---

## Code Walkthrough

### New modules

| File | Purpose |
|------|---------|
| `app/services/crawl_scope.py` | Pure helpers: defaults, normalization, confirmation check, `classify_links_for_scope()`, `discover_links_for_scope()` |
| `app/services/frontierpreview.py` | `create_frontier_preview()` — fetches seed, classifies links, persists `FrontierPreview` row |
| `app/services/extraction_quality.py` | `compute_extraction_quality()` — turns records + spec into a quality summary with per-field rates and warning codes |

### Schema changes

| Migration | Change |
|-----------|--------|
| `alembic/versions/008_phase25_foundation.py` | `crawl_scope JSONB` + `quality_summary JSONB` on `extraction_specs`; CREATE `frontier_previews` table; backfill existing specs with `LEGACY_COMPAT_CRAWL_SCOPE` |

### API endpoints added

| Method | Path | What it does |
|--------|------|-------------|
| `POST /projects/{id}/frontier-preview` | 201 | Fetch seed page, classify links, persist and return preview |
| `GET /projects/{id}/frontier-preview` | 200/404 | Return latest persisted preview |
| `GET /projects/{id}/records-page` | 200 | Paginated records with total, columns, next_skip |

### Frontend components added

| Component | Purpose |
|-----------|---------|
| `ScopeSelector.tsx` | Four-mode picker with AI suggestion badge, confirmation panel |
| `FrontierPreviewPanel.tsx` | Included/excluded URL tables with reason copy |
| `TrustSummaryPanel.tsx` | Quality overview with per-field rates and warning list |
| `PaginatedResultsTable.tsx` | Server-side pagination with page-size control |

---

## Runtime Lifecycle

### Scope confirmation gate — success path

```
POST /projects/{id}/extract
  → _owned_project() → 404 if not owned
  → load spec → ensure_default_spec() if none
  → load latest preview
  → if no preview AND not extract_anyway: 409 PREVIEW_REQUIRED
  → start_project_extraction(db, project, spec, preview)
      → assert_scope_confirmed(spec.crawl_scope)  ← gate runs here
      → project.transition_to(DISCOVERING)
      → delete existing CrawlPage/ExtractedRecord/Export rows
      → insert seed URL as CrawlPage(PENDING, depth=0)
  → background: execute_project_extraction(project_id, spec_id)
```

### Scope confirmation gate — failure path (409)

```
assert_scope_confirmed(scope):
  if scope.mode != CURRENT_PAGE and scope.status != USER_CONFIRMED:
    raise ScopeConfirmationError(scope)
  
endpoint catches ScopeConfirmationError:
  return 409 {"detail": {"error_code": "SCOPE_NOT_CONFIRMED", "scope": scope_dict, "message": "..."}}
```

Frontend catches the 409, reads `error_code === SCOPE_NOT_CONFIRMED`, and renders a user-language inline alert pointing to the scope confirmation panel.

### Frontier preview lifecycle

```
POST /frontier-preview
  → _owned_project() → check project state in {AWAITING_SETUP, ANALYSIS_READY, PREVIEW_READY, COMPLETED}
  → create_frontier_preview(db, project, spec)
      → load seed URL from spec
      → fetch seed page (real HTTP, same fetcher as extraction)
      → extract all anchor hrefs from HTML
      → classify_links_for_scope(links, seed_url, scope)
          → for each link: categorize as INCLUDED / EXCLUDED_SCOPE_MODE / EXCLUDED_DIFFERENT_ORIGIN / etc.
      → build FrontierPreview row with included_urls, excluded_urls, warnings, scope_hash
      → db.add(preview); await db.flush(); await db.refresh(preview)
      → return preview
  → commit; return 201 + FrontierPreviewResponse
```

### Extraction quality lifecycle

At the end of `execute_project_extraction()`, after the crawl loop and before COMPLETED:
```python
quality = compute_extraction_quality(records, spec)
spec.quality_summary = quality.model_dump()
await db.flush()
```

`quality_summary` is persisted on the spec (not on the export). Each re-extraction overwrites it. This means the quality summary reflects the *last* run, not a history of runs.

`ProjectResponse.extraction_quality` is mapped from `spec.quality_summary` via `ExtractionQuality.model_validate(spec.quality_summary)`.

---

## Concurrency and Crash Analysis

**Scope gate is idempotent.** `assert_scope_confirmed()` reads from the already-loaded spec. It does not write. Calling it twice is safe.

**Frontier preview creation is not concurrent-safe.** Two simultaneous `POST /frontier-preview` calls for the same project will both fetch the seed page and both insert a `FrontierPreview` row. The GET endpoint returns the *latest* row by `created_at`, so only one is visible. The orphan row accumulates. For self-hosted single-user, this is acceptable. A future fix: advisory lock on `(project_id)` in `create_frontier_preview()`.

**Quality summary write timing.** `compute_extraction_quality()` runs at extraction completion, before the project transitions to COMPLETED. If the process crashes between quality write and COMPLETED transition, the project stays in EXPORTING. The watchdog does not currently sweep EXPORTING (known gap — see `docs/reviews/01_codebase_audit.md` §8.7).

**Scope patch during active extraction.** A user can `PATCH /projects/{id}/spec` at any time (no state gate). If they update `crawl_scope` while extraction is running, the background executor has already loaded the spec at job start — it uses the snapshot, not the live DB value. The patch takes effect on the next extraction run.

---

## Pitfalls

**`extract_anyway=True` bypasses the preview-required gate but not the scope gate.** This is intentional — it allows validation scripts and power users to extract without a preview, but the scope must still be confirmed.

**Frontier preview uses the live HTTP fetcher.** It actually fetches the seed page. In test environments without `ALLOW_PRIVATE_NETWORK_URLS=true`, fetching `http://127.0.0.1/...` will be blocked by the SSRF validator. The validation script sets this env var.

**`EXCLUDED_DIFFERENT_ORIGIN` vs `EXCLUDED_SCOPE_MODE`.** Links to a different domain are `EXCLUDED_DIFFERENT_ORIGIN`. Links to the same domain that fall outside the scope mode are `EXCLUDED_SCOPE_MODE`. The `unrelated_same_origin_count` in the preview summary counts only `EXCLUDED_SCOPE_MODE` — not cross-origin links.

**`quality_summary` is `None` for projects that have never completed extraction.** `ProjectResponse.extraction_quality` is `None` when the spec has no quality summary. The frontend `TrustSummaryPanel` handles this by showing nothing.

**`columns` in `RecordPageResponse` is derived from the current page's records, not from the spec.** If a page has fewer records than expected, `columns` may be a subset of the full schema. The frontend uses spec field names (stable, ordered) as the primary column source and adds any extra keys from the current page.

---

## Safe-Evolution Notes

**Adding a new scope mode** (e.g., `CATEGORY`): add the string to `CrawlScopeMode` enum, add a branch in `classify_links_for_scope()`, add it to `_requires_confirmation_modes` in `assert_scope_confirmed()` if broad, add frontend copy in `scopeCopy.ts`.

**Adding a new frontier reason code**: add to `FrontierUrlDecision.reason` string union in `app/schemas/project.py`, add corresponding copy in `frontend/src/lib/frontierReasonCopy.ts`.

**Adding new quality warning codes**: add to `extraction_quality.py` and add copy in `frontend/src/lib/qualityCopy.ts`.

**Migrating `crawl_scope` shape**: bump `CRAWL_SCOPE_VERSION` constant (currently `1` in `app/models/job.py`). The version is stored in the JSONB, so old rows can be detected and migrated on read.

**Bumping `ANALYZER_VERSION`** (currently `"1"` in `app/services/analyzer.py`): invalidates the analysis cache. Every project analyzed before the bump will be re-analyzed on next access. Plan cache invalidation carefully if the user has many cached projects.

---

## Test Coverage

| Test file | Tests | What is covered |
|-----------|-------|-----------------|
| `tests/services/test_crawl_scope.py` | 26 | Scope defaults, normalization, confirmation gating, all four modes, navigation/dedupe, discovery limit |
| `tests/services/test_extraction_quality.py` | ~15 | All fields present, low success, required missing, no records, page failure thresholds, full-site risk |
| `tests/services/test_frontier_preview.py` | 12 | All four scope modes against the same `select_links_to_enqueue` seam used by the executor |
| `tests/api/v1/test_crawl_scope_gate.py` | 9 | Confirmation gate for all mode/status combos, legacy compat, extract_anyway |
| `frontend/src/lib/phase25.test.ts` | 39 | ScopeCopy, FrontierReasonCopy, QualityCopy, isScopeNotConfirmedError, buildColumns |
| `tests/validation/run_validation.py` | 8 E2E scenarios | Full end-to-end with live backend, real DB, real HTTP fixture server |

Total backend tests after Phase 2.5: **237 passed**.  
Total frontend tests after Phase 2.5: **70 passed**.
