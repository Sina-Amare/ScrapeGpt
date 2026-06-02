# ScrapGPT — Canonical Implementation Reference

> **Purpose:** Single document for a new engineer to understand the entire system before reading individual files.
> **Last updated:** 2026-06-02
> **Verified against:** All source files in the repository.

---

## 1. Executive Summary

ScrapGPT is an async FastAPI backend for credit-gated, single-URL web scraping with an LLM post-processing stub. A user authenticates with JWT, calls `POST /scrape/start`, and the server immediately returns `202` while a background task fetches the URL with httpx, extracts text with BeautifulSoup, deducts one credit, and runs the content through an LLM layer (currently a stub). The user polls `GET /scrape/tasks/{id}` to check progress.

The project is at **MVP backend stage**. The scraping and pipeline are functional. The LLM integration is a stub. There is no frontend. There are nine documented bugs (see Section 11) that must be fixed before new features are added.

The long-term product vision is an interactive AI-assisted multi-page crawler: user enters a URL, Gemini analyzes DOM structure and suggests CSS selectors, user confirms the extraction spec, and the crawler extracts structured data across same-site pages with checkpointed recovery. That vision is entirely in the planning stage.

---

## 2. Current Status

### What works today

| Feature | File(s) | Notes |
|---------|---------|-------|
| FastAPI app bootstrap, CORS, rate-limit middleware | `app/main.py` | |
| JWT register / login / refresh | `app/api/v1/endpoints/auth.py` | |
| User model with daily credit system | `app/models/user.py` | |
| ScrapeTask state machine | `app/models/scrape_task.py` | |
| Admission gate (credits + one-active-task) | `app/services/admission.py` | |
| Background scrape pipeline | `app/services/task_executor.py` | |
| httpx + BeautifulSoup scraper | `app/services/scraper.py` | |
| Atomic credit deduction at LLM phase | `app/services/task_state.py` | |
| Daily credit reset (multi-instance safe) | `app/core/scheduler.py` | |
| Watchdog (stuck-task cleanup) | `app/services/watchdog.py` | Has NULL-skip bug |
| Health / readiness / liveness endpoints | `app/api/v1/endpoints/health.py` | |
| 4-migration Alembic schema | `alembic/versions/001–004` | Has enum drift |
| 13 tests for health and readiness | `tests/` | |

### What does not work

| Issue | Severity | Fix location |
|-------|----------|-------------|
| `POST /scrape/start` rate limiting broken (SlowAPI collision) | 🔴 Critical | `scrape.py:64` |
| `GET /tasks/current` shadowed by `{task_id}` — returns 422 | 🔴 Critical | `scrape.py:124–153` |
| Watchdog skips fresh tasks (NULL `updated_at`) | 🔴 Critical | `watchdog.py:44` |
| LLM integration is a stub (returns mock dict, sleeps 1s) | 🟡 Planned | `llm_processor.py` |
| Per-user rate limiting falls back to IP (never per-user) | 🟠 Notable | `rate_limit.py` |
| JWT `int()` cast crashes on malformed tokens → 500 | 🟠 Notable | `deps.py:88`, `auth.py:204` |
| Migration enum drift (old values still in DB type) | 🟠 Notable | `alembic/versions/002` |
| `SCRAPE_CREDIT_COST` config unused (hardcoded to 1) | 🟡 Minor | `task_state.py:174` |
| No frontend | 🟡 Planned | — |
| No test coverage beyond health/readiness | 🟡 Planned | — |

### What is incomplete

- `app/schemas/scrape.py` defines `ScrapeRequest`, `ScrapeResponse`, `ScrapeError` but nothing imports them — dead code.
- `requirements.txt` lists `requests` (unused) and `httpx` twice.
- Several deprecated methods remain in the codebase (`ensure_credits_reset`, `use_credit`, `require_credits`, `deduct_credit`) — none are called by live code.
- `SoftDeleteMixin`, `IDMixin`, `TableNameMixin` in `base.py` are defined but no model uses them.
- `get_optional_user` in `deps.py` is defined and exported but no endpoint uses it.
- `MAX_CONCURRENT_JOBS` and `LLM_TIMEOUT` are in config but never enforced by runtime paths.

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  HTTP Layer (FastAPI)                                            │
│  app/api/v1/endpoints/{health, auth, scrape}.py                  │
│  ─ Parse requests, validate with Pydantic, delegate to services  │
│  ─ Auth via Depends(get_current_user), DB via Depends(get_db)    │
└───────────────────────────┬──────────────────────────────────────┘
                            │  delegates
┌───────────────────────────▼──────────────────────────────────────┐
│  Service Layer                                                   │
│  app/services/{admission, task_state, task_executor,             │
│                scraper, llm_processor, readiness, watchdog}      │
│  ─ All business logic. Services own database transactions.       │
└───────────────────────────┬──────────────────────────────────────┘
                            │  reads/writes
┌───────────────────────────▼──────────────────────────────────────┐
│  Data Layer                                                      │
│  app/models/{user, scrape_task}.py + app/db/database.py          │
│  ─ SQLAlchemy 2.0 async ORM, asyncpg driver                      │
│  ─ Schema changes via alembic/versions/                          │
└──────────────────────────────────────────────────────────────────┘

