"""Unit tests for app/core/logging_config.py."""

import json
import logging

import pytest

from app.core.log_context import (
    bind_user_id,
    clear_context,
    set_request_context,
    set_task_context,
)
from app.core.logging_config import (
    ContextInjectingFilter,
    DevFormatter,
    JsonFormatter,
    SecretRedactingFilter,
    configure_logging,
)


@pytest.fixture(autouse=True)
def _clean_context():
    """Ensure context is clean before and after each test."""
    clear_context()
    yield
    clear_context()


@pytest.fixture
def _reset_root_logger():
    """Remove all handlers from the root logger after each test."""
    root = logging.getLogger()
    yield
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()


class TestContextInjectingFilter:
    def test_injects_request_id_into_record(self):
        set_request_context(request_id="req-abc")
        filt = ContextInjectingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "test_event", (), None
        )
        filt.filter(record)
        assert record.request_id == "req-abc"

    def test_injects_user_id_into_record(self):
        bind_user_id(user_id=42)
        filt = ContextInjectingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "test_event", (), None
        )
        filt.filter(record)
        assert record.user_id == 42

    def test_injects_project_id_into_record(self):
        set_task_context(project_id=7, user_id=1)
        filt = ContextInjectingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "test_event", (), None
        )
        filt.filter(record)
        assert record.project_id == 7

    def test_does_not_overwrite_existing_extra(self):
        """Call-site extra={} takes precedence over ambient context."""
        set_task_context(project_id=7, user_id=1)
        filt = ContextInjectingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "test_event", (), None
        )
        record.project_id = 99  # call-site already set this
        filt.filter(record)
        assert record.project_id == 99  # not overwritten

    def test_returns_true_always(self):
        """Filter always returns True (never suppresses records)."""
        filt = ContextInjectingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "test_event", (), None
        )
        assert filt.filter(record) is True

    def test_empty_context_adds_no_attrs(self):
        """When context is empty, no new attributes are added."""
        clear_context()
        filt = ContextInjectingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "test_event", (), None
        )
        filt.filter(record)
        assert not hasattr(record, "request_id")
        assert not hasattr(record, "user_id")
        assert not hasattr(record, "project_id")
        assert not hasattr(record, "page_id")


class TestSecretRedactingFilter:
    def test_redacts_api_key_pattern_from_message(self):
        filt = SecretRedactingFilter()
        record = logging.LogRecord(
            "test",
            logging.INFO,
            "",
            0,
            "provider call with key sk-abc123def456ghi789jkl012mno345",
            (),
            None,
        )
        filt.filter(record)
        assert "sk-abc123" not in record.getMessage()
        assert "[REDACTED_SECRET]" in record.getMessage()

    def test_redacts_args(self):
        filt = SecretRedactingFilter()
        record = logging.LogRecord(
            "test",
            logging.INFO,
            "",
            0,
            "key: %s",
            ("sk-abc123def456ghi789jkl012mno345pqr678stu901vwx234yz"),
            None,
        )
        filt.filter(record)
        redacted_msg = record.getMessage()
        assert "sk-abc123" not in redacted_msg
        assert "[REDACTED_SECRET]" in redacted_msg

    def test_returns_true_always(self):
        """Filter always returns True (never suppresses records)."""
        filt = SecretRedactingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "clean message", (), None
        )
        assert filt.filter(record) is True

    def test_no_redaction_on_clean_message(self):
        filt = SecretRedactingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "no secrets here", (), None
        )
        filt.filter(record)
        assert record.getMessage() == "no secrets here"


class TestDevFormatter:
    def test_formats_basic_message(self):
        fmt = DevFormatter()
        record = logging.LogRecord(
            "app.services.analyzer",
            logging.INFO,
            "",
            0,
            "analyzer.completed",
            (),
            None,
        )
        output = fmt.format(record)
        assert "INFO" in output
        assert "analyzer.completed" in output
        assert "analyzer" in output

    def test_includes_extra_fields_as_key_value(self):
        fmt = DevFormatter()
        record = logging.LogRecord(
            "test",
            logging.INFO,
            "",
            0,
            "scope.classified",
            (),
            None,
        )
        record.scope_mode = "CURRENT_PAGE"
        record.included_count = 5
        output = fmt.format(record)
        assert "scope_mode=CURRENT_PAGE" in output
        assert "included_count=5" in output

    def test_shortens_logger_name(self):
        fmt = DevFormatter()
        record = logging.LogRecord(
            "app.core.deep.nested.module",
            logging.INFO,
            "",
            0,
            "test",
            (),
            None,
        )
        output = fmt.format(record)
        # Should show last two segments
        assert "nested.module" in output


class TestJsonFormatter:
    def test_produces_valid_json(self):
        fmt = JsonFormatter()
        record = logging.LogRecord(
            "test.logger",
            logging.INFO,
            "",
            0,
            "test_event",
            (),
            None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test.logger"
        assert parsed["event"] == "test_event"
        assert "timestamp" in parsed

    def test_includes_extra_fields_as_top_level_keys(self):
        fmt = JsonFormatter()
        record = logging.LogRecord(
            "test",
            logging.INFO,
            "",
            0,
            "scope.classified",
            (),
            None,
        )
        record.scope_mode = "CURRENT_PAGE"
        record.included_count = 5
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["scope_mode"] == "CURRENT_PAGE"
        assert parsed["included_count"] == 5

    def test_includes_exception_info(self):
        fmt = JsonFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            ei = sys.exc_info()
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            "",
            0,
            "test_error",
            (),
            ei,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]


class TestConfigureLogging:
    def test_configure_logging_does_not_raise(self):
        """configure_logging() should succeed without errors."""
        configure_logging()

    def test_configure_logging_idempotent(self):
        """Calling configure_logging() twice should not raise."""
        configure_logging()
        configure_logging()

    def test_json_format_when_env_set(self, monkeypatch, _reset_root_logger):
        monkeypatch.setattr(
            "app.core.config.settings.LOG_FORMAT", "json"
        )
        monkeypatch.setattr(
            "app.core.config.settings.LOG_LEVEL", "INFO"
        )
        configure_logging()
        root = logging.getLogger()
        handler = root.handlers[0]
        assert isinstance(handler.formatter, JsonFormatter)

    def test_text_format_by_default(self, monkeypatch, _reset_root_logger):
        monkeypatch.setattr(
            "app.core.config.settings.LOG_FORMAT", "text"
        )
        monkeypatch.setattr(
            "app.core.config.settings.LOG_LEVEL", "INFO"
        )
        configure_logging()
        root = logging.getLogger()
        handler = root.handlers[0]
        assert isinstance(handler.formatter, DevFormatter)

    def test_filters_attached_to_handler(
        self, monkeypatch, _reset_root_logger,
    ):
        monkeypatch.setattr(
            "app.core.config.settings.LOG_FORMAT", "text"
        )
        monkeypatch.setattr(
            "app.core.config.settings.LOG_LEVEL", "INFO"
        )
        configure_logging()
        root = logging.getLogger()
        handler = root.handlers[0]
        filter_names = [f.__class__.__name__ for f in handler.filters]
        assert "ContextInjectingFilter" in filter_names
        assert "SecretRedactingFilter" in filter_names

    def test_third_party_loggers_silenced(
        self, monkeypatch, _reset_root_logger,
    ):
        monkeypatch.setattr(
            "app.core.config.settings.LOG_FORMAT", "text"
        )
        monkeypatch.setattr(
            "app.core.config.settings.LOG_LEVEL", "INFO"
        )
        configure_logging()
        assert logging.getLogger("uvicorn.access").level == logging.WARNING
        assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING