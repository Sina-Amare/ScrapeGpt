"""
Watchdog service for stuck task cleanup.

Detects and fails tasks stuck in non-terminal states.
"""

import logging
import time
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func

from app.core.config import settings
from app.db.database import async_session_factory
from app.models.scrape_task import ScrapeTask, TaskState
from app.models.job import Job, JobState
from app.services.task_state import transition_to_failed
from app.services.job_state import transition_job_to_failed


logger = logging.getLogger(__name__)


async def cleanup_stuck_tasks() -> int:
    """
    Find and fail tasks stuck in non-terminal states.

    Cleans up:
    - PERMISSION_GRANTED: Pipeline never started
    - SCRAPING: Scrape took too long
    - LLM_PROCESSING: LLM processing took too long

    Returns:
        Number of tasks cleaned up
    """
    now = datetime.now(timezone.utc)
    cleaned = 0

    async with async_session_factory() as db:
        # Find tasks stuck in PERMISSION_GRANTED (pipeline never started)
        pg_cutoff = now - timedelta(
            minutes=settings.WATCHDOG_PERMISSION_GRANTED_TIMEOUT_MINUTES
        )
        result = await db.execute(
            select(ScrapeTask).where(
                ScrapeTask.state == TaskState.PERMISSION_GRANTED,
                func.coalesce(ScrapeTask.updated_at, ScrapeTask.created_at) < pg_cutoff,
            )
        )
        stuck_pg = result.scalars().all()

        for task in stuck_pg:
            mins = settings.WATCHDOG_PERMISSION_GRANTED_TIMEOUT_MINUTES
            error_msg = f"Watchdog: Pipeline did not start within {mins}m"
            res = await transition_to_failed(
                task.id,
                error_msg,
                expected_states={TaskState.PERMISSION_GRANTED},
            )
            if res.success:
                cleaned += 1
                logger.info(
                    "watchdog.task_reset",
                    extra={
                        "task_id": task.id,
                        "old_state": TaskState.PERMISSION_GRANTED.value,
                        "timeout_category": "permission_granted",
                    },
                )

        # Find tasks stuck in SCRAPING
        scraping_cutoff = now - timedelta(
            minutes=settings.WATCHDOG_SCRAPING_TIMEOUT_MINUTES
        )
        result = await db.execute(
            select(ScrapeTask).where(
                ScrapeTask.state == TaskState.SCRAPING,
                func.coalesce(ScrapeTask.updated_at, ScrapeTask.created_at) < scraping_cutoff,
            )
        )
        stuck_scraping = result.scalars().all()

        for task in stuck_scraping:
            mins = settings.WATCHDOG_SCRAPING_TIMEOUT_MINUTES
            error_msg = f"Watchdog: Stuck in SCRAPING for >{mins}m"
            res = await transition_to_failed(
                task.id,
                error_msg,
                expected_states={TaskState.SCRAPING},
            )
            if res.success:
                cleaned += 1
                logger.info(
                    "watchdog.task_reset",
                    extra={
                        "task_id": task.id,
                        "old_state": TaskState.SCRAPING.value,
                        "timeout_category": "scraping",
                    },
                )

        # Find tasks stuck in LLM_PROCESSING
        llm_cutoff = now - timedelta(
            minutes=settings.WATCHDOG_LLM_TIMEOUT_MINUTES
        )
        result = await db.execute(
            select(ScrapeTask).where(
                ScrapeTask.state == TaskState.LLM_PROCESSING,
                func.coalesce(ScrapeTask.updated_at, ScrapeTask.created_at) < llm_cutoff,
            )
        )
        stuck_llm = result.scalars().all()

        for task in stuck_llm:
            mins = settings.WATCHDOG_LLM_TIMEOUT_MINUTES
            error_msg = f"Watchdog: Stuck in LLM_PROCESSING for >{mins}m"
            res = await transition_to_failed(
                task.id,
                error_msg,
                expected_states={TaskState.LLM_PROCESSING},
            )
            if res.success:
                cleaned += 1
                logger.info(
                    "watchdog.task_reset",
                    extra={
                        "task_id": task.id,
                        "old_state": TaskState.LLM_PROCESSING.value,
                        "timeout_category": "llm_processing",
                    },
                )

        if cleaned > 0:
            logger.info("watchdog.cleanup_complete", extra={"cleaned": cleaned})

    return cleaned


async def cleanup_stuck_jobs() -> int:
    """
    Find and fail analysis jobs stuck in QUEUED or ANALYZING.

    Uses expected_states guard so concurrent state advances are not overwritten.
    Returns number of jobs cleaned up.
    """
    now = datetime.now(timezone.utc)
    cleaned = 0

    async with async_session_factory() as db:
        queued_cutoff = now - timedelta(
            minutes=settings.WATCHDOG_JOB_QUEUED_TIMEOUT_MINUTES
        )
        result = await db.execute(
            select(Job).where(
                Job.state == JobState.QUEUED,
                func.coalesce(Job.updated_at, Job.created_at) < queued_cutoff,
            )
        )
        for job in result.scalars().all():
            mins = settings.WATCHDOG_JOB_QUEUED_TIMEOUT_MINUTES
            res = await transition_job_to_failed(
                job.id,
                f"Watchdog: Job did not start within {mins}m",
                "ANALYSIS_FAILED",
                expected_states={JobState.QUEUED},
            )
            if res.success:
                cleaned += 1
                logger.info(
                    "watchdog.job_reset",
                    extra={
                        "job_id": job.id,
                        "old_state": JobState.QUEUED.value,
                    },
                )

        analyzing_cutoff = now - timedelta(
            minutes=settings.WATCHDOG_JOB_ANALYZING_TIMEOUT_MINUTES
        )
        result = await db.execute(
            select(Job).where(
                Job.state == JobState.ANALYZING,
                func.coalesce(Job.updated_at, Job.created_at) < analyzing_cutoff,
            )
        )
        for job in result.scalars().all():
            mins = settings.WATCHDOG_JOB_ANALYZING_TIMEOUT_MINUTES
            res = await transition_job_to_failed(
                job.id,
                f"Watchdog: Stuck in ANALYZING for >{mins}m",
                "ANALYSIS_FAILED",
                expected_states={JobState.ANALYZING},
            )
            if res.success:
                cleaned += 1
                logger.info(
                    "watchdog.job_reset",
                    extra={
                        "job_id": job.id,
                        "old_state": JobState.ANALYZING.value,
                    },
                )

    return cleaned


async def run_watchdog_once() -> None:
    """Run watchdog cleanup once. Called by background scheduler."""
    try:
        sweep_start = time.monotonic()
        logger.debug(
            "watchdog.sweep_started",
            extra={"timestamp": datetime.now(timezone.utc).isoformat()},
        )
        task_cleaned = await cleanup_stuck_tasks()
        job_cleaned = await cleanup_stuck_jobs()
        duration_ms = round((time.monotonic() - sweep_start) * 1000, 1)
        logger.info(
            "watchdog.sweep_completed",
            extra={
                "tasks_reset": task_cleaned,
                "jobs_reset": job_cleaned,
                "duration_ms": duration_ms,
            },
        )
    except Exception as e:
        logger.exception("watchdog.error", extra={"error": str(e)})
