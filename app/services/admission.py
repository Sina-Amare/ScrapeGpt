"""Admission service for scrape task creation.

SUPPORTED-LEGACY: this admits the one-shot ``/scrape`` pipeline (``ScrapeTask``).
The project-based workflow uses ``job_admission.py`` instead. The two are kept
separate by design because they admit different entities (tasks vs projects)
with different preflight rules; consolidating them only makes sense if/when the
legacy ``/scrape`` surface is retired (a product decision).
"""

import logging
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.scrape_task import ScrapeTask, TaskState, TERMINAL_STATES
from app.models.user import User


logger = logging.getLogger(__name__)


class AdmissionErrorType(str, Enum):
    """Types of admission failures."""
    TOO_MANY_ACTIVE_TASKS = "TOO_MANY_ACTIVE_TASKS"


@dataclass
class AdmissionError:
    """Error result from admission attempt."""
    error_type: AdmissionErrorType
    message: str
    active_task_id: int | None = None


@dataclass
class AdmissionSuccess:
    """Success result from admission attempt."""
    task: ScrapeTask


AdmissionResult = AdmissionSuccess | AdmissionError


async def admit_scrape_task(
    user: User,
    url: str,
    db: AsyncSession,
) -> AdmissionResult:
    """
    Create a scrape task in PERMISSION_GRANTED state.

    Checks:
    1. User has fewer active tasks than MAX_CONCURRENT_JOBS_PER_USER
    2. Count-and-insert is serialized per user with a transaction advisory lock

    Args:
        user: Authenticated user
        url: URL to scrape
        db: Database session

    Returns:
        AdmissionSuccess: Task created
        AdmissionError: User has reached the active task limit
    """
    try:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": user.id},
        )

        active_count_result = await db.execute(
            select(func.count(ScrapeTask.id)).where(
                ScrapeTask.user_id == user.id,
                ScrapeTask.state.notin_(TERMINAL_STATES),
            )
        )
        active_count = active_count_result.scalar_one()

        if active_count >= settings.MAX_CONCURRENT_JOBS_PER_USER:
            active_task_result = await db.execute(
                select(ScrapeTask.id)
                .where(
                    ScrapeTask.user_id == user.id,
                    ScrapeTask.state.notin_(TERMINAL_STATES),
                )
                .order_by(ScrapeTask.created_at.asc())
                .limit(1)
            )
            active_task_id = active_task_result.scalar_one_or_none()
            await db.rollback()

            logger.info(
                "admission.blocked.active_task_limit",
                extra={
                    "user_id": user.id,
                    "active_count": active_count,
                    "limit": settings.MAX_CONCURRENT_JOBS_PER_USER,
                    "active_task_id": active_task_id,
                },
            )

            return AdmissionError(
                error_type=AdmissionErrorType.TOO_MANY_ACTIVE_TASKS,
                message="Active scraping task limit reached",
                active_task_id=active_task_id,
            )

        task = ScrapeTask(
            user_id=user.id,
            url=url,
            state=TaskState.PERMISSION_GRANTED,
        )

        db.add(task)
        await db.flush()
        await db.commit()

        await db.refresh(task)

        logger.info(
            "admission.success",
            extra={"user_id": user.id, "task_id": task.id, "url": url},
        )

        return AdmissionSuccess(task=task)

    except Exception:
        await db.rollback()
        raise


