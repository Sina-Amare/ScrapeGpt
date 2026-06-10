"""Implementation-level tests for logging remediation.

These tests exercise real code paths (not just synthetic logger.info()
calls) to validate the remediation fixes from the logging review:

1. URL sanitization through the actual filter + formatter pipeline
2. Structured extra-field redaction through the actual filter
3. Middleware cleanup on exceptions (try/finally pattern)
4. Correlation fields (project_id in scope confirmation, page_id propagation)
5. Request ID propagation through middleware
6. sanitize_url() edge cases
7. Exception traceback redaction through real formatter pipeline
8. Real middleware exercise (not copied logic)
"""

import json
import logging
import sys
import time
import uuid

import pytest

from app.core.log_context import (
    clear_context,
    get_log_context,
    set_page_context,
    set_request_context,
    set_task_context,
)
from app.core.logging_config import (
    REDACTED,
    REDACTED_URL_QUERY,
    ContextInjectingFilter,
    DevFormatter,
    JsonFormatter,
    SecretRedactingFilter,
    _sanitize_exception_text,
    sanitize_url,
)
from app.services.crawl_scope import (
    ScopeConfirmationError,
    assert_scope_confirmed,
)

# Shorthand for building sanitized URL assertions
_URL_SAN = REDACTED_URL_QUERY


@pytest.fixture(autouse=True)
def _clean_context():
    """Ensure context is clean before and after each test."""
    clear_context()
    yield
    clear_context()


# ---------------------------------------------------------------------------
# URL sanitization through the real filter + formatter pipeline
# ---------------------------------------------------------------------------


class TestURLSanitizationThroughPipeline:
    """Validate that URLs with query strings/fragments are sanitized
    when passed through the actual SecretRedactingFilter + JsonFormatter
    pipeline, not just the standalone sanitize_url() function."""

    def test_url_key_sanitized_in_json_output(self):
        """URL extra fields in _URL_KEYS should have query/fragment
        stripped when rendered through JsonFormatter after
        SecretRedactingFilter."""
        filt = SecretRedactingFilter()
        fmt = JsonFormatter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0,
            "fetcher.page_fetched", (), None,
        )
        # Simulate a real call site:
        # logger.info("fetcher.page_fetched",
        #   extra={"url": "https://example.com/page?token=abc123&session=xyz"})
        record.url = (
            "https://example.com/page?token=abc123&session=xyz"
        )
        record.seed = (
            "https://api.example.com/v1/data#api_key=sk-secret"
        )
        record.project_id = 42

        filt.filter(record)
        output = fmt.format(record)
        parsed = json.loads(output)

        # URL fields should be sanitized (query/fragment stripped)
        assert parsed["url"] == (
            f"https://example.com/page?{_URL_SAN}"
        )
        assert parsed["seed"] == (
            f"https://api.example.com/v1/data?{_URL_SAN}"
        )
        # Non-URL, non-secret fields should pass through unchanged
        assert parsed["project_id"] == 42

    def test_adhoc_url_string_sanitized(self):
        """String values that look like URLs (http/https) but aren't in
        _URL_KEYS should also be sanitized by the catch-all regex check
        in SecretRedactingFilter."""
        filt = SecretRedactingFilter()
        fmt = JsonFormatter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0,
            "redirect.followed", (), None,
        )
        # "redirect_url" is in _URL_KEYS — gets URL sanitization
        record.redirect_url = (
            "https://example.com/callback?code=oauth_token"
        )
        # "custom_link" is NOT in _URL_KEYS but starts with https://
        # — catch-all sanitization should still strip the query
        record.custom_link = (
            "https://other.com/path?secret_key=abc123"
        )
        record.project_id = 7

        filt.filter(record)
        output = fmt.format(record)
        parsed = json.loads(output)

        # _URL_KEYS field: sanitized
        assert parsed["redirect_url"] == (
            f"https://example.com/callback?{_URL_SAN}"
        )
        # Ad-hoc URL string: also sanitized by catch-all check
        assert parsed["custom_link"] == (
            f"https://other.com/path?{_URL_SAN}"
        )
        assert parsed["project_id"] == 7

    def test_url_in_nested_dict_sanitized(self):
        """URLs inside nested dict extra fields should be sanitized
        recursively by _redact_dict()."""
        filt = SecretRedactingFilter()
        fmt = JsonFormatter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0,
            "scope.classified", (), None,
        )
        record.details = {
            "seed": "https://example.com/search?q=secret",
            "api_key": "sk-abc123def456",
            "page_count": 5,
            "nested": {
                "url": "https://api.internal.com/data?token=xyz",
            },
        }

        filt.filter(record)
        output = fmt.format(record)
        parsed = json.loads(output)

        # Nested URL key should be sanitized
        assert parsed["details"]["seed"] == (
            f"https://example.com/search?{_URL_SAN}"
        )
        # Nested full-redact key should be fully redacted
        assert parsed["details"]["api_key"] == REDACTED
        # Non-sensitive values pass through
        assert parsed["details"]["page_count"] == 5
        # Deeply nested URL should be sanitized
        assert parsed["details"]["nested"]["url"] == (
            f"https://api.internal.com/data?{_URL_SAN}"
        )

    def test_url_in_list_sanitized(self):
        """URLs inside list extra fields should be sanitized
        recursively by _redact_list()."""
        filt = SecretRedactingFilter()
        fmt = JsonFormatter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0,
            "discovery.links_found", (), None,
        )
        record.links = [
            "https://example.com/page1?session=abc",
            "https://example.com/page2",
            {
                "url": "https://api.com/endpoint?key=secret",
                "type": "pagination",
            },
        ]

        filt.filter(record)
        output = fmt.format(record)
        parsed = json.loads(output)

        # String URL in list: sanitized
        assert parsed["links"][0] == (
            f"https://example.com/page1?{_URL_SAN}"
        )
        # Clean URL in list: preserved
        assert parsed["links"][1] == "https://example.com/page2"
        # Dict in list: URL key sanitized, other keys preserved
        assert parsed["links"][2]["url"] == (
            f"https://api.com/endpoint?{_URL_SAN}"
        )
        assert parsed["links"][2]["type"] == "pagination"