Cross-cutting (app/core/):
  config.py    — Pydantic Settings singleton (env-driven)
  security.py  — bcrypt + JWT primitives
  rate_limit.py — SlowAPI limiter + key function
  scheduler.py  — APScheduler (credit reset + watchdog)

Background:
  FastAPI BackgroundTasks → execute_scrape_pipeline()
  (in-process; no external queue)
```

### Component responsibilities

| Component | Responsibility |
|-----------|---------------|
| `main.py` | App factory, CORS/rate-limit middleware, lifespan (scheduler start/stop) |
| `api/deps.py` | `get_db` generator, `get_current_user` JWT dependency |
| `endpoints/auth.py` | Register, login, refresh — no business logic |
| `endpoints/scrape.py` | Start task, get task, get current task — delegates to admission + executor |
| `endpoints/health.py` | `/health`, `/health/ready`, `/health/live` |
| `core/config.py` | Single typed `Settings` loaded from `.env` at startup |
| `core/security.py` | Password hashing (bcrypt), JWT create/verify |
| `core/scheduler.py` | APScheduler boot; two jobs: credit reset and watchdog |
| `db/database.py` | Async engine, session factory, `get_db` generator |
| `models/user.py` | Auth fields + credit fields |
| `models/scrape_task.py` | Task entity + `TaskState` enum + `VALID_TRANSITIONS` |
| `services/admission.py` | Gate: credit check + one-active-task check + task creation |
| `services/task_state.py` | All transition functions (each in its own transaction) |
| `services/task_executor.py` | Pipeline orchestrator with always-finalize guarantee |
| `services/scraper.py` | httpx fetch + BeautifulSoup text extraction |
| `services/llm_processor.py` | **Stub** — 1s sleep + mock dict |
| `services/readiness.py` | Bounded DB probe for `/health/ready` |
| `services/watchdog.py` | Periodic stuck-task cleanup (has NULL-skip bug) |

---

## 4. Runtime Flows

### 4.1 User registration and login

```
POST /api/v1/auth/register {email, password}
  → Check email not already taken (SELECT users WHERE email=...)
  → hash_password(password) [bcrypt, 12 rounds]
  → INSERT User(email, hashed_password, credits_remaining=5, daily_credit_limit=5)
  → create_access_token(user.id)  [15 min TTL, HS256]
  → create_refresh_token(user.id) [7 day TTL]
  → 201 { user: {...}, tokens: {access_token, refresh_token} }

POST /api/v1/auth/login [OAuth2 form: username=email, password]
  → SELECT User WHERE email=form.username
  → verify_password(form.password, user.hashed_password)
  → Check user.is_active
  → Return { access_token, refresh_token }

POST /api/v1/auth/refresh { refresh_token }
  → verify_token(token, type="refresh")
  → int(payload.sub)  ← ⚠️ Bug: ValueError if malformed → 500
  → SELECT User WHERE id=user_id
  → Return new { access_token, refresh_token }
```

### 4.2 Scrape request flow

```
POST /api/v1/scrape/start  Authorization: Bearer <access_token>
  Body: { "url": "https://example.com" }

1. get_current_user(token)
   → verify_token → int(payload.sub) → SELECT User
   → Check user.is_active

2. admit_scrape_task(user, url, db)
   → If user.credits_remaining <= 0: return 402
   → INSERT ScrapeTask(state=PERMISSION_GRANTED, url=url, user_id=user.id)
   → If partial-unique-index violation: return 409
   → Commit + return AdmissionSuccess(task)

3. background_tasks.add_task(execute_scrape_pipeline, task_id, user_id)

4. Return 202 { task_id, state="PERMISSION_GRANTED", url, message }
   (HTTP request ends here; pipeline runs in background)

BACKGROUND: execute_scrape_pipeline(task_id, user_id)

5. transition_to_scraping(task_id, db)
   [transaction] task.state = SCRAPING

6. scrape_url(url)
   → httpx.AsyncClient GET with SCRAPE_TIMEOUT (default 30s)
   → BeautifulSoup lxml parse
   → Remove script/style/nav/footer/header/aside
   → Extract text (max 50,000 chars)
   → Return "Title: {title}\n\n{text}"
   ↳ On failure → ScrapeError → transition_to_failed → end

7. transition_to_scraped(task_id, content, db)
   [transaction] task.state = SCRAPED, task.content = content

8. transition_to_llm_processing(task_id, user_id, db)
   [transaction]
   → Check not terminal
   → Verify task.user_id == user_id (ownership)
   → UPDATE users SET credits_remaining = credits_remaining - 1
     WHERE id = user_id AND credits_remaining > 0
   → If rowcount==0: task.state=FAILED, return
   → task.state = LLM_PROCESSING
   (credit deduction + state change in same transaction)

9. process_with_llm(content)       ← STUB: sleeps 1s, returns mock dict
   ↳ On LLMError → transition_to_failed → end

10. transition_to_completed(task_id, result, db)
    [transaction] task.state = COMPLETED, task.result = {summary, word_count, analysis}
```

### 4.3 Task polling flow

```
GET /api/v1/scrape/tasks/{task_id}  Authorization: Bearer <token>
  → get_current_user
  → db.get(ScrapeTask, task_id)
  → Check task.user_id == user.id (user can only see own tasks)
  → Return { task_id, state, url, error, result }

GET /api/v1/scrape/tasks/current    ← ⚠️ Bug: this route is SHADOWED by {task_id}
  → Currently receives 422 because "current" can't parse as int
  → When fixed: returns the user's non-terminal task or 404
