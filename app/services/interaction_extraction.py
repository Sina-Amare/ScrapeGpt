"""Variant-aware extraction orchestration.

Bridges the pure ``interaction_profile`` helpers and the deterministic
``extractor``. Used by both the preview and the crawl executor so they agree on
how variants are produced.

When the profile is disabled this is a thin pass-through to
``extract_records_from_html`` — existing single-variant projects are unaffected.

When enabled it produces one record set per selected variant combination:

* deterministic combinations read alternate columns from the single base HTML
  (no browser) via per-field selector overrides;
* interactive combinations get their HTML from ``fetch_variant_htmls`` (the
  browser runner), one snapshot per combination.

Each record is tagged with metadata columns (``interaction_variant_id``,
``interaction_variant_label`` and one column per group, e.g. ``unit_system``).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from app.models.job import ExtractionSpec, Project
from app.services.extractor import ExtractedPayload, extract_records_from_html
from app.services.interaction_profile import (
    InteractionError,
    apply_field_overrides,
    is_enabled,
    selected_combinations,
    tag_record_metadata,
)

logger = logging.getLogger(__name__)

# A callable that, given {combo_id: [recipe steps]}, returns {combo_id: html}.
# Must raise InteractionError(code="INTERACTION_BROWSER_REQUIRED") when no
# browser backend is available. Never silently omits a requested combo.
FetchVariantHtmls = Callable[[dict[str, list[dict[str, Any]]]], Awaitable[dict[str, str]]]


def _variant_spec(spec: ExtractionSpec, fields: list[dict[str, Any]]) -> Any:
    """A lightweight spec view the extractor accepts (reads mode/fields/config)."""
    return SimpleNamespace(
        mode=spec.mode,
        fields=fields,
        content_config=spec.content_config,
    )


async def extract_records_with_variants(
    *,
    base_html: str,
    source_url: str,
    project: Project,
    spec: ExtractionSpec,
    max_records: int,
    fetch_variant_htmls: FetchVariantHtmls | None = None,
) -> tuple[list[ExtractedPayload], list[str]]:
    """Return (records, warnings). Records carry variant metadata when enabled.

    Raises ``InteractionError`` (codes ``INTERACTION_VARIANT_LIMIT_EXCEEDED`` /
    ``INTERACTION_BROWSER_REQUIRED``) which callers translate to a failed page /
    project with that error code.
    """
    profile = getattr(spec, "interaction_profile", None)
    if not is_enabled(profile):
        records = extract_records_from_html(
            base_html,
            source_url=source_url,
            project=project,
            spec=spec,
            max_records=max_records,
        )
        return records, []

    combos = selected_combinations(profile)  # may raise VARIANT_LIMIT_EXCEEDED

    interactive = [c for c in combos if c.requires_browser]
    variant_html: dict[str, str] = {}
    if interactive:
        if fetch_variant_htmls is None:
            raise InteractionError(
                "This page needs a browser to capture the selected interactive "
                "variant(s), but none is available.",
                code="INTERACTION_BROWSER_REQUIRED",
            )
        recipes = {c.id: c.recipe for c in interactive}
        variant_html = await fetch_variant_htmls(recipes)

    base_fields = spec.fields or []
    payloads: list[ExtractedPayload] = []
    zero_variants: list[str] = []
    nonzero = 0

    for combo in combos:
        if combo.requires_browser:
            html = variant_html.get(combo.id)
            if not html:
                raise InteractionError(
                    f"No browser snapshot was produced for variant "
                    f"'{combo.label}'.",
                    code="INTERACTION_BROWSER_REQUIRED",
                )
        else:
            html = base_html

        variant_fields = apply_field_overrides(base_fields, combo)
        records = extract_records_from_html(
            html,
            source_url=source_url,
            project=project,
            spec=_variant_spec(spec, variant_fields),
            max_records=max_records,
        )
        if records:
            nonzero += 1
        else:
            zero_variants.append(combo.label)
        for r in records:
            payloads.append(
                ExtractedPayload(
                    raw_data=tag_record_metadata(r.raw_data, combo),
                    normalized_data=tag_record_metadata(r.normalized_data, combo),
                    warnings=r.warnings,
                )
            )

    warnings: list[str] = []
    # Partial-zero is a warning, not a hard failure: some variants may legitimately
    # be empty on a given page. An all-zero result falls through to the caller's
    # existing NO_RECORDS / ZERO_PREVIEW gate (payloads is empty).
    if nonzero and zero_variants:
        warnings.append(
            "No records for variant(s): " + ", ".join(zero_variants) + "."
        )
    logger.info(
        "interaction.variants_extracted",
        extra={
            "source_url": source_url,
            "combinations": len(combos),
            "interactive": len(interactive),
            "records": len(payloads),
            "zero_variants": len(zero_variants),
        },
    )
    return payloads, warnings
