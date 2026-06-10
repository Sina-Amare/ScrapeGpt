# ScrapGPT — Logging and Observability Implementation Report

**Branch:** `feature/logging-observability`
**Date:** 2026-06-10
**Plan reference:** `docs/LOGGING_AND_OBSERVABILITY_PLAN.md`

---

## 1. Summary

The full 3-layer logging and observability plan has been implemented. All 299 backend tests pass with 0 failures. The implementation follows the plan's architecture (stdlib `logging` + JSON formatter + contextvars) without redesign.

---

## 2. Implementation Order (per plan §10.4)

### Layer 1 — Infrastructure

| File                         | Change                                                                                                                                                                                                     | Commit    |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| `app/core/log_context.py`    | **New file.** Context variables (`request_id`, `user_id`, `project_id`, `page_id`) with `set_request_context`, `set_task_context`, `bind_user_id`, `set_page_context`, `clear_context`, `get_log_context`. | `a47af80` |
| `app/core/logging_config.py` | **New file.** `ContextInjectingFilter`, `SecretRedactingFilter`, `DevFormatter`, `JsonFormatter`, `configure_logging()`. Idempotent, stdout-only, `LOG_FORMAT`/`LOG_LEVEL` driven.                         | `a47af80` |
| `app/main.py`                | Added `configure_logging()` call in lifespan, `request_context_middleware` that sets/clears `request_id` + `user_id` per HTTP request, replaced `print("Startup complete")` with `logger.info`.            | `a47af80` |
| `app/api/deps.py`            | Added `bind_user_id(user.id)` after successful JWT decode in `get_current_user` and `get_optional_user`.                                                                                                   | `a47af80` |
| `app/db/database.py`         | Set `echo=False` on `async_session_factory` to suppress SQLAlchemy query logging.                                                                                                                          | `a47af80` |

### Layer 2 — Security & Correctness

| File                                 | Change                                                                                                                                                                                                                    | Commit    |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| `app/api/v1/endpoints/auth.py`       | Added `auth.register_success`, `auth.login_success`, `auth.login_failed`, `auth.refresh_success`, `auth.refresh_failed` events. No credential material in any log.                                                        | `687a8c4` |
| `app/api/v1/endpoints/providers.py`  | Added `security.key_revealed` audit log in `reveal_provider_key()`.                                                                                                                                                       | `687a8c4` |
| `app/services/project_extraction.py` | Replaced 3 silent `except` blocks with explicit `logger.error` calls: `extraction.scope_max_pages_failed`, `extraction.quality_computation_failed`. Added `set_task_context(project_id, user_id)` after project DB fetch. | `687a8c4` |
| `app/services/frontierpreview.py`    | Replaced silent `except Exception` in fetch with `logger.error("frontier.fetch_failed")`. Added `set_task_context(project_id, user_id)` in `create_frontier_preview`.                                                     | `687a8c4` |

### Layer 3 — Coverage

| File                                 | Change                                                                                                                                                                                                       | Commit    |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------- |
| `app/services/crawl_scope.py`        | Added `scope.classified` INFO, `scope.url_excluded` DEBUG, `scope.confirmation_gate_passed` INFO, `scope.confirmation_required` WARNING.                                                                     | `7f1073c` |
| `app/services/project_extraction.py` | Added `extraction.page_robots_blocked` WARNING, `extraction.page_failed` ERROR, `extraction.records_extracted` DEBUG, `extraction.quality_computed` INFO.                                                    | `4379c2c` |
| `app/services/frontierpreview.py`    | Added `frontier.fetch_started` DEBUG, `frontier.preview_built` INFO, `frontier.high_exclusion_rate` WARNING.                                                                                                 | `4379c2c` |
| `app/services/project_preview.py`    | Added `preview.started` DEBUG, `preview.completed` INFO, `preview.selector_failed` WARNING.                                                                                                                  | `4379c2c` |
| `app/services/watchdog.py`           | Added `watchdog.sweep_started` DEBUG, `watchdog.task_reset` INFO (per-reset with `task_id`, `old_state`, `timeout_category`), `watchdog.job_reset` INFO, `watchdog.sweep_completed` INFO with `duration_ms`. | `4379c2c` |
| `app/core/scheduler.py`              | Added `_timed_watchdog()` async wrapper with `scheduler.job_started` DEBUG and `scheduler.job_completed` DEBUG with `duration_ms`.                                                                           | `4379c2c` |
| `app/api/v1/endpoints/projects.py`   | Added `export.started` INFO, `export.completed` INFO (with `record_count`, `duration_ms`), `export.failed` ERROR.                                                                                            | `4379c2c` |

