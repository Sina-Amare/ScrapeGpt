# Architecture

This document describes how ScrapGPT is put together: the layers, the scrape pipeline, the data model, and the design decisions behind them. It is intended for someone picking up the codebase and wanting to understand *why*, not just *what*.

For implementation status (what's done, what's broken, what to do next), see [STATUS.md](STATUS.md).

## Layered architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│  HTTP / FastAPI                                                 │
│  app/api/v1/endpoints/{health,auth,scrape}.py                   │
│  ─ Parses requests, validates with Pydantic, returns DTOs       │
│  ─ Auth/DB acquired via Depends(get_current_user, get_db)       │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  Service layer                                                  │
│  app/services/{admission,task_state,task_executor,scraper,…}    │
│  ─ All business logic lives here                                │
│  ─ Endpoints delegate; services own transactions                │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  Data layer                                                     │
│  app/models/{user,scrape_task}.py + app/db/database.py          │
│  ─ SQLAlchemy 2.0 async ORM, asyncpg driver                     │
│  ─ Schema migrations in alembic/versions/                       │
└─────────────────────────────────────────────────────────────────┘
```

A few cross-cutting modules live in `app/core/`:

- `config.py` — single typed `Settings` object (env-driven, validated at startup).
- `security.py` — bcrypt + JWT primitives.
- `rate_limit.py` — SlowAPI limiter and key function.
- `scheduler.py` — APScheduler boot/shutdown for credit reset and watchdog.

## Domain model

```text
┌─────────────┐ 1     ∞ ┌─────────────────┐
│    User     │─────────│   ScrapeTask    │
└─────────────┘  user_id└─────────────────┘
                        │ state (enum)
                        │ url, content, error, result(JSONB)
                        │ created_at, updated_at
                        └─ partial unique idx on user_id
                           WHERE state NOT IN (COMPLETED, FAILED)
```

### `User`

Standard auth fields (`email`, `hashed_password`, `is_active`, `is_verified`) plus credit accounting:

| Field                  | Purpose                                                |
| ---------------------- | ------------------------------------------------------ |
| `credits_remaining`    | Current balance, decremented on credit consumption     |
| `daily_credit_limit`   | Per-user ceiling reset to this value at 00:00 UTC      |
| `credits_reset_at`     | Last reset timestamp                                   |

Credits are **not** reset lazily on read — they are reset by a scheduled job, see [Scheduled jobs](#scheduled-jobs).

### `ScrapeTask` and the state machine

```text
PERMISSION_GRANTED ──► SCRAPING ──► SCRAPED ──► LLM_PROCESSING ──► COMPLETED
                          │            │              │
                          ▼            ▼              ▼
                                    FAILED  (terminal)
```

Two invariants:

1. **At most one non-terminal task per user** — enforced at the DB level by a partial unique index on `user_id` where `state NOT IN ('COMPLETED', 'FAILED')`. The application checks for an active task as an admission gate; the index is the safety net under concurrency.
2. **Always finalize** — every task path ends in `COMPLETED` or `FAILED`. The pipeline orchestrator wraps each phase and has a catch-all so unexpected exceptions still mark the task `FAILED`.

`VALID_TRANSITIONS` (in `app/models/scrape_task.py`) is the source of truth for legal state moves; `can_transition_to(new_state)` checks against it.

### `system_state` (key/value)

A single-row-per-key table used as a coordination point. Today it holds the credit-reset cursor — see [Scheduled jobs](#scheduled-jobs).

## Request flow: `POST /scrape/start`

```text
client                  endpoint              admission                   db
  │  POST /scrape/start    │                       │                       │
  │  {url, JWT}            │                       │                       │
  │ ─────────────────────► │                       │                       │
  │                        │ get_current_user       │                       │
  │                        │ ───────────────────────────────────────────► │  (decode JWT, load User)
  │                        │ admit_scrape_task(user, url)                  │
  │                        │ ─────────────────────►│                       │
  │                        │                       │ check credits ≥ 1     │
  │                        │                       │ check no active task  │
  │                        │                       │ INSERT ScrapeTask     │
  │                        │                       │   state=PERMISSION_…  │
  │                        │                       │ ─────────────────────►│
  │                        │ ◄───── AdmissionSuccess(task) ───────         │
  │                        │ background_tasks.add_task(execute_pipeline)   │
  │ ◄── 202 + task_id ──── │                       │                       │
  │                        │ ▼ (returns immediately)                        │
  │                        ▼                                                │
  │             execute_scrape_pipeline (background)                        │
  │             ─ transition_to_scraping                                    │
  │             ─ scrape_url(url)                                           │
  │             ─ transition_to_scraped(content)                            │
  │             ─ transition_to_llm_processing  (atomic credit deduct here) │
  │             ─ process_with_llm(content)                                 │
  │             ─ transition_to_completed(result)                           │
  │             [any exception → transition_to_failed(error)]               │
  │                                                                        │
  │  GET /scrape/tasks/{id} (poll for state)                                │
  │ ─────────────────────────────────────────────────────────────────────► │