```

### 4.4 Error flow

```
Any exception in execute_scrape_pipeline:
  → Caught by inner try/except per phase
  → transition_to_failed(task_id, error_message, db)
    [transaction]
    → Check not already terminal (prevent double-fail)
    → task.state = FAILED, task.error = error_message

Outer catch-all (unexpected exceptions):
  → Opens NEW session (original may be broken)
  → transition_to_failed(task_id, "Unexpected error: ...", db)
  → If this also fails: logs "pipeline.failed_to_mark_failed"
    (task may remain stuck; watchdog will eventually clean it)
```

### 4.5 Scheduled jobs

```
Startup:
  start_scheduler()
    → configure_scheduler() (adds jobs to APScheduler)
    → scheduler.start()
    → asyncio.create_task(try_reset_all_credits())  [runs on first startup]

Job 1: Credit reset — CronTrigger(hour=0, minute=0, UTC), misfire_grace_time=3600s
  try_reset_all_credits():
    [transaction]
    UPDATE system_state SET value=today WHERE key='last_credit_reset' AND value!=today
    → rowcount==0: already done today, return
    → rowcount==1: this instance wins
      UPDATE users SET credits_remaining=daily_credit_limit, credits_reset_at=NOW()

Job 2: Watchdog — IntervalTrigger(seconds=60)
  cleanup_stuck_tasks():
    → SELECT tasks WHERE state=PERMISSION_GRANTED AND updated_at < (now - 3min)
    → SELECT tasks WHERE state=SCRAPING        AND updated_at < (now - 5min)
    → SELECT tasks WHERE state=LLM_PROCESSING  AND updated_at < (now - 10min)
    ← ⚠️ Bug: ScrapeTask.updated_at has no INSERT default — only onupdate=func.now().
               So a task in PERMISSION_GRANTED (pipeline never started) has updated_at=NULL.
               NULL < cutoff evaluates to NULL (falsy) in PostgreSQL, so the watchdog
               never catches it. It will stay stuck indefinitely — the watchdog cannot
               clean it up until a state transition sets updated_at.
    → For each: task.state=FAILED, task.error="Watchdog: stuck in X for >Ym"
    → db.commit() if any cleaned