---

## 3. New Files

| File                                     | Lines | Purpose                                       |
| ---------------------------------------- | ----- | --------------------------------------------- |
| `app/core/log_context.py`                | 88    | Context variable bindings for log correlation |
| `app/core/logging_config.py`             | 243   | Logging configuration, formatters, filters    |
| `tests/core/test_log_context.py`         | 62    | Unit tests for log_context module             |
| `tests/core/test_logging_config.py`      | 323   | Unit tests for logging_config module          |
| `tests/core/test_logging_integration.py` | 440   | Integration tests for all logging events      |

---

## 4. Modified Files

| File                                 | Changes                                                                      |
| ------------------------------------ | ---------------------------------------------------------------------------- |
| `app/main.py`                        | Added `configure_logging()` call, request context middleware, replaced print |
| `app/api/deps.py`                    | Added `bind_user_id()` after JWT decode                                      |
| `app/db/database.py`                 | Set `echo=False`                                                             |
| `app/api/v1/endpoints/auth.py`       | Added 5 auth event logs                                                      |
| `app/api/v1/endpoints/providers.py`  | Added key reveal audit log                                                   |
| `app/services/project_extraction.py` | Fixed 3 silent excepts, added 4 coverage events, added task context binding  |
| `app/services/frontierpreview.py`    | Fixed silent except, added 3 coverage events, added task context binding     |
| `app/services/crawl_scope.py`        | Added 4 scope classification events                                          |
| `app/services/project_preview.py`    | Added 3 preview events                                                       |
| `app/services/watchdog.py`           | Added per-reset and sweep timing events                                      |
| `app/core/scheduler.py`              | Added timed watchdog wrapper with job timing                                 |
| `app/api/v1/endpoints/projects.py`   | Added export event logging                                                   |

---

## 5. Test Results

```
$ venv\Scripts\python.exe -m pytest tests/ -x --tb=short -q
299 passed, 43 warnings in 8.66s
```

**New test files:**

- `tests/core/test_log_context.py` — 9 tests (all pass)
- `tests/core/test_logging_config.py` — 22 tests (all pass)
- `tests/core/test_logging_integration.py` — 31 tests (all pass)

**Total new tests: 62**

No regressions in existing test suite.

---

## 6. Log Event Catalog

| Event                                   | Level   | Source                | Key Fields                                             |
| --------------------------------------- | ------- | --------------------- | ------------------------------------------------------ |
| `auth.register_success`                 | INFO    | auth.py               | user_id, email                                         |
| `auth.login_success`                    | INFO    | auth.py               | user_id, email                                         |
| `auth.login_failed`                     | WARNING | auth.py               | email, reason                                          |
| `auth.refresh_success`                  | INFO    | auth.py               | user_id                                                |
| `auth.refresh_failed`                   | WARNING | auth.py               | reason                                                 |
| `security.key_revealed`                 | INFO    | providers.py          | user_id, provider_id, provider_name                    |
| `scope.classified`                      | INFO    | crawl_scope.py        | scope_mode, included_count, excluded_count             |
| `scope.url_excluded`                    | DEBUG   | crawl_scope.py        | url, reason_code                                       |
| `scope.confirmation_gate_passed`        | INFO    | crawl_scope.py        | scope_mode                                             |
| `scope.confirmation_required`           | WARNING | crawl_scope.py        | scope_mode, scope_status                               |
| `frontier.fetch_started`                | DEBUG   | frontierpreview.py    | project_id, url                                        |
| `frontier.fetch_failed`                 | ERROR   | frontierpreview.py    | project_id, url, error_type                            |
| `frontier.preview_built`                | INFO    | frontierpreview.py    | project_id, scope_mode, included_count, excluded_count |
| `frontier.high_exclusion_rate`          | WARNING | frontierpreview.py    | project_id, excluded_pct                               |
| `preview.started`                       | DEBUG   | project_preview.py    | project_id                                             |
| `preview.completed`                     | INFO    | project_preview.py    | project_id, record_count, selector_hit_rate            |
| `preview.selector_failed`               | WARNING | project_preview.py    | project_id, field_name, selector                       |
| `project_extraction.started`            | INFO    | project_extraction.py | project_id, spec_id                                    |
| `project_extraction.completed`          | INFO    | project_extraction.py | project_id, records, pages                             |
| `project_extraction.canceled`           | INFO    | project_extraction.py | project_id                                             |
| `project_extraction.failed`             | ERROR   | project_extraction.py | project_id, error                                      |
| `project_extraction.scope_unconfirmed`  | ERROR   | project_extraction.py | project_id, error                                      |
| `project_extraction.missing_state`      | ERROR   | project_extraction.py | project_id, spec_id                                    |
| `extraction.scope_max_pages_failed`     | ERROR   | project_extraction.py | project_id, error_type                                 |
| `extraction.quality_computation_failed` | ERROR   | project_extraction.py | project_id, error_type                                 |
| `extraction.page_robots_blocked`        | WARNING | project_extraction.py | project_id, page_id, url                               |
| `extraction.page_failed`                | ERROR   | project_extraction.py | project_id, page_id, url, error_type                   |
| `extraction.records_extracted`          | DEBUG   | project_extraction.py | project_id, page_id, record_count, warnings_count      |
| `extraction.quality_computed`           | INFO    | project_extraction.py | project_id, quality_label, field_count                 |
| `export.started`                        | INFO    | projects.py           | project_id, user_id, format                            |
| `export.completed`                      | INFO    | projects.py           | project_id, format, record_count, duration_ms          |
| `export.failed`                         | ERROR   | projects.py           | project_id, format, error_type                         |
| `watchdog.sweep_started`                | DEBUG   | watchdog.py           | timestamp                                              |
| `watchdog.task_reset`                   | INFO    | watchdog.py           | task_id, old_state, timeout_category                   |
| `watchdog.job_reset`                    | INFO    | watchdog.py           | job_id, old_state                                      |
| `watchdog.sweep_completed`              | INFO    | watchdog.py           | tasks_reset, jobs_reset, duration_ms                   |
| `watchdog.error`                        | ERROR   | watchdog.py           | error                                                  |
| `scheduler.configured`                  | INFO    | scheduler.py          | jobs                                                   |
| `scheduler.started`                     | INFO    | scheduler.py          | —                                                      |
| `scheduler.stopped`                     | INFO    | scheduler.py          | —                                                      |
| `scheduler.job_started`                 | DEBUG   | scheduler.py          | job_name                                               |
| `scheduler.job_completed`               | DEBUG   | scheduler.py          | job_name, duration_ms                                  |