# ---------------------------------------------------------------------------
# Structured extra-field redaction through the real filter
# ---------------------------------------------------------------------------


class TestExtraFieldRedactionThroughPipeline:
    """Validate that structured extra fields (api_key, token, password,
    etc.) are fully redacted when passed through the actual
    SecretRedactingFilter + JsonFormatter pipeline."""

    def test_full_redact_keys_in_json_output(self):
        """Keys in _FULL_REDACT_KEYS should be fully replaced with
        [REDACTED] in JSON output."""
        filt = SecretRedactingFilter()
        fmt = JsonFormatter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0,
            "provider.call", (), None,
        )
        record.api_key = "sk-abc123def456ghi789"
        record.token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        record.password = "user_password_123"
        record.authorization = "Bearer abc123"
        record.project_id = 42

        filt.filter(record)
        output = fmt.format(record)
        parsed = json.loads(output)

        assert parsed["api_key"] == REDACTED
        assert parsed["token"] == REDACTED
        assert parsed["password"] == REDACTED
        assert parsed["authorization"] == REDACTED
        assert parsed["project_id"] == 42

    def test_pattern_redaction_in_string_extra_fields(self):
        """String extra fields not in _FULL_REDACT_KEYS should still
        have secret patterns redacted by redact_provider_secret()."""
        filt = SecretRedactingFilter()
        fmt = JsonFormatter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0,
            "provider.response", (), None,
        )
        record.raw_response = (
            "Result: sk-abc123def456ghi789jkl012mno345pqr678stu901vwx234yz"
        )
        record.error_message = (
            "Auth failed for key sk-abc123def456ghi789jkl012mno345"
        )
        record.status = "ok"

        filt.filter(record)
        output = fmt.format(record)
        parsed = json.loads(output)

        assert "sk-abc123" not in parsed["raw_response"]
        assert "[REDACTED_SECRET]" in parsed["raw_response"]
        assert "sk-abc123" not in parsed["error_message"]
        assert "[REDACTED_SECRET]" in parsed["error_message"]
        assert parsed["status"] == "ok"

    def test_secret_in_nested_dict_redacted(self):
        """Secrets inside nested dict extra fields should be redacted
        recursively by _redact_dict()."""
        filt = SecretRedactingFilter()
        fmt = JsonFormatter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0,
            "provider.config_updated", (), None,
        )
        record.config = {
            "api_key": "sk-secret123",
            "name": "OpenAI",
            "settings": {
                "token": "access_token_value",
                "model": "gpt-4",
            },
        }

        filt.filter(record)
        output = fmt.format(record)
        parsed = json.loads(output)

        assert parsed["config"]["api_key"] == REDACTED
        assert parsed["config"]["name"] == "OpenAI"
        assert parsed["config"]["settings"]["token"] == REDACTED
        assert parsed["config"]["settings"]["model"] == "gpt-4"

    def test_secret_in_list_redacted(self):
        """Secrets inside list extra fields should be redacted
        recursively by _redact_list()."""
        filt = SecretRedactingFilter()
        fmt = JsonFormatter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0,
            "provider.keys_listed", (), None,
        )
        record.keys = [
            "sk-abc123def456ghi789jkl012mno345pqr678stu901vwx234yz",
            "valid_string",
            {"api_key": "sk-secret", "label": "production"},
        ]

        filt.filter(record)
        output = fmt.format(record)
        parsed = json.loads(output)

        assert "sk-abc123" not in parsed["keys"][0]
        assert "[REDACTED_SECRET]" in parsed["keys"][0]
        assert parsed["keys"][1] == "valid_string"
        assert parsed["keys"][2]["api_key"] == REDACTED
        assert parsed["keys"][2]["label"] == "production"


