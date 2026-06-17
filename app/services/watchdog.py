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

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta

from sqlalchemy import delete, select, func, update

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


# Run IDs the watchdog has handed to a re-dispatched (resumed) worker that has
# not finished yet. Prevents the next 60s sweep from starting a SECOND worker
# for the same run while the resumed one is still spinning up or running. Lost
# on process restart, which is fine — the next sweep simply re-evaluates.
_resuming_run_ids: set[int] = set()


async def _resume_extraction_run(
    project_id: int, spec_id: int, run_id: int
) -> None:
    """Re-dispatch the extraction loop for a stalled run, releasing the guard
    when it finishes. Imported lazily to keep the watchdog -> extraction
    dependency one-directional."""
    from app.services.project_extraction import execute_project_extraction

    try:
        await execute_project_extraction(project_id, spec_id, run_id)
    except Exception:
        logger.exception(
            "watchdog.resume_failed",
            extra={"project_id": project_id, "run_id": run_id},
        )
    finally:
        _resuming_run_ids.discard(run_id)


def _schedule_resume(project_id: int, spec_id: int, run_id: int) -> None:
    """Reserve the run and launch a background resume worker.

    The single patchable seam for tests: resume *decisions* can be asserted
    without actually running an extraction against a real database.
    """
    _resuming_run_ids.add(run_id)
    asyncio.create_task(_resume_extraction_run(project_id, spec_id, run_id))