```

---

## 5. Important Invariants

### 5.1 One non-terminal task per user

**What:** At any moment, `COUNT(tasks WHERE user_id=X AND state NOT IN (COMPLETED, FAILED)) <= 1`

**Why:** Prevents resource abuse, avoids race conditions in per-user credit accounting, simplifies client UX.

**Where enforced (twice):**
1. Application-level check in `admission.py`: admission returns `ALREADY_HAS_ACTIVE_TASK` before INSERT
2. Database-level partial unique index (migration 003):
   ```sql
   CREATE UNIQUE INDEX ix_one_active_task_per_user
   ON scrape_tasks (user_id)
   WHERE state NOT IN ('COMPLETED', 'FAILED')
   ```
   This is the safety net — even if the app-level check races, the DB rejects the second INSERT with `IntegrityError`.

**What breaks if violated:** Two concurrent tasks per user would both attempt credit deduction; the `UPDATE WHERE credits > 0` would prevent double-charge, but the second task would fail in `LLM_PROCESSING` after resources were already consumed for scraping.

**Evolution note:** If you add a new terminal state (e.g., `CANCELED`), you must add it to the index condition: `WHERE state NOT IN ('COMPLETED', 'FAILED', 'CANCELED')`.

### 5.2 Credits deducted exactly once at LLM phase

**What:** `credits_remaining` is decremented atomically in `transition_to_llm_processing`, nowhere else.

**Why:** Scraping can fail for reasons outside user control (target down, timeout, bot-block). Charging at admission would penalize users for those failures. Charging at LLM phase means: scrape succeeded, content exists, expensive resource (LLM) is about to be used.

**Where enforced:** `task_state.py:transition_to_llm_processing` — raw SQL `UPDATE WHERE credits > 0` inside the same `db.begin()` transaction as the state change. Either both commit or neither does.

**What breaks if violated:** Moving deduction to admission means every scrape failure costs a credit. Moving to completion means users could spam partial scrapes for free.

**Current gap:** The transition uses hardcoded `-1` instead of `settings.SCRAPE_CREDIT_COST`. If the config value is changed, it won't take effect until this is fixed.

### 5.3 Always-finalize

**What:** Every task eventually reaches `COMPLETED` or `FAILED`. No task is ever left in a non-terminal state permanently.

**Why:** Tasks in intermediate states block the user from starting new ones (invariant 5.1). A stuck task with no cleanup path would permanently lock the user.

**Where enforced:**
1. Each pipeline phase has its own try/except → `transition_to_failed`
2. Outer catch-all in `task_executor.py` opens a fresh session and marks FAILED
3. Watchdog periodically force-fails tasks stuck past configurable timeouts

**Current gap:** The watchdog NULL-skip bug means freshly-created tasks that get stuck immediately won't be cleaned up until `updated_at` is populated (which only happens on the first UPDATE, i.e., when a transition occurs — but if the background task never starts, no transition occurs, so `updated_at` stays NULL forever, and the watchdog misses it).

### 5.4 Multi-instance-safe credit reset

**What:** Exactly one instance resets credits per day regardless of how many workers are running.

**Why:** With `uvicorn --workers N` or multiple hosts, every worker runs its own scheduler. Without coordination, all would reset credits simultaneously — harmless but wasteful and potentially racy.

**Where enforced:** Compare-and-swap on `system_state` table key `last_credit_reset`. The UPDATE condition `WHERE value != today` means only the first instance to run it gets `rowcount=1` and proceeds. Others see `rowcount=0` and skip.

**What breaks if violated:** Multiple resets per day would give users extra credits; they would also interfere if credits were decremented during the reset window.

---

## 6. Directory Walkthrough

```
scrapegpt/
│
├── app/
│   ├── main.py              App factory, lifespan, middleware wiring.
│   │                        create_app() returns the FastAPI instance.
│   │                        Lifespan starts/stops scheduler and closes DB pool.
│   │
│   ├── api/
│   │   ├── deps.py          FastAPI dependencies: get_db (session generator),
│   │   │                    get_current_user (JWT decode + User lookup).
│   │   │                    Also contains deprecated require_credits and deduct_credit
│   │   │                    — both marked deprecated, nothing calls them.
│   │   │
│   │   └── v1/
│   │       ├── router.py    Mounts health/auth/scrape routers under api_v1_router.
│   │       └── endpoints/
│   │           ├── health.py  /health (trivial), /health/ready (DB probe), /health/live
│   │           ├── auth.py    /auth/register, /auth/login, /auth/refresh
│   │           └── scrape.py  /scrape/start, /scrape/tasks/{id}, /scrape/tasks/current
│   │                          ⚠️ Two active bugs: rate-limit collision + route shadowing
│   │
│   ├── core/
│   │   ├── config.py        Pydantic BaseSettings. All config from .env.
│   │   │                    Single `settings` singleton via @lru_cache.
│   │   │                    No raw os.environ anywhere in the app.
│   │   │
│   │   ├── security.py      hash_password, verify_password (bcrypt),
│   │   │                    create_access_token, create_refresh_token,
│   │   │                    verify_token (returns TokenPayload | None),
│   │   │                    decode_token (UNVERIFIED — debug only, never use in prod).
│   │   │
│   │   ├── rate_limit.py    SlowAPI limiter with get_user_identifier key function.
│   │   │                    ⚠️ Key function tries request.state.user (never set)
│   │   │                    → always falls back to IP. Per-user limiting is broken.
│   │   │
│   │   └── scheduler.py     APScheduler instance + configure_scheduler() + start/stop.
│   │                        Two jobs: try_reset_all_credits (cron 00:00 UTC) +
│   │                        run_watchdog_once (interval 60s).
│   │
│   ├── db/
│   │   └── database.py      Async SQLAlchemy engine + async_session_factory.
│   │                        get_db() async generator for FastAPI DI.
│   │                        NullPool commented out (available for tests).
│   │
│   ├── models/
│   │   ├── base.py          Declarative Base + TimestampMixin (created_at/updated_at).
│   │   │                    Also defines SoftDeleteMixin, IDMixin, TableNameMixin
│   │   │                    — none of these are currently used by any model.
│   │   │
│   │   ├── user.py          User table: id, email, hashed_password, is_active,
│   │   │                    is_verified, credits_remaining, daily_credit_limit,
│   │   │                    credits_reset_at. Inherits TimestampMixin.
│   │   │                    Contains deprecated use_credit(), ensure_credits_reset(),
│   │   │                    has_credits — none called by live pipeline.
│   │   │
│   │   └── scrape_task.py   ScrapeTask table + TaskState enum + VALID_TRANSITIONS.
│   │                        ⚠️ updated_at: Mapped[datetime | None] with only
│   │                        onupdate=func.now() — NO insert default. This is the
│   │                        root cause of the watchdog NULL-skip bug.
│   │
│   ├── schemas/
│   │   ├── auth.py          Auth DTOs: UserRegisterRequest, UserLoginRequest,
│   │   │                    TokenRefreshRequest, TokenResponse, UserResponse, AuthResponse.
│   │   │
│   │   └── scrape.py        ScrapeRequest, ScrapeResponse, ScrapeError.
│   │                        ⚠️ DEAD CODE — nothing imports this file.
│   │                        The scrape endpoint defines its own inline schemas.
│   │
│   └── services/
│       ├── admission.py     admit_scrape_task() — the only entry point for task creation.
│       │                    Returns AdmissionSuccess | AdmissionError (never raises).
│       │
│       ├── task_state.py    Five transition functions (each opens db.begin()).
│       │                    transition_to_llm_processing is the only credit deduction point.
│       │
│       ├── task_executor.py execute_scrape_pipeline() — the background task entry point.
│       │                    Always-finalize: outer catch-all opens a fresh session.
│       │
│       ├── scraper.py       scrape_url(url): httpx GET → BS4 parse → text extraction.
│       │                    Raises ScrapeError on any failure.
│       │
│       ├── llm_processor.py process_with_llm(content): STUB.
│       │                    Sleeps 1 second, returns hardcoded dict.
│       │                    ⚠️ LLM_TIMEOUT local constant (120s) is NOT enforced —
│       │                    there is no asyncio.timeout() wrapping the stub.
│       │
│       ├── readiness.py     check_db_ready(db, timeout): runs 5 SQL probes wrapped
│       │                    in asyncio.wait_for(). Returns DBReadinessResult with
│       │                    typed ReadinessCode, never raw exception text.
│       │
│       └── watchdog.py      cleanup_stuck_tasks(): three SELECT queries per state.
│                            ⚠️ Filters on updated_at < cutoff; NULL < cutoff = NULL
│                            (falsy in SQL), so fresh tasks with NULL updated_at are skipped.
│
├── alembic/
│   ├── env.py               Standard Alembic async env setup.
│   └── versions/
│       ├── 001_create_users.py         users table, indexes
│       ├── 002_create_scrape_tasks.py  scrape_tasks with OLD enum values:
│       │                               PERMISSION_GRANTED, SCRAPED, LLM_ANALYZED,
│       │                               OUTPUT_GENERATION, FINALIZED
│       │                               ⚠️ These old values still exist in the DB type
│       ├── 003_update_task_states.py   Adds SCRAPING, LLM_PROCESSING, COMPLETED, FAILED.
│       │                               Drops ix_one_active_task_per_user (WHERE state!='FINALIZED')
│       │                               and recreates it (WHERE state NOT IN ('COMPLETED','FAILED'))
│       └── 004_system_state.py         system_state table with last_credit_reset='1970-01-01'
│
├── tests/
│   ├── conftest.py          async_client fixture (requires per-test app fixture from the test file)
│   ├── api/v1/test_health_readiness.py  5 tests: 200/503 responses, sanitized output
│   └── services/test_readiness.py       8 tests: probe logic, timeout, error mapping
│
├── docs/                    See Section 12 for reading order
├── .agent/rules/            documenting.md: mandatory learning docs after each task
│                            task-review.md, workflow.md: process rules
├── requirements.txt         ⚠️ httpx listed twice; requests listed but unused
├── .env                     Local config (not in git)
└── .env.example             Template for .env
```

---

## 7. Data Model Walkthrough

### `users` table

```
id               INTEGER  PK, autoincrement
email            VARCHAR(255)  UNIQUE, NOT NULL, indexed
hashed_password  VARCHAR(255)  NOT NULL  (bcrypt $2b$ hash)
is_active        BOOLEAN  NOT NULL  DEFAULT true   (soft-disable)
is_verified      BOOLEAN  NOT NULL  DEFAULT false  (not enforced — gate exists but not used)
credits_remaining INTEGER NOT NULL  DEFAULT 5       (current balance)
daily_credit_limit INTEGER NOT NULL DEFAULT 5       (per-user ceiling for reset)
credits_reset_at TIMESTAMPTZ NOT NULL DEFAULT NOW() (last reset, informational)
created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()  (via TimestampMixin)
```

**Key design:** `credits_remaining` and `daily_credit_limit` are separate to allow per-user premium tiers in future (admin sets `daily_credit_limit=100` for a premium user; reset brings `credits_remaining` back to that limit).

### `scrape_tasks` table

```
id          INTEGER  PK, autoincrement
user_id     INTEGER  FK → users.id ON DELETE CASCADE
state       task_state enum  NOT NULL  DEFAULT 'PERMISSION_GRANTED'
url         VARCHAR(2048)  NOT NULL
content     TEXT  NULL     (populated at SCRAPED transition)
error       TEXT  NULL     (populated on FAILED)
result      JSONB NULL     (populated at COMPLETED — LLM output)
created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
updated_at  TIMESTAMPTZ NULL  (NO insert default — only set by onupdate trigger)
            ⚠️ Bug: users.updated_at has server_default=NOW() (via TimestampMixin);
               ScrapeTask.updated_at does not. Fresh tasks have updated_at=NULL,
               which is the root cause of the watchdog NULL-skip bug.
