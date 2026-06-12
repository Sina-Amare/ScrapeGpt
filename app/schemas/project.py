"""Project workflow DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import soupsieve as sv
from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.schemas.job import ContentAnalysis, StructuredAnalysis


class ProjectAdvancedOptions(BaseModel):
    extraction_mode: str | None = None
    workflow_mode: str | None = None
    render_mode: str | None = None
    provider_config_id: int | None = None

    @field_validator("extraction_mode")
    @classmethod
    def validate_extraction_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in ("STRUCTURED", "CONTENT"):
            raise ValueError("extraction_mode must be STRUCTURED or CONTENT")
        return value

    @field_validator("workflow_mode")
    @classmethod
    def validate_workflow_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in ("GUIDED", "FAST"):
            raise ValueError("workflow_mode must be GUIDED or FAST")
        return value

    @field_validator("render_mode")
    @classmethod
    def validate_render_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in ("AUTO", "STATIC", "BROWSER"):
            raise ValueError("render_mode must be AUTO, STATIC, or BROWSER")
        return value


class ProjectAnalyzeRequest(BaseModel):
    url: HttpUrl
    advanced: ProjectAdvancedOptions | None = None


class FieldSpec(BaseModel):
    name: str | None = None
    label: str | None = None
    user_label: str | None = None
    selector: str | None = None
    type: str = "string"

    @field_validator("selector")
    @classmethod
    def validate_selector_syntax(cls, value: str | None) -> str | None:
        if value is not None and value.strip():
            try:
                sv.compile(value.strip())
            except Exception as exc:
                raise ValueError(f"Invalid CSS selector: {exc}") from exc
        return value
    selected: bool = True
    required: bool = False
    confidence: float | None = None
    sample_values: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


VALID_CRAWL_SCOPE_MODES = ("CURRENT_PAGE", "PAGINATION", "DATASET", "FULL_SITE")
VALID_CRAWL_SCOPE_STATUSES = ("AI_SUGGESTED", "USER_CONFIRMED", "SYSTEM_DEFAULTED")


class CrawlScopeLinkRule(BaseModel):
    role: str
    action: str
    selector: str | None = None
    pattern: str | None = None
    reason: str | None = None
    confidence: float | None = None


class CrawlScopePagination(BaseModel):
    selector: str | None = None
    url_pattern: str | None = None
    estimated_pages: int | None = None


class CrawlScopeAiRecommendation(BaseModel):
    recommended_mode: str
    confidence: float
    warnings: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class CrawlScope(BaseModel):
    version: int = 1
    mode: str
    status: str
    seed_url: str | None = None
    max_pages: int = 500
    max_depth: int | None = None
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    pagination: CrawlScopePagination = Field(default_factory=CrawlScopePagination)
    link_rules: list[CrawlScopeLinkRule] = Field(default_factory=list)
    ai_recommendation: CrawlScopeAiRecommendation | None = None
    user_confirmed_at: datetime | None = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in VALID_CRAWL_SCOPE_MODES:
            raise ValueError(
                f"crawl_scope.mode must be one of {VALID_CRAWL_SCOPE_MODES}"
            )
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in VALID_CRAWL_SCOPE_STATUSES:
            raise ValueError(
                f"crawl_scope.status must be one of {VALID_CRAWL_SCOPE_STATUSES}"
            )
        return value

    @field_validator("max_pages")
    @classmethod
    def validate_max_pages(cls, value: int) -> int:
        if value < 1 or value > 5000:
            raise ValueError("crawl_scope.max_pages must be between 1 and 5000")
        return value


class FrontierUrlDecision(BaseModel):
    url: str
    normalized_url: str
    source_url: str | None = None
    depth: int = 0
    decision: str  # "included" | "excluded"
    role: str | None = None
    reason_code: str
    reason: str
    confidence: float | None = None
    link_text: str | None = None


class FrontierPreviewResponse(BaseModel):
    id: int
    project_id: int
    spec_id: int
    scope_hash: str
    included_urls: list[FrontierUrlDecision]
    excluded_urls: list[FrontierUrlDecision]
    estimated_page_count: int | None = None
    warnings: list[Any] = Field(default_factory=list)
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class FrontierPreviewSummary(BaseModel):
    id: int
    project_id: int
    spec_id: int
    scope_hash: str
    estimated_page_count: int | None = None
    created_at: datetime | None = None


class ExtractionSpecUpdate(BaseModel):
    fields: list[FieldSpec] | None = None
    content_config: dict[str, Any] | None = None
    url_patterns: list[dict[str, Any]] | None = None
    page_limit: int | None = Field(default=None, ge=1, le=5000)
    export_format: str | None = None
    crawl_scope: CrawlScope | None = None

    @field_validator("export_format")
    @classmethod
    def validate_export_format(cls, value: str | None) -> str | None:
        if value is not None and value not in ("csv", "json", "xlsx"):
            raise ValueError("export_format must be csv, json, or xlsx")
        return value



class ExtractionSpecResponse(BaseModel):
    id: int
    project_id: int
    mode: str
    fields: list[dict[str, Any]]
    content_config: dict[str, Any]
    url_patterns: list[dict[str, Any]]
    page_limit: int
    export_format: str
    crawl_scope: dict[str, Any] | None = None
    quality_summary: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None



class PreviewResponse(BaseModel):
    id: int
    project_id: int
    spec_id: int
    sample_records: list[dict[str, Any]]
    warnings: list[Any]
    missing_fields: list[Any]
    quality_summary: dict[str, Any]
    created_at: datetime | None = None


class BlockedPageDetail(BaseModel):
    url: str
    block_reason: str
    error: str | None = None


class ExtractionProgress(BaseModel):
    crawl_pages_total: int = 0
    crawl_pages_pending: int = 0
    crawl_pages_fetching: int = 0
    crawl_pages_extracted: int = 0
    crawl_pages_blocked: int = 0
    crawl_pages_failed: int = 0
    extracted_records_total: int = 0
    exports_total: int = 0
    blocked_pages_detail: list[BlockedPageDetail] = Field(default_factory=list)
    failed_pages_detail: list[BlockedPageDetail] = Field(default_factory=list)


class ProjectListItem(BaseModel):
    id: int
    url: str
    system_state: str
    product_status: str
    product_status_label: str
    product_status_tone: str
    detected_type: str | None = None
    confidence: float | None = None
    confidence_label: str
    selected_field_count: int
    extraction_mode: str
    last_activity: datetime | None = None
    error: str | None = None
    error_code: str | None = None


class ExtractionQuality(BaseModel):
    overall: str = "unknown"  # "good" | "needs_review" | "risky" | "unknown"
    field_success_rates: dict[str, float] = Field(default_factory=dict)
    missing_field_rates: dict[str, float] = Field(default_factory=dict)
    warnings: list[Any] = Field(default_factory=list)


class ProjectResponse(ProjectListItem):
    workflow_mode: str
    render_mode: str
    provider_config_id: int | None = None
    browser_session_id: int | None = None
    warnings: list[str] = Field(default_factory=list)
    analysis: StructuredAnalysis | ContentAnalysis | dict[str, Any] | None = None
    fetch_metadata: dict[str, Any] | None = None
    spec: ExtractionSpecResponse | None = None
    preview: PreviewResponse | None = None
    frontier_preview: FrontierPreviewResponse | None = None
    extraction_quality: ExtractionQuality | None = None
    preview_stale: bool = False
    progress: ExtractionProgress = Field(default_factory=ExtractionProgress)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RecordPageResponse(BaseModel):
    items: list[RecordResponse]
    total: int
    skip: int
    limit: int
    next_skip: int | None = None
    has_more: bool
    columns: list[str] = Field(default_factory=list)



class ExtractRequest(BaseModel):
    extract_anyway: bool = False


class RecordResponse(BaseModel):
    id: int
    source_url: str
    raw_data: dict[str, Any]
    normalized_data: dict[str, Any] | None = None
    warnings: list[Any] = Field(default_factory=list)
    created_at: datetime | None = None
