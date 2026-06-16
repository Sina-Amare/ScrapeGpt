"""Minimal Prometheus metrics for extraction reliability.

Import-safe: if ``prometheus_client`` is not installed the helpers degrade to
no-ops and ``/metrics`` reports that metrics are unavailable, so the app runs
either way. Labels are deliberately low-cardinality and contain NO PII or
content — never a URL, user id, email, raw HTML, or record value.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:  # pragma: no cover - exercised via the no-op path in tests
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Histogram,
        generate_latest,
    )

    _ENABLED = True
except ImportError:  # prometheus_client optional
    _ENABLED = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


def metrics_available() -> bool:
    return _ENABLED


if _ENABLED:
    _RUNS = Counter(
        "scrapegpt_extraction_runs_total",
        "Extraction runs by terminal state.",
        ["state"],
    )
    _PAGES = Counter(
        "scrapegpt_extraction_pages_total",
        "Crawl page outcomes during extraction.",
        ["outcome"],
    )
    _RATE_LIMIT_RETRIES = Counter(
        "scrapegpt_provider_rate_limit_retries_total",
        "Provider rate-limit (429) retries performed.",
    )
    _RUN_DURATION = Histogram(
        "scrapegpt_extraction_run_duration_seconds",
        "Wall-clock duration of an extraction run.",
        buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800),
    )


def record_run_state(state: str) -> None:
    if _ENABLED:
        try:
            _RUNS.labels(state=state).inc()
        except Exception:  # never let metrics break the pipeline
            logger.debug("metrics.record_run_state_failed", exc_info=True)


def record_page_outcome(outcome: str) -> None:
    if _ENABLED:
        try:
            _PAGES.labels(outcome=outcome).inc()
        except Exception:
            logger.debug("metrics.record_page_outcome_failed", exc_info=True)


def record_rate_limit_retry() -> None:
    if _ENABLED:
        try:
            _RATE_LIMIT_RETRIES.inc()
        except Exception:
            logger.debug("metrics.record_rate_limit_retry_failed", exc_info=True)


def observe_run_duration(seconds: float) -> None:
    if _ENABLED and seconds >= 0:
        try:
            _RUN_DURATION.observe(seconds)
        except Exception:
            logger.debug("metrics.observe_run_duration_failed", exc_info=True)


def render_latest() -> bytes:
    """Prometheus exposition text, or a short notice when unavailable."""
    if _ENABLED:
        return generate_latest()
    return b"# prometheus_client not installed; metrics unavailable\n"
