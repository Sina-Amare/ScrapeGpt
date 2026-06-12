# Logging and Observability

**Date:** June 2026
**Branch:** `feature/logging-observability`
**Status:** Implemented and validated — 348 backend tests passing.

---

## Problem and Purpose

Before this implementation, ScrapeGPT had no deliberate observability strategy:

- `LOG_LEVEL` and `LOG_FORMAT` settings existed in `config.py` but were never applied.
- `main.py` used bare `print()` for startup events. The root logger had no configured
  handlers.
- The entire HTTP API layer (`auth.py`, `projects.py`, `providers.py`) had zero logging.
- Three silent `except Exception: pass / {}` blocks in `project_extraction.py` and
  `frontierpreview.py` corrupted the extraction record with no log evidence.
- No correlation IDs — background tasks could not be traced to the HTTP request that
  triggered them.
- Provider API key reveals had no audit trail.

A production incident with this state would have no recoverable evidence: no timing for
LLM calls, no scope classification trace, no per-page failure breakdown, no auth history.

---

## Invariants Enforced

**Stdout only.** The application never writes log files. Docker and process supervisors
collect stdout. This is non-negotiable — file-based logging adds rotation, permission, and
path management complexity with no benefit.

**No credential material in logs.** Auth events log `user_id` only. Passwords and JWT
tokens are never logged. Provider API keys pass through `safe_provider_error_message()`
before any error logging. The `SecretRedactingFilter` in the logging pipeline provides a
backstop.

**No extracted content in logs.** Extraction events log counts and quality labels only.
Raw field values and record content stay in the database.

**Key reveal audit trail.** Any access to a plaintext provider API key produces a
`security.key_revealed` WARNING event recording who revealed which provider config.

**Silent exceptions are observability bugs.** The three `except Exception: pass/{}` blocks
were treated as correctness issues, not cosmetic cleanup. Each was replaced with an explicit
`logger.error()` call.

**Configuration drives behavior.** `LOG_FORMAT=text` → human-readable DevFormatter.
`LOG_FORMAT=json` → one JSON object per line, Docker-ready. `LOG_LEVEL` gates all output.
These settings were already defined in `config.py`; they are now wired.

---

## Design Decisions and Trade-offs

### stdlib `logging` over structlog

The roadmap originally proposed structlog for Phase 5. The implementation uses standard
Python `logging` with a thin JSON formatter and `contextvars` instead.

**Why:** All 20+ existing `logging.getLogger(__name__)` declarations and `extra={}` call
sites work unchanged — zero migration cost. structlog's primary advantage is async context
binding, which Python's built-in `contextvars` module provides natively. Adding structlog
would introduce a dependency and a 20-file migration for a capability stdlib already covers.

**Trade-off:** structlog's `ConsoleRenderer` produces slightly prettier dev output.
Acceptable cost — the DevFormatter is readable and visually distinct by log level.

**Future path:** If structlog is later adopted, the migration is mechanical. Event names
and `extra={}` dict patterns stay identical. No log consumer breaks.

### `contextvars` for correlation

Three binding points carry context through async call chains without threading through
function signatures:

1. **HTTP request middleware** (`main.py`) — binds `request_id`. The `get_current_user`
   dependency in `deps.py` calls `bind_user_id(user.id)` after JWT decode.

2. **Background extraction task** (`project_extraction.py`) — calls
   `set_task_context(project_id, user_id)` after fetching the project from the DB.
   Per-page loop calls `set_page_context(page.id)`.

3. **Frontier preview** (`frontierpreview.py`) — calls `set_task_context(project_id,
   user_id)` at `create_frontier_preview()` entry.

`contextvars` propagate automatically to async sub-calls in Python 3.7+. When extraction
moves to separate worker processes (celery, arq), calling `set_task_context()` at task
entry is the only change required — `configure_logging()` is called identically in each
worker's entrypoint.

### `disable_existing_loggers: False`

The `dictConfig` sets `disable_existing_loggers: False`. This preserves all existing
third-party loggers (uvicorn, SQLAlchemy, APScheduler, LiteLLM, httpx). Without this,
configuring the root logger silences all third-party output.

### URL sanitization scope

`sanitize_url()` strips query strings and fragments from log output. This prevents
token-bearing URLs (OAuth callbacks, signed storage URLs) from appearing in logs. The
`SecretRedactingFilter` applies URL sanitization to:
- Fields whose keys are in `_URL_KEYS` (e.g., `url`, `seed`, `source_url`)
- Any string extra field containing an embedded URL substring
- Exception traceback text (via overridden `formatException()`)