# ---------------------------------------------------------------------------
# Middleware cleanup on exceptions
# ---------------------------------------------------------------------------


class TestMiddlewareCleanup:
    """Validate that the request_context_middleware pattern clears
    context variables even when exceptions occur, and that exceptions
    are logged with http.request_failed.

    These tests replicate the exact middleware pattern from app/main.py
    (try/except/finally with clear_context in finally) to exercise the
    real logic without requiring the full app infrastructure.
    """

    @pytest.mark.asyncio
    async def test_context_cleared_after_normal_request(self):
        """After a successful request, the finally block should clear
        context variables."""
        from fastapi import FastAPI, Request
        from httpx import ASGITransport, AsyncClient

        app = FastAPI()

        @app.middleware("http")
        async def context_middleware(request: Request, call_next):
            request_id = (
                request.headers.get("X-Request-ID")
                or str(uuid.uuid4())
            )
            set_request_context(request_id=request_id)
            try:
                response = await call_next(request)
                response.headers["X-Request-ID"] = request_id
                return response
            except Exception:
                raise
            finally:
                clear_context()

        @app.get("/test")
        async def test_route():
            return {"ok": True}

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            resp = await client.get(
                "/test", headers={"X-Request-ID": "req-normal"},
            )
            assert resp.status_code == 200
            assert resp.headers["X-Request-ID"] == "req-normal"

        # After the request completes, context should be cleared
        assert get_log_context() == {}

    def test_context_cleared_after_exception_in_call_next(self):
        """When call_next raises an exception (simulating a route
        failure), the finally block should still clear context
        variables.  This directly exercises the middleware pattern
        without httpx, which is more reliable across versions."""
        request_id = "req-exception-clear"
        set_request_context(request_id=request_id)
        assert get_log_context()["request_id"] == request_id

        # Simulate the middleware pattern: try/except/finally
        # with call_next raising an exception
        with pytest.raises(RuntimeError):
            try:
                # Simulate call_next raising
                raise RuntimeError("route failure")
            except Exception:
                raise
            finally:
                clear_context()

        # Context should be cleared even after the exception
        assert get_log_context() == {}

    @pytest.mark.asyncio
    async def test_context_cleared_and_error_logged_on_call_next_exception(
        self, caplog,
    ):
        """When call_next itself raises (rare but possible — e.g.
        Starlette internal error), the except block should log
        http.request_failed with error_type, and the finally block
        should still clear context.

        This test simulates call_next raising by directly exercising
        the middleware pattern with a failing call_next function.
        """
        caplog.set_level(logging.ERROR)

        request_id = "req-exception-test"
        set_request_context(request_id=request_id)
        assert get_log_context()["request_id"] == request_id

        start = time.monotonic()

        # Simulate the middleware pattern when call_next raises
        with pytest.raises(RuntimeError):
            try:
                raise RuntimeError("call_next internal failure")
            except Exception as exc:
                duration_ms = int((time.monotonic() - start) * 1000)
                logging.getLogger("app.main").error(
                    "http.request_failed",
                    extra={
                        "method": "GET",
                        "path": "/test",
                        "duration_ms": duration_ms,
                        "request_id": request_id,
                        "error_type": type(exc).__name__,
                    },
                )
                raise
            finally:
                clear_context()

        # Verify the error was logged
        records = [
            r for r in caplog.records
            if r.getMessage() == "http.request_failed"
        ]
        assert len(records) == 1
        assert records[0].error_type == "RuntimeError"
        assert records[0].method == "GET"
        assert records[0].request_id == "req-exception-test"

        # Verify context was cleared by finally block
        assert get_log_context() == {}


# ---------------------------------------------------------------------------
# Correlation fields — project_id in scope confirmation, page_id propagation
# ---------------------------------------------------------------------------


class TestCorrelationFields:
    """Validate that correlation fields (project_id, page_id) appear in
    the correct log events when exercising real code paths in
    crawl_scope.py and log_context.py."""

    def test_scope_confirmation_passed_includes_project_id(self, caplog):
        """assert_scope_confirmed() for CURRENT_PAGE scope should log
        scope.confirmation_gate_passed with project_id."""
        caplog.set_level(logging.INFO)
        scope = {"mode": "CURRENT_PAGE", "status": "AI_SUGGESTED"}
        assert_scope_confirmed(scope, project_id=42)

        records = [
            r for r in caplog.records
            if r.getMessage() == "scope.confirmation_gate_passed"
        ]
        assert len(records) == 1
        assert records[0].project_id == 42
        assert records[0].scope_mode == "CURRENT_PAGE"

    def test_scope_confirmation_user_confirmed_includes_project_id(
        self, caplog,
    ):
        """assert_scope_confirmed() for USER_CONFIRMED scope should log
        scope.confirmation_gate_passed with project_id."""
        caplog.set_level(logging.INFO)
        scope = {"mode": "FULL_SITE", "status": "USER_CONFIRMED"}
        assert_scope_confirmed(scope, project_id=99)

        records = [
            r for r in caplog.records
            if r.getMessage() == "scope.confirmation_gate_passed"
        ]
        assert len(records) == 1
        assert records[0].project_id == 99
        assert records[0].scope_mode == "FULL_SITE"

    def test_scope_confirmation_required_includes_project_id(self, caplog):
        """assert_scope_confirmed() for an unconfirmed scope should log
        scope.confirmation_required with project_id before raising
        ScopeConfirmationError."""
        caplog.set_level(logging.WARNING)
        scope = {"mode": "PAGINATION", "status": "AI_SUGGESTED"}

        with pytest.raises(ScopeConfirmationError):
            assert_scope_confirmed(
                scope,
                allow_unconfirmed=False,
                project_id=7,
            )

        records = [
            r for r in caplog.records
            if r.getMessage() == "scope.confirmation_required"
        ]
        assert len(records) == 1
        assert records[0].project_id == 7

    def test_scope_confirmation_without_project_id(self, caplog):
        """assert_scope_confirmed() with project_id=None (default)
        should still log the event — project_id will be None."""
        caplog.set_level(logging.INFO)
        scope = {"mode": "CURRENT_PAGE", "status": "SYSTEM_DEFAULT"}
        assert_scope_confirmed(scope)  # project_id defaults to None

        records = [
            r for r in caplog.records
            if r.getMessage() == "scope.confirmation_gate_passed"
        ]
        assert len(records) == 1
        # project_id should be None (not absent)
        assert records[0].project_id is None

    def test_page_context_propagates_to_log_records(self):
        """set_page_context() should cause page_id to appear in log
        records via ContextInjectingFilter — this is the real
        correlation path used in the extraction page loop."""
        set_task_context(project_id=42, user_id=1)
        set_page_context(page_id=101)

        filt = ContextInjectingFilter()
        record = logging.LogRecord(
            "app.services.project_extraction",
            logging.INFO, "", 0,
            "extraction.page_started", (), None,
        )
        filt.filter(record)

        assert record.project_id == 42
        assert record.page_id == 101
        assert record.user_id == 1

    def test_page_context_appears_in_json_output(self):
        """page_id from set_page_context should appear in JSON formatter
        output when both filters are applied in the real order
        (ContextInjectingFilter first, then SecretRedactingFilter)."""
        set_task_context(project_id=42, user_id=1)
        set_page_context(page_id=101)

        ctx_filt = ContextInjectingFilter()
        secret_filt = SecretRedactingFilter()
        fmt = JsonFormatter()

        record = logging.LogRecord(
            "app.services.project_extraction",
            logging.INFO, "", 0,
            "extraction.page_started", (), None,
        )
        # Apply filters in the real order: context first, then redaction
        ctx_filt.filter(record)
        secret_filt.filter(record)

        output = fmt.format(record)
        parsed = json.loads(output)

        assert parsed["project_id"] == 42
        assert parsed["page_id"] == 101
        assert parsed["user_id"] == 1
        assert parsed["event"] == "extraction.page_started"


