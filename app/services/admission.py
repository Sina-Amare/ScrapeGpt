"""
Admission service for scrape task creation.

Creates task in PERMISSION_GRANTED state.
Credits are NOT deducted here - only at LLM processing phase.
"""

from dataclasses import dataclass
from enum import Enum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scrape_task import ScrapeTask, TaskState
from app.models.user import User


class AdmissionErrorType(str, Enum):
    """Types of admission failures."""
    ALREADY_HAS_ACTIVE_TASK = "ALREADY_HAS_ACTIVE_TASK"


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

    Credits are NOT deducted here - deduction happens at LLM processing.

    The partial unique index enforces: at most one active task per user.

    Args:
        user: Authenticated user
        url: URL to scrape
        db: Database session

    Returns:
        AdmissionSuccess: Task created
        AdmissionError: Already has active task
    """
    task = ScrapeTask(
        user_id=user.id,
        url=url,
        state=TaskState.PERMISSION_GRANTED,
    )

    try:
        async with db.begin():
            db.add(task)
            await db.flush()

        await db.refresh(task)

        return AdmissionSuccess(task=task)

    except IntegrityError as e:
        if "ix_one_active_task_per_user" in str(e.orig):
            # Find the existing active task
            from sqlalchemy import select
            from app.models.scrape_task import TERMINAL_STATES

            result = await db.execute(
                select(ScrapeTask.id).where(
                    ScrapeTask.user_id == user.id,
                    ScrapeTask.state.notin_(TERMINAL_STATES),
                )
            )
            active_task_id = result.scalar_one_or_none()

            return AdmissionError(
                error_type=AdmissionErrorType.ALREADY_HAS_ACTIVE_TASK,
                message="You already have an active scraping task",
                active_task_id=active_task_id,
            )
        raise