```

**Key index:** `ix_one_active_task_per_user` — partial unique on `(user_id) WHERE state NOT IN ('COMPLETED', 'FAILED')`. This is the concurrency safety net.

**Enum values in DB** (after migration 003):
- Active values: `PERMISSION_GRANTED`, `SCRAPING`, `SCRAPED`, `LLM_PROCESSING`, `COMPLETED`, `FAILED`
- Stale values (still exist in PostgreSQL type, never used by model): `LLM_ANALYZED`, `OUTPUT_GENERATION`, `FINALIZED`

### `system_state` table

```
key        VARCHAR(50)  PK
value      TEXT  NOT NULL
updated_at TIMESTAMPTZ  DEFAULT NOW()
```

Only one row currently used: `key='last_credit_reset'`, `value='YYYY-MM-DD'` (ISO date). The compare-and-swap logic in `scheduler.py` uses this for multi-instance credit reset coordination.

### `alembic_version` table (managed by Alembic)

```
version_num  VARCHAR(32)  PK
```

The readiness probe checks this table to confirm migrations are applied.

### Relationships

- `User` → `ScrapeTask`: one-to-many. `ScrapeTask.user_id` FK with `ON DELETE CASCADE`. The SQLAlchemy relationship is `user.scrape_tasks` (all tasks) and `task.user` (owner).

---

## 8. External Integrations

### PostgreSQL

**Required for:** All data persistence, partial unique index (PostgreSQL-specific), JSONB column on `scrape_tasks.result`, native enum type `task_state`.

**Connection:** `asyncpg` driver via SQLAlchemy 2.0 async. Connection string from `DATABASE_URL` env var. Pool: 5 persistent + up to 10 overflow. `pool_pre_ping=True` refreshes stale connections.

**Failure behavior:** App starts even if PostgreSQL is down. The `/health/ready` endpoint returns 503 with `database: "db_unreachable"`. Authenticated API calls that need DB will return 500 if the pool is exhausted or the connection is broken.

### httpx (scraping)

**Used by:** `scraper.py:scrape_url()`

**Behavior:**
- `follow_redirects=True`
- Timeout: `settings.SCRAPE_TIMEOUT` (default 30s)
- User-agent: `settings.USER_AGENT` (default `"ScrapGPT/1.0"`)
- No proxy, no cookie jar, no session persistence
- Raises `ScrapeError` on timeout, HTTP errors, or network failures

**Failure behavior:** `ScrapeError` propagates to `task_executor.py` which transitions the task to `FAILED`. No retries. No credit charged.

### SlowAPI (rate limiting)

**Used by:** `main.py` (middleware) + `scrape.py` (`@limiter.limit`)

**Storage:** In-memory (`memory://`). Rate limits reset on app restart. Not shared across workers.

