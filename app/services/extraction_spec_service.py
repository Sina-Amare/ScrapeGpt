"""Extraction spec creation and update helpers."""

from __future__ import annotations

import logging
from typing import Any

import soupsieve as sv
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.job import ExtractionMode, ExtractionSpec, Project
from app.services.crawl_scope import default_crawl_scope

logger = logging.getLogger(__name__)

# Maximum containers to probe when testing field selectors.
_VALIDATION_CONTAINER_SAMPLE = 5


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


def _selector_matches(scope: Any, selector: str) -> bool:
    """Return True if *selector* matches at least one element inside *scope*."""
    try:
        return bool(scope.select(selector))
    except Exception:
        return False


def validate_selectors_against_html(
    analysis: dict[str, Any], html: str
) -> dict[str, Any]:
    """Validate LLM-generated CSS selectors against the actual fetched HTML.

    Called immediately after analyze_page() in the job pipeline so that even
    cached analysis results are re-checked against fresh HTML.

    Rules applied:
    - repeated_item_selector that matches nothing → overall confidence capped at
      0.4 and a warning added; field validation continues against the full page.
    - A field selector that matches nothing in any sampled container:
        * required → set to False (can't require a field with no data)
        * confidence → capped at 0.3
        * warning added to the field's warning list
    - Selectors that do match are left completely unchanged.

    Returns the same *analysis* dict, mutated in place.
    """
    if not analysis or not html:
        return analysis

    fields: list[dict[str, Any]] = analysis.get("candidate_fields") or []
    if not fields:
        return analysis

    soup = BeautifulSoup(html, "lxml")

    # --- Validate the container selector ---
    container_sel: str | None = analysis.get("repeated_item_selector")
    containers: list[Any] = []
    if container_sel:
        try:
            containers = soup.select(str(container_sel))
        except Exception:
            containers = []

    if container_sel and not containers:
        analysis.setdefault("warnings", [])
        analysis["warnings"].append(
            f"Container selector '{container_sel}' matched no elements in the "
            "fetched HTML. Field selectors will be tested against the full page."
        )
        analysis["confidence"] = round(
            min(float(analysis.get("confidence") or 0.5), 0.4), 2
        )
        logger.warning(
            "spec.container_selector_zero_match",
            extra={"selector": container_sel},
        )

    # Scopes to probe: up to N sampled containers, or the full page if none.
    scopes: list[Any] = (
        containers[:_VALIDATION_CONTAINER_SAMPLE] if containers else [soup]
    )

    # --- Validate each candidate field ---
    for field in fields:
        selector = field.get("selector")
        if not selector:
            continue

        matched = any(_selector_matches(scope, str(selector)) for scope in scopes)

        if not matched:
            original_conf = float(field.get("confidence") or 0)
            field["confidence"] = round(min(original_conf, 0.3), 2)
            field["required"] = False
            field.setdefault("warnings", [])
            field["warnings"].append(
                f"Selector '{selector}' matched no elements in the fetched HTML. "
                "Field marked non-required; verify the selector in Sample Preview."
            )
            logger.warning(
                "spec.field_selector_zero_match",
                extra={
                    "field": field.get("name"),
                    "selector": selector,
                    "container": container_sel,
                },
            )

    return analysis


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
        # Propagate any warnings added by validate_selectors_against_html.
        "warnings": list(field.get("warnings") or []),
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