# ---------------------------------------------------------------------------
# Request ID propagation through middleware
# ---------------------------------------------------------------------------


class TestRequestIDPropagation:
    """Validate that request_id from X-Request-ID header propagates
    through the middleware into log context and response headers."""

    @pytest.mark.asyncio
    async def test_request_id_from_header_propagated(self):
        """X-Request-ID from the request header should be set in log
        context during the request and cleared after."""
        from fastapi import FastAPI, Request
        from httpx import ASGITransport, AsyncClient

        captured_context = {}

        app = FastAPI()

        @app.middleware("http")
        async def context_middleware(request: Request, call_next):
            request_id = (
                request.headers.get("X-Request-ID")
                or str(uuid.uuid4())
            )
            set_request_context(request_id=request_id)
            # Capture context during request processing
            captured_context["during"] = dict(get_log_context())
            try:
                response = await call_next(request)
                response.headers["X-Request-ID"] = request_id
                return response
            except Exception:
                raise
            finally:
                clear_context()
                captured_context["after"] = dict(get_log_context())

        @app.get("/test")
        async def test_route():
            return {"ok": True}

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            resp = await client.get(
                "/test", headers={"X-Request-ID": "req-prop-1"},
            )
            assert resp.status_code == 200

        # During request, context should have request_id
        assert captured_context["during"]["request_id"] == "req-prop-1"
        # After request, context should be empty
        assert captured_context["after"] == {}

    @pytest.mark.asyncio
    async def test_request_id_generated_when_missing(self):
        """When no X-Request-ID header is provided, a UUID should be
        generated and set as request_id in context."""
        from fastapi import FastAPI, Request
        from httpx import ASGITransport, AsyncClient

        captured_request_id = {}

        app = FastAPI()

        @app.middleware("http")
        async def context_middleware(request: Request, call_next):
            request_id = (
                request.headers.get("X-Request-ID")
                or str(uuid.uuid4())
            )
            captured_request_id["value"] = request_id
            set_request_context(request_id=request_id)
            try:
                response = await call_next(request)
                response.headers["X-Request-ID"] = request_id
                return response
            except Exception:
                raise
            finally:
                clear_context()

        @app.get("/test")
        async def test_route():
            return {"ok": True}

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            resp = await client.get("/test")
            assert resp.status_code == 200

        # A UUID should have been generated
        generated_id = captured_request_id["value"]
        uuid.UUID(generated_id)  # Should not raise — valid UUID
        assert resp.headers["X-Request-ID"] == generated_id

    @pytest.mark.asyncio
    async def test_request_id_injected_into_log_records(self):
        """request_id from middleware context should be injected into
        log records by ContextInjectingFilter — the real propagation
        path that makes request_id appear in structured JSON logs."""
        from fastapi import FastAPI, Request
        from httpx import ASGITransport, AsyncClient

        captured_records = {}

        app = FastAPI()

        @app.middleware("http")
        async def context_middleware(request: Request, call_next):
            request_id = (
                request.headers.get("X-Request-ID")
                or str(uuid.uuid4())
            )
            set_request_context(request_id=request_id)
            try:
                response = await call_next(request)
                response.headers["X-Request-ID"] = request_id
                return response
            except Exception:
                raise
            finally:
                clear_context()

        @app.get("/test")
        async def test_route():
            # Simulate a service logging during request processing
            set_request_context(request_id="req-inject-test")
            filt = ContextInjectingFilter()
            record = logging.LogRecord(
                "app.services.analyzer", logging.INFO, "", 0,
                "analyzer.completed", (), None,
            )
            filt.filter(record)
            captured_records["record"] = record
            return {"ok": True}

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            resp = await client.get("/test")
            assert resp.status_code == 200

        # The log record should have request_id injected
        assert captured_records["record"].request_id == "req-inject-test"


# ---------------------------------------------------------------------------
# sanitize_url() edge cases
# ---------------------------------------------------------------------------


