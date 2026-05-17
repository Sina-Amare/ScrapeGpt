# ScrapGPT Unified Roadmap

Last updated: 2026-05-17

## Vision

ScrapGPT is intended to become a local-first, AI-assisted web scraping platform. The user enters a URL, the system validates and fetches the page, AI helps identify extractable data patterns, the user chooses exactly what to extract, and the platform crawls matching same-site pages with durable progress and export options.

The important architecture decision is that Gemini should assist the scraper, not become the scraper. Deterministic code should fetch pages, inspect DOM structure, validate selectors, crawl links, extract records, checkpoint progress, and generate exports. Gemini should be used sparingly for compact page understanding, selector suggestions, page classification, and crawl hints.

## Current State

What exists today:

- FastAPI backend with JWT auth, PostgreSQL persistence, SQLAlchemy async ORM, Alembic migrations, and health/readiness endpoints.
- Credit-gated scrape task admission with one active task per user.
- A single-URL background scrape pipeline using `httpx`, BeautifulSoup, and a stub LLM step.
- A state machine for `ScrapeTask` plus atomic credit deduction at the `SCRAPED -> LLM_PROCESSING` transition.
- APScheduler jobs for daily credit reset and stuck-task watchdog cleanup.
- Narrow tests for health/readiness only.

What does not exist yet:

- Frontend GUI.
- Real Gemini integration.
- Browser rendering for JavaScript-heavy pages.
- User-selectable element extraction.
- Multi-page crawling.
- Page-level checkpointing and resume.
- Export pipeline.
- Comprehensive tests around auth, admission, state transitions, watchdog, pipeline execution, crawling, and export.

## Product Constraints

- Use only the free Google AI Studio Gemini API key. No paid APIs or premium services.
- Build a local MVP first, not a public hosted SaaS.
- Crawl same-site pages only for v1.
- Do not bypass logins, captchas, paywalls, or anti-bot systems.
- Treat model IDs and free-tier limits as configuration. Verify current Google AI Studio docs before implementation.
- Preserve the existing backend invariants unless there is a deliberate migration plan.

## Core Principles

- Deterministic extraction first: use DOM parsing, CSS selectors, URL normalization, hashes, database constraints, and explicit state transitions.
- Gemini assists only where it adds value: structure analysis, selector suggestions, page classification, and crawl strategy hints.
- Never call Gemini per page for normal extraction. That would be slow, expensive under free-tier limits, and less reliable than validated selectors.
- Validate every Gemini response with Pydantic before using it.
- Test Gemini-suggested selectors against real DOM samples before presenting them as user choices.
- Commit progress after each page and record so crashes do not lose completed work.
- Keep public APIs explicit and versioned under `/api/v1`.

## Phase 0: Stabilize the Backend

Fix these before building new features:

1. Fix `POST /scrape/start` SlowAPI parameter collision by using a real `Request` parameter and renaming the body parameter to `payload`.
2. Fix `/scrape/tasks/current` route shadowing by declaring the static route before `/tasks/{task_id}`.
3. Fix watchdog cleanup so nullable `updated_at` does not skip fresh stuck tasks. Use `COALESCE(updated_at, created_at)` or make `updated_at` non-null with a migration.
4. Resolve migration enum drift from old states such as `FINALIZED`, `LLM_ANALYZED`, and `OUTPUT_GENERATION`.
5. Wrap JWT `int(payload.sub)` conversions so malformed tokens return 401 instead of 500.
6. Wire authenticated rate limiting properly. `rate_limit.py` expects `request.state.user`, but nothing sets it today.
7. Apply auth endpoint rate limits or remove the unused constant.
8. Decide the `/scrape/tasks/current` empty-state contract: return 404 or return `null`, then make schema and implementation match.
9. Wire or remove unused config such as `SCRAPE_CREDIT_COST`, `LLM_TIMEOUT`, and `MAX_CONCURRENT_JOBS`.
10. Add regression tests for each fix.

## Phase 1: URL Validation and Fetching

Add a preview-oriented fetch layer:

