"""Preview generation for saved extraction specs."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import ExtractionSpec, PreviewResult, Project, ProjectState
from app.services.anti_bot import CHALLENGE_MESSAGES, anti_bot_challenge_reason
from app.services.extractor import extract_records_from_html
from app.services.fetcher import FetchError, fetch_url
from app.services.session_service import get_cookies_for_session
from app.services.url_validator import URLValidationError, validate_url

logger = logging.getLogger(__name__)


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


async def build_selector_preview_payload(
    project: Project,
    spec: ExtractionSpec,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    """Fetch the seed page and execute saved selectors for a real preview."""
    try:
        validated_url = validate_url(project.normalized_url or project.url)
    except URLValidationError as exc:
        raise RuntimeError(str(exc)) from exc

    effective_render_mode = project.render_mode.value
    if (
        effective_render_mode == "AUTO"
        and isinstance(project.fetch_metadata, dict)
        and project.fetch_metadata.get("render_mode_used") == "BROWSER"
    ):
        effective_render_mode = "BROWSER"

    session_cookies: list[dict] | None = None
    if db is not None and project.browser_session_id is not None:
        session_cookies = await get_cookies_for_session(
            db,
            project.browser_session_id,
            owner_user_id=project.user_id,
        )

    try:
        fetched = await fetch_url(
            validated_url,
            effective_render_mode,
            browser_session_cookies=session_cookies,
        )
    except FetchError as exc:
        raise RuntimeError(str(exc)) from exc
    challenge_reason = anti_bot_challenge_reason(fetched.html, fetched.final_url)
    if challenge_reason:
        raise RuntimeError(
            CHALLENGE_MESSAGES.get(challenge_reason, f"Anti-bot challenge detected: {challenge_reason}")
        )

    extracted = extract_records_from_html(
        fetched.html,
        source_url=fetched.final_url,
        project=project,
        spec=spec,
        max_records=5,
    )
    sample_records = [item.normalized_data for item in extracted[:5]]
    selected_fields = _selected_fields(spec)
    missing_fields = []
    for field in selected_fields:
        key = field.get("user_label") or field.get("label") or field.get("name")
        if key and not any(record.get(str(key)) not in (None, "") for record in sample_records):
            missing_fields.append(
                {
                    "name": field.get("name"),
                    "label": key,
                    "reason": "No value was found by the saved selector on the preview page.",
                }
            )
    warnings = list(project.warnings or [])
    for item in extracted:
        warnings.extend(item.warnings)

    return {
        "sample_records": sample_records,
        "warnings": list(dict.fromkeys(str(warning) for warning in warnings if warning)),
        "missing_fields": missing_fields,
        "quality_summary": {
            "sample_record_count": len(sample_records),
            "selected_field_count": len(selected_fields),
            "missing_field_count": len(missing_fields),
            "warning_count": len(warnings),
            "source": "selector_preview",
            "final_url": fetched.final_url,
            "render_mode_used": fetched.render_mode_used.value,
        },
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
    logger.debug("preview.started", extra={"project_id": project.id})
    project.transition_to(ProjectState.PREVIEWING)
    await db.flush()

    try:
        payload = await build_selector_preview_payload(project, spec, db)
    except Exception as exc:
        project.transition_to(ProjectState.FAILED)
        project.error = f"Preview failed: {exc}"
        project.error_code = "PREVIEW_FAILED"
        await db.flush()
        raise

    # Log selector failures for missing fields
    for field in payload.get("missing_fields", []):
        logger.warning(
            "preview.selector_failed",
            extra={
                "project_id": project.id,
                "field_name": field.get("name"),
                "selector": field.get("label"),
            },
        )

    preview = PreviewResult(
        project_id=project.id,
        spec_id=spec.id,
        sample_records=payload["sample_records"],
        warnings=payload["warnings"],
        missing_fields=payload["missing_fields"],
        quality_summary=payload["quality_summary"],
    )
    db.add(preview)
    project.transition_to(ProjectState.PREVIEW_READY)
    await db.flush()
    await db.refresh(preview)

    record_count = len(preview.sample_records or [])
    qs = preview.quality_summary or {}
    selected_count = qs.get("selected_field_count", 0)
    missing_count = qs.get("missing_field_count", 0)
    hit_rate = (
        round((selected_count - missing_count) / selected_count * 100, 1)
        if selected_count > 0
        else 0.0
    )
    logger.info(
        "preview.completed",
        extra={
            "project_id": project.id,
            "record_count": record_count,
            "selector_hit_rate": hit_rate,
        },
    )
    return preview
