# ScrapGPT Logging and Observability Review

**Date:** 2026-06-10  
**Reviewed documents:**

- `docs/LOGGING_AND_OBSERVABILITY_PLAN.md`
- `docs/LOGGING_IMPLEMENTATION_REPORT.md`

**Reviewed code:** actual backend logging infrastructure, auth/provider/project endpoints, extraction/frontier/scope/preview/watchdog services, and logging tests.

## Verdict

**REJECT**

The implementation is substantial and most planned logging hooks exist, but the security goal is not met yet. The main blocker is that the redaction filter only redacts `record.msg` and `record.args`, while both formatters serialize structured `extra` fields verbatim. Existing log calls put raw user-controlled URLs into `extra`, so tokens, API keys, session IDs, or emails in query strings can leak directly into logs.

## Findings

### blocker - Structured `extra` fields are not redacted, and existing URL logs can leak tokens or secrets

`SecretRedactingFilter` only mutates `record.msg` and `record.args` in `app/core/logging_config.py:61-70`. Both formatters then serialize all non-standard `LogRecord` fields directly: `DevFormatter` emits them as key/value pairs in `app/core/logging_config.py:119-126`, and `JsonFormatter` emits them as top-level JSON fields in `app/core/logging_config.py:166-168`.

This means any secret in `extra={...}` bypasses the redaction backstop. The current code logs raw URLs in structured fields, including `frontier.fetch_started` / `frontier.fetch_failed` (`app/services/frontierpreview.py:204-221`), `extraction.page_robots_blocked` / `extraction.page_failed` (`app/services/project_extraction.py:287-397`), `scope.url_excluded` (`app/services/crawl_scope.py:333-339`), and older fetch/scrape/robots logs. If a submitted URL contains `?token=...`, `?api_key=...`, `?email=...`, signed URLs, password reset links, or session material, that value is logged unredacted.

The plan explicitly required leak prevention for tokens, API keys, secrets, and sensitive URLs. This is a production-blocking logging leak.

### major - Request middleware does not log or clear context on unhandled exceptions

`request_context_middleware` sets request context, awaits `call_next`, then logs `http.request` and calls `clear_context()` in the success path only (`app/main.py:131-152`). There is no `try/finally`.

If `call_next` raises before producing a response, the middleware emits no `http.request` event and does not clear request/user context. That leaves the exact failure path that most needs observability without request-level evidence, and risks context contamination in later work on the same async execution path.

### major - Project/scope correlation is incomplete for the confirmation gate and page loop context

The plan requires scope confirmation events to include `project_id`. `assert_scope_confirmed()` logs `scope.confirmation_required` with only `scope_mode` and `scope_status` (`app/services/crawl_scope.py:216-235`). In `start_project_extraction()`, this is called before any task context is bound (`app/services/project_extraction.py:61-72`), so an extraction blocked by unconfirmed scope is not correlated to `project_id` except indirectly through the HTTP path.

The plan also called for page context propagation via `set_page_context()`. The implementation defines it in `app/core/log_context.py:54-56`, but `project_extraction.py` never imports or calls it. Some page-level events include `page_id` manually, but nested service logs from fetcher/robots/classification cannot inherit page context.

### major - Tests do not actually exercise most implementation paths

The targeted logging tests pass, but `tests/core/test_logging_integration.py` mostly emits log records directly from test code instead of invoking the endpoints/services that are supposed to log. Examples include auth events (`tests/core/test_logging_integration.py:27-113`), provider reveal (`tests/core/test_logging_integration.py:119-143`), extraction events (`tests/core/test_logging_integration.py:146-265`), and frontier/preview/watchdog/export events.

This means tests can pass while the application logs different event names, levels, or fields. There are also stale expectations: the integration tests expect `auth.refresh_success` / `auth.refresh_failed` and email fields, while the actual code logs `auth.token_refresh_success` / `auth.token_refresh_failed` and does not log emails (`app/api/v1/endpoints/auth.py:237-290`). The tests therefore do not verify the implementation described by either the plan or the current code.

### major - JSON formatter does not match the planned schema

The plan's JSON examples and validation strategy require an `event` field. The implementation uses `"message": record.getMessage()` and never sets `"event"` (`app/core/logging_config.py:153-168`). This is not a security problem, but it is a contract mismatch for log consumers and contradicts the plan's stated one-event-per-line structure.

### major - Project endpoint error logging remains incomplete

