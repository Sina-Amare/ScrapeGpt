"""
ScrapeTask model for tracking scraping job states.

Each user can have at most one non-terminal task at a time,
enforced by a partial unique index at the database level.
"""

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class TaskState(str, enum.Enum):
    """
    State machine for scrape tasks.

    Flow:
        PERMISSION_GRANTED → SCRAPING → SCRAPED → LLM_PROCESSING → COMPLETED
                                 ↓                      ↓
                              FAILED                 FAILED

    Terminal states: COMPLETED, FAILED
    """
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    SCRAPING = "SCRAPING"
    SCRAPED = "SCRAPED"
    LLM_PROCESSING = "LLM_PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# Valid state transitions
VALID_TRANSITIONS: dict[TaskState, list[TaskState]] = {
    TaskState.PERMISSION_GRANTED: [TaskState.SCRAPING],
    TaskState.SCRAPING: [TaskState.SCRAPED, TaskState.FAILED],
    TaskState.SCRAPED: [TaskState.LLM_PROCESSING, TaskState.FAILED],
    TaskState.LLM_PROCESSING: [TaskState.COMPLETED, TaskState.FAILED],
    TaskState.COMPLETED: [],  # Terminal
    TaskState.FAILED: [],  # Terminal
}

TERMINAL_STATES = {TaskState.COMPLETED, TaskState.FAILED}


class ScrapeTask(Base):
    """
    Scrape task tracking model.

    Invariant: At most one non-terminal task per user.
    Enforced by partial unique index: WHERE state NOT IN ('COMPLETED', 'FAILED')
    """

    __tablename__ = "scrape_tasks"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    state: Mapped[TaskState] = mapped_column(
        Enum(TaskState, name="task_state", native_enum=True),
        nullable=False,
        default=TaskState.PERMISSION_GRANTED,
        index=True,
    )

    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )

    # Scraped content
    content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Error message (populated on FAILED)
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # LLM result (populated on COMPLETED)
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )

    user = relationship("User", back_populates="scrape_tasks")

    @property
    def is_terminal(self) -> bool:
        """Check if task is in a terminal state."""
        return self.state in TERMINAL_STATES

    def can_transition_to(self, new_state: TaskState) -> bool:
        """Check if transition to new_state is valid."""
        return new_state in VALID_TRANSITIONS.get(self.state, [])

    def __repr__(self) -> str:
        return f"<ScrapeTask {self.id} state={self.state.value}>"

