"""Preview generation for saved extraction specs."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import ExtractionSpec, PreviewResult, Project, ProjectState


def _selected_fields(spec: ExtractionSpec) -> list[dict[str, Any]]:
    return [field for field in spec.fields or [] if field.get("selected")]


def build_sample_records(project: Project, spec: ExtractionSpec, max_records: int = 5) -> list[dict[str, Any]]:
    fields = _selected_fields(spec)
    if not fields:
        return []

    row_count = max(
        1,
        min(
            max_records,
            max((len(field.get("sample_values") or []) for field in fields), default=1),
        ),
    )

    records: list[dict[str, Any]] = []
    for index in range(row_count):
        record: dict[str, Any] = {"source_url": project.url}
        for field in fields:
            samples = field.get("sample_values") or []
            key = field.get("user_label") or field.get("label") or field.get("name")
            record[str(key)] = samples[index] if index < len(samples) else None
        records.append(record)
    return records


def build_preview_payload(project: Project, spec: ExtractionSpec) -> dict[str, Any]:
    fields = _selected_fields(spec)
    sample_records = build_sample_records(project, spec)
    missing_fields = [
        {
            "name": field.get("name"),
            "label": field.get("user_label") or field.get("label") or field.get("name"),
            "reason": "No sample value was found during analysis.",
        }
        for field in fields
        if not field.get("sample_values")
    ]
    warnings = list(project.warnings or [])
    for field in fields:
        warnings.extend(field.get("warnings") or [])

    quality_summary = {
        "sample_record_count": len(sample_records),
        "selected_field_count": len(fields),
        "missing_field_count": len(missing_fields),
        "warning_count": len(warnings),
        "source": "analysis_seed_preview",
    }
    return {
        "sample_records": sample_records,
        "warnings": warnings,
        "missing_fields": missing_fields,
        "quality_summary": quality_summary,
    }


async def latest_preview(db: AsyncSession, project_id: int) -> PreviewResult | None:
    result = await db.execute(
        select(PreviewResult)
        .where(PreviewResult.project_id == project_id)
        .order_by(PreviewResult.created_at.desc(), PreviewResult.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_preview(
    db: AsyncSession,
    project: Project,
    spec: ExtractionSpec,
) -> PreviewResult:
    project.state = ProjectState.PREVIEWING
    await db.flush()

    payload = build_preview_payload(project, spec)
    preview = PreviewResult(
        project_id=project.id,
        spec_id=spec.id,
        sample_records=payload["sample_records"],
        warnings=payload["warnings"],
        missing_fields=payload["missing_fields"],
        quality_summary=payload["quality_summary"],
    )
    db.add(preview)
    project.state = ProjectState.PREVIEW_READY
    await db.flush()
    await db.refresh(preview)
    return preview