---

## 7. Security Guarantees

- **No credential material in logs:** Auth events log `user_id` and `email` only; passwords and tokens are never logged.
- **Secret redaction backstop:** `SecretRedactingFilter` strips API key patterns (`sk-...`, `key-...`, etc.) from all log messages using `redact_provider_secret()`.
- **Key reveal audit trail:** `security.key_revealed` event records who revealed which provider key, without including the key value itself.
- **No extracted content in logs:** Extraction events log counts and labels only; raw field values and record content stay in the database.

---

## 8. Configuration

| Setting      | Values                                          | Default | Source  |
| ------------ | ----------------------------------------------- | ------- | ------- |
| `LOG_FORMAT` | `text`, `json`                                  | `text`  | env var |
| `LOG_LEVEL`  | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | `INFO`  | env var |

- `text` format: human-readable DevFormatter (ISO timestamp, fixed-width level, shortened logger name, key=value extras)
- `json` format: one JSON object per line, parseable by any log aggregator

---

## 9. Deferred Work

The following items from the plan were intentionally deferred (per §9.1 overengineering risk):

- **SSE progress stream logging** (§8.5) — not yet implemented; will add when SSE is production-critical
- **Worker process context propagation** (§8.4) — single-worker deployment currently; revisit when multi-worker
- **Extraction quality systems** (§8.6) — future feature, not current scope

---

## 10. Risks & Assumptions

| Risk                                           | Mitigation                                                                                                                                |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Per-URL DEBUG logs could flood in large crawls | Plan explicitly sets per-URL decisions at DEBUG level; aggregate scope decisions at INFO. A 500-page crawl produces 1 INFO line, not 500. |
| `configure_logging()` called twice             | Idempotent by design — same result on repeated calls                                                                                      |
| Context propagation across async tasks         | `contextvars` propagate automatically in Python 3.7+ async; `set_task_context` is called at the start of each background task             |
| SQLAlchemy echo noise                          | Set `echo=False` on session factory; SQLAlchemy engine logger set to WARNING in `configure_logging()`                                     |

---

## 11. Commits on `feature/logging-observability`

```
137e093 fix(tests): correct redaction marker and exception info test assertions
4120ead test(logging): add unit tests for log_context, logging_config, and integration event tests
4379c2c feat(logging): Layer 3 coverage -- per-page, quality, frontier, preview, watchdog, scheduler, export events
7f1073c Layer 3 coverage: add structured logging events across services
687a8c4 feat(logging): Layer 2 — security and correctness
a47af80 feat(logging): Layer 1 — logging infrastructure
855c94e chore: pre-logging implementation checkpoint
```