**Current state:** Broken for `POST /scrape/start` (parameter collision). Per-user keying is broken (falls back to IP).

### APScheduler

**Version:** 3.10.0 (AsyncIOScheduler)

**Jobs:**
1. `try_reset_all_credits` — CronTrigger 00:00 UTC, misfire grace 3600s
2. `run_watchdog_once` — IntervalTrigger every 60s

**Important note:** The scheduler runs in-process, one instance per worker. With multiple workers or hosts, each runs its own scheduler. The credit reset is safe (DB CAS). The watchdog is not coordinated across instances — multiple instances may try to fail the same task simultaneously (both would succeed due to idempotent FAILED transition).

### Google Gemini (planned, not implemented)

Not integrated yet. `llm_processor.py` is a stub. The roadmap calls for `google-genai` SDK, free-tier Google AI Studio key, structured Pydantic-validated output.

---

## 9. Testing Guide

### How to run

```powershell
# Activate venv
.\venv\Scripts\activate

# All tests (13 pass)
pytest -v

# Specific file
pytest tests/api/v1/test_health_readiness.py -v

# Specific test
pytest tests/services/test_readiness.py::test_check_db_ready_healthy_returns_ok -v
```

**Important:** Tests require `DEBUG=true` to avoid Pydantic validation failures on `ENVIRONMENT` field. The project-specific run command is:
```
cmd.exe /c "set DEBUG=true&& venv\Scripts\python.exe -m pytest -q"
```

### What is tested

| Test file | What it covers |
|-----------|---------------|
| `tests/services/test_readiness.py` | 8 unit tests: probe logic, all error codes, timeout bounding, response sanitization. Uses `FakeSession` — no real DB needed. |
| `tests/api/v1/test_health_readiness.py` | 5 integration tests: HTTP status codes (200/503), response structure, sanitized output. Uses FastAPI test client with mocked `check_db_ready`. |

### Test architecture

`conftest.py` provides `async_client(app)` — each test file must provide its own `app` fixture. The health tests override `get_db` with a `DummySession()` that accepts calls but has no logic. Tests that need real DB operations would need a different test DB setup.

### What is not tested (everything else)

- Auth endpoints (register, login, refresh)
- Admission service (credit gate, one-active-task, IntegrityError path)
- State transitions (including atomic credit deduction)
- Pipeline execution (happy path, scrape failure, LLM failure)
- Watchdog (stuck task detection, NULL `updated_at` bug)
- Rate limiting
- Scheduler (credit reset, CAS logic)
- The nine known bugs listed in STATUS.md

The ROADMAP.md has a detailed test plan (12 categories) for what to build next.

---

## 10. Local Development Guide

### Prerequisites

- Python 3.11+
- PostgreSQL 14+ running locally
- Git

### Setup

```powershell
# Clone and enter project
cd "scrapegpt"

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Configure

```powershell
# Copy example config
copy .env.example .env

# Edit .env — minimum required values:
# DATABASE_URL=postgresql+asyncpg://postgres:yourpassword@localhost:5432/scrapegpt
# SECRET_KEY=<32+ char random string>  # generate: openssl rand -hex 32
# DEBUG=true  # enables /docs, /redoc, /openapi.json
```

### Database setup

```powershell
# Create the database (psql or pgAdmin)
psql -U postgres -c "CREATE DATABASE scrapegpt;"

# Apply all migrations
alembic upgrade head

# Verify
psql -U postgres -d scrapegpt -c "\dt"
# Should see: alembic_version, scrape_tasks, system_state, users
```

### Run the server

```powershell
uvicorn app.main:app --reload
# Server: http://127.0.0.1:8000
# Docs (requires DEBUG=true): http://127.0.0.1:8000/docs
```

### Verify it works

```
GET http://127.0.0.1:8000/api/v1/health/live
→ { "alive": true }

GET http://127.0.0.1:8000/api/v1/health/ready
→ { "status": "ready", "database": "ok", ... }
```

### Common issues

| Problem | Likely cause | Fix |
|---------|-------------|-----|
| `pydantic_settings.EnvSettingsError` | `ENVIRONMENT` not set or set to something other than `development/staging/production` | Set `ENVIRONMENT=development` in .env |
| `asyncpg.InvalidCatalogNameError` | Database doesn't exist | Run `CREATE DATABASE scrapegpt` |
| `alembic.util.exc.CommandError: Can't locate revision` | Migrations out of sync | `alembic downgrade base && alembic upgrade head` |
| `422 Unprocessable Entity` on `POST /scrape/start` | Body field named `url` but url format invalid | Use full URL with scheme: `https://example.com` |
| Rate limit decorator crash on `/scrape/start` | SlowAPI collision bug | Phase 0 fix required before using this endpoint |

