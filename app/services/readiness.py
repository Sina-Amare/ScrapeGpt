"""
Readiness checks for database connectivity and schema compatibility.

This module provides a bounded readiness probe used by /health/ready.
Responses use controlled reason codes and never expose raw exception text.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import (
    DBAPIError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
    SQLAlchemyError,
)
from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)

ReadinessCode = Literal[
    "ok",
    "db_unreachable",
    "schema_incompatible",
    "query_failed",
    "timeout",
]


@dataclass(frozen=True)
class DBReadinessResult:
    """Result of a readiness probe."""

    ready: bool
    code: ReadinessCode


class SchemaIncompatibleError(Exception):
    """Raised when required schema/migration state is missing or incompatible."""


async def _run_probe(db: AsyncSession) -> None:
    """
    Run connectivity and schema compatibility probes.

    This function raises on failure and returns None on success.
    """
    # Connectivity probe
    await db.execute(text("SELECT 1"))

    # Migration presence probe
    result = await db.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
    if result.scalar_one_or_none() is None:
        raise SchemaIncompatibleError("alembic_version has no applied revision")

    # Required schema probes (column-level sanity checks)
    await db.execute(
        text(
            """
            SELECT id, email, hashed_password, is_active, default_provider_id
            FROM users
            LIMIT 0
            """
        )
    )
    await db.execute(
        text(
            """
            SELECT id, user_id, state, url, error, result
            FROM scrape_tasks
            LIMIT 0
            """
        )
    )
    await db.execute(
        text(
            """
            SELECT id, user_id, provider, model, api_key_encrypted, capability_flags
            FROM provider_configs
            LIMIT 0
            """
        )
    )
    await db.execute(
        text(
            """
            SELECT id, user_id, state, url, extraction_mode, workflow_mode
            FROM projects
            LIMIT 0
            """
        )
    )
    await db.execute(
        text(
            """
            SELECT id, project_id, mode, fields, content_config
            FROM extraction_specs
            LIMIT 0
            """
        )
    )
    await db.execute(
        text(
            """
            SELECT id, project_id, spec_id, sample_records, quality_summary
            FROM preview_results
            LIMIT 0
            """
        )
    )
    await db.execute(
        text(
            """
            SELECT id, project_id, state, normalized_url, lease_expires_at
            FROM crawl_pages
            LIMIT 0
            """
        )
    )
    await db.execute(
        text(
            """
            SELECT id, project_id, source_url, raw_data
            FROM extracted_records
            LIMIT 0
            """
        )
    )
    await db.execute(
        text(
            """
            SELECT id, project_id, format, record_count
            FROM exports
            LIMIT 0
            """
        )
    )
    await db.execute(
        text(
            """
            SELECT id, content_hash, extraction_mode, provider, model, analyzer_version
            FROM analysis_cache
            LIMIT 0
            """
        )
    )


def _failure(code: ReadinessCode, exc: Exception | None = None) -> DBReadinessResult:
    """Build a failed readiness result and log sanitized failure details."""
    extra = {"reason": code}
    if exc is not None:
        extra["error_type"] = exc.__class__.__name__
    logger.warning("readiness.check_failed", extra=extra)
    return DBReadinessResult(ready=False, code=code)


async def check_db_ready(db: AsyncSession, timeout_seconds: float) -> DBReadinessResult:
    """
    Check whether DB is ready for core operation within a bounded timeout.

    The full probe is wrapped in asyncio.wait_for to keep runtime bounded.
    """
    try:
        await asyncio.wait_for(_run_probe(db), timeout=timeout_seconds)
        return DBReadinessResult(ready=True, code="ok")
    except asyncio.TimeoutError:
        return _failure("timeout")
    except SchemaIncompatibleError as exc:
        return _failure("schema_incompatible", exc)
    except (OperationalError, InterfaceError) as exc:
        return _failure("db_unreachable", exc)
    except ProgrammingError as exc:
        return _failure("schema_incompatible", exc)
    except DBAPIError as exc:
        if getattr(exc, "connection_invalidated", False):
            return _failure("db_unreachable", exc)
        return _failure("query_failed", exc)
    except SQLAlchemyError as exc:
        return _failure("query_failed", exc)
    except Exception as exc:  # Defensive fallback: preserve sanitized output.
        return _failure("query_failed", exc)
