"""Job model for analysis job state machine."""

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class JobState(str, enum.Enum):
    QUEUED = "QUEUED"
    ANALYZING = "ANALYZING"
    AWAITING_SETUP = "AWAITING_SETUP"
    ANALYSIS_READY = "ANALYSIS_READY"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class ExtractionMode(str, enum.Enum):
    STRUCTURED = "STRUCTURED"
    CONTENT = "CONTENT"


class WorkflowMode(str, enum.Enum):
    GUIDED = "GUIDED"
    FAST = "FAST"


class RenderMode(str, enum.Enum):
    AUTO = "AUTO"
    STATIC = "STATIC"
    BROWSER = "BROWSER"


VALID_JOB_TRANSITIONS: dict[JobState, list[JobState]] = {
    JobState.QUEUED: [JobState.ANALYZING, JobState.FAILED, JobState.CANCELED],
    JobState.ANALYZING: [
        JobState.AWAITING_SETUP,
        JobState.ANALYSIS_READY,
        JobState.FAILED,
        JobState.CANCELED,
    ],
    JobState.AWAITING_SETUP: [],
    JobState.ANALYSIS_READY: [],
    JobState.FAILED: [],
    JobState.CANCELED: [],
}

# States where the background executor is finished — no more transitions expected.
TERMINAL_JOB_STATES = {
    JobState.AWAITING_SETUP,
    JobState.ANALYSIS_READY,
    JobState.FAILED,
    JobState.CANCELED,
}

# States counted against the active-job admission limit.
ACTIVE_JOB_STATES = {JobState.QUEUED, JobState.ANALYZING}

# States from which the job can be deleted.
DELETABLE_JOB_STATES = TERMINAL_JOB_STATES


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    provider_config_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("provider_configs.id", ondelete="SET NULL"),
        nullable=True,
    )

    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    normalized_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    extraction_mode: Mapped[ExtractionMode] = mapped_column(
        Enum(ExtractionMode, name="extraction_mode", native_enum=True),
        nullable=False,
        default=ExtractionMode.STRUCTURED,
    )

    workflow_mode: Mapped[WorkflowMode] = mapped_column(
        Enum(WorkflowMode, name="workflow_mode", native_enum=True),
        nullable=False,
        default=WorkflowMode.GUIDED,
    )

    render_mode: Mapped[RenderMode] = mapped_column(
        Enum(RenderMode, name="render_mode", native_enum=True),
        nullable=False,
        default=RenderMode.AUTO,
    )

    state: Mapped[JobState] = mapped_column(
        Enum(JobState, name="job_state", native_enum=True),
        nullable=False,
        default=JobState.QUEUED,
        index=True,
    )

    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    warnings: Mapped[list[Any] | None] = mapped_column(
        JSONB, nullable=True, server_default="'[]'::jsonb"
    )

    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    fetch_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

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

    user = relationship("User", backref="jobs")
    provider_config = relationship("ProviderConfig", foreign_keys=[provider_config_id])

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_JOB_STATES

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_JOB_STATES

    def can_transition_to(self, new_state: JobState) -> bool:
        return new_state in VALID_JOB_TRANSITIONS.get(self.state, [])

    def __repr__(self) -> str:
        return f"<Job {self.id} state={self.state.value}>"


class AnalysisCache(Base):
    __tablename__ = "analysis_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_mode: Mapped[ExtractionMode] = mapped_column(
        Enum(ExtractionMode, name="extraction_mode", native_enum=True), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(16), nullable=False)

    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    normalized_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
