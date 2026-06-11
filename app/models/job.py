"""Project models for the extraction workflow.

``Job`` remains as a compatibility alias while the product moves to
project-based extraction.
"""

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ProjectState(str, enum.Enum):
    QUEUED = "QUEUED"
    ANALYZING = "ANALYZING"
    AWAITING_SETUP = "AWAITING_SETUP"
    ANALYSIS_READY = "ANALYSIS_READY"
    PREVIEWING = "PREVIEWING"
    PREVIEW_READY = "PREVIEW_READY"
    DISCOVERING = "DISCOVERING"
    EXTRACTING = "EXTRACTING"
    EXPORTING = "EXPORTING"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
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


class CrawlPageState(str, enum.Enum):
    PENDING = "PENDING"
    FETCHING = "FETCHING"
    FETCHED = "FETCHED"
    EXTRACTED = "EXTRACTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class CrawlScopeMode(str, enum.Enum):
    CURRENT_PAGE = "CURRENT_PAGE"
    PAGINATION = "PAGINATION"
    DATASET = "DATASET"
    FULL_SITE = "FULL_SITE"


class CrawlScopeStatus(str, enum.Enum):
    AI_SUGGESTED = "AI_SUGGESTED"
    USER_CONFIRMED = "USER_CONFIRMED"
    SYSTEM_DEFAULTED = "SYSTEM_DEFAULTED"


CRAWL_SCOPE_VERSION = 1
DEFAULT_CRAWL_SCOPE: dict[str, Any] = {
    "version": CRAWL_SCOPE_VERSION,
    "mode": CrawlScopeMode.CURRENT_PAGE.value,
    "status": CrawlScopeStatus.SYSTEM_DEFAULTED.value,
    "seed_url": None,
    "max_pages": 500,
    "max_depth": 0,
    "include_patterns": [],
    "exclude_patterns": [],
    "pagination": {},
    "link_rules": [],
    "ai_recommendation": None,
    "user_confirmed_at": None,
}

LEGACY_COMPAT_CRAWL_SCOPE: dict[str, Any] = {
    "version": CRAWL_SCOPE_VERSION,
    "mode": CrawlScopeMode.FULL_SITE.value,
    "status": CrawlScopeStatus.SYSTEM_DEFAULTED.value,
    "seed_url": None,
    "max_pages": 500,
    "max_depth": None,
    "include_patterns": [],
    "exclude_patterns": [],
    "pagination": {},
    "link_rules": [],
    "ai_recommendation": None,
    "user_confirmed_at": None,
}



VALID_PROJECT_TRANSITIONS: dict[ProjectState, list[ProjectState]] = {
    ProjectState.QUEUED: [ProjectState.ANALYZING, ProjectState.FAILED, ProjectState.CANCELED],
    ProjectState.ANALYZING: [
        ProjectState.AWAITING_SETUP,
        ProjectState.ANALYSIS_READY,
        ProjectState.FAILED,
        ProjectState.CANCELED,
    ],
    ProjectState.AWAITING_SETUP: [
        ProjectState.PREVIEWING,
        ProjectState.DISCOVERING,
        ProjectState.FAILED,
        ProjectState.CANCELED,
    ],
    ProjectState.ANALYSIS_READY: [
        ProjectState.PREVIEWING,
        ProjectState.DISCOVERING,
        ProjectState.FAILED,
        ProjectState.CANCELED,
    ],
    ProjectState.PREVIEWING: [
        ProjectState.PREVIEW_READY,
        ProjectState.FAILED,
        ProjectState.CANCELED,
    ],
    ProjectState.PREVIEW_READY: [
        ProjectState.DISCOVERING,
        ProjectState.FAILED,
        ProjectState.CANCELED,
    ],
    ProjectState.DISCOVERING: [
        ProjectState.EXTRACTING,
        ProjectState.PAUSED,
        ProjectState.FAILED,
        ProjectState.CANCELED,
    ],
    ProjectState.EXTRACTING: [
        ProjectState.EXPORTING,
        ProjectState.PAUSED,
        ProjectState.FAILED,
        ProjectState.CANCELED,
    ],
    ProjectState.EXPORTING: [
        ProjectState.COMPLETED,
        ProjectState.FAILED,
        ProjectState.CANCELED,
    ],
    ProjectState.PAUSED: [ProjectState.DISCOVERING, ProjectState.EXTRACTING, ProjectState.FAILED, ProjectState.CANCELED],
    ProjectState.COMPLETED: [ProjectState.DISCOVERING],
    ProjectState.FAILED: [
        ProjectState.QUEUED,          # re-run full analysis (analysis itself failed)
        ProjectState.ANALYSIS_READY,  # reset for re-extraction (analysis was OK)
        ProjectState.PREVIEW_READY,   # reset for re-extraction (preview was also OK)
    ],
    ProjectState.CANCELED: [],
}

# States where no background work is running.
TERMINAL_PROJECT_STATES = {
    ProjectState.AWAITING_SETUP,
    ProjectState.ANALYSIS_READY,
    ProjectState.PREVIEW_READY,
    ProjectState.COMPLETED,
    ProjectState.FAILED,
    ProjectState.CANCELED,
}