---

## 11. Known Limitations

### Active bugs (Phase 0)

These make certain features completely non-functional:

1. **SlowAPI parameter collision** — `POST /scrape/start` will crash when rate limiting fires.
   - Root cause: function signature has `request: StartScrapeRequest` (Pydantic model) + `fastapi_request: Request = None` (Starlette Request). SlowAPI finds `request` but it's the wrong type.
   - Fix: Rename body param to `payload: StartScrapeRequest`, rename `fastapi_request` to `request: Request`.
   - File: [app/api/v1/endpoints/scrape.py:64](app/api/v1/endpoints/scrape.py)

2. **Route shadowing** — `GET /scrape/tasks/current` always returns 422.
   - Root cause: `/tasks/{task_id}` is registered at line 124, `/tasks/current` at line 153. FastAPI matches "current" as `task_id`, then fails `int("current")`.
   - Fix: Move the `/tasks/current` route registration above `/tasks/{task_id}`.
   - File: [app/api/v1/endpoints/scrape.py:124–153](app/api/v1/endpoints/scrape.py)

3. **Watchdog NULL-skip** — Fresh stuck tasks are never cleaned up.
   - Root cause: `ScrapeTask.updated_at` is nullable with no INSERT default. `NULL < cutoff` is SQL NULL (falsy). Newly-created tasks that get stuck before their first transition (e.g., the background task never starts) have `updated_at=NULL` and are invisible to the watchdog.
   - Fix: Add `COALESCE(updated_at, created_at)` in watchdog queries, or add a non-null insert default to `updated_at`.
   - File: [app/services/watchdog.py:44](app/services/watchdog.py)

### Notable bugs (fixable without schema changes)

4. **JWT int() cast → 500**: `int(payload.sub)` in `deps.py:88` and `auth.py:204` raises `ValueError` for malformed tokens. Fix: wrap in try/except → 401.

5. **Per-user rate limiting never works**: `rate_limit.py` key function checks `request.state.user` but nothing sets it → always IP-based. Fix: set `request.state.user` in `get_current_user` dependency, or pass user ID in a different way.

6. **Auth endpoints unprotected by rate limiting**: `AUTH_RATE_LIMIT` constant exists but is never applied. Fix: add `@limiter.limit(AUTH_RATE_LIMIT)` to register/login/refresh.

7. **`/tasks/current` response model mismatch**: Returns `TaskResponse | None` but always raises 404 on no task. Fix: pick one behavior and align schema + implementation.

### Technical debt

8. **Migration enum drift**: Old enum values (`LLM_ANALYZED`, `OUTPUT_GENERATION`, `FINALIZED`) still exist in the PostgreSQL type. Not immediately harmful but causes drift warnings and complicates future enum migrations. Fix: squash migrations since there's no production data.

9. **`SCRAPE_CREDIT_COST` config is ignored**: `task_state.py:174` hardcodes `credits_remaining - 1` instead of using `settings.SCRAPE_CREDIT_COST`. Fix: replace `- 1` with `- settings.SCRAPE_CREDIT_COST`.

10. **`requests` library in requirements.txt**: Imported at line 77 but `httpx` is what the code actually uses. `requests` is synchronous and unused. Also `httpx` is listed twice (lines 77 and 89).

11. **Dead code**: `app/schemas/scrape.py` (never imported), `use_credit()` and `ensure_credits_reset()` in `User`, `require_credits` and `deduct_credit` in `deps.py`, `SoftDeleteMixin`/`IDMixin`/`TableNameMixin` in `base.py`, `get_optional_user` in `deps.py`.

### Architectural constraints

- **No external job queue**: BackgroundTasks are in-process. A server restart while a task is in `SCRAPING` or `LLM_PROCESSING` will leave it stuck (watchdog eventually cleans it). For production, migrate to Celery, RQ, or Arq.
- **In-process scheduler**: APScheduler runs per-worker. On multi-host deployments, run the scheduler in a dedicated worker.
- **No retry logic**: A single network failure or timeout fails the task immediately.
- **No webhook/SSE**: Client must poll. Progress notifications require adding SSE or webhook support.
- **No browser rendering**: JavaScript-heavy sites will return incomplete content. Playwright Chromium is planned.
- **No URL validation or SSRF prevention**: Any HTTP/HTTPS URL is accepted and fetched, including localhost and internal network addresses. This is a security risk if the app is ever publicly hosted.

---

## 12. Recommended Next Learning Path

For a new engineer who just read this document, the recommended order:

### Step 1 — Understand the state machine (30 min)

Read: `app/models/scrape_task.py` — the `TaskState` enum, `VALID_TRANSITIONS`, `TERMINAL_STATES`, `can_transition_to()`.

This is the foundation. Everything else in the pipeline makes sense once you understand that:
- Every task progresses through exactly these states
- Transitions are validated both in Python and in the DB
- Terminal states release the one-active-task lock

### Step 2 — Understand admission and credits (30 min)

Read: `app/services/admission.py`, `app/services/task_state.py:transition_to_llm_processing`