```

### Admission gate (`app/services/admission.py`)

Before a task is created, admission checks two conditions in one place:

1. `user.credits_remaining >= 1`
2. No row exists with `user_id == user.id` and `state NOT IN TERMINAL_STATES`.

Returns either `AdmissionSuccess(task)` or an `AdmissionError` enum (`INSUFFICIENT_CREDITS`, `ALREADY_HAS_ACTIVE_TASK`, generic). The endpoint translates these into HTTP 402 / 409 / 400.

**Credits are not deducted at admission time.** This is deliberate — see [Why deduct credits at the LLM phase](#why-deduct-credits-at-the-llm-phase).

### Pipeline orchestration (`app/services/task_executor.py`)

`execute_scrape_pipeline(task_id, user_id)` is the entry point invoked as a FastAPI `BackgroundTask`. It:

1. Loads the task by `task_id`. Returns early (with a log error) if the task no longer exists.
2. Drives each transition with a wrapped try/except so any failure marks the task `FAILED` with a useful error message.
3. Has an outer catch-all `try/except` so even unexpected errors (e.g. DB blip during a transition) still finalize the task — no task is left dangling.

> **Note:** The ownership check (`task.user_id == user_id`) is performed inside `transition_to_llm_processing`, not at the top of the executor. If the IDs do not match, the task is immediately marked `FAILED` and the credit deduction is skipped.

### State transitions (`app/services/task_state.py`)

Each transition is its own async function (`transition_to_scraping`, `transition_to_scraped`, etc.). They share a pattern:

- `async with db.begin()` so each transition is one transactional unit.
- Re-fetch the task inside the transaction, check `is_terminal` to avoid resurrecting a FAILED task.
- Validate the transition against `VALID_TRANSITIONS`.
- Apply state + side effects (e.g. write `content`, `error`, or `result`).

`transition_to_llm_processing` is special: it is the **only** place credits are deducted, and the deduction happens inside the same transaction as the state change.

## Why deduct credits at the LLM phase

The naive option is to deduct at admission. The problem: scraping can fail for reasons that aren't the user's fault (target down, bot block, network). If we charged at admission, every transient failure costs the user a credit.

The other extreme — charge at completion — means a user can spam expensive scrapes that all "almost finish" and only get charged for the ones that succeed.

Deducting at the LLM transition is the middle ground:

- The scrape stage has already succeeded. We have content. We're about to spend the expensive resource (LLM call).
- The deduction is atomic with the state transition. Either both happen or neither.
- Failures *before* this point cost the user nothing (admission still gates against zero-credit users at request time).
- Failures *after* this point still cost a credit — by then we've consumed real resources.

## Scheduled jobs

`app/core/scheduler.py` boots APScheduler in the FastAPI lifespan. Two jobs run:

### Credit reset (00:00 UTC daily)

Multi-instance safety is achieved with a check-and-set on the `system_state` table:

1. Read `system_state` row keyed by `credits_last_reset`.
2. If the stored date < today, attempt to update it to today with a `WHERE` clause matching the old value (compare-and-swap).
3. Only the worker whose UPDATE affects 1 row proceeds to bulk-reset all users' `credits_remaining` to their `daily_credit_limit`. Others see 0 rows updated and back off.

This means even if the scheduler accidentally runs in multiple workers, only one performs the reset.

### Watchdog (periodic stuck-task cleanup)

`cleanup_stuck_tasks()` finds tasks in non-terminal states whose `updated_at` is older than configurable thresholds and force-fails them with a "Watchdog: …" error. Three timeout buckets:

- `PERMISSION_GRANTED` (default 3 min) — pipeline never started.
- `SCRAPING` (default 5 min) — scrape stalled.
- `LLM_PROCESSING` (default 10 min) — LLM stalled.

> ⚠️ Known bug: `updated_at` is nullable and has no insert default, so freshly-created tasks slip past the `updated_at < cutoff` filter. See [STATUS.md](STATUS.md).

## Authentication

Standard JWT with two token types:

- **Access token** — short-lived (15 min default). Sent on every authenticated request via `Authorization: Bearer …`.
- **Refresh token** — longer-lived (7 days default). Used only against `POST /auth/refresh` to mint new access tokens.

`get_current_user` (in `app/api/deps.py`) decodes the access token, casts the `sub` claim to `int`, and loads the `User` row. The bcrypt cost is configurable (`PASSWORD_HASH_ROUNDS`, default 12).

## Rate limiting

SlowAPI is used as the rate-limit backend. Limits are configured in `app/core/rate_limit.py`:

- Default: `RATE_LIMIT_PER_MINUTE` (60/min).
- Scrape: `RATE_LIMIT_SCRAPE_PER_MINUTE` (10/min) — applied as a `@limiter.limit` decorator on `POST /scrape/start`.
- Auth: `AUTH_RATE_LIMIT` constant exists but is not yet applied to auth endpoints.

The custom key function tries `request.state.user` first, then falls back to remote IP — but no middleware sets `request.state.user`, so today it always falls back to IP. See [STATUS.md](STATUS.md).

## Health and readiness

Three endpoints, intentionally distinct:

| Endpoint        | Cost            | Purpose                                                      |
| --------------- | --------------- | ------------------------------------------------------------ |
| `/health/live`  | trivial         | "Process is up." Use as a Kubernetes liveness probe.         |
| `/health/ready` | bounded DB hit  | "Process can serve traffic." Checks DB + alembic + schema.   |
| `/health`       | trivial         | Human-readable status (env, version) for casual checks.      |

`check_db_ready` (in `app/services/readiness.py`) is bounded by `READINESS_TIMEOUT_SECONDS` so a hung database can never block the readiness response — it returns a structured failure code instead. See [docs/ops/health.md](ops/health.md) for operator notes.

## Configuration

All configuration is environment-driven via `pydantic-settings`. The `Settings` class in `app/core/config.py` is the single source of truth — every field has a type, a default, and (where it matters) a validator. There is no untyped `os.environ` lookup anywhere in the application.

`settings` is exported as a module-level singleton (`@lru_cache` on `get_settings`).

## What's intentionally simple

- **In-process scheduler.** Fine for a single host; for multi-host deployments, run the scheduler in a dedicated worker.
- **In-process background tasks.** FastAPI's `BackgroundTasks` is enough for the current scale. A real queue (Celery / RQ / Arq) would be the upgrade path.
- **No retry on transient scrape failures.** First failure → task fails. Adding retries is straightforward inside `task_executor`.
- **No webhook on completion.** Clients poll. A websocket / SSE endpoint or webhook is a future addition.

## What's intentionally rigorous

- **State machine is enforced in code AND in the DB.** `VALID_TRANSITIONS` is the application invariant; the partial unique index is the database-level safety net for the "one active task" rule.
- **Atomic credit deduction.** Transition + deduction in one transaction.
- **Always-finalize.** The pipeline cannot leave a task dangling.
- **Bounded readiness.** A hung DB cannot hang the load balancer's health check.
- **Multi-instance-safe credit reset.** Compare-and-swap on `system_state`, not "first one to wake up wins."
