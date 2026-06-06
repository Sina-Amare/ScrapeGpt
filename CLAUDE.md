# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

ScrapGPT is being redesigned as an open-source, self-hosted, BYOK (Bring Your Own Key) AI-assisted web data extraction platform. The credit-gated SaaS model is replaced with BYOK multi-provider AI, dual-mode extraction (structured + RAG/content), and a non-technical-friendly UX. **Phase 0 and Phase 0.5 are complete and merged to main.** See `docs/product/strategic_redesign.md` for the authoritative current state, architecture decisions, and phase-by-phase roadmap. The pre-redesign reference is in `docs/archive/project_master.md`.

## Commands

PowerShell on Windows; a venv lives in `venv/`.

```powershell
# Activate venv
.\venv\Scripts\activate

# Install deps
pip install -r requirements.txt

# Run dev server (auto-reload, enables /docs)
uvicorn app.main:app --reload

# Tests
pytest -v                                         # all tests
pytest tests/api/v1/test_health_readiness.py -v   # one file
pytest tests/services/test_readiness.py::test_name -v   # one test

# Migrations (Alembic)
alembic upgrade head                              # apply all
alembic downgrade -1                              # roll back one
alembic revision --autogenerate -m "message"      # new migration after model edits
```

Notes:
- A running PostgreSQL is required for `alembic` and for `/health/ready` — set `DATABASE_URL` in `.env` (copy from `.env.example`).
- `ruff` and `mypy` are referenced in the README but are **commented out** in `requirements.txt` — they are not installed. Don't assume a lint/typecheck step exists.
- The backend test suite has **95 passing tests** — all run without a database (fully mocked). No `TEST_DATABASE_URL` needed.
- The frontend test suite has **16 passing tests**: `npm test` from the `frontend/` directory.
- Frontend typecheck: `npm run typecheck` from `frontend/`. Both must stay green before merging.

## Mandatory workflow rules (`.agent/rules/`)

These are always-on project rules and override default behavior:

- **`documenting.md`** — For **every completed task**, create a Markdown doc that lets the owner fully understand the design. Place it in a logical `docs/` subfolder with an ordered, descriptive filename (e.g. `docs/learning/05_xyz.md` — follow the existing numbering). It must cover: problem/purpose, invariants enforced, design decisions + rejected alternatives + trade-offs, code walkthrough (the *why*, not just the *what*), runtime lifecycle (success and failure paths), concurrency/crash analysis, pitfalls, and safe-evolution notes. This is why `docs/learning/` and `docs/reviews/` exist — keep that practice going.
- **`task-review.md` / `workflow.md`** — Understand the system before acting. Prioritize correctness, invariants, concurrency, and crash safety over speed. State trade-offs explicitly; surface only meaningful risks, not nitpicks.

(`AGENTS.md` at the repo root is a large generic AI-operating-principles document, not project-specific design — skim only if needed.)

## Architecture

Three layers, strict direction of dependency (`api` → `services` → `models`/`db`):

- **`app/api/v1/endpoints/`** — HTTP only. Parse, validate with Pydantic, delegate. Endpoints hold no business logic.
- **`app/services/`** — All business logic. **Services own database transactions**, not endpoints.
- **`app/models/`** + **`app/db/`** — SQLAlchemy 2.0 async ORM (asyncpg). Schema changes go through `alembic/versions/`.

Cross-cutting modules in `app/core/`: `config.py` (single typed `Settings` singleton, env-driven, validated at startup — there is no raw `os.environ` access anywhere), `security.py` (bcrypt + JWT), `rate_limit.py` (SlowAPI), `scheduler.py` (APScheduler).

### The scrape pipeline (the heart of the system)

A scrape task moves through a state machine defined in `app/models/scrape_task.py`:

```
PERMISSION_GRANTED → SCRAPING → SCRAPED → LLM_PROCESSING → COMPLETED
                        ↓           ↓            ↓
                              FAILED  (terminal)
```

Request flow for `POST /scrape/start`:
1. `app/services/admission.py` gates the task: user has fewer than `MAX_CONCURRENT_JOBS_PER_USER` active tasks (no credit system — Phase 0.5 removed it entirely). Returns `AdmissionSuccess` or a typed `AdmissionError`. Creates the task in `PERMISSION_GRANTED`.
2. The endpoint returns `202` immediately and queues `execute_scrape_pipeline` via FastAPI `BackgroundTasks` (in-process — there is no external job queue).
3. `app/services/task_executor.py` orchestrates each phase with a catch-all so **every task always reaches a terminal state** ("always-finalize" guarantee).
4. `app/services/task_state.py` holds the per-transition functions, each wrapped in `async with db.begin()` (one transaction per transition).

### Invariants that must not be broken casually

- **Per-user active task limit.** Enforced in `admission.py` as a count-based check against `MAX_CONCURRENT_JOBS_PER_USER` (configurable, default 3). The old PostgreSQL partial unique index from migration `003` was **dropped** in migration `005` — do not restore it.
- **No credit system.** Credits, `credits_remaining`, `daily_credit_limit`, `credits_reset_at`, and `system_state` were fully removed in migration `005`. Do not reintroduce any credit logic anywhere in the codebase.
- **Provider API keys are encrypted at rest** using Fernet (`PROVIDER_KEY_ENCRYPTION_SECRET`). Keys are never returned in list/create/update/test responses. Reveal requires `POST /providers/{id}/reveal-key` with a password body — verified against `user.hashed_password`.
- **`VALID_TRANSITIONS`** in `scrape_task.py` is the source of truth for legal state moves; transitions re-check `is_terminal` to avoid resurrecting a finished task.
- **Terminal-only task deletion.** `DELETE /scrape/tasks/{id}` checks `task.is_terminal` before deleting. Active tasks cannot be deleted — the background worker still holds a reference to them.

### Scheduler & watchdog

`app/core/scheduler.py` boots APScheduler **in-process** inside the FastAPI lifespan. One job: `watchdog.py`, which force-fails tasks stuck past configurable timeouts (`WATCHDOG_*_TIMEOUT_MINUTES`). The credit reset job was removed in Phase 0.5. With multiple workers the scheduler runs per-worker — fine on a single host, but a consideration for multi-host deploys.

## Gotchas

- **Phase 0 and Phase 0.5 are complete.** SlowAPI collision, route shadowing, watchdog NULL-skip, JWT int() cast, per-user rate limiting, and migration enum drift are all fixed. Provider BYOK, LLM integration, frontend, security hardening, and task deletion are all merged to main. See `docs/product/strategic_redesign.md` for the current phase roadmap.
- **Adding a new terminal state** (e.g., `CANCELED`) requires: (1) adding the new enum value in a migration using `op.get_context().autocommit_block()` — PostgreSQL `ADD VALUE` cannot run inside a transaction; (2) updating the partial unique index to include the new terminal state; (3) adding it to `TERMINAL_STATES` in `scrape_task.py`.
- **Each transition function must open its own session.** Never pass a session into a `transition_to_*` function. SQLAlchemy 2.0 autobegin fires on `db.get()`, and a second `db.begin()` on the same session raises `InvalidRequestError`.
- **Use `verify_token`, never `decode_token` in production paths.** `decode_token` skips signature verification and is for debugging only. Using it for rate-limit keying or any auth decision is a security hole.
- `/docs`, `/redoc`, `/openapi.json` are only mounted when `DEBUG=true`.