The plan listed project endpoints as a high-risk blind spot and required HTTP error/background dispatch logging. The implementation added export events only (`app/api/v1/endpoints/projects.py:534-586`). Preview errors, frontier preview errors, extraction state conflicts, scope confirmation HTTP 409s, and analysis dispatch still rely mostly on the generic `http.request` status log or service logs with partial context.

This is improved over the previous state, but it does not match the plan's endpoint coverage goal.

### minor - Implementation report contains inaccurate security/event catalog entries

`docs/LOGGING_IMPLEMENTATION_REPORT.md:104-109` says auth events log emails and `security.key_revealed` is INFO with `provider_id` / `provider_name`. The actual auth code does not log email fields, and provider reveal uses WARNING with `provider_config_id` (`app/api/v1/endpoints/providers.py:163-169`).

The code is better than the report here, but the report is misleading and should be corrected because it describes a less secure contract than the implementation.

### minor - Some planned event details are missing or imprecise

`frontier.fetch_failed` logs `project_id`, `url`, and `error_type`, but not `status_code` as planned (`app/services/frontierpreview.py:212-221`). `preview.selector_failed` logs `selector` as `field.get("label")`, not the actual CSS selector (`app/services/project_preview.py:167-175`). These are not blockers, but they reduce diagnostic value.

### observation - Core infrastructure and many service hooks are present

The implementation does add `log_context.py`, `logging_config.py`, stdout-only logging, text/JSON formatters, context injection, SQLAlchemy `echo=False`, auth/security events, frontier/extraction/scope/preview/watchdog/export events, and explicit logs for the previously silent extraction/frontier exception blocks.

### observation - No obvious current auth email/password/token logging in application code

The actual auth endpoint logs reasons and user IDs only; it does not log submitted emails, passwords, access tokens, or refresh token values. Provider reveal logs do not include plaintext API keys. This does not offset the structured-extra URL leak above.

## Plan Match Summary

**Matches:**

- Stdlib logging with stdout handler and text/JSON modes was implemented.
- `LOG_LEVEL` and `LOG_FORMAT` are wired.
- Request IDs are generated and returned via `X-Request-ID` on successful requests.
- User IDs are bound after successful auth dependency resolution.
- SQLAlchemy echo is disabled.
- Key reveal audit logging exists at WARNING level.
- The named silent exception blocks in extraction/frontier are no longer silent.
- Backend tests pass.

**Does not match:**

- Redaction is not applied to structured extras.
- JSON output lacks the planned `event` field.
- Request middleware lacks `try/finally` cleanup and failure logging.
- Scope confirmation logs do not reliably include `project_id`.
- Page context propagation is defined but unused.
- Project endpoint error/background dispatch logging is incomplete.
- Tests are mostly synthetic log-emission tests rather than implementation tests.

## Security Review Notes

I did not find app code that logs plaintext provider API keys directly. The dangerous path is indirect: user-controlled URLs and any future structured `extra` fields are serialized without redaction. Because this app accepts arbitrary URLs for scraping, raw URL query strings should be treated as potentially sensitive.

## Verification Run

Commands run:

```powershell
venv\Scripts\python.exe -m pytest tests\core\test_logging_config.py tests\core\test_logging_integration.py tests\core\test_log_context.py -q
venv\Scripts\python.exe -m pytest tests\ -x --tb=short -q
```

Results:

- Logging/core targeted tests: `62 passed`
- Full backend tests: `299 passed, 43 warnings`

The passing tests do not clear the findings above because several of the relevant guarantees are not covered by tests that exercise real application paths.

## Required Changes Before Approval

1. Redact/sanitize structured `extra` fields before formatting, especially keys like `url`, `normalized_url`, `source_url`, `error`, `exception`, `api_key`, `authorization`, `token`, `secret`, `password`, and any unknown string value matching secret patterns.
2. Sanitize logged URLs by default: strip query/fragment or redact sensitive query parameters before they reach log records.
3. Wrap `request_context_middleware` in `try/finally`; log failures and always clear context.
4. Ensure scope confirmation and page-loop logs carry `project_id` and page-loop nested logs can inherit `page_id`.
5. Add implementation-level tests that call real endpoints/services and assert emitted records/output, including negative tests for URL query secret redaction and middleware exception cleanup.
6. Align JSON output with the documented `event` field, or update the plan/report and tests to make `"message"` the explicit contract.
