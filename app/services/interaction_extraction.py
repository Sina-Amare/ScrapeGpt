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

import asyncio
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
) -> list[ExtractedPayload] | None:
    """Collapse per-variant records into one row per entity, matched by a stable
    key — NOT by row index (different variants can omit/sort/filter rows).

    The key is the set of fields no variant overrides (deterministic
    ``field_selectors``). Returns ``None`` to signal the caller to fall back to
    row-per-variant (with a warning) when a safe merge is not possible:
    interactive/url-param variants (we can't know which fields vary), no stable
    key fields, or a key that is non-unique within a variant.
    """
    combos = [c for c, _ in per_combo]
    # Interactive / url_param variants change the whole page, so we cannot tell
    # which columns vary — index/key merging both risk mixing entities.
    if any(c.requires_browser or c.requires_url_fetch for c in combos):
        return None

    varying: set[str] = set()
    for c in combos:
        varying |= set(c.field_selectors.keys())
    key_fields = [k for k in field_keys if k not in varying]
    if not key_fields:
        return None

    def _key(p: ExtractedPayload) -> tuple:
        return tuple(str(p.normalized_data.get(k)) for k in key_fields)

    # Index each combo's rows by key; bail if a key is ambiguous within a combo.
    per_combo_by_key: list[tuple[VariantCombination, dict[tuple, ExtractedPayload]]] = []
    order: list[tuple] = []
    seen_keys: set[tuple] = set()
    for combo, recs in per_combo:
        by_key: dict[tuple, ExtractedPayload] = {}
        for p in recs:
            k = _key(p)
            if k in by_key:
                return None  # non-unique key within a variant -> unsafe to merge
            by_key[k] = p
            if k not in seen_keys:
                seen_keys.add(k)
                order.append(k)
        per_combo_by_key.append((combo, by_key))

    merged: list[ExtractedPayload] = []
    for k in order:
        present = [(c, bk[k]) for c, bk in per_combo_by_key if k in bk]
        if not present:
            continue
        first = present[0][1]
        raw: dict[str, Any] = {"source_url": first.raw_data.get("source_url")}
        norm: dict[str, Any] = {"source_url": first.normalized_data.get("source_url")}
        # Stable key fields appear once.
        for kf in key_fields:
            norm[kf] = first.normalized_data.get(kf)
            raw[kf] = first.raw_data.get(kf)
        # Varying fields get one column per variant.
        warns: list[str] = []
        for fk in field_keys:
            if fk in key_fields:
                continue
            for combo, p in present:
                col = f"{fk} ({combo.label})"
                norm[col] = p.normalized_data.get(fk)
                raw[col] = p.raw_data.get(fk)
        for _c, p in present:
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
    # CPU-bound BeautifulSoup/lxml parsing runs in a worker thread so a large
    # page can't block the event loop (and the whole worker) for everyone.
    # Snapshot the only ORM attribute the extractor reads — never hand a live
    # SQLAlchemy object to a thread (a lazy load there would be unsafe).
    proj_snap = SimpleNamespace(analysis=getattr(project, "analysis", None))

    async def _extract(html: str, spec_view: Any) -> list[ExtractedPayload]:
        return await asyncio.to_thread(
            extract_records_from_html,
            html,
            source_url=source_url,
            project=proj_snap,
            spec=spec_view,
            max_records=max_records,
        )

    if not is_enabled(profile):
        records = await _extract(base_html, _variant_spec(spec, spec.fields or []))
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
        records = await _extract(html, _variant_spec(spec, variant_fields))
        if records:
            nonzero += 1
        else:
            zero_variants.append(combo.label)
        per_combo.append((combo, records))

    warnings: list[str] = []

    merged_payloads = (
        _merge_variant_records(per_combo, _field_keys(spec))
        if merge_enabled(profile)
        else None
    )
    if merge_enabled(profile) and merged_payloads is None:
        # A safe one-row-per-entity merge was not possible (interactive/url-param
        # variants, no stable key, or a non-unique key). Fall back to the
        # row-per-variant output and tell the user, rather than pairing rows by
        # index and silently mixing different entities.
        warnings.append(
            "Could not merge variants into one row per item safely; output is "
            "one row per variant instead."
        )
    if merged_payloads is not None:
        payloads = merged_payloads
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