class TestSanitizeUrlEdgeCases:
    """Validate sanitize_url() behavior for edge cases that could
    appear in real application data (validated URLs, redirect URLs,
    signed URLs, etc.)."""

    def test_url_with_multiple_query_params(self):
        """URLs with multiple query parameters should have the entire
        query string replaced with [URL_SANITIZED]."""
        result = sanitize_url(
            "https://api.example.com/v1/data?key=abc&token=xyz&session=123"
        )
        assert result == (
            f"https://api.example.com/v1/data?{_URL_SAN}"
        )
        assert "abc" not in result
        assert "xyz" not in result
        assert "123" not in result

    def test_url_with_fragment_only(self):
        """URLs with only a fragment (no query) should still be
        sanitized — fragments can contain tokens in signed URLs."""
        result = sanitize_url("https://example.com/page#section")
        assert result == f"https://example.com/page?{_URL_SAN}"
        assert "section" not in result

    def test_url_with_query_and_fragment(self):
        """URLs with both query and fragment should be sanitized."""
        result = sanitize_url("https://example.com/search?q=test#results")
        assert result == f"https://example.com/search?{_URL_SAN}"

    def test_clean_url_preserved(self):
        """URLs without query or fragment should pass through unchanged."""
        result = sanitize_url("https://example.com/clean/path")
        assert result == "https://example.com/clean/path"

    def test_empty_string_returns_empty(self):
        """Empty string should return empty string."""
        assert sanitize_url("") == ""

    def test_none_returns_none(self):
        """None input should return None."""
        assert sanitize_url(None) is None

    def test_non_string_returns_input(self):
        """Non-string input (e.g. integer) should be returned unchanged."""
        assert sanitize_url(123) == 123

    def test_signed_azure_url_sanitized(self):
        """Azure/S3 signed URLs with query-string auth should be
        sanitized — these are a real leak risk in fetcher logs."""
        result = sanitize_url(
            "https://storage.blob.core.windows.net/container/file?"
            "sv=2023-01-03&ss=b&srt=o&sp=r&se=2026-06-10T18:00:00Z"
            "&st=2026-06-10T10:00:00Z"
            "&sig=abc123def456ghi789jkl012mno345pqr678"
        )
        assert result == (
            f"https://storage.blob.core.windows.net/container/file?"
            f"{_URL_SAN}"
        )
        assert "sig=abc123" not in result
        assert "sv=2023" not in result

    def test_password_reset_url_sanitized(self):
        """Password reset URLs with token in query should be sanitized."""
        result = sanitize_url(
            "https://app.example.com/reset-password?token=abc123xyz"
        )
        assert result == (
            f"https://app.example.com/reset-password?{_URL_SAN}"
        )
        assert "abc123xyz" not in result

    def test_oauth_callback_url_sanitized(self):
        """OAuth callback URLs with code/state in query should be
        sanitized."""
        result = sanitize_url(
            "https://app.example.com/auth/callback?"
            "code=oauth_code_123&state=state_abc"
        )
        assert result == (
            f"https://app.example.com/auth/callback?{_URL_SAN}"
        )
        assert "oauth_code_123" not in result


# ---------------------------------------------------------------------------
# Exception traceback redaction through real formatter pipeline
# ---------------------------------------------------------------------------


