"""
Admission service for scrape task creation.

Handles atomic task creation with credit deduction.
"""

from dataclasses import dataclass
from enum import Enum

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scrape_task import ScrapeTask, TaskState
from app.models.user import User


class AdmissionErrorType(str, Enum):
    """Types of admission failures."""
    ALREADY_HAS_ACTIVE_TASK = "ALREADY_HAS_ACTIVE_TASK"
    INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS"


@dataclass
class AdmissionError:
    """Error result from admission attempt."""
    error_type: AdmissionErrorType
    message: str


@dataclass
class AdmissionSuccess:
    """Success result from admission attempt."""
    task: ScrapeTask
    credits_remaining: int


AdmissionResult = AdmissionSuccess | AdmissionError


async def ensure_credits_reset(user: User, db: AsyncSession) -> None:
    """
    Reset user credits if 24h have passed.
    
    Called OUTSIDE the main transaction to avoid holding locks
    during reset logic.
    """
    if user.ensure_credits_reset():
        await db.commit()
        await db.refresh(user)


async def admit_scrape_task(
    user: User,
    url: str,
    db: AsyncSession,
) -> AdmissionResult:
    """
    Atomically create a scrape task and deduct credit.
    
    Sequence:
    1. INSERT scrape_task (proves permission via partial unique index)
    2. UPDATE credits atomically (payment)
    3. COMMIT or ROLLBACK
    
    Args:
        user: Authenticated user (must be attached to session)
        url: URL to scrape
        db: Database session
        
    Returns:
        AdmissionSuccess: Task created, credit deducted
        AdmissionError: Task not created, reason provided
    """
    # Step 1: INSERT task
    # The partial unique index will reject if user has active task
    task = ScrapeTask(
        user_id=user.id,
        url=url,
        state=TaskState.PERMISSION_GRANTED,
    )
    db.add(task)
    
    try:
        # Flush to trigger INSERT and check unique constraint
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        # Check if it's the partial unique index violation
        if "ix_one_active_task_per_user" in str(e.orig):
            return AdmissionError(
                error_type=AdmissionErrorType.ALREADY_HAS_ACTIVE_TASK,
                message="You already have an active scraping task",
            )
        # Re-raise unexpected integrity errors
        raise
    
    # Step 2: Atomic credit deduction
    # UPDATE only if credits > 0, returns affected row count
    result = await db.execute(
        text("""
            UPDATE users 
            SET credits_remaining = credits_remaining - 1,
                updated_at = NOW()
            WHERE id = :user_id AND credits_remaining > 0
        """),
        {"user_id": user.id},
    )
    
    if result.rowcount == 0:
        # No rows affected = insufficient credits
        await db.rollback()
        return AdmissionError(
            error_type=AdmissionErrorType.INSUFFICIENT_CREDITS,
            message="Not enough credits",
        )
    
    # Step 3: Commit
    await db.commit()
    
    # Refresh to get updated values
    await db.refresh(user)
    await db.refresh(task)
    
    return AdmissionSuccess(
        task=task,
        credits_remaining=user.credits_remaining,
    )