Key question to answer: "Why aren't credits deducted at admission?" Answer in `docs/learning/02_admission_and_credits.md`.

### Step 3 — Trace a request end-to-end (45 min)

Read: `app/api/v1/endpoints/scrape.py:start_scrape` → `app/services/task_executor.py:execute_scrape_pipeline` → `app/services/scraper.py` → `app/services/llm_processor.py` (stub).

Set `DEBUG=true`, start the server, register a user via `/docs`, start a scrape, poll for completion.

### Step 4 — Understand the safety mechanisms (30 min)

Read: `app/services/watchdog.py`, `app/core/scheduler.py:try_reset_all_credits`

Why does the watchdog filter on `updated_at`? Why is the credit reset a compare-and-swap? What would break without these?

### Step 5 — Read the bug list carefully (20 min)

Read: `docs/STATUS.md` (authoritative bug list), then verify each bug against the code directly.

The most educational one is the route-shadowing bug — it demonstrates how FastAPI route registration order matters.

### Step 6 — Plan Phase 0 fixes (ongoing)

Attempt fixing the nine bugs in `docs/STATUS.md` order. Each fix should come with a regression test (none currently exist).

### Files to read last

- `docs/plan/ROADMAP.md` — the full product vision (Phases 1–6). Understand Phase 0 first.
- `docs/architecture.md` — well-written architectural overview, mostly accurate.
- `docs/learning/01–04` — decision logs explaining *why* things are built as they are.

---

## Appendix A — API Reference (Current State)

### Auth endpoints (no rate limiting applied today)

```
POST /api/v1/auth/register
  Body: { "email": "...", "password": "..." }
  → 201: { user: {...}, tokens: { access_token, refresh_token, token_type } }
  → 400: email already registered

POST /api/v1/auth/login
  Form: username=<email>&password=<password>  (OAuth2 format)
  → 200: { access_token, refresh_token, token_type }
  → 401: invalid credentials
  → 403: account deactivated

POST /api/v1/auth/refresh
  Body: { "refresh_token": "..." }
  → 200: { access_token, refresh_token, token_type }
  → 401: invalid/expired token
```

### Scrape endpoints (all require Authorization: Bearer <access_token>)

```
POST /api/v1/scrape/start
  Body: { "url": "https://..." }
  → 202: { task_id, state, url, message }
  → 402: no credits
  → 409: already has active task
  ⚠️ Rate limiting currently broken (SlowAPI collision)

GET /api/v1/scrape/tasks/{task_id}
  → 200: { task_id, state, url, error, result }
  → 404: task not found or belongs to another user

GET /api/v1/scrape/tasks/current
  ⚠️ BROKEN — returns 422 (shadowed by /{task_id})
  When fixed: 200 with active task, or 404 if none
```

### Health endpoints (no auth required)

```
GET /api/v1/health/live
  → 200: { "alive": true }

GET /api/v1/health/ready
  → 200: { status: "ready", environment, version, database: "ok" }
  → 503: { status: "not_ready", ..., database: <reason_code> }
  reason_codes: db_unreachable | schema_incompatible | query_failed | timeout

GET /api/v1/health
  → 200: { status: "healthy", environment, version }
```

### OpenAPI docs

Only available when `DEBUG=true`:
- `/docs` (Swagger UI)
- `/redoc`
- `/openapi.json`

---

## Appendix B — Environment Variables Reference

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:password@localhost:5432/scrapegpt` | **Yes** | PostgreSQL connection string |
| `SECRET_KEY` | *(insecure default)* | **Yes** | JWT signing key (min 32 chars) |
| `ENVIRONMENT` | `development` | No | `development` / `staging` / `production` |
| `DEBUG` | `false` | No | Enables /docs, /redoc; SQL logging |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | No | Access token TTL |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | No | Refresh token TTL |
| `DEFAULT_DAILY_CREDITS` | `5` | No | Starting credits for new users |
| `SCRAPE_CREDIT_COST` | `1` | No | ⚠️ Declared but not enforced (hardcoded to 1) |
| `SCRAPE_TIMEOUT` | `30` | No | HTTP fetch timeout in seconds |
| `LLM_TIMEOUT` | `120` | No | ⚠️ Declared but not enforced by stub |
| `MAX_CONCURRENT_JOBS` | `5` | No | ⚠️ Declared but not enforced anywhere |
| `READINESS_TIMEOUT_SECONDS` | `2.0` | No | Max time for readiness probe |
| `WATCHDOG_PERMISSION_GRANTED_TIMEOUT_MINUTES` | `3` | No | Stuck task timeout for PERMISSION_GRANTED |
| `WATCHDOG_SCRAPING_TIMEOUT_MINUTES` | `5` | No | Stuck task timeout for SCRAPING |
| `WATCHDOG_LLM_TIMEOUT_MINUTES` | `10` | No | Stuck task timeout for LLM_PROCESSING |
| `RATE_LIMIT_PER_MINUTE` | `60` | No | Default rate limit |
| `RATE_LIMIT_SCRAPE_PER_MINUTE` | `10` | No | Scrape endpoint rate limit (currently broken) |
| `RATE_LIMIT_AUTH_PER_MINUTE` | `5` | No | Auth rate limit (declared, not applied) |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:8000` | No | Comma-separated allowed origins |
| `PASSWORD_HASH_ROUNDS` | `12` | No | bcrypt cost factor |
