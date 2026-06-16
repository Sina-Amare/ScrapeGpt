"""
Watchdog service for stuck task cleanup and crash recovery.

Detects and fails tasks/projects stuck in non-terminal states,
and recovers crawl pages whose leases have expired (indicating
a crashed worker).

Ownership boundaries:
- cleanup_expired_crawl_page_leases: page-level recovery.
  Resets FETCHING pages with expired leases back to PENDING so
  they can be retried. Only operates on pages within projects
  that are still in active extraction states.

- cleanup_stuck_projects: project-level recovery.
  Fails projects whose background extraction task has clearly
  crashed (stuck in DISCOVERING/EXTRACTING/EXPORTING beyond
  their timeout). Uses expected_states guards to avoid
  overwriting concurrent state advances.

These two mechanisms are complementary: the lease reaper
prepares pages for retry, and the stuck-project watchdog
fails the project if the background task has died. A project
that is extraction-active for 60+ minutes with no progress
is almost certainly a crashed task, not a slow one.
"""

import logging
import time
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func, update

from app.core.config import settings
from app.db.database import async_session_factory
from app.models.job import (
    ACTIVE_EXTRACTION_RUN_STATES,
    AnalysisCache,
    CrawlPage,
    CrawlPageState,
    ExtractionRun,
    ExtractionRunState,
    Job,
    JobState,
    Project,
    ProjectState,
)
from app.models.scrape_task import ScrapeTask, TaskState
from app.services.job_state import transition_job_to_failed
from app.services.task_state import transition_to_failed


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
                func.coalesce(
                    ScrapeTask.updated_at, ScrapeTask.created_at
                ) < pg_cutoff,
            )
        )
        stuck_pg = result.scalars().all()

        for task in stuck_pg:
            mins = settings.WATCHDOG_PERMISSION_GRANTED_TIMEOUT_MINUTES
            error_msg = (
                f"Watchdog: Pipeline did not start within {mins}m"
            )
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
                        "old_state": (
                            TaskState.PERMISSION_GRANTED.value
                        ),
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
                func.coalesce(
                    ScrapeTask.updated_at, ScrapeTask.created_at
                ) < scraping_cutoff,
            )
        )
        stuck_scraping = result.scalars().all()

        for task in stuck_scraping:
            mins = settings.WATCHDOG_SCRAPING_TIMEOUT_MINUTES
            error_msg = (
                f"Watchdog: Stuck in SCRAPING for >{mins}m"
            )
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
                func.coalesce(
                    ScrapeTask.updated_at, ScrapeTask.created_at
                ) < llm_cutoff,
            )
        )
        stuck_llm = result.scalars().all()

        for task in stuck_llm:
            mins = settings.WATCHDOG_LLM_TIMEOUT_MINUTES
            error_msg = (
                f"Watchdog: Stuck in LLM_PROCESSING for >{mins}m"
            )
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
                        "old_state": (
                            TaskState.LLM_PROCESSING.value
                        ),
                        "timeout_category": "llm_processing",
                    },
                )

        if cleaned > 0:
            logger.info(
                "watchdog.cleanup_complete",
                extra={"cleaned": cleaned},
            )

    return cleaned