- Validate only `http` and `https` URLs.
- Resolve DNS and reject localhost/private-network targets by default to prevent SSRF if the app is later hosted.
- Follow redirects safely and store the final URL.
- Check content type, response size, and timeout behavior.
- Use `HEAD` first, then safe `GET` fallback when servers reject `HEAD`.
- Respect `robots.txt` by default and surface blocked/warned states in the UI.
- Create a unified fetch interface with `render_mode: auto | static | browser`.
- Use static mode for `httpx + BeautifulSoup`.
- Use browser mode for Playwright Chromium.
- In auto mode, try static first and fall back to browser when the page appears empty, JS-dependent, or link-poor.

## Phase 2: AI Page Analysis

Replace the current `llm_processor.py` stub with a Gemini-backed analysis layer:

- Add typed settings: `GEMINI_API_KEY`, `GEMINI_MODEL_FAST`, `GEMINI_MODEL_REASONING`, request limits, and timeout values.
- Use the official `google-genai` SDK.
- Build compact DOM summaries before calling Gemini: repeated cards, tables, lists, schema.org JSON-LD, headings, labels, links, and sample text.
- Ask Gemini for structured output: page type, candidate fields, selectors, repeated item groups, likely detail-page links, warnings, and confidence scores.
- Validate Gemini output with Pydantic.
- Cache analysis by content hash and normalized site pattern to reduce API usage.
- Fail gracefully on 429s and timeouts with retry/backoff or a paused state.

## Phase 3: GUI Workflow

Create the first usable interface as the actual tool, not a landing page.

Recommended frontend stack:

- React
- Vite
- TypeScript
- Tailwind
- TanStack Query

Main screens:

- URL entry and validation: URL input, render mode, validation result, credit status.
- Preview and selection: screenshot/DOM preview, detected groups, suggested fields, sample rows, editable selectors.
- Run configuration: page limit, same-site crawl scope, export format.
- Progress: live task progress via Server-Sent Events with polling fallback.
- Results: table preview, failed-page list, partial export, final export.

## Phase 4: Multi-Page Crawling

Implement same-site discovery with deterministic crawling:

- Normalize URLs and dedupe by normalized URL and content hash.
- Crawl same host by default.
- Use BFS with priority: seed/list pages first, likely detail pages next.
- Parse sitemap files when available.
- Detect pagination, next/previous links, list pages, detail pages, and rendered links.
- Classify pages as `index`, `detail`, `pagination`, or `irrelevant`.
- Use Gemini only on samples or ambiguous pages, not every page.
- Stop when the requested `page_limit` records are extracted or no matching pages remain.

For random-ID pages, the crawler can discover them only if they are linked, rendered, exposed through sitemap/search/category/API responses, or otherwise discoverable from reachable pages. It should not pretend to guess completely unlinked random identifiers.

## Phase 5: Checkpointing and Recovery

Add page-level persistence:

- Track each page as queued, fetching, fetched, extracting, extracted, failed, or canceled.
- Store normalized URL, final URL, content hash, retry count, last error, lease owner, and lease expiry.
- Use unique constraints such as `(task_id, normalized_url)` to prevent duplicate work.
- Commit after every page and every extracted record.
- Requeue expired in-progress pages until retry limit.
- Mark permanently failed pages without failing the entire task unless the task has no usable results.
- Allow partial exports from committed records at any point.

## Phase 6: Export and Results

Build exports from committed records, not in-memory task state:

- CSV
- JSON
- JSONL
- XLSX

Each record should include:

- Extracted fields.
- `source_url`.
- Extraction timestamp.
- Optional per-field confidence or extraction warnings.

Store generated export artifacts under task-local storage and regenerate when field configuration changes.

## Public API Shape

Keep the current `/api/v1/scrape/start` endpoint as legacy simple mode while adding the richer workflow.

### `POST /api/v1/scrape/preview`

Input:

```json
{
  "url": "https://example.com",
  "render_mode": "auto"
}
```

Behavior:

