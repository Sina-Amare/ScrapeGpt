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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models.job import ExtractionSpec, Project
from app.services.extractor import ExtractedPayload, extract_records_from_html
from app.services.interaction_profile import (
    VariantCombination,
    InteractionError,
    apply_field_overrides,
    is_enabled,
    merge_enabled,
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


def _field_keys(spec: ExtractionSpec) -> list[str]:
    keys: list[str] = []
    for f in spec.fields or []:
        if not isinstance(f, dict) or not f.get("selected", True):
            continue
        key = f.get("user_label") or f.get("label") or f.get("name")
        if key:
            keys.append(str(key))
    return keys


def build_variant_url(source_url: str, url_params: dict[str, str]) -> str:
    """Apply variant query params to a URL (replacing any existing same-key)."""
    if not url_params:
        return source_url
    parts = urlsplit(source_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(url_params)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _merge_variant_records(
    per_combo: list[tuple[VariantCombination, list[ExtractedPayload]]],
    field_keys: list[str],
) -> list[ExtractedPayload]:
    """Collapse aligned per-variant records into one row per entity.

    Records from each combo are paired by extraction order (same page, same row
    order across variants). A field whose value is identical across variants is
    written once; a field that differs gets one column per variant, named
    ``"<field> (<variant label>)"``.
    """
    n = max((len(recs) for _, recs in per_combo), default=0)
    merged: list[ExtractedPayload] = []
    for i in range(n):
        group = [(combo, recs[i]) for combo, recs in per_combo if i < len(recs)]
        if not group:
            continue
        first = group[0][1]
        raw: dict[str, Any] = {"source_url": first.raw_data.get("source_url")}
        norm: dict[str, Any] = {"source_url": first.normalized_data.get("source_url")}
        warns: list[str] = []
        for fk in field_keys:
            values = [p.normalized_data.get(fk) for _c, p in group]
            if len({str(v) for v in values}) <= 1:
                norm[fk] = values[0]
                raw[fk] = group[0][1].raw_data.get(fk)
            else:
                for combo, p in group:
                    col = f"{fk} ({combo.label})"
                    norm[col] = p.normalized_data.get(fk)
                    raw[col] = p.raw_data.get(fk)
        for _c, p in group:
            warns.extend(p.warnings or [])
        merged.append(ExtractedPayload(raw, norm, list(dict.fromkeys(warns))))
    return merged


async def extract_records_with_variants(
    *,
    base_html: str,
    source_url: str,
    project: Project,
    spec: ExtractionSpec,
    max_records: int,
    fetch_variant_htmls: FetchVariantHtmls | None = None,
    fetch_variant_url_htmls: FetchVariantHtmls | None = None,
) -> tuple[list[ExtractedPayload], list[str]]:
    """Return (records, warnings). Records carry variant metadata when enabled
    (default row-per-variant), or are merged into one row per entity when the
    profile sets ``merge_variants``.

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

    # Interactive combos -> one browser snapshot each (batched in one session).
    interactive = [c for c in combos if c.requires_browser]
    variant_html: dict[str, str] = {}
    if interactive:
        if fetch_variant_htmls is None:
            raise InteractionError(
                "This page needs a browser to capture the selected interactive "
                "variant(s), but none is available.",
                code="INTERACTION_BROWSER_REQUIRED",
            )
        variant_html = await fetch_variant_htmls({c.id: c.recipe for c in interactive})

    # URL-parameter combos -> static fetch of the variant URL (no browser).
    url_combos = [c for c in combos if c.requires_url_fetch and not c.requires_browser]
    url_html: dict[str, str] = {}
    if url_combos:
        if fetch_variant_url_htmls is None:
            raise InteractionError(
                "URL-parameter variants need a fetcher to load the variant URLs.",
                code="INTERACTION_FETCH_UNAVAILABLE",
            )
        url_html = await fetch_variant_url_htmls(
            {c.id: build_variant_url(source_url, c.url_params) for c in url_combos}
        )

    base_fields = spec.fields or []
    per_combo: list[tuple[VariantCombination, list[ExtractedPayload]]] = []
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
        elif combo.requires_url_fetch:
            html = url_html.get(combo.id) or ""
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
        per_combo.append((combo, records))

    if merge_enabled(profile):
        payloads = _merge_variant_records(per_combo, _field_keys(spec))
    else:
        payloads = [
            ExtractedPayload(
                raw_data=tag_record_metadata(r.raw_data, combo),
                normalized_data=tag_record_metadata(r.normalized_data, combo),
                warnings=r.warnings,
            )
            for combo, records in per_combo
            for r in records
        ]

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
            "url_param": len(url_combos),
            "merged": merge_enabled(profile),
            "records": len(payloads),
            "zero_variants": len(zero_variants),
        },
    )
    return payloads, warnings
