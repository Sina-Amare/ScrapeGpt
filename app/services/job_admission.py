"""Admission service for analysis job creation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.job import ACTIVE_JOB_STATES, Job, JobState, ExtractionMode, WorkflowMode, RenderMode
from app.models.provider_config import ProviderConfig
from app.models.user import User

logger = logging.getLogger(__name__)


class JobAdmissionErrorType(str, Enum):
    NO_PROVIDER_CONFIGURED = "NO_PROVIDER_CONFIGURED"
    ACTIVE_JOB_LIMIT_REACHED = "ACTIVE_JOB_LIMIT_REACHED"


@dataclass
class JobAdmissionError:
    error_type: JobAdmissionErrorType
    message: str


@dataclass
class JobAdmissionSuccess:
    job: Job
    provider_config: ProviderConfig


JobAdmissionResult = JobAdmissionSuccess | JobAdmissionError


async def admit_job(
    user: User,
    url: str,
    extraction_mode: str,
    workflow_mode: str,
    render_mode: str,
    provider_config_id: int | None,
    db: AsyncSession,
) -> JobAdmissionResult:
    """
    Validate and create an analysis job in QUEUED state.

    Checks (in order):
    1. User has a usable provider config (explicit or default).
    2. User has fewer active jobs than MAX_CONCURRENT_JOBS_PER_USER.

    The count check is serialized per user with a transaction advisory lock.
    """
    # ------------------------------------------------------------------
    # Step 1: resolve provider config — fail fast before locking
    # ------------------------------------------------------------------
    provider_config = await _resolve_provider(db, user, provider_config_id)
    if provider_config is None:
        return JobAdmissionError(
            error_type=JobAdmissionErrorType.NO_PROVIDER_CONFIGURED,
            message=(
                "No provider configured. Add a provider in Settings → Providers "
                "before creating an analysis job."
            ),
        )

    # ------------------------------------------------------------------
    # Step 2: serialize per-user and check active job count
    # ------------------------------------------------------------------
    try:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": user.id},
        )

        active_count_result = await db.execute(
            select(func.count(Job.id)).where(
                Job.user_id == user.id,
                Job.state.in_(ACTIVE_JOB_STATES),
            )
        )
        active_count = active_count_result.scalar_one()

        if active_count >= settings.MAX_CONCURRENT_JOBS_PER_USER:
            await db.rollback()
            return JobAdmissionError(
                error_type=JobAdmissionErrorType.ACTIVE_JOB_LIMIT_REACHED,
                message=(
                    f"Active job limit reached ({active_count}/{settings.MAX_CONCURRENT_JOBS_PER_USER}). "
                    "Wait for a running job to complete."
                ),
            )

        job = Job(
            user_id=user.id,
            provider_config_id=provider_config.id,
            url=url,
            extraction_mode=ExtractionMode(extraction_mode),
            workflow_mode=WorkflowMode(workflow_mode),
            render_mode=RenderMode(render_mode),
            state=JobState.QUEUED,
        )
        db.add(job)
        await db.flush()
        await db.commit()
        await db.refresh(job)

        logger.info(
            "job_admission.success",
            extra={
                "user_id": user.id,
                "job_id": job.id,
                "extraction_mode": extraction_mode,
                "workflow_mode": workflow_mode,
            },
        )
        return JobAdmissionSuccess(job=job, provider_config=provider_config)

    except Exception:
        await db.rollback()
        raise


async def _resolve_provider(
    db: AsyncSession, user: User, provider_config_id: int | None
) -> ProviderConfig | None:
    """Return the provider config to use: explicit ID > user default > first owned."""
    if provider_config_id is not None:
        result = await db.execute(
            select(ProviderConfig).where(
                ProviderConfig.id == provider_config_id,
                ProviderConfig.user_id == user.id,
            )
        )
        return result.scalar_one_or_none()

    # Try user's default provider
    if user.default_provider_id is not None:
        result = await db.execute(
            select(ProviderConfig).where(
                ProviderConfig.id == user.default_provider_id,
                ProviderConfig.user_id == user.id,
            )
        )
        provider = result.scalar_one_or_none()
        if provider is not None:
            return provider

    # Fall back to any provider owned by this user
    result = await db.execute(
        select(ProviderConfig)
        .where(ProviderConfig.user_id == user.id)
        .order_by(ProviderConfig.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()
