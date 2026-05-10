# ScrapGPT

An async FastAPI backend for authenticated, credit-gated URL scraping with an LLM post-processing stage.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)
[![Status](https://img.shields.io/badge/status-MVP%20%2F%20WIP-orange.svg)](docs/STATUS.md)

> **Status:** MVP architecture is in place and end-to-end runnable. Several known wiring bugs and unfinished pieces remain — see [docs/STATUS.md](docs/STATUS.md) for the current punch list and where to continue from.

## What it does

1. User registers / logs in (JWT).
2. User submits a URL via `POST /api/v1/scrape/start`.
3. Server admits the task (one active task per user, credit ≥ 1) and returns `202 Accepted` with a `task_id`.
4. A background pipeline drives the task through a state machine: `PERMISSION_GRANTED → SCRAPING → SCRAPED → LLM_PROCESSING → COMPLETED` (or `FAILED` from any non-terminal state).
5. User polls `GET /api/v1/scrape/tasks/{task_id}` for status and final result.

Credits reset daily at 00:00 UTC via an APScheduler job that uses a `system_state` row as a check-and-set lock (multi-instance safe). A second scheduled job (the watchdog) fails tasks stuck in non-terminal states past configurable timeouts.

## Tech stack

| Concern         | Library                                             |
| --------------- | --------------------------------------------------- |
| Web framework   | FastAPI 0.115 (async)                               |
| ASGI server     | Uvicorn (Gunicorn for prod)                         |
| ORM             | SQLAlchemy 2.0 async + asyncpg                      |
| Migrations      | Alembic                                             |
| Validation      | Pydantic 2 + pydantic-settings                      |
| Auth            | python-jose (JWT) + passlib/bcrypt                  |
| Scraping        | httpx + BeautifulSoup4 + lxml                       |
| Background jobs | APScheduler (in-process)                            |
| Rate limiting   | SlowAPI                                             |
| Tests           | pytest + pytest-asyncio (skeleton only — see STATUS) |

PostgreSQL 14+ is required (uses JSONB and partial unique indexes).

## Quick start

```bash
# 1. Clone and create venv
git clone <your-fork-url> scrapegpt
cd scrapegpt
python -m venv venv
.\venv\Scripts\activate         # Windows
# source venv/bin/activate      # Linux/Mac

# 2. Install
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env: at minimum set DATABASE_URL and a real SECRET_KEY
#   openssl rand -hex 32

# 4. Migrate
createdb scrapegpt
alembic upgrade head

# 5. Run
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for Swagger UI.

## API surface

All routes are under `/api/v1`.

### Health

| Method | Path             | Auth | Description                                    |
| ------ | ---------------- | ---- | ---------------------------------------------- |
| GET    | `/health`        | no   | Basic status (env, version)                    |
| GET    | `/health/ready`  | no   | Readiness probe with DB + schema sanity check  |
| GET    | `/health/live`   | no   | Minimal liveness probe                         |

### Auth

| Method | Path             | Auth | Description                                |
| ------ | ---------------- | ---- | ------------------------------------------ |
| POST   | `/auth/register` | no   | Create account; returns user + token pair  |
| POST   | `/auth/login`    | no   | OAuth2 form login; returns token pair      |
| POST   | `/auth/refresh`  | no   | Exchange refresh token for new access token |

### Scraping

| Method | Path                       | Auth | Description                                          |
| ------ | -------------------------- | ---- | ---------------------------------------------------- |
| POST   | `/scrape/start`            | yes  | Admit + queue a scrape task (returns 202)            |
| GET    | `/scrape/tasks/{task_id}`  | yes  | Get task status (owner-only, 404 otherwise)          |
| GET    | `/scrape/tasks/current`    | yes  | Get the user's current non-terminal task (404 if none) |

> ⚠️ Two known endpoint-wiring bugs affect the scrape routes today (SlowAPI parameter collision and route-order shadowing). See [docs/STATUS.md](docs/STATUS.md#critical--high-bugs).

## Project structure

```text
scrapegpt/
├── app/
│   ├── main.py                    # FastAPI factory + lifespan (starts scheduler)
│   ├── api/
│   │   ├── deps.py                # get_db, get_current_user
│   │   └── v1/
│   │       ├── router.py          # Mounts health/auth/scrape routers
│   │       └── endpoints/
│   │           ├── health.py      # /, /ready, /live
│   │           ├── auth.py        # register, login, refresh
│   │           └── scrape.py      # start, tasks/{id}, tasks/current
│   ├── core/
│   │   ├── config.py              # Pydantic Settings (env-driven)
│   │   ├── security.py            # bcrypt hash + JWT issue/verify
│   │   ├── rate_limit.py          # SlowAPI limiter + key fn
│   │   └── scheduler.py           # APScheduler: daily credit reset + watchdog
│   ├── db/
│   │   └── database.py            # async engine, sessionmaker, close_db
│   ├── models/
│   │   ├── base.py                # Declarative Base
│   │   ├── user.py                # users (auth + credits)
│   │   └── scrape_task.py         # scrape_tasks + TaskState enum + transitions
│   ├── schemas/
│   │   ├── auth.py                # register/login/token DTOs
│   │   └── scrape.py              # scrape DTOs
│   └── services/
│       ├── admission.py           # one-active-task + credit gating
│       ├── task_state.py          # explicit transition fns (atomic credit deduct in LLM phase)
│       ├── task_executor.py       # pipeline orchestrator (always-finalize)
│       ├── scraper.py             # httpx + BeautifulSoup fetch/extract
│       ├── llm_processor.py       # ⚠️ STUB — returns mock dict
│       ├── readiness.py           # bounded DB readiness probe
│       └── watchdog.py            # fails tasks stuck past timeout
├── alembic/versions/
│   ├── 001_create_users.py
│   ├── 002_create_scrape_tasks.py # ⚠️ legacy enum values; see STATUS
│   ├── 003_update_task_states.py  # current enum + partial unique idx
│   └── 004_system_state.py        # check-and-set table for credit reset
├── docs/
│   ├── architecture.md            # System design overview
│   ├── STATUS.md                  # Where to continue from (punch list)
│   ├── implementation_audit.md    # 2026-02-16 full audit (source of STATUS)
│   ├── ops/health.md              # Health/readiness operations notes
│   ├── learning/                  # Decision logs (01–04)
│   └── reviews/                   # Self-review notes per feature
├── tests/                         # ⚠️ skeleton only — health + readiness only
├── requirements.txt
└── .env.example
```

## Configuration

All settings come from `.env` and are validated at startup by `app/core/config.py`. Highlights:

| Variable                                       | Default                                  | Purpose                                                  |
| ---------------------------------------------- | ---------------------------------------- | -------------------------------------------------------- |
| `ENVIRONMENT`                                  | `development`                            | One of `development` / `staging` / `production`          |
| `DEBUG`                                        | `false`                                  | Enables `/docs`, `/redoc`, `/openapi.json` and reload    |
| `DATABASE_URL`                                 | `postgresql+asyncpg://…/scrapegpt`       | Async PostgreSQL DSN                                     |
| `SECRET_KEY`                                   | placeholder (warns)                      | JWT signing key — **must change for production**         |
| `ACCESS_TOKEN_EXPIRE_MINUTES`                  | `15`                                     | Access token TTL                                         |
| `REFRESH_TOKEN_EXPIRE_DAYS`                    | `7`                                      | Refresh token TTL                                        |
| `DEFAULT_DAILY_CREDITS`                        | `5`                                      | Daily allowance for new users                            |
| `SCRAPE_CREDIT_COST`                           | `1`                                      | Credits deducted per task (deducted at LLM phase)        |
| `SCRAPE_TIMEOUT` / `LLM_TIMEOUT`               | `30` / `120`                             | Per-stage HTTP / LLM timeouts (seconds)                  |
| `WATCHDOG_*_TIMEOUT_MINUTES`                   | `3` / `5` / `10`                         | Stuck-task thresholds for PERMISSION_GRANTED / SCRAPING / LLM_PROCESSING |
| `RATE_LIMIT_PER_MINUTE` / `_SCRAPE_` / `_AUTH_`| `60` / `10` / `5`                        | SlowAPI limits                                           |
| `READINESS_TIMEOUT_SECONDS`                    | `2.0`                                    | Bound on `/health/ready` DB probe                        |

See [.env.example](.env.example) for the full list.

## Development

```bash
# Run dev server (auto-reload)
uvicorn app.main:app --reload

# Run tests (currently only health + readiness)
pytest -v

# Create a new migration after model edits
alembic revision --autogenerate -m "your message"
alembic upgrade head
```

## Production notes

1. Set `ENVIRONMENT=production` and `DEBUG=false` (this disables `/docs`).
2. Generate a real `SECRET_KEY`: `openssl rand -hex 32`.
3. Set `CORS_ORIGINS` to your real frontend origin(s).
4. Run with multiple workers — but note that **APScheduler runs in-process**. With multiple workers, the credit-reset `system_state` check-and-set still serializes correctly across instances, but the watchdog will fire from each worker. For a single-host deployment this is fine; for multi-host, run the scheduler in a dedicated process.

```bash
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

## Where to go next

Read [docs/STATUS.md](docs/STATUS.md) — it's the source of truth for what's done, what's broken, and what to pick up next, organized by priority.

For a deeper architectural walkthrough, see [docs/architecture.md](docs/architecture.md).

## License

MIT.