### Exception traceback redaction

Both `DevFormatter` and `JsonFormatter` override `formatException()` to pass the formatted
traceback through `_sanitize_exception_text()` before output. This prevents secrets from
leaking through `logger.exception()` calls even if they appear in exception messages or
chained exception context.

---

## Runtime Lifecycle

**Startup:** `configure_logging()` is the first call in `main.py`'s lifespan function,
before any service imports. It is idempotent — safe to call twice.

**Per-request:** The `request_context_middleware` in `main.py` generates a `request_id`
(or reads `X-Request-ID` from the incoming header), binds it via `set_request_context()`,
runs the request inside a `try/finally` block that always clears the context, and logs
`http.request` on completion with method, path, status code, and duration.

**Per-background-task:** The task entry point binds `project_id` and `user_id`. All
service calls made within the task automatically include these fields in every log line.

**Per-page:** The extraction loop binds `page_id` before processing each crawl page.
Per-page events (`extraction.page_fetched`, `extraction.records_extracted`) appear at
DEBUG level to avoid INFO-level log flood on large crawls.

---

## Failure Paths

**Scope max pages computation failure:** If `scope_max_pages()` raises, the error is logged
as `extraction.scope_max_pages_failed` (ERROR) and extraction continues with the system
default page limit. This is the correct degradation — the crawl should not abort because of
a scope config anomaly.

**Quality computation failure:** If `compute_extraction_quality()` raises, the error is
logged as `extraction.quality_computation_failed` (ERROR) and `quality_summary` is set to
`{}`. The extraction is still marked COMPLETED. The empty quality summary is the
observable signal that the computation failed.

**Frontier preview fetch failure:** If the seed URL fetch fails, the error is logged as
`frontier.fetch_failed` (ERROR) with `error_type` and available status code. Previously
this was silently swallowed.

---

## Log Event Catalog

| Event | Level | Source | Key Fields |
|---|---|---|---|
| `app.starting` | INFO | main.py | — |
| `app.shutting_down` | INFO | main.py | app_name |
| `app.shutdown_complete` | INFO | main.py | — |
| `http.request` | INFO | main.py | method, path, status_code, duration_ms, request_id |
| `http.request_failed` | ERROR | main.py | method, path, duration_ms, request_id, error_type |
| `auth.register_success` | INFO | auth.py | user_id |
| `auth.register_failed` | WARNING | auth.py | reason |
| `auth.login_success` | INFO | auth.py | user_id |
| `auth.login_failed` | WARNING | auth.py | reason |
| `auth.token_refresh_success` | INFO | auth.py | user_id |
| `auth.token_refresh_failed` | WARNING | auth.py | reason |
| `security.key_revealed` | WARNING | providers.py | user_id, provider_config_id, provider_name |
| `security.key_reveal_failed` | WARNING | providers.py | user_id, provider_config_id, reason |
| `scope.classified` | INFO | crawl_scope.py | scope_mode, included_count, excluded_count |
| `scope.url_excluded` | DEBUG | crawl_scope.py | url, reason_code |
| `scope.confirmation_gate_passed` | INFO | crawl_scope.py | scope_mode, project_id |
| `scope.confirmation_required` | WARNING | crawl_scope.py | scope_mode, scope_status, project_id |
| `frontier.fetch_started` | DEBUG | frontierpreview.py | project_id, url |
| `frontier.fetch_failed` | ERROR | frontierpreview.py | project_id, url, error_type |
| `frontier.preview_built` | INFO | frontierpreview.py | project_id, scope_mode, included_count, excluded_count |
| `frontier.high_exclusion_rate` | WARNING | frontierpreview.py | project_id, excluded_pct |
| `preview.started` | DEBUG | project_preview.py | project_id |
| `preview.completed` | INFO | project_preview.py | project_id, record_count, selector_hit_rate |
| `preview.selector_failed` | WARNING | project_preview.py | project_id, field_name, selector |
| `project_extraction.started` | INFO | project_extraction.py | project_id, spec_id |
| `project_extraction.completed` | INFO | project_extraction.py | project_id, records, pages |
| `project_extraction.canceled` | INFO | project_extraction.py | project_id |
| `project_extraction.failed` | ERROR | project_extraction.py | project_id, error |
| `project_extraction.scope_unconfirmed` | ERROR | project_extraction.py | project_id |
| `project_extraction.missing_state` | ERROR | project_extraction.py | project_id, spec_id |
| `extraction.scope_max_pages_failed` | ERROR | project_extraction.py | project_id, error_type |
| `extraction.quality_computation_failed` | ERROR | project_extraction.py | project_id, error_type |
| `extraction.page_robots_blocked` | WARNING | project_extraction.py | project_id, page_id, url |
| `extraction.page_failed` | ERROR | project_extraction.py | project_id, page_id, url, error_type |
| `extraction.records_extracted` | DEBUG | project_extraction.py | project_id, page_id, record_count, warnings_count |
| `extraction.quality_computed` | INFO | project_extraction.py | project_id, quality_label, field_count |
| `export.started` | INFO | projects.py | project_id, user_id, format |
| `export.completed` | INFO | projects.py | project_id, format, record_count, duration_ms |
| `export.failed` | ERROR | projects.py | project_id, format, error_type |
| `watchdog.sweep_started` | DEBUG | watchdog.py | timestamp |
| `watchdog.task_reset` | INFO | watchdog.py | task_id, old_state, timeout_category |
| `watchdog.job_reset` | INFO | watchdog.py | job_id, old_state |
| `watchdog.sweep_completed` | INFO | watchdog.py | tasks_reset, jobs_reset, duration_ms |
| `watchdog.error` | ERROR | watchdog.py | error |
| `scheduler.configured` | INFO | scheduler.py | jobs |
| `scheduler.started` | INFO | scheduler.py | — |
| `scheduler.stopped` | INFO | scheduler.py | — |
| `scheduler.job_started` | DEBUG | scheduler.py | job_name |
| `scheduler.job_completed` | DEBUG | scheduler.py | job_name, duration_ms |