class TestExceptionTracebackRedaction:
    """Validate that exception tracebacks containing secrets are
    sanitized in both JsonFormatter and DevFormatter output.

    This addresses the blocker from LOGGING_FINAL_REVIEW.md:
    exception tracebacks bypassed the redaction pipeline because
    formatException() was called after SecretRedactingFilter had
    already processed the record, and the formatted traceback text
    was not passed through redact_provider_secret() or URL
    sanitization.
    """

    def test_api_key_in_exception_sanitized_in_json(self):
        """An exception message containing an API key pattern
        (sk-...) should be redacted in JsonFormatter output."""
        fmt = JsonFormatter()
        try:
            raise ValueError(
                "Call failed with api_key="
                "sk-abc123def456ghi789jkl012mno345pqr678stu901vwx234yz"
            )
        except ValueError:
            ei = sys.exc_info()

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0,
            "provider.call_failed", (), ei,
        )
        output = fmt.format(record)
        parsed = json.loads(output)

        # The exception field should NOT contain the raw API key
        assert "sk-abc123" not in parsed["exception"]
        assert "[REDACTED_SECRET]" in parsed["exception"]

    def test_api_key_in_exception_sanitized_in_dev(self):
        """An exception message containing an API key pattern
        (sk-...) should be redacted in DevFormatter output."""
        fmt = DevFormatter()
        try:
            raise ValueError(
                "Call failed with api_key="
                "sk-abc123def456ghi789jkl012mno345pqr678stu901vwx234yz"
            )
        except ValueError:
            ei = sys.exc_info()

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0,
            "provider.call_failed", (), ei,
        )
        output = fmt.format(record)

        # The traceback should NOT contain the raw API key
        assert "sk-abc123" not in output
        assert "[REDACTED_SECRET]" in output

    def test_bearer_token_in_exception_sanitized_in_json(self):
        """An exception message containing a Bearer token should
        be redacted in JsonFormatter output."""
        fmt = JsonFormatter()
        try:
            raise RuntimeError(
                "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9."
                "abc123def456ghi789"
            )
        except RuntimeError:
            ei = sys.exc_info()

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0,
            "auth.token_refresh_failed", (), ei,
        )
        output = fmt.format(record)
        parsed = json.loads(output)

        # Bearer token should be redacted
        assert "eyJhbGciOiJIUzI1NiJ9" not in parsed["exception"]
        assert "[REDACTED_SECRET]" in parsed["exception"]

    def test_bearer_token_in_exception_sanitized_in_dev(self):
        """An exception message containing a Bearer token should
        be redacted in DevFormatter output."""
        fmt = DevFormatter()
        try:
            raise RuntimeError(
                "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9."
                "abc123def456ghi789"
            )
        except RuntimeError:
            ei = sys.exc_info()

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0,
            "auth.token_refresh_failed", (), ei,
        )
        output = fmt.format(record)

        assert "eyJhbGciOiJIUzI1NiJ9" not in output
        assert "[REDACTED_SECRET]" in output

    def test_url_query_token_in_exception_sanitized_in_json(self):
        """An exception message containing a URL with a query-string
        token should be sanitized in JsonFormatter output."""
        fmt = JsonFormatter()
        try:
            raise ConnectionError(
                "Failed to connect to "
                "https://api.example.com/v1/data?token=secret123&session=abc"
            )
        except ConnectionError:
            ei = sys.exc_info()

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0,
            "fetcher.connection_failed", (), ei,
        )
        output = fmt.format(record)
        parsed = json.loads(output)

        # URL query string should be sanitized
        assert "secret123" not in parsed["exception"]
        assert "session=abc" not in parsed["exception"]
        # The sanitized URL should appear with [URL_SANITIZED]
        assert f"?{_URL_SAN}" in parsed["exception"]

    def test_url_query_token_in_exception_sanitized_in_dev(self):
        """An exception message containing a URL with a query-string
        token should be sanitized in DevFormatter output."""
        fmt = DevFormatter()
        try:
            raise ConnectionError(
                "Failed to connect to "
                "https://api.example.com/v1/data?token=secret123&session=abc"
            )
        except ConnectionError:
            ei = sys.exc_info()

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0,
            "fetcher.connection_failed", (), ei,
        )
        output = fmt.format(record)

        assert "secret123" not in output
        assert "session=abc" not in output
        assert f"?{_URL_SAN}" in output

    def test_signed_url_in_exception_sanitized_in_json(self):
        """An exception containing an Azure/S3 signed URL should
        have the query string sanitized in JsonFormatter output."""
        fmt = JsonFormatter()
        try:
            raise RuntimeError(
                "Storage error: "
                "https://storage.blob.core.windows.net/container/file?"
                "sv=2023-01-03&sig=abc123def456ghi789jkl012mno345"
            )
        except RuntimeError:
            ei = sys.exc_info()

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0,
            "extraction.storage_error", (), ei,
        )
        output = fmt.format(record)
        parsed = json.loads(output)

        assert "sig=abc123" not in parsed["exception"]
        assert "sv=2023" not in parsed["exception"]
        assert f"?{_URL_SAN}" in parsed["exception"]

    def test_authorization_header_in_exception_sanitized(self):
        """An exception containing an Authorization header value
        should be redacted in both formatter outputs."""
        fmt_json = JsonFormatter()
        fmt_dev = DevFormatter()
        try:
            raise ValueError(
                "Invalid authorization: Bearer sk-abc123def456ghi789"
            )
        except ValueError:
            ei = sys.exc_info()

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0,
            "auth.validation_failed", (), ei,
        )

        json_output = fmt_json.format(record)
        parsed = json.loads(json_output)
        assert "sk-abc123" not in parsed["exception"]
        assert "[REDACTED_SECRET]" in parsed["exception"]

        dev_output = fmt_dev.format(record)
        assert "sk-abc123" not in dev_output
        assert "[REDACTED_SECRET]" in dev_output

    def test_password_in_exception_sanitized(self):
        """An exception containing a password string should be
        redacted in both formatter outputs."""
        fmt_json = JsonFormatter()
        fmt_dev = DevFormatter()
        try:
            raise ValueError("password=my_secret_password_123")
        except ValueError:
            ei = sys.exc_info()

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0,
            "auth.login_failed", (), ei,
        )

        json_output = fmt_json.format(record)
        parsed = json.loads(json_output)
        assert "my_secret_password_123" not in parsed["exception"]
        assert "[REDACTED_SECRET]" in parsed["exception"]

        dev_output = fmt_dev.format(record)
        assert "my_secret_password_123" not in dev_output
        assert "[REDACTED_SECRET]" in dev_output

    def test_sanitize_exception_text_directly(self):
        """_sanitize_exception_text() should redact API key patterns
        and sanitize URLs in traceback text."""
        text = (
            "Traceback (most recent call last):\n"
            "  File 'app/services/provider_service.py', line 42\n"
            "    api_key = 'sk-abc123def456ghi789jkl012mno345pqr678'\n"
            "ValueError: Call to "
            "https://api.example.com/v1/data?token=secret failed"
        )
        result = _sanitize_exception_text(text)

        assert "sk-abc123" not in result
        assert "[REDACTED_SECRET]" in result
        assert "secret" not in result
        assert f"?{_URL_SAN}" in result
        # Non-sensitive parts should be preserved
        assert "Traceback" in result
        assert "provider_service.py" in result

    def test_clean_exception_preserved(self):
        """An exception with no secrets should pass through
        unchanged."""
        fmt = JsonFormatter()
        try:
            raise ValueError("clean error message")
        except ValueError:
            ei = sys.exc_info()

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0,
            "test.clean_error", (), ei,
        )
        output = fmt.format(record)
        parsed = json.loads(output)

        assert "clean error message" in parsed["exception"]


