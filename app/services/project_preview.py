"""Preview generation for saved extraction specs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import ExtractionSpec, PreviewResult, Project, ProjectState
from app.services.anti_bot import CHALLENGE_MESSAGES, anti_bot_challenge_reason
from app.services.extraction_quality import detect_duplicate_column_warnings
from app.services.fetcher import (
    FetchError,
    apply_interactions_and_capture,
    fetch_url,
)
from app.services.interaction_extraction import extract_records_with_variants
from app.services.interaction_profile import InteractionError, is_enabled
from app.services.session_service import get_cookies_for_session
from app.services.url_validator import URLValidationError, validate_url

logger = logging.getLogger(__name__)


def _selected_fields(spec: ExtractionSpec) -> list[dict[str, Any]]:
    return [field for field in spec.fields or [] if field.get("selected")]


def _field_key(field: dict[str, Any]) -> str:
    return str(field.get("user_label") or field.get("label") or field.get("name") or "field")


def _spec_preview_fingerprint(spec: ExtractionSpec) -> str:
    """Stable fingerprint for the parts of a spec that affect the SAMPLE preview.

    Timestamps are not enough because some legacy or direct JSON mutations can
    leave an old PreviewResult attached to a changed spec row. The fingerprint
    lets the API say "this preview validates this exact spec shape".

    Only the inputs that change what the sample preview extracts from the SEED
    page are included: the extraction mode, the field selectors, the content
    config, and the interaction/variant profile. ``crawl_scope`` (and the
    crawl-breadth knobs ``page_limit``/``url_patterns``) are deliberately
    EXCLUDED — they govern which OTHER pages get crawled, not the seed-page
    sample, and the scope has its own confirmation gate (SCOPE_NOT_CONFIRMED).
    Including ``crawl_scope`` made an unrelated action — generating a frontier
    preview, which self-configures/normalises the scope onto the spec — falsely
    mark a fresh sample preview stale and block extraction in the natural UI
    order (preview fields -> view crawl frontier -> extract).
    """
    payload = {
        "mode": spec.mode.value if hasattr(spec.mode, "value") else str(spec.mode),
        "fields": spec.fields or [],
        "content_config": spec.content_config or {},
        "interaction_profile": getattr(spec, "interaction_profile", None) or {},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _legacy_preview_shape_matches_spec(preview: PreviewResult, spec: ExtractionSpec) -> bool:
    """Best-effort guard for previews created before fingerprints existed."""
    selected_keys = [_field_key(field) for field in _selected_fields(spec)]
    summary = preview.quality_summary or {}
    selected_count = summary.get("selected_field_count")
    if selected_count is not None:
        try:
            if int(selected_count) != len(selected_keys):
                return False
        except (TypeError, ValueError):
            return False

    samples = preview.sample_records or []
    if samples and selected_keys:
        seen_keys = {
            key
            for sample in samples
            if isinstance(sample, dict)
            for key in sample.keys()
        }
        if not set(selected_keys).issubset(seen_keys):
            return False
    return True


def preview_matches_spec(preview: PreviewResult | None, spec: ExtractionSpec | None) -> bool:
    """Whether *preview* can be trusted as validation for the current *spec*."""
    if preview is None or spec is None:
        return False
    if preview.spec_id != spec.id:
        return False

    summary = preview.quality_summary or {}
    actual_fingerprint = summary.get("spec_fingerprint")
    if actual_fingerprint:
        return actual_fingerprint == _spec_preview_fingerprint(spec)

    spec_updated = spec.updated_at or spec.created_at
    preview_created = preview.created_at
    if spec_updated is None or preview_created is None or spec_updated > preview_created:
        return False
    return _legacy_preview_shape_matches_spec(preview, spec)


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
        err = RuntimeError(str(exc))
        # Preserve the stable fetch code (e.g. BROWSER_DRIVER_CRASHED) so the
        # preview caller can classify the failure and the UI can show friendly
        # copy instead of a raw driver string.
        err.error_code = exc.error_code  # type: ignore[attr-defined]
        raise err from exc
    challenge_reason = await asyncio.to_thread(
        anti_bot_challenge_reason, fetched.html, fetched.final_url
    )
    if challenge_reason:
        raise RuntimeError(
            CHALLENGE_MESSAGES.get(challenge_reason, f"Anti-bot challenge detected: {challenge_reason}")
        )

    variants_on = is_enabled(getattr(spec, "interaction_profile", None))

    async def _fetch_variant_htmls(
        recipes: dict[str, list[dict]],
    ) -> dict[str, str]:
        try:
            return await apply_interactions_and_capture(
                fetched.final_url, recipes, cookies=session_cookies
            )
        except FetchError as exc:
            if exc.error_code == "BROWSER_UNAVAILABLE":
                raise InteractionError(
                    "A browser backend is required to preview the selected "
                    "interactive variant(s).",
                    code="INTERACTION_BROWSER_REQUIRED",
                ) from exc
            raise

    async def _fetch_variant_url_htmls(urls: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for vid, vurl in urls.items():
            v = validate_url(vurl)
            vf = await fetch_url(
                v, effective_render_mode, browser_session_cookies=session_cookies
            )
            out[vid] = vf.html
        return out

    extracted, variant_warnings = await extract_records_with_variants(
        base_html=fetched.html,
        source_url=fetched.final_url,
        project=project,
        spec=spec,
        max_records=5,
        fetch_variant_htmls=_fetch_variant_htmls,
        fetch_variant_url_htmls=_fetch_variant_url_htmls,
    )
    # Show more sample rows when variants are on so several show through.
    display_limit = 10 if variants_on else 5
    sample_records = [item.normalized_data for item in extracted[:display_limit]]
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
    warnings.extend(variant_warnings)
    for item in extracted:
        warnings.extend(item.warnings)

    # Flag fields that returned identical values on every sample row — a
    # near-certain wrong-selector signal. Advisory only (no value is changed).
    field_keys = [
        str(field.get("user_label") or field.get("label") or field.get("name"))
        for field in selected_fields
        if (field.get("user_label") or field.get("label") or field.get("name"))
    ]
    for dup in detect_duplicate_column_warnings(field_keys, sample_records):
        warnings.append(dup["message"])

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
            "spec_fingerprint": _spec_preview_fingerprint(spec),
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


# Ready states a transient preview failure can fall back to. The preview
# endpoint only starts a preview from one of these, so prior_state is always a
# valid revert target; FAILED is the defensive fallback.
_PREVIEW_REVERT_STATES = frozenset(
    {
        ProjectState.AWAITING_SETUP,
        ProjectState.ANALYSIS_READY,
        ProjectState.PREVIEW_READY,
    }
)


async def create_preview(
    db: AsyncSession,
    project: Project,
    spec: ExtractionSpec,
) -> PreviewResult:
    logger.debug("preview.started", extra={"project_id": project.id})
    prior_state = project.state
    project.transition_to(ProjectState.PREVIEWING)
    # Clear any stale error from a previous attempt; set again only on failure.
    project.error = None
    project.error_code = None
    await db.flush()

    try:
        payload = await build_selector_preview_payload(project, spec, db)
    except InteractionError as exc:
        # Genuine spec/config problem (interactive variant needs a browser the
        # environment lacks, or the variant cap was exceeded). The user must
        # change the spec, so the project legitimately fails.
        project.transition_to(ProjectState.FAILED)
        project.error = f"Preview failed: {exc}"
        project.error_code = getattr(exc, "code", None) or "PREVIEW_FAILED"
        await db.flush()
        logger.warning(
            "preview.failed_spec",
            extra={"project_id": project.id, "error_code": project.error_code},
        )
        raise
    except Exception as exc:
        # Transient preview-fetch failure (browser-driver crash, network,
        # anti-bot). Do NOT strand the project in FAILED — revert to the ready
        # state it came from so the user can retry in place. The transition goes
        # through transition_to() to preserve the state-machine invariant.
        target = (
            prior_state
            if prior_state in _PREVIEW_REVERT_STATES
            else ProjectState.FAILED
        )
        project.transition_to(target)
        project.error = f"Preview failed: {exc}"
        project.error_code = (
            getattr(exc, "error_code", None)
            or getattr(exc, "code", None)
            or "PREVIEW_FAILED"
        )
        await db.flush()
        logger.warning(
            "preview.failed_transient",
            extra={
                "project_id": project.id,
                "error_code": project.error_code,
                "reverted_to": target.value,
            },
        )
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
