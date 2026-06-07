"""Extraction spec creation and update helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.job import ExtractionMode, ExtractionSpec, Project


def default_spec_from_analysis(project: Project) -> dict[str, Any]:
    analysis = project.analysis or {}
    fields: list[dict[str, Any]] = []
    content_config: dict[str, Any] = {}

    if project.extraction_mode == ExtractionMode.STRUCTURED:
        for field in analysis.get("candidate_fields", []) or []:
            confidence = float(field.get("confidence") or 0)
            fields.append(
                {
                    "name": field.get("name"),
                    "label": field.get("label") or field.get("name"),
                    "user_label": field.get("label") or field.get("name"),
                    "selector": field.get("selector"),
                    "type": field.get("data_type") or field.get("type") or "string",
                    "selected": confidence >= 0.7,
                    "required": bool(field.get("required")),
                    "confidence": confidence,
                    "sample_values": field.get("sample_values") or [],
                    "warnings": [],
                }
            )
    else:
        content_config = {
            "primary_selector": analysis.get("primary_content_selector"),
            "recommended_chunking": analysis.get("recommended_chunking"),
            "content_type": analysis.get("content_type"),
            "metadata_fields": analysis.get("metadata_fields") or [],
        }
        for field in analysis.get("metadata_fields", []) or []:
            confidence = float(field.get("confidence") or 0)
            fields.append(
                {
                    "name": field.get("name"),
                    "label": field.get("label") or field.get("name"),
                    "user_label": field.get("label") or field.get("name"),
                    "selector": field.get("selector"),
                    "type": "string",
                    "selected": confidence >= 0.7,
                    "required": False,
                    "confidence": confidence,
                    "sample_values": field.get("sample_values") or [],
                    "warnings": [],
                }
            )

    return {
        "mode": project.extraction_mode,
        "fields": fields,
        "content_config": content_config,
        "url_patterns": [],
        "page_limit": settings.MAX_PAGES_PER_JOB,
        "export_format": "csv",
    }


async def latest_spec(db: AsyncSession, project_id: int) -> ExtractionSpec | None:
    result = await db.execute(
        select(ExtractionSpec)
        .where(ExtractionSpec.project_id == project_id)
        .order_by(ExtractionSpec.created_at.desc(), ExtractionSpec.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def ensure_default_spec(db: AsyncSession, project: Project) -> ExtractionSpec | None:
    spec = await latest_spec(db, project.id)
    if spec is not None:
        return spec
    if not project.analysis:
        return None

    defaults = default_spec_from_analysis(project)
    spec = ExtractionSpec(project_id=project.id, **defaults)
    db.add(spec)
    await db.flush()
    await db.refresh(spec)
    return spec


def selected_field_count(spec: ExtractionSpec | None) -> int:
    if spec is None:
        return 0
    return sum(1 for field in spec.fields or [] if field.get("selected"))
