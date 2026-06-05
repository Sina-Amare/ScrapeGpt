"""
Task state management with atomic transitions.

Handles state transitions with validation and atomicity.
"""

import logging
from dataclasses import dataclass
from collections.abc import Collection

from sqlalchemy import text, select

from app.db.database import async_session_factory
from app.core.config import settings
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
) -> TransitionResult:
    """Transition task from PERMISSION_GRANTED to SCRAPING."""
    async with async_session_factory() as db:
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
) -> TransitionResult:
    """Transition task from SCRAPING to SCRAPED with content."""
    async with async_session_factory() as db:
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
) -> TransitionResult:
    """
    Atomically transition to LLM_PROCESSING and deduct credit.

    This is the ONLY place credits are deducted.
    ATOMIC: Credit deduction AND state change happen in ONE transaction.

    Validates task ownership before processing.
    SAFE: Will not corrupt tasks already in terminal states.

    Returns:
        success=True: Credit deducted, state=LLM_PROCESSING
        success=False: Validation error or already terminal
    """
    async with async_session_factory() as db:
        async with db.begin():
            task = await db.get(ScrapeTask, task_id)
            if not task:
                return TransitionResult(success=False, task=None, error="Task not found")

            # SAFETY: Never modify a task already in terminal state
            if task.is_terminal:
                logger.warning(
                    "task.already_terminal",
                    extra={"task_id": task_id, "state": task.state.value},
                )
                return TransitionResult(
                    success=False,
                    task=task,
                    error=f"Task already in terminal state: {task.state.value}",
                )

            # Security: validate ownership
            if task.user_id != user_id:
                logger.error(
                    "security.ownership_mismatch",
                    extra={
                        "task_id": task_id,
                        "task_user": task.user_id,
                        "caller": user_id
                    },
                )
                return TransitionResult(
                    success=False,
                    task=task,
                    error="Task ownership mismatch",
                )

            # Capture original state before any mutation
            original_state = task.state.value

            if not task.can_transition_to(TaskState.LLM_PROCESSING):
                # Mark as FAILED to maintain always-finalize guarantee
                task.state = TaskState.FAILED
                task.error = f"Invalid transition from {original_state}"
                await db.flush()
                await db.refresh(task)
                return TransitionResult(
                    success=False,
                    task=task,
                    error=f"Cannot transition from {original_state} to LLM_PROCESSING",
                )

            # Atomic credit deduction
            result = await db.execute(
                text("""
                    UPDATE users
                    SET credits_remaining = credits_remaining - :cost,
                        updated_at = NOW()
                    WHERE id = :user_id AND credits_remaining >= :cost
                """),
                {"user_id": user_id, "cost": settings.SCRAPE_CREDIT_COST},
            )

            if result.rowcount == 0:
                # Insufficient credits - mark as FAILED (same transaction)
                task.state = TaskState.FAILED
                task.error = "Insufficient credits for LLM processing"
                logger.warning(
                    "task.failed.no_credits",
                    extra={"task_id": task_id, "user_id": user_id},
                )
                # Transaction commits on exit - FAILED state persisted atomically
                await db.flush()
                await db.refresh(task)
                return TransitionResult(
                    success=False,
                    task=task,
                    error="Insufficient credits",
                )

            # Credit deducted successfully - update state (same transaction)
            task.state = TaskState.LLM_PROCESSING
            logger.info(
                "task.llm_processing",
                extra={"task_id": task_id, "credit_deducted": True},
            )
            # Transaction commits on exit - both credit deduction and state persisted atomically

        await db.refresh(task)
    return TransitionResult(success=True, task=task)


async def transition_to_completed(
    task_id: int,
    result_data: dict,
) -> TransitionResult:
    """Transition task to COMPLETED with LLM result."""
    async with async_session_factory() as db:
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
    expected_states: Collection[TaskState] | None = None,
) -> TransitionResult:
    """Transition task to FAILED with error reason."""
    async with async_session_factory() as db:
        async with db.begin():
            task = await db.get(ScrapeTask, task_id)
            if not task:
                return TransitionResult(success=False, task=None, error="Task not found")

            if expected_states is not None and task.state not in expected_states:
                logger.info(
                    "task.fail_skipped.state_changed",
                    extra={
                        "task_id": task_id,
                        "current_state": task.state.value,
                        "expected_states": [state.value for state in expected_states],
                    },
                )
                return TransitionResult(
                    success=False,
                    task=task,
                    error="Task state changed concurrently",
                )

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