async def _hard_fail_project(
    db, project_id: int, message: str, code: str
) -> bool:
    """Fail a still-active project and its active run atomically.

    The state guard prevents clobbering a concurrent advance (e.g. the worker
    completed between our read and this write). Returns True if the project row
    was actually flipped.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(Project)
        .where(
            Project.id == project_id,
            Project.state.in_({
                ProjectState.DISCOVERING,
                ProjectState.EXTRACTING,
                ProjectState.EXPORTING,
            }),
        )
        .values(state=ProjectState.FAILED, error=message, error_code=code)
    )
    if not (result.rowcount or 0):
        return False
    await db.execute(
        update(ExtractionRun)
        .where(
            ExtractionRun.project_id == project_id,
            ExtractionRun.state.in_(ACTIVE_EXTRACTION_RUN_STATES),
        )
        .values(
            state=ExtractionRunState.FAILED.value,
            finished_at=now,
            error=message,
            error_code=code,
        )
    )
    return True


async def cleanup_stuck_projects() -> int:
    """Recover projects whose in-process extraction worker has stalled.

    DISCOVERING and EXTRACTING are *resumable*: a crashed worker (e.g. a server
    restart — in-process BackgroundTasks do not survive one) leaves the run
    active but its pages stale. Instead of hard-failing (the pre-A1 behavior),
    the watchdog re-dispatches ``execute_project_extraction`` up to
    ``WATCHDOG_MAX_RESUME_ATTEMPTS`` times, then hard-fails with
    ``EXTRACTION_RESUME_EXHAUSTED``. EXPORTING is finalization-only and is still
    hard-failed.

    Two safeguards prevent duplicate live workers and stale transitions:

    * Liveness is judged by per-run **page activity**, not ``Project.updated_at``
      — a healthy long crawl commits page rows continuously but never touches the
      project row, so keying off the project timestamp would re-dispatch workers
      that are still making progress.
    * A re-dispatched run is held in ``_resuming_run_ids`` until its worker
      finishes, so a later sweep cannot start a second worker for it. Page
      leasing (FOR UPDATE SKIP LOCKED + fencing token) and record idempotency are
      the database-level backstop if two workers ever overlap.

    The resumed worker re-validates run/project state at entry and routes every
    transition through ``Project.transition_to()``, so a run that completed or was
    canceled between sweeps simply exits without transitioning.

    Returns the number of projects hard-failed (resumes are logged separately).
    """
    now = datetime.now(timezone.utc)
    failed = 0
    to_resume: list[tuple[int, int, int]] = []

    async with async_session_factory() as db:
        # --- Resumable states: DISCOVERING, EXTRACTING ---
        for state, timeout_min in (
            (
                ProjectState.DISCOVERING,
                settings.WATCHDOG_PROJECT_DISCOVERING_TIMEOUT_MINUTES,
            ),
            (
                ProjectState.EXTRACTING,
                settings.WATCHDOG_PROJECT_EXTRACTING_TIMEOUT_MINUTES,
            ),
        ):
            cutoff = now - timedelta(minutes=timeout_min)
            # Most recent page activity for the run (None until the seed page is
            # touched). Correlated to the outer ExtractionRun row.
            last_page_activity = (
                select(
                    func.max(
                        func.coalesce(
                            CrawlPage.updated_at, CrawlPage.created_at
                        )
                    )
                )
                .where(CrawlPage.extraction_run_id == ExtractionRun.id)
                .correlate(ExtractionRun)
                .scalar_subquery()
            )
            rows = (
                await db.execute(
                    select(
                        Project.id,
                        ExtractionRun.id,
                        ExtractionRun.spec_id,
                        ExtractionRun.resume_count,
                    )
                    .join(
                        ExtractionRun,
                        ExtractionRun.project_id == Project.id,
                    )
                    .where(
                        Project.state == state,
                        ExtractionRun.state.in_(
                            ACTIVE_EXTRACTION_RUN_STATES
                        ),
                        func.coalesce(
                            last_page_activity,
                            ExtractionRun.started_at,
                            ExtractionRun.created_at,
                        ) < cutoff,
                    )
                )
            ).all()

            for project_id, run_id, spec_id, resume_count in rows:
                # A resume from a prior sweep is still running — don't double it.
                if run_id in _resuming_run_ids:
                    continue

                exhausted = (
                    resume_count >= settings.WATCHDOG_MAX_RESUME_ATTEMPTS
                )
                if exhausted or spec_id is None:
                    if exhausted:
                        code = "EXTRACTION_RESUME_EXHAUSTED"
                        msg = (
                            "Watchdog: extraction worker stalled and exceeded "
                            f"{settings.WATCHDOG_MAX_RESUME_ATTEMPTS} resume "
                            "attempt(s)."
                        )
                    else:
                        code = "EXTRACTION_FAILED"
                        msg = "Watchdog: stalled run has no spec to resume from."
                    if await _hard_fail_project(db, project_id, msg, code):
                        failed += 1
                        logger.warning(
                            "watchdog.project_resume_exhausted"
                            if exhausted
                            else "watchdog.project_reset",
                            extra={
                                "project_id": project_id,
                                "run_id": run_id,
                                "resume_count": resume_count,
                                "timeout_category": state.value.lower(),
                            },
                        )
                    continue

                # Bounded resume: record the attempt in this transaction so the
                # re-dispatched worker (its own session) reads the bumped count.
                run = await db.get(ExtractionRun, run_id)
                if run is None or run.state not in ACTIVE_EXTRACTION_RUN_STATES:
                    continue
                run.resume_count = (run.resume_count or 0) + 1
                to_resume.append((project_id, spec_id, run_id))
                logger.info(
                    "watchdog.project_resumed",
                    extra={
                        "project_id": project_id,
                        "run_id": run_id,
                        "attempt": run.resume_count,
                        "from_state": state.value,
                    },
                )

        # --- EXPORTING: finalization-only, still hard-failed ---
        exporting_cutoff = now - timedelta(
            minutes=settings.WATCHDOG_PROJECT_EXPORTING_TIMEOUT_MINUTES
        )
        mins_exporting = settings.WATCHDOG_PROJECT_EXPORTING_TIMEOUT_MINUTES
        result = await db.execute(
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
        exporting_failed = result.rowcount or 0
        failed += exporting_failed
        if exporting_failed:
            logger.info(
                "watchdog.project_reset",
                extra={
                    "count": exporting_failed,
                    "timeout_category": "exporting",
                },
            )

        # Keep run state consistent: any active run whose project is now FAILED
        # is marked FAILED. current_extraction_run_id is untouched so the
        # previous completed run stays visible.
        run_stmt = (
            update(ExtractionRun)
            .where(
                ExtractionRun.state.in_(ACTIVE_EXTRACTION_RUN_STATES),
                ExtractionRun.project_id.in_(
                    select(Project.id).where(
                        Project.state == ProjectState.FAILED
                    )
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

        await db.commit()

    # Dispatch resumes AFTER the watchdog session is committed and closed, so the
    # resumed worker's own session reads the bumped resume_count and we are not
    # holding the sweep's session while a (possibly long) extraction runs.
    for project_id, spec_id, run_id in to_resume:
        _schedule_resume(project_id, spec_id, run_id)
    if to_resume:
        logger.info(
            "watchdog.resumes_dispatched", extra={"count": len(to_resume)}
        )

    return failed


async def cleanup_expired_analysis_cache() -> int:
    """Purge analysis cache entries whose expires_at has passed.

    Only runs when ANALYSIS_CACHE_TTL_DAYS > 0. Returns the number
    of entries deleted.
    """
    if settings.ANALYSIS_CACHE_TTL_DAYS <= 0:
        return 0

    now = datetime.now(timezone.utc)

    async with async_session_factory() as db:
        # Single bulk DELETE rather than load-all-then-delete-row-by-row.
        result = await db.execute(
            delete(AnalysisCache).where(
                AnalysisCache.expires_at.isnot(None),
                AnalysisCache.expires_at < now,
            )
        )
        cleaned = int(result.rowcount or 0)
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
