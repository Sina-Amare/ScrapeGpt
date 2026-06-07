"""Job DTOs and analysis result schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, HttpUrl, field_validator


# ---------------------------------------------------------------------------
# Analysis result schemas — locked; frontend and tests reference these shapes
# ---------------------------------------------------------------------------


class StructuredCandidateField(BaseModel):
    name: str
    label: str
    selector: str
    data_type: str
    required: bool
    confidence: float
    sample_values: list[str]


class StructuredAnalysis(BaseModel):
    page_type: str
    repeated_item_selector: str
    candidate_fields: list[StructuredCandidateField]
    detail_link_selector: str | None = None
    pagination_selector: str | None = None
    estimated_pages: int | None = None
    warnings: list[str]
    confidence: float


class ContentMetadataField(BaseModel):
    name: str
    label: str
    selector: str
    confidence: float
    sample_values: list[str]


class ContentAnalysis(BaseModel):
    content_type: str
    primary_content_selector: str
    estimated_pages: int | None = None
    avg_content_length: int | None = None
    recommended_chunking: str | None = None
    metadata_fields: list[ContentMetadataField]
    warnings: list[str]
    confidence: float


# ---------------------------------------------------------------------------
# Request / response DTOs
# ---------------------------------------------------------------------------


class JobCreate(BaseModel):
    url: HttpUrl
    extraction_mode: str = "STRUCTURED"
    workflow_mode: str = "GUIDED"
    render_mode: str = "AUTO"
    provider_config_id: int | None = None

    @field_validator("extraction_mode")
    @classmethod
    def validate_extraction_mode(cls, v: str) -> str:
        if v not in ("STRUCTURED", "CONTENT"):
            raise ValueError("extraction_mode must be STRUCTURED or CONTENT")
        return v

    @field_validator("workflow_mode")
    @classmethod
    def validate_workflow_mode(cls, v: str) -> str:
        if v not in ("GUIDED", "FAST"):
            raise ValueError("workflow_mode must be GUIDED or FAST")
        return v

    @field_validator("render_mode")
    @classmethod
    def validate_render_mode(cls, v: str) -> str:
        if v not in ("AUTO", "STATIC", "BROWSER"):
            raise ValueError("render_mode must be AUTO, STATIC, or BROWSER")
        return v


class JobResponse(BaseModel):
    """Full job detail response."""
    id: int
    state: str
    url: str
    extraction_mode: str
    workflow_mode: str
    render_mode: str
    confidence: float | None = None
    warnings: list[str] | None = None
    analysis: dict[str, Any] | None = None
    fetch_metadata: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    provider_config_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class JobListItem(BaseModel):
    """Lightweight job row for list endpoint."""
    id: int
    state: str
    url: str
    extraction_mode: str
    workflow_mode: str
    render_mode: str
    confidence: float | None = None
    error: str | None = None
    error_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True
