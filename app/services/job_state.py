"""Job state transitions — one session + transaction per transition."""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from app.db.database import async_session_factory
from app.models.job import Job, JobState, TERMINAL_JOB_STATES

logger = logging.getLogger(__name__)


@dataclass
class JobTransitionResult:
    success: bool
    job: Job | None
    error: str | None = None


async def transition_job_to_analyzing(job_id: int) -> JobTransitionResult:
    async with async_session_factory() as db:
        async with db.begin():
            job = await db.get(Job, job_id)
            if not job:
                return JobTransitionResult(success=False, job=None, error="Job not found")
            if not job.can_transition_to(JobState.ANALYZING):
                return JobTransitionResult(
                    success=False, job=job,
                    error=f"Cannot transition from {job.state} to ANALYZING",
                )
            job.state = JobState.ANALYZING
            logger.info("job.analyzing", extra={"job_id": job_id})
        await db.refresh(job)
    return JobTransitionResult(success=True, job=job)


async def transition_job_to_awaiting_setup(
    job_id: int,
    analysis: dict[str, Any],
    confidence: float,
    warnings: list[str],
    fetch_metadata: dict[str, Any],
) -> JobTransitionResult:
    async with async_session_factory() as db:
        async with db.begin():
            job = await db.get(Job, job_id)
            if not job:
                return JobTransitionResult(success=False, job=None, error="Job not found")
            if not job.can_transition_to(JobState.AWAITING_SETUP):
                return JobTransitionResult(
                    success=False, job=job,
                    error=f"Cannot transition from {job.state} to AWAITING_SETUP",
                )
            job.state = JobState.AWAITING_SETUP
            job.analysis = analysis
            job.confidence = confidence
            job.warnings = warnings
            job.fetch_metadata = fetch_metadata
            logger.info(
                "job.awaiting_setup",
                extra={"job_id": job_id, "confidence": confidence},
            )
        await db.refresh(job)
    return JobTransitionResult(success=True, job=job)


async def transition_job_to_analysis_ready(
    job_id: int,
    analysis: dict[str, Any],
    confidence: float,
    warnings: list[str],
    fetch_metadata: dict[str, Any],
) -> JobTransitionResult:
    async with async_session_factory() as db:
        async with db.begin():
            job = await db.get(Job, job_id)
            if not job:
                return JobTransitionResult(success=False, job=None, error="Job not found")
            if not job.can_transition_to(JobState.ANALYSIS_READY):
                return JobTransitionResult(
                    success=False, job=job,
                    error=f"Cannot transition from {job.state} to ANALYSIS_READY",
                )
            job.state = JobState.ANALYSIS_READY
            job.analysis = analysis
            job.confidence = confidence
            job.warnings = warnings
            job.fetch_metadata = fetch_metadata
            logger.info(
                "job.analysis_ready",
                extra={"job_id": job_id, "confidence": confidence},
            )
        await db.refresh(job)
    return JobTransitionResult(success=True, job=job)


async def transition_job_to_failed(
    job_id: int,
    error_message: str,
    error_code: str = "ANALYSIS_FAILED",
    expected_states: Collection[JobState] | None = None,
) -> JobTransitionResult:
    async with async_session_factory() as db:
        async with db.begin():
            job = await db.get(Job, job_id)
            if not job:
                return JobTransitionResult(success=False, job=None, error="Job not found")

            if expected_states is not None and job.state not in expected_states:
                logger.info(
                    "job.fail_skipped.state_changed",
                    extra={
                        "job_id": job_id,
                        "current_state": job.state.value,
                        "expected_states": [s.value for s in expected_states],
                    },
                )
                return JobTransitionResult(
                    success=False, job=job, error="Job state changed concurrently"
                )

            if job.state in TERMINAL_JOB_STATES:
                return JobTransitionResult(
                    success=False, job=job,
                    error=f"Job already terminal: {job.state}",
                )

            job.state = JobState.FAILED
            job.error = error_message
            job.error_code = error_code
            logger.error(
                "job.failed",
                extra={"job_id": job_id, "reason": error_message, "code": error_code},
            )
        await db.refresh(job)
    return JobTransitionResult(success=True, job=job)


async def transition_job_to_canceled(
    job_id: int,
    expected_states: Collection[JobState] | None = None,
) -> JobTransitionResult:
    async with async_session_factory() as db:
        async with db.begin():
            job = await db.get(Job, job_id)
            if not job:
                return JobTransitionResult(success=False, job=None, error="Job not found")

            if expected_states is not None and job.state not in expected_states:
                return JobTransitionResult(
                    success=False, job=job, error="Job state changed concurrently"
                )

            if job.state in TERMINAL_JOB_STATES:
                return JobTransitionResult(
                    success=False, job=job,
                    error=f"Job already terminal: {job.state}",
                )

            job.state = JobState.CANCELED
            logger.info("job.canceled", extra={"job_id": job_id})
        await db.refresh(job)
    return JobTransitionResult(success=True, job=job)
