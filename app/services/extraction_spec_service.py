"""Extraction spec creation and update helpers."""

from __future__ import annotations

import logging
from typing import Any

import soupsieve as sv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.job import ExtractionMode, ExtractionSpec, Project
from app.services.crawl_scope import default_crawl_scope

logger = logging.getLogger(__name__)


def _validated_selector(raw: str | None, field_name: str | None) -> str | None:
    if not raw or not raw.strip():
        return raw
    try:
        sv.compile(raw.strip())
        return raw
    except Exception as exc:
        logger.warning(
            "spec.invalid_selector_from_analysis",
            extra={"field_name": field_name, "selector": raw, "error": str(exc)},
        )
        return None


def _build_field(field: dict[str, Any], confidence: float) -> dict[str, Any]:
    name = field.get("name")
    return {
        "name": name,
        "label": field.get("label") or name,
        "user_label": field.get("label") or name,
        "selector": _validated_selector(field.get("selector"), name),
        "type": field.get("data_type") or field.get("type") or "string",
        "selected": confidence >= 0.7,
        "required": bool(field.get("required")),
        "confidence": confidence,
        "sample_values": field.get("sample_values") or [],
        "warnings": [],
    }


def default_spec_from_analysis(project: Project) -> dict[str, Any]:
    analysis = project.analysis or {}
    fields: list[dict[str, Any]] = []
    content_config: dict[str, Any] = {}

    if project.extraction_mode == ExtractionMode.STRUCTURED:
        for field in analysis.get("candidate_fields", []) or []:
            confidence = float(field.get("confidence") or 0)
            fields.append(_build_field(field, confidence))
    else:
        content_config = {
            "primary_selector": analysis.get("primary_content_selector"),
            "recommended_chunking": analysis.get("recommended_chunking"),
            "content_type": analysis.get("content_type"),
            "metadata_fields": analysis.get("metadata_fields") or [],
        }
        for field in analysis.get("metadata_fields", []) or []:
            confidence = float(field.get("confidence") or 0)
            entry = _build_field(field, confidence)
            entry["type"] = "string"
            entry["required"] = False
            fields.append(entry)

    return {
        "mode": project.extraction_mode,
        "fields": fields,
        "content_config": content_config,
        "url_patterns": [],
        "page_limit": settings.MAX_PAGES_PER_JOB,
        "export_format": "csv",
        "crawl_scope": default_crawl_scope(project, analysis),
    }


async def latest_spec(
    db: AsyncSession, project_id: int
) -> ExtractionSpec | None:
    result = await db.execute(
        select(ExtractionSpec)
        .where(ExtractionSpec.project_id == project_id)
        .order_by(ExtractionSpec.created_at.desc(), ExtractionSpec.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def ensure_default_spec(
    db: AsyncSession, project: Project
) -> ExtractionSpec | None:
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