async def cleanup_stuck_jobs() -> int:
    """
    Find and fail analysis jobs stuck in QUEUED or ANALYZING.

    Uses expected_states guard so concurrent state advances
    are not overwritten.
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
                func.coalesce(
                    Job.updated_at, Job.created_at
                ) < queued_cutoff,
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
                func.coalesce(
                    Job.updated_at, Job.created_at
                ) < analyzing_cutoff,
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


async def cleanup_expired_crawl_page_leases() -> int:
    """
    Reset crawl pages whose leases have expired.

    When a worker claims a page, it sets state=FETCHING and
    lease_expires_at=now()+5min. If the worker process crashes,
    the lease expires but the page stays in FETCHING indefinitely,
    blocking the project. This function resets expired leases
    back to PENDING so the pages can be retried.

    Only operates on pages within projects that are still in
    active extraction states (DISCOVERING or EXTRACTING).
    Pages in completed/failed/canceled projects are not touched.

    Returns:
        Number of pages reset to PENDING.
    """
    now = datetime.now(timezone.utc)
    reset_count = 0

    async with async_session_factory() as db:
        # Find active project IDs (only reset pages in projects
        # that are still being worked on)
        active_project_ids_result = await db.execute(
            select(Project.id).where(
                Project.state.in_({
                    ProjectState.DISCOVERING,
                    ProjectState.EXTRACTING,
                })
            )
        )
        active_project_ids = [
            pid for pid in active_project_ids_result.scalars().all()
        ]

        if not active_project_ids:
            return 0

        # Find pages with expired leases in active projects
        result = await db.execute(
            select(CrawlPage).where(
                CrawlPage.state == CrawlPageState.FETCHING,
                CrawlPage.lease_expires_at < now,
                CrawlPage.project_id.in_(active_project_ids),
            )
        )
        expired_pages = result.scalars().all()

        for page in expired_pages:
            page.state = CrawlPageState.PENDING
            page.lease_expires_at = None
            # Clear the fencing token so the previous (lost) worker can no longer
            # finalize this page — only a fresh claimant's token will match.
            page.lease_token = None
            page.error = None
            reset_count += 1
            logger.info(
                "watchdog.lease_recovered",
                extra={
                    "page_id": page.id,
                    "project_id": page.project_id,
                    "url": page.normalized_url,
                },
            )

        if reset_count > 0:
            await db.commit()
            logger.info(
                "watchdog.lease_sweep_complete",
                extra={"pages_reset": reset_count},
            )

    return reset_count


async def cleanup_stuck_projects() -> int:
    """
    Find and fail projects stuck in extraction states.

    Covers DISCOVERING, EXTRACTING, and EXPORTING states that
    have been active beyond their configured timeout. Uses
    atomic UPDATE with WHERE-clause state guards to avoid
    overwriting concurrent state advances — the same
    concurrency-safety pattern as expected_states guards
    in the task/job transition functions, but applied at
    the SQL level for projects (which have no dedicated
    transition function).

    Returns:
        Number of projects cleaned up.
    """
    now = datetime.now(timezone.utc)
    cleaned = 0

    async with async_session_factory() as db:
        # DISCOVERING: brief setup state before crawl loop.
        # If stuck here, the background task failed to start
        # the crawl loop.
        discovering_cutoff = now - timedelta(
            minutes=(
                settings.WATCHDOG_PROJECT_DISCOVERING_TIMEOUT_MINUTES
            )
        )
        mins_discovering = (
            settings.WATCHDOG_PROJECT_DISCOVERING_TIMEOUT_MINUTES
        )
        stmt = (
            update(Project)
            .where(
                Project.state == ProjectState.DISCOVERING,
                func.coalesce(
                    Project.updated_at, Project.created_at
                ) < discovering_cutoff,
            )
            .values(
                state=ProjectState.FAILED,
                error=(
                    f"Watchdog: Project stuck in DISCOVERING "
                    f"for >{mins_discovering}m"
                ),
                error_code="EXTRACTION_FAILED",
            )
        )
        result = await db.execute(stmt)
        discovering_cleaned = result.rowcount or 0
        cleaned += discovering_cleaned
        if discovering_cleaned:
            logger.info(
                "watchdog.project_reset",
                extra={
                    "count": discovering_cleaned,
                    "timeout_category": "discovering",
                },
            )

        # EXTRACTING: the main crawl/extract loop. A 500-page
        # extraction with 0.5s delay takes ~4min minimum plus
        # fetch time. 60min accommodates slow sites and large
        # crawls.
        extracting_cutoff = now - timedelta(
            minutes=(
                settings.WATCHDOG_PROJECT_EXTRACTING_TIMEOUT_MINUTES
            )
        )
        mins_extracting = (
            settings.WATCHDOG_PROJECT_EXTRACTING_TIMEOUT_MINUTES
        )
        stmt = (
            update(Project)
            .where(
                Project.state == ProjectState.EXTRACTING,
                func.coalesce(
                    Project.updated_at, Project.created_at
                ) < extracting_cutoff,
            )
            .values(
                state=ProjectState.FAILED,
                error=(
                    f"Watchdog: Project stuck in EXTRACTING "
                    f"for >{mins_extracting}m"
                ),
                error_code="EXTRACTION_FAILED",
            )
        )
        result = await db.execute(stmt)
        extracting_cleaned = result.rowcount or 0
        cleaned += extracting_cleaned
        if extracting_cleaned:
            logger.info(
                "watchdog.project_reset",
                extra={
                    "count": extracting_cleaned,
                    "timeout_category": "extracting",
                },
            )

        # EXPORTING: very brief state for quality computation
        # and export row creation. If stuck here, the background
        # task crashed during finalization.
        exporting_cutoff = now - timedelta(
            minutes=(
                settings.WATCHDOG_PROJECT_EXPORTING_TIMEOUT_MINUTES
            )
        )
        mins_exporting = (
            settings.WATCHDOG_PROJECT_EXPORTING_TIMEOUT_MINUTES
        )
        stmt = (
            update(Project)
            .where(
                Project.state == ProjectState.EXPORTING,
                func.coalesce(
                    Project.updated_at, Project.created_at
                ) < exporting_cutoff,
            )
            .values(
                state=ProjectState.FAILED,
                error=(
                    f"Watchdog: Project stuck in EXPORTING "
                    f"for >{mins_exporting}m"
                ),
                error_code="EXTRACTION_FAILED",
            )
        )
        result = await db.execute(stmt)
        exporting_cleaned = result.rowcount or 0
        cleaned += exporting_cleaned
        if exporting_cleaned:
            logger.info(
                "watchdog.project_reset",
                extra={
                    "count": exporting_cleaned,
                    "timeout_category": "exporting",
                },
            )

        # Keep run state consistent with project state: any active run whose
        # project the watchdog just failed (or that is otherwise FAILED) is
        # marked FAILED. current_extraction_run_id is left untouched so the
        # previous completed run stays visible.
        run_stmt = (
            update(ExtractionRun)
            .where(
                ExtractionRun.state.in_(ACTIVE_EXTRACTION_RUN_STATES),
                ExtractionRun.project_id.in_(
                    select(Project.id).where(Project.state == ProjectState.FAILED)
                ),
            )
            .values(
                state=ExtractionRunState.FAILED.value,
                finished_at=now,
                error="Watchdog: project failed while run was active",
                error_code="EXTRACTION_FAILED",
            )
        )
        runs_failed = (await db.execute(run_stmt)).rowcount or 0
        if runs_failed:
            logger.info("watchdog.run_failed", extra={"count": runs_failed})

        if cleaned > 0 or runs_failed:
            await db.commit()

    return cleaned


async def cleanup_expired_analysis_cache() -> int:
    """Purge analysis cache entries whose expires_at has passed.

    Only runs when ANALYSIS_CACHE_TTL_DAYS > 0. Returns the number
    of entries deleted.
    """
    if settings.ANALYSIS_CACHE_TTL_DAYS <= 0:
        return 0

    now = datetime.now(timezone.utc)
    cleaned = 0

    async with async_session_factory() as db:
        result = await db.execute(
            select(AnalysisCache).where(
                AnalysisCache.expires_at.isnot(None),
                AnalysisCache.expires_at < now,
            )
        )
        expired = result.scalars().all()

        for entry in expired:
            await db.delete(entry)
            cleaned += 1

        if cleaned > 0:
            await db.commit()
            logger.info(
                "watchdog.cache_purged",
                extra={"count": cleaned},
            )

    return cleaned


async def run_watchdog_once() -> None:
    """Run watchdog cleanup once. Called by background scheduler."""
    try:
        sweep_start = time.monotonic()
        logger.debug(
            "watchdog.sweep_started",
            extra={
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
        )
        task_cleaned = await cleanup_stuck_tasks()
        job_cleaned = await cleanup_stuck_jobs()
        lease_recovered = await cleanup_expired_crawl_page_leases()
        project_cleaned = await cleanup_stuck_projects()
        cache_purged = await cleanup_expired_analysis_cache()
        duration_ms = round(
            (time.monotonic() - sweep_start) * 1000, 1
        )
        logger.info(
            "watchdog.sweep_completed",
            extra={
                "tasks_reset": task_cleaned,
                "jobs_reset": job_cleaned,
                "leases_recovered": lease_recovered,
                "projects_reset": project_cleaned,
                "cache_purged": cache_purged,
                "duration_ms": duration_ms,
            },
        )
    except Exception as e:
        logger.exception(
            "watchdog.error", extra={"error": str(e)}
        )
