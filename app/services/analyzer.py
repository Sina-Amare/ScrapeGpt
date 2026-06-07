"""LLM-powered page analyzer with content-hash cache."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import async_session_factory
from app.models.job import AnalysisCache, ExtractionMode
from app.models.provider_config import ProviderConfig
from app.schemas.job import ContentAnalysis, StructuredAnalysis
from app.services.provider_service import (
    ProviderCallError,
    ProviderJSONError,
    call_json_model,
    safe_provider_error_message,
)

logger = logging.getLogger(__name__)

ANALYZER_VERSION = "1"

_STRUCTURED_PROMPT = """\
You are a web scraping analyst. Analyze the following page structure and identify \
extractable data fields.

{dom_summary}

Return a JSON object with this exact schema (no extra keys):
{{
  "page_type": "listing|detail|mixed|search|other",
  "repeated_item_selector": "<CSS selector for repeated item container, e.g. .product-card>",
  "candidate_fields": [
    {{
      "name": "<snake_case field name>",
      "label": "<human-readable label>",
      "selector": "<CSS selector relative to the container>",
      "data_type": "string|number|url|date|boolean|image",
      "required": true,
      "confidence": 0.0-1.0,
      "sample_values": ["<example 1>", "<example 2>"]
    }}
  ],
  "detail_link_selector": "<CSS selector for links to detail pages, or null>",
  "pagination_selector": "<CSS selector for next-page control, or null>",
  "estimated_pages": <integer or null>,
  "warnings": ["<any concerns about extraction>"],
  "confidence": 0.0-1.0
}}
Confidence 1.0 = very certain. Provide at least 1 candidate field."""

_CONTENT_PROMPT = """\
You are a web content analyst. Analyze the following page structure and identify \
the primary content and useful metadata for RAG/content extraction.

{dom_summary}

Return a JSON object with this exact schema (no extra keys):
{{
  "content_type": "article|blog|documentation|product|listing|forum|other",
  "primary_content_selector": "<CSS selector for the main content block>",
  "estimated_pages": <integer or null>,
  "avg_content_length": <estimated characters per page, integer or null>,
  "recommended_chunking": "paragraph|section|page|sentence|null",
  "metadata_fields": [
    {{
      "name": "<snake_case field name>",
      "label": "<human-readable label>",
      "selector": "<CSS selector>",
      "confidence": 0.0-1.0,
      "sample_values": ["<example>"]
    }}
  ],
  "warnings": ["<any concerns>"],
  "confidence": 0.0-1.0
}}"""


def _schema_for_mode(mode: ExtractionMode) -> type:
    return StructuredAnalysis if mode == ExtractionMode.STRUCTURED else ContentAnalysis


def _prompt_for_mode(mode: ExtractionMode, dom_summary: str) -> str:
    template = (
        _STRUCTURED_PROMPT if mode == ExtractionMode.STRUCTURED else _CONTENT_PROMPT
    )
    return template.format(dom_summary=dom_summary)


async def _lookup_cache(
    db: AsyncSession,
    content_hash: str,
    extraction_mode: ExtractionMode,
    provider: str,
    model: str,
) -> dict[str, Any] | None:
    result = await db.execute(
        select(AnalysisCache).where(
            AnalysisCache.content_hash == content_hash,
            AnalysisCache.extraction_mode == extraction_mode,
            AnalysisCache.provider == provider,
            AnalysisCache.model == model,
            AnalysisCache.analyzer_version == ANALYZER_VERSION,
        )
    )
    row = result.scalar_one_or_none()
    return row.result if row is not None else None


async def _store_cache(
    db: AsyncSession,
    content_hash: str,
    extraction_mode: ExtractionMode,
    provider: str,
    model: str,
    result: dict[str, Any],
    normalized_url: str | None,
) -> None:
    entry = AnalysisCache(
        content_hash=content_hash,
        extraction_mode=extraction_mode,
        provider=provider,
        model=model,
        analyzer_version=ANALYZER_VERSION,
        result=result,
        normalized_url=normalized_url,
    )
    db.add(entry)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        # Cache write failure is non-fatal — analysis result is still returned.
        logger.warning("analyzer.cache_write_failed", extra={"content_hash": content_hash})


async def analyze_page(
    provider_config: ProviderConfig,
    dom_summary: str,
    extraction_mode: ExtractionMode,
    content_hash: str,
    normalized_url: str | None = None,
) -> dict[str, Any]:
    """
    Analyze a page DOM summary with the configured provider.

    Checks the analysis_cache first (by content_hash + mode + provider + model + version).
    Calls the provider on cache miss and stores the result.

    Returns the validated analysis dict.
    Raises ProviderCallError or ProviderJSONError on failure.
    """
    provider = provider_config.provider
    model = provider_config.model

    async with async_session_factory() as db:
        cached = await _lookup_cache(db, content_hash, extraction_mode, provider, model)
        if cached is not None:
            logger.info(
                "analyzer.cache_hit",
                extra={"content_hash": content_hash[:8], "mode": extraction_mode.value},
            )
            return cached

    # Cache miss — call the LLM
    schema = _schema_for_mode(extraction_mode)
    prompt = _prompt_for_mode(extraction_mode, dom_summary)
    messages = [{"role": "user", "content": prompt}]

    try:
        result = await call_json_model(provider_config, messages, schema, max_retries=3)
    except (ProviderCallError, ProviderJSONError) as exc:
        api_key = None
        try:
            from app.services.provider_service import decrypt_api_key
            api_key = decrypt_api_key(provider_config.api_key_encrypted)
        except Exception:
            pass
        safe_msg = safe_provider_error_message(exc, api_key)
        raise ProviderCallError(safe_msg) from exc

    analysis_dict = result.data.model_dump()

    logger.info(
        "analyzer.completed",
        extra={
            "content_hash": content_hash[:8],
            "mode": extraction_mode.value,
            "confidence": analysis_dict.get("confidence"),
        },
    )

    async with async_session_factory() as db:
        await _store_cache(
            db, content_hash, extraction_mode, provider, model, analysis_dict, normalized_url
        )

    return analysis_dict