- Validate URL.
- Fetch or render seed page.
- Discover initial same-site links.
- Generate deterministic DOM candidates.
- Call Gemini for compact page-structure suggestions.
- Return a task in `AWAITING_SELECTION`.

### `POST /api/v1/scrape/tasks/{task_id}/run`

Input:

```json
{
  "fields": [
    {
      "name": "title",
      "type": "text",
      "selector": "h1",
      "attribute": null,
      "required": true
    }
  ],
  "page_limit": 25,
  "export_format": "csv",
  "crawl_scope": "same_site"
}
```

Behavior:

- Validate the approved extraction spec.
- Start or resume extraction.
- Crawl same-site links up to `page_limit`.
- Extract records into durable storage.

### Other Endpoints

- `GET /api/v1/scrape/tasks/{task_id}`: progress and state.
- `GET /api/v1/scrape/tasks/{task_id}/records`: paginated extracted records.
- `GET /api/v1/scrape/tasks/{task_id}/export`: committed records as `csv`, `json`, `jsonl`, or `xlsx`.
- `POST /api/v1/scrape/tasks/{task_id}/cancel`: graceful cancel.
- `GET /api/v1/scrape/tasks/{task_id}/stream`: optional SSE progress stream.

## Planned Core Types

```python
class ExtractField(BaseModel):
    name: str
    type: Literal["text", "number", "url", "image", "date", "boolean"]
    selector: str
    attribute: str | None = None
    required: bool = False


class RunRequest(BaseModel):
    fields: list[ExtractField]
    page_limit: int
    export_format: Literal["csv", "json", "jsonl", "xlsx"]
    crawl_scope: Literal["same_site"] = "same_site"


class PreviewRequest(BaseModel):
    url: HttpUrl
    render_mode: Literal["auto", "static", "browser"] = "auto"
```

Planned task states:

- `AWAITING_SELECTION`
- `DISCOVERING`
- `EXTRACTING`
- `EXPORTING`
- `COMPLETED`
- `FAILED`
- `CANCELED`

## Test Plan

Add tests in this order:

1. Regression tests for Phase 0 backend bugs.
2. Auth happy paths and malformed token behavior.
3. Admission service tests, including insufficient credits and one-active-task behavior.
4. State transition tests, especially atomic credit deduction.
5. Pipeline tests with mocked scrape and LLM calls.
6. Watchdog tests, including nullable `updated_at`.
7. URL validation and SSRF/private-network blocking tests.
8. Static fetch and browser fetch integration tests.
9. Gemini response parsing tests with mocked SDK responses.
10. Local fixture-site crawling tests.
11. Recovery tests that simulate process interruption and resume.
12. Export writer tests for CSV, JSON, JSONL, and XLSX.

Fixture sites should include:

- Static nutrition site with 100 random-ID detail pages.
- JS-rendered version of the same fixture.
- Broken links.
- Duplicate links.
- Missing fields.
- Pagination and non-sequential detail URLs.

Acceptance target:

- From a random-ID 100-page fixture, request `page_limit=25`.
- Extract exactly 25 records.
- Survive interruption and resume.
- Export identical CSV and JSON after resume.

## Documentation Rules

For every completed implementation task, add a learning document under `docs/learning/` or another appropriate docs folder. Explain the problem, invariants, design decisions, trade-offs, lifecycle, failure modes, and safe evolution notes.

Keep these docs aligned:

- `docs/PROJECT_CONTEXT.md`: LLM/human handoff context.
- `docs/STATUS.md`: current known issues and next work.
- `docs/architecture.md`: current implemented backend architecture.
- `docs/plan/ROADMAP.md`: unified product and implementation roadmap.

## Assumptions

- No paid APIs or paid infrastructure.
- Gemini free-tier limits vary by model/project and must be configurable.
- Free-tier Gemini content may be used by Google to improve products; do not send sensitive private data unless that trade-off is acceptable.
- Browser rendering increases CPU/memory cost and bot-detection exposure; use it only when needed.
- The first implementation remains local-first.
- Production SaaS concerns such as billing, abuse handling, tenant isolation, admin tooling, and distributed workers are out of scope for the first usable version.
