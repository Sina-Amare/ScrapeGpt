"""Product-facing project status helpers."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.job import Project, ProjectState


@dataclass(frozen=True)
class ProductStatus:
    code: str
    label: str
    tone: str


def product_status_for(project: Project) -> ProductStatus:
    """Single source of truth for the system-state -> product-status mapping.

    ``system_state`` (ProjectState) stays the authoritative state used for all
    control flow; ``product_status`` is only the user-facing presentation derived
    here and serialized onto ProjectResponse. The frontend renders the serialized
    product_status_* fields and must NOT re-derive a parallel mapping from
    system_state — keep this the one place the mapping lives.
    """
    mapping = {
        ProjectState.QUEUED: ProductStatus("analyzing", "Analyzing site", "warning"),
        ProjectState.ANALYZING: ProductStatus("analyzing", "Analyzing site", "warning"),
        ProjectState.AWAITING_SETUP: ProductStatus("ready_to_review", "Ready to choose fields", "success"),
        ProjectState.ANALYSIS_READY: ProductStatus("ready_to_review", "Ready to choose fields", "success"),
        ProjectState.PREVIEWING: ProductStatus("previewing", "Preparing preview", "warning"),
        ProjectState.PREVIEW_READY: ProductStatus("preview_ready", "Preview ready", "success"),
        ProjectState.DISCOVERING: ProductStatus("extracting", "Finding pages", "warning"),
        ProjectState.EXTRACTING: ProductStatus("extracting", "Extracting data", "warning"),
        ProjectState.EXPORTING: ProductStatus("exporting", "Preparing results", "warning"),
        ProjectState.COMPLETED: ProductStatus("completed", "Results ready", "success"),
        ProjectState.PAUSED: ProductStatus("paused", "Paused", "neutral"),
        ProjectState.FAILED: ProductStatus("failed", "Failed", "danger"),
        ProjectState.CANCELED: ProductStatus("canceled", "Canceled", "neutral"),
    }
    return mapping.get(project.state, ProductStatus("unknown", "Needs attention", "neutral"))


def confidence_label(confidence: float | None, warnings: list | None = None) -> str:
    if confidence is None:
        return "Unknown"
    if confidence >= 0.85 and not warnings:
        return "High"
    if confidence >= 0.65:
        return "Needs review"
    return "Low"


def detected_type(project: Project) -> str | None:
    analysis = project.analysis or {}
    if project.extraction_mode.value == "CONTENT":
        value = analysis.get("content_type")
    else:
        value = analysis.get("page_type")
    return str(value).replace("_", " ").title() if value else None
