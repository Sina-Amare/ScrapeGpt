"""
Job executor with always-finalize guarantee.

Orchestrates: QUEUED → ANALYZING → AWAITING_SETUP | ANALYSIS_READY | FAILED
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.db.database import async_session_factory
from app.models.job import ExtractionMode, Job, JobState, WorkflowMode
from app.models.provider_config import ProviderConfig
from app.services.analyzer import analyze_page
from app.services.dom_summary import assess_html_quality, build_dom_summary
from app.services.extraction_spec_service import validate_selectors_against_html
from app.services.fetcher import FetchError, fetch_url
from app.services.job_state import (
    transition_job_to_analysis_ready,
    transition_job_to_analyzing,
    transition_job_to_awaiting_setup,
    transition_job_to_failed,
)
from app.services.extraction_mode import detect_alternate_mode
from app.services.project_events import record_project_event
from app.services.url_validator import URLValidationError, validate_url
from app.services.provider_service import ProviderCallError, ProviderJSONError

logger = logging.getLogger(__name__)


async def execute_job_pipeline(job_id: int, provider_config_id: int) -> None:
    """
    Execute the full analysis pipeline for a job.

    Always-finalize guarantee: every code path ends in a terminal state.
    Runs as a FastAPI BackgroundTask, separate from the HTTP request.
    """
    logger.info("job_pipeline.started", extra={"job_id": job_id})

    user_id: int | None = None
    try:
        async with async_session_factory() as db:
            job = await db.get(Job, job_id)
            if not job:
                logger.error("job_pipeline.job_not_found", extra={"job_id": job_id})
                return
            url = job.url
            extraction_mode = job.extraction_mode
            workflow_mode = job.workflow_mode
            render_mode = job.render_mode.value
            user_id = job.user_id

            provider_config = await db.get(ProviderConfig, provider_config_id)
            if not provider_config:
                await transition_job_to_failed(
                    job_id,
                    "Provider config not found",
                    "NO_PROVIDER_CONFIGURED",
                )
                await record_project_event(
                    job_id, user_id, "analysis.failed", level="error",
                    message="Analysis failed: no provider configured.",
                    metadata={"error_code": "NO_PROVIDER_CONFIGURED"},
                )
                return

        # ---- Phase 1: QUEUED → ANALYZING ----
        result = await transition_job_to_analyzing(job_id)
        if not result.success:
            logger.error(
                "job_pipeline.transition_failed",
                extra={"job_id": job_id, "error": result.error},
            )
            return
        await record_project_event(
            job_id, user_id, "analysis.started", message="Analysis started."
        )

        # ---- Phase 2: Validate URL ----
        try:
            validated_url = validate_url(url)
        except URLValidationError as exc:
            await transition_job_to_failed(job_id, str(exc), exc.reason.value)
            await record_project_event(
                job_id, user_id, "analysis.failed", level="error",
                message="Analysis failed: the URL could not be fetched safely.",
                metadata={"error_code": exc.reason.value},
            )
            return

        # ---- Phase 3: Fetch page ----
        try:
            fetch_result = await fetch_url(validated_url, render_mode)
        except FetchError as exc:
            await transition_job_to_failed(job_id, str(exc), exc.error_code)
            await record_project_event(
                job_id, user_id, "analysis.failed", level="error",
                message="Analysis failed: the page could not be fetched.",
                metadata={"error_code": exc.error_code},
            )
            return

        # ---- Phase 4b: Gate on fetch quality ----
        # If the fetched body is undecodable binary or has no usable structure
        # (even after the fetcher's browser fallback), fail with a precise cause
        # instead of feeding garbage to the LLM (which hallucinates selectors)
        # and poisoning the analysis cache.
        quality = assess_html_quality(fetch_result.html)
        if not quality.is_usable:
            code = "PAGE_DECODE_FAILED" if quality.is_binary else "FETCH_HTML_QUALITY_FAILED"
            detail = "; ".join(quality.reasons) or "unusable page content"
            user_msg = (
                "Analysis failed: the page could not be decoded "
                "(unsupported compression or encoding)."
                if quality.is_binary
                else "Analysis failed: the fetched page had no usable HTML content."
            )
            logger.warning(
                "analysis.fetch_quality_failed",
                extra={
                    "job_id": job_id,
                    "error_code": code,
                    "quality_label": quality.label,
                    "replacement_ratio": round(quality.replacement_ratio, 4),
                    "render_mode_used": fetch_result.render_mode_used.value,
                },
            )
            await transition_job_to_failed(job_id, detail, code)
            await record_project_event(
                job_id, user_id, "analysis.failed", level="error",
                message=user_msg, metadata={"error_code": code},
            )
            return

        # ---- Phase 5: Build DOM summary ----
        dom_summary = build_dom_summary(fetch_result.html, fetch_result.final_url)

        # ---- Phase 6: Analyze ----
        try:
            analysis = await analyze_page(
                provider_config=provider_config,
                dom_summary=dom_summary,
                extraction_mode=extraction_mode,
                content_hash=fetch_result.content_hash,
                normalized_url=fetch_result.final_url,
            )
        except (ProviderCallError, ProviderJSONError) as exc:
            await transition_job_to_failed(job_id, str(exc), "ANALYSIS_FAILED")
            await record_project_event(
                job_id, user_id, "analysis.failed", level="error",
                message="Analysis failed: the AI provider call did not succeed.",
                metadata={"error_code": "ANALYSIS_FAILED"},
            )
            return

        # ---- Phase 6b: Validate selectors against actual HTML ----
        # Runs even on cache hits so stale selectors are always re-checked.
        analysis = validate_selectors_against_html(analysis, fetch_result.html)

        confidence = float(analysis.get("confidence", 0.0))
        warnings = list(analysis.get("warnings", []))
        fetch_meta = {
            **fetch_result.fetch_metadata,
            "final_url": fetch_result.final_url,
            "render_mode_used": fetch_result.render_mode_used.value,
            # If the page also has the other kind of data, suggest a sibling
            # project so the user doesn't have to choose one and lose the other.
            "alternate_mode_suggestion": detect_alternate_mode(
                fetch_result.html, extraction_mode.value
            ),
        }

        # ---- Phase 7: Choose final state ----
        if (
            workflow_mode == WorkflowMode.FAST
            and confidence >= settings.ANALYSIS_CONFIDENCE_FAST_THRESHOLD
            and not warnings
        ):
            res = await transition_job_to_analysis_ready(
                job_id, analysis, confidence, warnings, fetch_meta
            )
        else:
            res = await transition_job_to_awaiting_setup(
                job_id, analysis, confidence, warnings, fetch_meta
            )

        if not res.success:
            logger.error(
                "job_pipeline.final_transition_failed",
                extra={"job_id": job_id, "error": res.error},
            )

        final_state = res.job.state.value if res.job else "unknown"
        logger.info(
            "job_pipeline.completed",
            extra={"job_id": job_id, "final_state": final_state},
        )
        if res.success:
            await record_project_event(
                job_id,
                user_id,
                "analysis.ready",
                message=(
                    "Analysis ready — review and configure fields."
                    if final_state == "AWAITING_SETUP"
                    else "Analysis complete."
                ),
                metadata={"state": final_state, "confidence": confidence},
            )

    except Exception as exc:
        logger.exception("job_pipeline.unexpected_error", extra={"job_id": job_id, "error": str(exc)})
        try:
            await transition_job_to_failed(job_id, f"Unexpected error: {exc}", "ANALYSIS_FAILED")
            if user_id is not None:
                await record_project_event(
                    job_id, user_id, "analysis.failed", level="error",
                    message="Analysis failed unexpectedly.",
                    metadata={"error_code": "ANALYSIS_FAILED"},
                )
        except Exception:
            logger.exception("job_pipeline.failed_to_mark_failed", extra={"job_id": job_id})
