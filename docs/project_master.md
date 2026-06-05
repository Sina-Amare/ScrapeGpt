# ScrapGPT — Master Project Reference

> Read this file to fully understand the project state, architecture, testing approach, and the complete roadmap. It is the single source of truth for onboarding or resuming work.

---

## Table of Contents

1. [What ScrapGPT Is](#1-what-scrapegpt-is)
2. [Setup & Running Locally](#2-setup--running-locally)
3. [Current Implementation Status](#3-current-implementation-status)
4. [Architecture Deep Dive](#4-architecture-deep-dive)
5. [Database Schema](#5-database-schema)
6. [Key Invariants — Do Not Break](#6-key-invariants--do-not-break)
7. [Testing Guide](#7-testing-guide)
8. [Implementation Roadmap (Phases 1–7)](#8-implementation-roadmap-phases-17)

---

## 1. What ScrapGPT Is

ScrapGPT is an async FastAPI backend for authenticated, credit-gated URL scraping with an AI post-processing stage. The full vision is:

- User enters a URL
- Backend fetches and renders the page (static or Playwright browser)
- Gemini AI analyzes the DOM and suggests extractable fields (name, price, link, etc.)
- User reviews/edits the field config and starts crawl
- Backend BFS-crawls the site, extracts structured records from every page
- User downloads results as CSV / JSON / JSONL / XLSX

**Current state:** The backend auth, task pipeline, and credit system are fully implemented. The LLM stage is still a stub. No frontend exists.

---

## 2. Setup & Running Locally

### Prerequisites
- Python 3.11+
- PostgreSQL 14+ running locally
- Node.js 18+ (for frontend, Phase 4)

### First-time setup

```powershell
# 1. Create and activate venv
python -m venv venv
.\venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and fill in env vars
cp .env.example .env
# Edit .env — set DATABASE_URL and SECRET_KEY at minimum

# 4. Create the database (PostgreSQL must be running)
# Connect to psql and run: CREATE DATABASE scrapegpt;

# 5. Apply all migrations
alembic upgrade head

# 6. Run the dev server
uvicorn app.main:app --reload
# API docs available at http://localhost:8000/docs (only when DEBUG=true)
```

### Environment variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | ✓ | `postgresql+asyncpg://user:pass@localhost/scrapegpt` |
| `SECRET_KEY` | ✓ | 64-char random hex string (`openssl rand -hex 32`) |
| `DEBUG` | — | `true` to mount `/docs`, `/redoc`, `/openapi.json` |
| `GEMINI_API_KEY` | Phase 2 | Google Gemini API key |
| `REDIS_URL` | Phase 7 | `redis://localhost:6379/0` (optional — memory fallback) |

### Useful dev commands

```powershell
# Run all tests
pytest -v

# Run a specific file
pytest tests/api/v1/test_health_readiness.py -v

# Run a specific test
pytest tests/services/test_readiness.py::test_name -v

# Create a new migration after editing models
alembic revision --autogenerate -m "description"

# Roll back last migration
alembic downgrade -1

# Show migration history
alembic history

# Check current DB revision
alembic current
```

---

## 3. Current Implementation Status

### Fully working

| Feature | Files | Notes |
|---------|-------|-------|
| User registration | `app/api/v1/endpoints/auth.py`, `app/services/` | bcrypt hashing, JWT issued on register |
| Login | same | Returns access (15min) + refresh (7d) JWT |
| Token refresh | same | `POST /auth/refresh` with refresh token |
| Rate limiting | `app/core/rate_limit.py` | Per-user (JWT-gated) or IP fallback; SlowAPI |
| Task admission | `app/services/admission.py` | Credit gate + unique index enforcement |
| Scrape pipeline | `app/services/task_executor.py` | `POST /scrape/start` → httpx + BS4 |
| State machine | `app/models/scrape_task.py` | 6 states, VALID_TRANSITIONS, terminal guard |
| Credit deduction | `app/services/task_state.py` | Atomic at `SCRAPED → LLM_PROCESSING` |
| Daily credit reset | `app/core/scheduler.py` | APScheduler CronTrigger 00:00 UTC, CAS |
| Watchdog | `app/services/watchdog.py` | Unsticks tasks stuck in non-terminal states |
| Health endpoints | `app/api/v1/endpoints/health.py` | `/health/live`, `/health/ready` |
| Route ordering | `app/api/v1/endpoints/scrape.py` | `/tasks/current` correctly before `/{task_id}` |

### Stubs (not real yet)

| Feature | File | Status |
|---------|------|--------|
| LLM processing | `app/services/llm_processor.py` | Sleeps 1s, returns mock dict — will be replaced by Gemini in Phase 2 |

### Not yet built

- URL validation / SSRF prevention
- robots.txt compliance
- Playwright browser rendering
- Gemini AI page analysis
- New state machine (AWAITING_SELECTION, DISCOVERING, EXTRACTING, EXPORTING, CANCELED)
- Multi-page BFS crawler
- Per-page checkpointing
- Extracted records storage
- Export pipeline (CSV / JSON / JSONL / XLSX)
- SSE progress stream
- React frontend
- User profile endpoints
- Email verification
- Redis distributed rate limiting

---

## 4. Architecture Deep Dive

### Layer structure (strict dependency direction)

```
app/api/v1/endpoints/   ← HTTP only. Parse, validate, delegate. No business logic.
app/services/           ← All business logic. Own database transactions.
app/models/ + app/db/   ← SQLAlchemy 2.0 async ORM (asyncpg driver).
app/core/               ← Cross-cutting: config, security, rate_limit, scheduler.
```

### The scrape pipeline (current)

```
POST /scrape/start
  │
  ├─ admission.py: check credits + check no active task
  │   ├─ Creates task in PERMISSION_GRANTED
  │   └─ Returns AdmissionSuccess or typed AdmissionError
  │
  ├─ 202 returned immediately
  │
  └─ BackgroundTasks: execute_scrape_pipeline(task_id)
        │
        ├─ transition_to_scraping   (own session + db.begin())
        ├─ httpx.get(url) → HTML
        ├─ transition_to_scraped    (stores content)
        ├─ llm_processor.process()  (STUB — returns mock)
        ├─ transition_to_llm_processing  ← CREDIT DEDUCTED HERE
        ├─ transition_to_completed  (stores result JSON)
        │
        └─ on any exception → transition_to_failed (always-finalize guarantee)
```

### Session management (critical pattern)

**Each transition function owns its own session.** This was the most important bug fix (Phase 0).

```python
# CORRECT pattern in task_state.py
async def transition_to_scraping(task_id: int) -> TransitionResult:
    async with async_session_factory() as db:
        async with db.begin():
            task = await db.get(ScrapeTask, task_id)
            # ... validate, mutate ...
        await db.refresh(task)
    return TransitionResult(success=True, task=task)
```

Why: SQLAlchemy 2.0 autobegin fires on `db.get()`. If a session is shared from the executor, calling `async with db.begin()` inside a transition raises `InvalidRequestError`. The fix: each transition opens its own fresh session.

### Atomic credit deduction

```python
# In transition_to_llm_processing (task_state.py)
await db.execute(
    text("UPDATE users SET credits_remaining = credits_remaining - :cost "
         "WHERE id = :user_id AND credits_remaining >= :cost"),
    {"cost": SCRAPE_CREDIT_COST, "user_id": task.user_id}
)
```

This runs inside the same `db.begin()` as the state change. If the row update affects 0 rows (credits already 0), the function raises, the transaction rolls back, and the task goes to FAILED. Scrape failures do NOT cost credits — only reaching the LLM stage does.

### Rate limiting

```python
# In rate_limit.py — uses verify_token (signature check), NOT decode_token
def get_user_identifier(request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        payload = verify_token(token, token_type="access")  # ← full validation
        if payload:
            return f"user:{payload.sub}"
    return get_remote_address(request)
```

Why `verify_token` and not `decode_token`: `decode_token` skips signature verification, which would let an attacker forge a JWT with any `sub` and exhaust another user's rate limit bucket.

### Watchdog

Runs every 60s via APScheduler. Marks tasks stuck past configurable timeouts as FAILED using `transition_to_failed()` (which opens its own session). Uses `func.coalesce(updated_at, created_at)` to handle tasks where `updated_at` is NULL.

### Credit reset

Runs daily at 00:00 UTC. Uses check-and-set on `system_state` table:
```sql
UPDATE system_state
SET value = :new_timestamp
WHERE key = 'last_credit_reset'
  AND value < :cutoff        -- only the worker that wins this race proceeds
```
Safe with multiple workers — only one worker will execute the reset.

---

## 5. Database Schema

### Current tables and migrations

| Migration | Description |
|-----------|-------------|
| `001` | Create `users`, `scrape_tasks` tables |
| `002` | Add legacy enum values (FINALIZED, LLM_ANALYZED, etc.) — now cleaned up |
| `003` | Add partial unique index: `(user_id) WHERE state NOT IN ('COMPLETED','FAILED')` |
| `004` | Add `system_state` table for credit reset CAS |
| `fe292fc905ad` | Remove legacy enum values, clean enum to 6 valid states |

### `users`
```
id                    SERIAL PK
email                 VARCHAR UNIQUE NOT NULL
hashed_password       VARCHAR NOT NULL
is_active             BOOLEAN DEFAULT true
is_verified           BOOLEAN DEFAULT false
credits_remaining     INTEGER DEFAULT 10
daily_credit_limit    INTEGER DEFAULT 10
credits_reset_at      TIMESTAMPTZ nullable
created_at            TIMESTAMPTZ DEFAULT NOW()
updated_at            TIMESTAMPTZ
```

### `scrape_tasks`
```
id                    SERIAL PK
user_id               INTEGER FK → users(id) ON DELETE CASCADE
state                 task_state enum (see below)
url                   VARCHAR NOT NULL
content               TEXT nullable   (raw HTML/text from scrape)
error                 TEXT nullable   (error message if FAILED)
result                JSONB nullable  (LLM output)
created_at            TIMESTAMPTZ DEFAULT NOW()
updated_at            TIMESTAMPTZ

INDEX: ix_scrape_tasks_user_id
UNIQUE INDEX: ix_one_active_task_per_user (user_id) WHERE state NOT IN ('COMPLETED','FAILED')
```

### `task_state` enum
```
PERMISSION_GRANTED, SCRAPING, SCRAPED, LLM_PROCESSING, COMPLETED, FAILED
```

### `system_state`
```
key         VARCHAR PK
value       VARCHAR NOT NULL
updated_at  TIMESTAMPTZ
```

---

## 6. Key Invariants — Do Not Break

**1. One non-terminal task per user**
Enforced twice: admission check in `admission.py` AND PostgreSQL partial unique index. The index is the concurrency safety net for concurrent requests. Never relax either guard.

**2. Credits deducted exactly once, atomically**
In `transition_to_llm_processing()`. Same transaction as state change. Never move this to admission or completion. Scrape failures must not cost credits.

**3. VALID_TRANSITIONS is the source of truth**
In `app/models/scrape_task.py`. All transition functions re-check this before mutating. The `is_terminal` check prevents resurrecting a finished task.

**4. Always-finalize guarantee**
`task_executor.py` has an outer `try/except` that catches all exceptions and calls `transition_to_failed`. Every task reaches a terminal state, even on unexpected crashes.

**5. Each transition owns its own session**
Never pass a session into a transition function. Never call `db.begin()` on a session that has already auto-begun. Each `transition_to_*` function opens `async with async_session_factory() as db: async with db.begin():`.

**6. Credit reset is multi-instance safe**
The CAS in `scheduler.py` uses a `WHERE value < :cutoff` guard. Do not replace this with lazy/on-read reset.

---

## 7. Testing Guide

### Current test coverage

| File | What it tests |
|------|--------------|
| `tests/api/v1/test_health_readiness.py` | `/health/live`, `/health/ready`, DB connectivity |
| `tests/services/test_readiness.py` | Readiness service logic |
| `tests/core/test_rate_limit.py` | Rate limit key function, forged JWT rejection, refresh endpoint rate limiting |

**Total: 16 tests, all passing.**

### Running tests

```powershell
# All tests
pytest -v

# One file
pytest tests/core/test_rate_limit.py -v

# Tests matching a name pattern
pytest -k "rate_limit" -v

# Stop on first failure
pytest -x -v
```

### Test database setup (for future service/API tests)

Service tests (admission, task_state, watchdog) require a real DB because these services call `async_session_factory` directly. To extend tests:

1. Create a test DB: `CREATE DATABASE scrapegpt_test;`
2. Add to `.env`: `TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/scrapegpt_test`
3. Extend `tests/conftest.py`:
   ```python
   # session-scoped engine — create_all on start, drop_all on teardown
   # function-scoped db_session — yields AsyncSession, rolls back after each test
   # test_user fixture — creates User in db_session
   # auth_headers fixture — {"Authorization": "Bearer <valid_token>"}
   # async_client fixture — AsyncClient(app=app) with get_db overridden to use db_session
   ```
4. Patch `app.db.database.async_session_factory` in service tests so it uses the test engine.

### Key test patterns already in codebase

```python
# test_rate_limit.py — constructing a fake Starlette Request
def _request_with_authorization(value: str | None) -> Request:
    headers = []
    if value is not None:
        headers.append((b"authorization", value.encode("ascii")))
    return Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": headers,
        "client": ("203.0.113.9", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    })

# Forging a JWT to test that verify_token rejects it
forged_token = jwt.encode(
    {"sub": "victim-user", "type": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
    "wrong-secret",
    algorithm=settings.JWT_ALGORITHM,
)
assert get_user_identifier(_request_with_authorization(f"Bearer {forged_token}")) == "203.0.113.9"
```

---

## 8. Implementation Roadmap (Phases 1–7)

### Phase 1 — Test Infrastructure + URL Hardening

**Goal:** Build the test harness everything else will rely on, and harden the URL fetch layer.

**New files:**
- `app/services/url_validator.py` — SSRF prevention, private IP blocking, scheme enforcement
- `app/services/robots.py` — robots.txt fetch + parse with LRU cache
- `app/services/fetcher.py` — unified fetch: `fetch_static` (httpx), `fetch_browser` (Playwright), `fetch_auto` (static first, browser if < 50 words)

**`fetcher.py` key design:**
```python
@dataclass
class FetchResult:
    html: str
    final_url: str
    render_mode_used: Literal["static", "browser"]
    status_code: int
    content_type: str
    content_hash: str   # SHA-256 hex — used for Gemini cache key

# fetch_browser: module-level Playwright Chromium singleton.
# get_browser() launches once. main.py lifespan closes it on shutdown.
# One ephemeral context per fetch (not one page on shared context).
```

**`app/core/config.py` additions:**
```python
MAX_PAGE_SIZE_BYTES: int = 10_485_760
BROWSER_TIMEOUT_MS: int = 30_000
ROBOTS_CHECK_ENABLED: bool = True
SSRF_BLOCK_PRIVATE: bool = True
PLAYWRIGHT_BROWSER: str = "chromium"
PLAYWRIGHT_HEADLESS: bool = True
GEMINI_API_KEY: str = ""
GEMINI_MODEL_FAST: str = "gemini-1.5-flash"
GEMINI_MODEL_REASONING: str = "gemini-1.5-pro"
GEMINI_MAX_RETRIES: int = 3
CRAWL_MAX_PAGES: int = 200
CRAWL_CONCURRENCY: int = 3
CRAWL_DELAY_SECONDS: float = 1.0
EXPORT_DIR: str = "./exports"
EXPORT_MAX_RECORDS: int = 10_000
REDIS_URL: str = ""
WATCHDOG_DISCOVERING_TIMEOUT_MINUTES: int = 15
WATCHDOG_EXTRACTING_TIMEOUT_MINUTES: int = 60
WATCHDOG_AWAITING_SELECTION_TIMEOUT_HOURS: int = 24
```

**`requirements.txt` additions:**
```
playwright>=1.40.0
google-genai>=1.0.0
sse-starlette>=1.6.0
openpyxl>=3.1.0
aiofiles>=23.0.0
```

**New tests:** `test_url_validator.py`, `test_robots.py`, `test_fetcher.py` (static with `respx` mocks; browser tests marked `@pytest.mark.slow`).

---

### Phase 2 — New State Machine + DB Models + Gemini

**Goal:** Expand the state machine, create all new DB tables, wire real Gemini, build the preview pipeline.

#### New state machine

```
PERMISSION_GRANTED → SCRAPING → SCRAPED → AWAITING_SELECTION
                                                 ↓ (user submits field config)
                                           DISCOVERING → EXTRACTING → EXPORTING → COMPLETED
                     (any non-terminal) → FAILED | CANCELED

TERMINAL_STATES = {COMPLETED, FAILED, CANCELED}
```

#### Migration 006 — fix partial unique index for CANCELED
```python
def upgrade():
    op.execute("DROP INDEX IF EXISTS ix_one_active_task_per_user")
    op.execute("""
        CREATE UNIQUE INDEX ix_one_active_task_per_user
        ON scrape_tasks (user_id)
        WHERE state NOT IN ('COMPLETED', 'FAILED', 'CANCELED')
    """)
```

#### Migration 007 — new enum values + all new tables

> PostgreSQL `ADD VALUE` cannot run inside a transaction. Use `op.get_context().autocommit_block()` for enum extensions only.

New tables: `crawl_pages`, `extraction_specs`, `extracted_records`, `exports`, `gemini_cache`

New columns on `scrape_tasks`: `analysis JSONB`, `render_mode VARCHAR(20)`

**New models:** `app/models/crawl_page.py`, `extraction_spec.py`, `extracted_record.py`, `export.py`, `gemini_cache.py`

`CrawlPage` key design:
```python
# Unique constraint prevents re-queueing the same URL within a task
UniqueConstraint("task_id", "normalized_url", name="uq_task_page_url")
# Index for BFS QUEUED batch queries
Index("ix_crawl_pages_task_state", "task_id", "state")
```

#### Gemini service (`app/services/gemini.py`)

```python
class PageAnalysis(BaseModel):
    page_type: Literal["listing", "detail", "mixed", "search", "other"]
    repeated_item_selector: str | None
    candidate_fields: list[ExtractFieldSuggestion]
    detail_link_selector: str | None
    pagination_selector: str | None
    confidence: float
    warnings: list[str]

async def analyze_page(html, url, model=None, use_cache=True) -> PageAnalysis:
    # 1. build_dom_summary (extract title, h1-h3, links, JSON-LD, repeated classes)
    # 2. SHA-256 of summary → check gemini_cache table
    # 3. On cache miss: genai.Client().aio.models.generate_content(response_schema=PageAnalysis)
    # 4. On parse failure: retry with GEMINI_MODEL_REASONING
    # 5. Store in gemini_cache with 24h TTL
    # 6. On 429: exponential backoff up to GEMINI_MAX_RETRIES
```

#### Credit deduction moves to `transition_to_awaiting_selection`

Same atomic pattern as before — `UPDATE users SET credits_remaining - :cost WHERE id = :user_id AND credits_remaining >= :cost` inside the same `db.begin()` as the state change.

#### Preview pipeline (`app/services/preview_executor.py`)

```
POST /scrape/preview → admit → BackgroundTasks → execute_preview_pipeline
  1. transition_to_scraping
  2. validate_url (SSRF check)
  3. check_robots_txt
  4. fetch_url (static or browser per render_mode)
  5. transition_to_scraped
  6. analyze_page (Gemini)
  7. transition_to_awaiting_selection  ← credit deducted here
  On any error: transition_to_failed (always-finalize)
```

#### New endpoint: `POST /scrape/preview`
Returns 202 with task ID. Client polls `GET /scrape/tasks/{id}` until state == `AWAITING_SELECTION`, then reads `task.analysis.candidate_fields`.

#### New endpoint: `POST /scrape/tasks/{id}/cancel`
Valid from any non-terminal state. Calls `transition_to_canceled`.

---

### Phase 3 — New API Endpoints (Run / Records / Export / SSE)

**Goal:** Complete the backend API surface.

#### New schemas in `app/schemas/scrape.py`

```python
class ExtractField(BaseModel):
    name: str       # regex: ^[a-zA-Z_][a-zA-Z0-9_]*$, max 64
    type: Literal["text", "number", "url", "image", "date", "boolean"]
    selector: str   # CSS selector, max 512
    attribute: str | None = None   # "href", "src", or None (text content)
    required: bool = False

class RunRequest(BaseModel):
    fields: list[ExtractField]      # 1–50 items
    page_limit: int = Field(25, ge=1, le=500)
    export_format: Literal["csv", "json", "jsonl", "xlsx"] = "csv"
    crawl_scope: Literal["same_site"] = "same_site"
```

#### New endpoints

**`POST /scrape/tasks/{id}/run`** — Verify state == AWAITING_SELECTION, validate CSS selectors (`cssselect.parse`), upsert ExtractionSpec, queue `execute_crawl_pipeline`.

**`GET /scrape/tasks/{id}/records?page=1&page_size=50`** — Paginated ExtractedRecord rows.

**`GET /scrape/tasks/{id}/export?format=csv`** — Check Export cache → generate if miss → StreamingResponse with correct Content-Disposition. Works even for FAILED tasks (partial export).

**`GET /scrape/tasks/{id}/stream`** (SSE) — EventSourceResponse polling task + counts every 2s. Sends `completed`/`failed` event on terminal state then stops.

**`GET /scrape/tasks/{id}`** — Extended response with `pages_discovered`, `pages_extracted`, `records_count`, `analysis`, `final_url`, `render_mode`.

---

### Phase 4 — React Frontend

**Goal:** Build all UI screens.

**Stack:** React + Vite + TypeScript + Tailwind CSS + TanStack Query + Zustand + shadcn/ui

**Location:** `frontend/` at project root (separate from Python backend)

**Key design decisions:**
- Access token in **memory only** (not localStorage) — XSS prevention
- Refresh token in localStorage
- Axios interceptor: auto-refresh on 401, then retry original request
- Vite proxy: `/api` → `http://localhost:8000` in dev

**User flow:**
1. **PreviewPage:** Enter URL → `POST /scrape/preview` → poll until `AWAITING_SELECTION` → show `analysis.candidate_fields` in editable table (FieldEditor)
2. User edits field config → clicks "Start Extraction" → `POST /tasks/{id}/run`
3. **ProgressPage:** SSE from `/tasks/{id}/stream` → live pages/records counts → auto-navigate on `completed`
4. **ResultsPage:** Paginated records table + export buttons for each format

**Add to `app/core/config.py`:**
```python
CORS_ORIGINS: list[str] = ["http://localhost:5173"]  # Vite default
```

---

### Phase 5 — Multi-Page BFS Crawler + Checkpointing

**Goal:** Implement the full crawl pipeline with per-page transaction commits for crash recovery.

#### `app/services/url_normalizer.py`
Normalize → lowercase scheme+host, remove fragment, sort query params, strip tracking params (utm_*, fbclid, gclid), reject non-HTML extensions, reject cross-origin.

#### `app/services/extractor.py`
CSS extraction with lxml + cssselect. Per-field: select, get text or attribute, type-coerce (number → float, url → resolve relative, date → ISO string). Missing required field → warning + `None` value. For list pages: returns one dict per matched container.

#### `app/services/crawl_executor.py` — BFS loop with checkpointing

```python
async def execute_crawl_pipeline(task_id, user_id):
    # transition_to_discovering
    # seed page: re-fetch, extract links, INSERT QUEUED pages
    # transition_to_extracting
    # BFS loop:
    #   batch = SELECT crawl_pages WHERE state=QUEUED LIMIT CRAWL_CONCURRENCY
    #   gather(process_page(p) for p in batch)
    # transition_to_exporting
    # generate_export
    # transition_to_completed

async def process_page(page_id, spec, semaphore):
    # Tx 1: mark FETCHING with FOR UPDATE (prevents double-processing)
    # Fetch HTML
    # Tx 2: store final_url + content_hash, mark FETCHED
    # Discover links → INSERT QUEUED ON CONFLICT DO NOTHING
    # Tx 3: extract_record → INSERT ExtractedRecords + mark EXTRACTED
```

Three separate transactions per page = crash-safe. If process dies mid-extraction, watchdog resets FETCHING pages to QUEUED and they get reprocessed.

#### Watchdog extensions for new states
- `AWAITING_SELECTION` > 24h → FAILED
- `DISCOVERING` > 15min → FAILED
- `EXTRACTING` > 60min → FAILED
- `CrawlPage` in `FETCHING` > 10min → reset to QUEUED (page-level recovery, not task failure)

---

### Phase 6 — Export Pipeline

**Goal:** Generate all four export formats from ExtractedRecord rows.

**`app/services/export.py`:**
```python
async def generate_export(task_id, format, spec) -> Export:
    # spec_hash = SHA-256(json.dumps(spec.fields, sort_keys=True))
    # Check exports table for cache hit (same task_id + format + spec_hash)
    # Load records with yield_per(500) to avoid OOM on large datasets
    # Write to exports/{task_id}/{format}_{spec_hash[:8]}.{ext} via aiofiles
    # Store Export row

# Per-format generators:
generate_csv   # csv.DictWriter, UTF-8 BOM (Excel-compatible)
generate_json  # json.dumps indent=2, with metadata header
generate_jsonl # one JSON object per line
generate_xlsx  # openpyxl Workbook, auto column widths
```

Each record includes: all extracted fields in spec order, `source_url`, `extracted_at`, `_warnings` (list, omitted if empty).

---

### Phase 7 — User Management + Operational Hardening

#### User endpoints (`app/api/v1/endpoints/users.py`)
```
GET    /users/me          → UserResponse (credits, limit, verified, created_at)
PUT    /users/me          → change password (requires current_password)
GET    /users/me/tasks    → paginated task history with records_count per task
DELETE /users/me          → soft delete (set is_active=False)
```

#### Email verification
New migration: `email_verification_token VARCHAR(64)`, `email_verification_expires_at TIMESTAMPTZ`

```
POST /auth/send-verification   → generate token, send email via aiosmtplib
GET  /auth/verify-email?token= → validate, set is_verified=True
```

In `DEBUG=True`: log token to console instead of sending email.

New config: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`.

#### Redis distributed rate limiting
```python
# rate_limit.py — one-line change
storage_uri = settings.REDIS_URL if settings.REDIS_URL else "memory://"
limiter = Limiter(key_func=get_user_identifier, storage_uri=storage_uri)
```

Add `limits[redis]>=3.0.0` to requirements.txt. Fallback to memory if REDIS_URL not set.

#### Structured logging
When `LOG_FORMAT=json`: output one JSON line per log entry with `timestamp`, `level`, `event`, `request_id`, `user_id`, `task_id`. Add `structlog>=23.0.0`.

---

## Architectural Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Playwright memory (1 Chromium per worker) | Ephemeral context per fetch; keep browser singleton alive. On OOM: reduce CRAWL_CONCURRENCY. |
| BackgroundTasks for 200-page crawl (in-process) | Acceptable for single-host. Upgrade path: `arq` + Redis (transition functions are already queue-compatible — just extract them). |
| Gemini 429 rate limits | GeminiCache by content_hash avoids repeated calls; exponential backoff; on retry exhaustion → task FAILED with `gemini_rate_limit` error (user can retry). |
| Migration 007 enum ADD VALUE in transaction | Use `op.get_context().autocommit_block()` for those statements only; rest of migration runs normally. |
| Partial export on FAILED task | ExtractedRecord rows survive task failure; export endpoint explicitly supports FAILED tasks with `_partial: true` metadata. |
| CANCELED + partial unique index | Migration 006 (Phase 2 start) adds CANCELED to the exclusion list before any task can enter that state. |
| Multi-host APScheduler | Current: scheduler runs per-worker. Credit reset is safe (CAS). Watchdog may double-fire (idempotent — second try finds tasks already terminal). For multi-host production: use `APScheduler[redis]` distributed lock. |