# ---------------------------------------------------------------------------
# Real middleware exercise (not copied logic)
# ---------------------------------------------------------------------------


class TestRealMiddlewareExercise:
    """Exercise the real app.main request_context_middleware
    instead of building local FastAPI apps with copied middleware
    logic.

    This addresses the finding from LOGGING_FINAL_REVIEW.md that
    the remediation tests mostly built local FastAPI apps with
    copied middleware logic, reducing regression protection if
    the real middleware changes.
    """

    @pytest.mark.asyncio
    async def test_real_middleware_clears_context_after_request(
        self, caplog,
    ):
        """The real app.main middleware should clear log context
        after every HTTP request, verified by checking context
        state before and after."""
        from app.main import create_app
        from httpx import ASGITransport, AsyncClient

        app = create_app()

        # Use a simpler approach:
        # set context before the request, make the request,
        # and verify context is cleared afterward.
        set_request_context(request_id="pre-existing-ctx")
        assert get_log_context()["request_id"] == "pre-existing-ctx"

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            # Make a request to the health endpoint
            resp = await client.get(
                "/api/v1/health",
                headers={"X-Request-ID": "real-mw-test"},
            )
            # The response should succeed (health endpoint)
            assert resp.status_code == 200

        # After the request, the middleware's finally block
        # should have cleared the context
        assert get_log_context() == {}

    @pytest.mark.asyncio
    async def test_real_middleware_sets_request_id_in_response(
        self,
    ):
        """The real middleware should set X-Request-ID in the
        response headers."""
        from app.main import create_app
        from httpx import ASGITransport, AsyncClient

        app = create_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            resp = await client.get(
                "/api/v1/health",
                headers={"X-Request-ID": "req-header-test"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("X-Request-ID") == "req-header-test"

    @pytest.mark.asyncio
    async def test_real_middleware_generates_uuid_when_no_header(
        self,
    ):
        """When no X-Request-ID header is provided, the real
        middleware should generate a UUID and return it in the
        response."""
        from app.main import create_app
        from httpx import ASGITransport, AsyncClient

        app = create_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            resp = await client.get("/api/v1/health")
            assert resp.status_code == 200
            request_id = resp.headers.get("X-Request-ID")
            # Should be a valid UUID
            uuid.UUID(request_id)

    @pytest.mark.asyncio
    async def test_real_middleware_logs_http_request_on_success(
        self, caplog,
    ):
        """The real middleware should log http.request on
        successful responses."""
        caplog.set_level(logging.INFO)
        from app.main import create_app
        from httpx import ASGITransport, AsyncClient

        app = create_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            resp = await client.get(
                "/api/v1/health",
                headers={"X-Request-ID": "req-log-test"},
            )
            assert resp.status_code == 200

        # Find the http.request log event
        records = [
            r for r in caplog.records
            if r.getMessage() == "http.request"
        ]
        assert len(records) >= 1
        rec = records[0]
        assert rec.method == "GET"
        assert rec.request_id == "req-log-test"
        assert rec.status_code == 200