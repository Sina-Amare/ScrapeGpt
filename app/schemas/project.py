"""Project workflow DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
    selected: bool = True
    required: bool = False
    confidence: float | None = None
    sample_values: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExtractionSpecUpdate(BaseModel):
    fields: list[FieldSpec] | None = None
    content_config: dict[str, Any] | None = None
    url_patterns: list[dict[str, Any]] | None = None
    page_limit: int | None = Field(default=None, ge=1, le=5000)
    export_format: str | None = None

    @field_validator("export_format")
    @classmethod
    def validate_export_format(cls, value: str | None) -> str | None:
        if value is not None and value not in ("csv", "json"):
            raise ValueError("export_format must be csv or json")
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


class ExtractionProgress(BaseModel):
    crawl_pages_total: int = 0
    extracted_records_total: int = 0
    exports_total: int = 0


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


class ProjectResponse(ProjectListItem):
    workflow_mode: str
    render_mode: str
    provider_config_id: int | None = None
    warnings: list[str] = Field(default_factory=list)
    analysis: StructuredAnalysis | ContentAnalysis | dict[str, Any] | None = None
    fetch_metadata: dict[str, Any] | None = None
    spec: ExtractionSpecResponse | None = None
    preview: PreviewResponse | None = None
    progress: ExtractionProgress = Field(default_factory=ExtractionProgress)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExtractRequest(BaseModel):
    extract_anyway: bool = False


class RecordResponse(BaseModel):
    id: int
    source_url: str
    raw_data: dict[str, Any]
    normalized_data: dict[str, Any] | None = None
    warnings: list[Any] = Field(default_factory=list)
    created_at: datetime | None = None
