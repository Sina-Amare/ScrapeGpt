"""
Task state management with atomic transitions.

Handles state transitions with validation and atomicity.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scrape_task import (
    ScrapeTask,
    TaskState,
    VALID_TRANSITIONS,
    TERMINAL_STATES,
)
from app.models.user import User


logger = logging.getLogger(__name__)


class InvalidTransitionError(Exception):
    """Raised when attempting an invalid state transition."""
    pass


class InsufficientCreditsError(Exception):
    """Raised when user has no credits for LLM processing."""
    pass


@dataclass
class TransitionResult:
    """Result of a state transition."""
    success: bool
    task: ScrapeTask
    error: str | None = None


async def transition_to_scraping(
    task_id: int,
    db: AsyncSession,
) -> TransitionResult:
    """Transition task from PERMISSION_GRANTED to SCRAPING."""
    async with db.begin():
        task = await db.get(ScrapeTask, task_id)
        if not task:
            return TransitionResult(
                success=False,
                task=None,
                error="Task not found",
            )

        if not task.can_transition_to(TaskState.SCRAPING):
            return TransitionResult(
                success=False,
                task=task,
                error=f"Cannot transition from {task.state} to SCRAPING",
            )

        task.state = TaskState.SCRAPING
        logger.info("task.scraping", extra={"task_id": task_id})

    await db.refresh(task)
    return TransitionResult(success=True, task=task)


async def transition_to_scraped(
    task_id: int,
    content: str,
    db: AsyncSession,
) -> TransitionResult:
    """Transition task from SCRAPING to SCRAPED with content."""
    async with db.begin():
        task = await db.get(ScrapeTask, task_id)
        if not task:
            return TransitionResult(success=False, task=None, error="Task not found")

        if not task.can_transition_to(TaskState.SCRAPED):
            return TransitionResult(
                success=False,
                task=task,
                error=f"Cannot transition from {task.state} to SCRAPED",
            )

        task.state = TaskState.SCRAPED
        task.content = content
        logger.info(
            "task.scraped",
            extra={"task_id": task_id, "content_length": len(content)},
        )

    await db.refresh(task)
    return TransitionResult(success=True, task=task)


async def transition_to_llm_processing(
    task_id: int,
    user_id: int,
    db: AsyncSession,
) -> TransitionResult:
    """
    Atomically transition to LLM_PROCESSING and deduct credit.

    This is the ONLY place credits are deducted.
    Both state transition and credit deduction happen in one transaction.
    """
    async with db.begin():
        task = await db.get(ScrapeTask, task_id)
        if not task:
            return TransitionResult(success=False, task=None, error="Task not found")

        if not task.can_transition_to(TaskState.LLM_PROCESSING):
            return TransitionResult(
                success=False,
                task=task,
                error=f"Cannot transition from {task.state} to LLM_PROCESSING",
            )

        # Atomic credit deduction
        result = await db.execute(
            text("""
                UPDATE users
                SET credits_remaining = credits_remaining - 1,
                    updated_at = NOW()
                WHERE id = :user_id AND credits_remaining > 0
            """),
            {"user_id": user_id},
        )

        if result.rowcount == 0:
            # Insufficient credits - mark as failed
            task.state = TaskState.FAILED
            task.error = "Insufficient credits for LLM processing"
            logger.warning(
                "task.failed.no_credits",
                extra={"task_id": task_id, "user_id": user_id},
            )
            await db.commit()
            await db.refresh(task)
            return TransitionResult(
                success=False,
                task=task,
                error="Insufficient credits",
            )

        task.state = TaskState.LLM_PROCESSING
        logger.info(
            "task.llm_processing",
            extra={"task_id": task_id, "credit_deducted": True},
        )

    await db.refresh(task)
    return TransitionResult(success=True, task=task)


async def transition_to_completed(
    task_id: int,
    result_data: dict,
    db: AsyncSession,
) -> TransitionResult:
    """Transition task to COMPLETED with LLM result."""
    async with db.begin():
        task = await db.get(ScrapeTask, task_id)
        if not task:
            return TransitionResult(success=False, task=None, error="Task not found")

        if not task.can_transition_to(TaskState.COMPLETED):
            return TransitionResult(
                success=False,
                task=task,
                error=f"Cannot transition from {task.state} to COMPLETED",
            )

        task.state = TaskState.COMPLETED
        task.result = result_data
        logger.info("task.completed", extra={"task_id": task_id})

    await db.refresh(task)
    return TransitionResult(success=True, task=task)


async def transition_to_failed(
    task_id: int,
    error_message: str,
    db: AsyncSession,
) -> TransitionResult:
    """Transition task to FAILED with error reason."""
    async with db.begin():
        task = await db.get(ScrapeTask, task_id)
        if not task:
            return TransitionResult(success=False, task=None, error="Task not found")

        if task.state in TERMINAL_STATES:
            return TransitionResult(
                success=False,
                task=task,
                error=f"Task already in terminal state {task.state}",
            )

        task.state = TaskState.FAILED
        task.error = error_message
        logger.error("task.failed", extra={"task_id": task_id, "reason": error_message})

    await db.refresh(task)
    return TransitionResult(success=True, task=task)