---

## Security Guarantees

**Auth events:** `user_id` only on success. `reason` only on failure. Email addresses are
never included in failure logs.

**Secret redaction:** `SecretRedactingFilter` strips API key patterns from log messages,
args, and structured `extra` fields. It also fully redacts keys in `_FULL_REDACT_KEYS`
(`api_key`, `token`, `password`, `hashed_password`, `api_key_encrypted`, etc.) and
sanitizes URL fields in `_URL_KEYS` by stripping query strings and fragments. The
`_SECRET_PATTERNS` regex covers `sk-...`, `bearer ...`, and common secret-bearing
query parameters.

**URL sanitization in extras:** Arbitrary string extras (e.g., `error`, `detail`,
`reason`) are scanned for embedded URLs; any found URL has its query string stripped.
This covers signed storage URLs and token-bearing OAuth callbacks that may appear in
exception messages.

**Exception traceback redaction:** `formatException()` is overridden in both formatters.
Tracebacks pass through `_sanitize_exception_text()` before output.

**No extracted content:** Record content stays in the database. Log events carry counts,
labels, and IDs only.

---

## Configuration

| Setting | Values | Default | Effect |
|---|---|---|---|
| `LOG_FORMAT` | `text`, `json` | `text` | DevFormatter vs JsonFormatter |
| `LOG_LEVEL` | `DEBUG`…`CRITICAL` | `INFO` | Gates all log output |

For Docker or any structured log aggregator: `LOG_FORMAT=json LOG_LEVEL=INFO`.
For local development: defaults are fine; set `LOG_LEVEL=DEBUG` to see per-page events.

---

## Safe Evolution Notes

**Adding a new log event:** Follow the `"component.event_name"` naming convention.
Include relevant correlation IDs from context vars (`project_id`, `page_id`, etc.) in
`extra={}`. Assign DEBUG level for per-item events, INFO for lifecycle milestones.

**Adding new secret patterns:** Extend `_SECRET_PATTERNS` in `provider_service.py` and
add the new key name to `_FULL_REDACT_KEYS` in `logging_config.py` if it should be fully
redacted rather than pattern-matched.

**Moving extraction to a worker process:** Call `configure_logging()` at the worker
entrypoint. Call `set_task_context(project_id, user_id)` at task entry. No other changes
are required — `contextvars` propagation is process-local.

**SSE progress streams:** `contextvars` propagate into async generators in Python 3.7+.
Logging inside SSE generator code inherits the request context automatically.

---

## Test Coverage

| File | Tests | What it covers |
|---|---|---|
| `tests/core/test_log_context.py` | 9 | Context variable bindings, get/set/clear |
| `tests/core/test_logging_config.py` | 22 | Formatters, filters, `configure_logging()` idempotency |
| `tests/core/test_logging_integration.py` | 31 | All log events fired from real code paths |
| `tests/core/test_logging_security.py` | 45 | URL sanitization pipeline, embedded-URL redaction, exception traceback redaction, middleware try/finally |