# States counted against the active-project admission limit.
ACTIVE_PROJECT_STATES = {
    ProjectState.QUEUED,
    ProjectState.ANALYZING,
    ProjectState.PREVIEWING,
    ProjectState.DISCOVERING,
    ProjectState.EXTRACTING,
    ProjectState.EXPORTING,
    ProjectState.PAUSED,
}

# States from which a project can be deleted.
DELETABLE_PROJECT_STATES = TERMINAL_PROJECT_STATES


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    provider_config_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("provider_configs.id", ondelete="SET NULL"),
        nullable=True,
    )

    browser_session_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("browser_sessions.id", ondelete="SET NULL"),
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

    state: Mapped[ProjectState] = mapped_column(
        Enum(ProjectState, name="job_state", native_enum=True),
        nullable=False,
        default=ProjectState.QUEUED,
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

    user = relationship("User", backref="projects")
    provider_config = relationship(
        "ProviderConfig", foreign_keys=[provider_config_id]
    )
    browser_session = relationship(
        "BrowserSession",
        foreign_keys=[browser_session_id],
        back_populates="projects",
    )
    specs = relationship(
        "ExtractionSpec",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ExtractionSpec.created_at.desc()",
    )
    preview_results = relationship(
        "PreviewResult",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="PreviewResult.created_at.desc()",
    )
    crawl_pages = relationship(
        "CrawlPage",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    extracted_records = relationship(
        "ExtractedRecord",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    exports = relationship(
        "Export",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    frontier_previews = relationship(
        "FrontierPreview",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="FrontierPreview.created_at.desc()",
    )

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_PROJECT_STATES

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_PROJECT_STATES

    def can_transition_to(self, new_state: ProjectState) -> bool:
        return new_state in VALID_PROJECT_TRANSITIONS.get(self.state, [])

    def transition_to(self, new_state: ProjectState) -> None:
        """Validate and apply a state transition.

        Raises ValueError if the transition is not allowed by
        VALID_PROJECT_TRANSITIONS.  This keeps the state-machine
        invariant enforced at the model layer rather than relying
        on each caller to check manually.
        """
        if not self.can_transition_to(new_state):
            raise ValueError(
                f"Illegal transition: {self.state.value} → {new_state.value} "
                f"(project {self.id})"
            )
        self.state = new_state

    def __repr__(self) -> str:
        return f"<Project {self.id} state={self.state.value}>"


class ExtractionSpec(Base):
    __tablename__ = "extraction_specs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mode: Mapped[ExtractionMode] = mapped_column(
        Enum(ExtractionMode, name="extraction_mode", native_enum=True),
        nullable=False,
        default=ExtractionMode.STRUCTURED,
    )
    fields: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="'[]'::jsonb")
    content_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="'{}'::jsonb"
    )
    url_patterns: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="'[]'::jsonb")
    page_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=500, server_default="500")
    export_format: Mapped[str] = mapped_column(String(16), nullable=False, default="csv", server_default="csv")
    crawl_scope: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    quality_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    project = relationship("Project", back_populates="specs")


class PreviewResult(Base):
    __tablename__ = "preview_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    spec_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("extraction_specs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sample_records: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="'[]'::jsonb")
    missing_fields: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )
    quality_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="'{}'::jsonb"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    project = relationship("Project", back_populates="preview_results")
    spec = relationship("ExtractionSpec")


class CrawlPage(Base):
    __tablename__ = "crawl_pages"
    __table_args__ = (UniqueConstraint("project_id", "normalized_url", name="uq_crawl_pages_project_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    normalized_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    state: Mapped[CrawlPageState] = mapped_column(
        Enum(CrawlPageState, name="crawl_page_state", native_enum=True),
        nullable=False,
        default=CrawlPageState.PENDING,
        index=True,
    )
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    block_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    project = relationship("Project", back_populates="crawl_pages")


class ExtractedRecord(Base):
    __tablename__ = "extracted_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("crawl_pages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    normalized_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="'[]'::jsonb")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    project = relationship("Project", back_populates="extracted_records")
    page = relationship("CrawlPage")


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    spec_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    project = relationship("Project", back_populates="exports")


class FrontierPreview(Base):
    __tablename__ = "frontier_previews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    spec_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("extraction_specs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    included_urls: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="'[]'::jsonb")
    excluded_urls: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="'[]'::jsonb")
    estimated_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="'[]'::jsonb")
    quality_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="'{}'::jsonb")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    project = relationship("Project", back_populates="frontier_previews")
    spec = relationship("ExtractionSpec")


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

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment=(
            "When this cache entry expires. Null = no expiry. "
            "Purged by the watchdog when past now()."
        ),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )


# Compatibility exports for Phase 1 API/tests.
Job = Project
JobState = ProjectState
VALID_JOB_TRANSITIONS = VALID_PROJECT_TRANSITIONS
TERMINAL_JOB_STATES = TERMINAL_PROJECT_STATES
ACTIVE_JOB_STATES = ACTIVE_PROJECT_STATES
DELETABLE_JOB_STATES = DELETABLE_PROJECT_STATES
