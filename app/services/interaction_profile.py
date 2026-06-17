"""Page-variant interaction profile: pure helpers (no DB, no HTTP, no browser).

A page can expose the same records in several *variants* the user might want all
of: e.g. nutrition values "per 100 g" vs "per serving", or units "Metric" vs
"Imperial". An ``interaction_profile`` describes those variant groups so
extraction can emit one row per variant combination, each tagged with metadata
columns (``unit_system``, ``serving_basis``, …).

Two execution kinds, decided per group:

* ``deterministic`` — the alternate values are *already in the static DOM* as
  distinct elements/columns. Each option carries per-field selector overrides;
  extraction reads them straight from the single fetched HTML, **no browser**.
* ``interactive`` — the values only appear after a click/select (computed
  client-side). Each option carries a ``recipe`` of steps a browser runs before
  capturing HTML. Selecting an interactive option with no browser available is a
  hard error (``INTERACTION_BROWSER_REQUIRED``) — never a silent downgrade.

This module is intentionally pure and fully unit-testable; the browser runner
lives in ``fetcher`` and the orchestration in ``interaction_extraction``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import product
from typing import Any

# Hard cap on how many variant combinations we will ever extract per page.
DEFAULT_MAX_VARIANT_COMBINATIONS = 12
HARD_MAX_VARIANT_COMBINATIONS = 12

EXECUTION_DETERMINISTIC = "deterministic"
EXECUTION_INTERACTIVE = "interactive"
EXECUTION_URL_PARAM = "url_param"
# A merged axis: a browser recipe reaches the variant AND per-option field
# selectors say which columns to read from the rendered HTML. Used when a page
# exposes the same axis as static columns (some fields) AND a browser toggle
# (the rest) — e.g. static per-100g/per-serving calories + a "Show per serving"
# toggle that's the only source of the per-serving serving size.
EXECUTION_MIXED = "mixed"

# Reserved metadata column names every variant row carries.
META_VARIANT_ID = "interaction_variant_id"
META_VARIANT_LABEL = "interaction_variant_label"
RESERVED_META_KEYS = (META_VARIANT_ID, META_VARIANT_LABEL)

# Well-known group labels -> stable metadata keys.
_STANDARD_METADATA_KEYS = {
    "metric/imperial": "unit_system",
    "unit": "unit_system",
    "units": "unit_system",
    "unit system": "unit_system",
    "per 100 g / per serving": "serving_basis",
    "serving": "serving_basis",
    "serving size": "serving_basis",
    "basis": "serving_basis",
}


class InteractionError(ValueError):
    """Raised when a profile cannot be applied (browser missing, cap exceeded)."""

    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass
class VariantCombination:
    """One concrete combination of one option per selected group."""

    id: str
    label: str
    # group metadata_key -> chosen option label, for the row metadata columns.
    metadata: dict[str, str]
    # per-field selector overrides (deterministic groups), merged across groups.
    field_selectors: dict[str, str]
    # ordered browser steps to reach this combination (interactive groups).
    recipe: list[dict[str, Any]] = field(default_factory=list)
    # query params to apply to the seed URL (url_param groups), merged.
    url_params: dict[str, str] = field(default_factory=dict)

    @property
    def requires_browser(self) -> bool:
        return bool(self.recipe)

    @property
    def requires_url_fetch(self) -> bool:
        return bool(self.url_params)


def sanitize_metadata_key(label: str) -> str:
    """Map a group label to a stable snake_case metadata column name."""
    normalized = re.sub(r"\s+", " ", (label or "").strip().lower())
    if normalized in _STANDARD_METADATA_KEYS:
        return _STANDARD_METADATA_KEYS[normalized]
    key = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return key or "variant"


def is_enabled(profile: dict[str, Any] | None) -> bool:
    """Whether a profile is present, enabled, and has at least one usable group."""
    if not isinstance(profile, dict):
        return False
    if not profile.get("enabled"):
        return False
    return bool(_selected_groups(profile))


def _selected_groups(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Groups that have at least one selected option (others are inert)."""
    groups: list[dict[str, Any]] = []
    for group in profile.get("groups") or []:
        if not isinstance(group, dict):
            continue
        selected = [
            o for o in (group.get("options") or [])
            if isinstance(o, dict) and o.get("selected", True)
        ]
        if selected:
            groups.append(group)
    return groups


def merge_enabled(profile: dict[str, Any] | None) -> bool:
    """Whether variants should be merged into one row per entity (vs row-per-variant)."""
    return bool(isinstance(profile, dict) and profile.get("merge_variants"))


def max_variant_combinations(profile: dict[str, Any] | None) -> int:
    raw = (profile or {}).get("max_variant_combinations")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = DEFAULT_MAX_VARIANT_COMBINATIONS
    return max(1, min(n, HARD_MAX_VARIANT_COMBINATIONS))


def _option_label(option: dict[str, Any]) -> str:
    return str(option.get("label") or option.get("id") or "option")


def _option_recipe(option: dict[str, Any]) -> list[dict[str, Any]]:
    recipe = option.get("recipe")
    return [s for s in recipe if isinstance(s, dict)] if isinstance(recipe, list) else []


def selected_combinations(profile: dict[str, Any] | None) -> list[VariantCombination]:
    """Cartesian product of one selected option per selected group.

    Returns an empty list when the profile is disabled or has no usable groups.
    Raises ``InteractionError(INTERACTION_VARIANT_LIMIT_EXCEEDED)`` when the
    number of combinations exceeds the profile's cap.
    """
    if not is_enabled(profile):
        return []
    assert profile is not None  # for type-checkers; is_enabled guarantees it
    groups = _selected_groups(profile)

    per_group_options: list[list[tuple[dict[str, Any], dict[str, Any]]]] = []
    for group in groups:
        selected = [
            o for o in (group.get("options") or [])
            if isinstance(o, dict) and o.get("selected", True)
        ]
        per_group_options.append([(group, o) for o in selected])

    total = 1
    for opts in per_group_options:
        total *= len(opts)
    cap = max_variant_combinations(profile)
    if total > cap:
        raise InteractionError(
            f"Selected variants produce {total} combinations (limit {cap}). "
            "Deselect some options.",
            code="INTERACTION_VARIANT_LIMIT_EXCEEDED",
        )

    combinations: list[VariantCombination] = []
    for combo in product(*per_group_options):
        meta: dict[str, str] = {}
        field_selectors: dict[str, str] = {}
        recipe: list[dict[str, Any]] = []
        url_params: dict[str, str] = {}
        label_parts: list[str] = []
        id_parts: list[str] = []
        for group, option in combo:
            metadata_key = str(
                group.get("metadata_key") or sanitize_metadata_key(group.get("label", ""))
            )
            option_label = _option_label(option)
            meta[metadata_key] = option_label
            label_parts.append(option_label)
            id_parts.append(str(option.get("id") or sanitize_metadata_key(option_label)))
            execution = group.get("execution") or EXECUTION_DETERMINISTIC
            if execution == EXECUTION_INTERACTIVE:
                recipe.extend(_option_recipe(option))
            elif execution == EXECUTION_MIXED:
                # Browser recipe AND per-option selector overrides applied to the
                # rendered HTML.
                recipe.extend(_option_recipe(option))
                overrides = option.get("field_selectors") or {}
                if isinstance(overrides, dict):
                    for fk, sel in overrides.items():
                        if sel:
                            field_selectors[str(fk)] = str(sel)
            elif execution == EXECUTION_URL_PARAM:
                query = option.get("query") or {}
                if isinstance(query, dict):
                    for k, v in query.items():
                        url_params[str(k)] = str(v)
            else:
                overrides = option.get("field_selectors") or {}
                if isinstance(overrides, dict):
                    for fk, sel in overrides.items():
                        if sel:
                            field_selectors[str(fk)] = str(sel)
        combinations.append(
            VariantCombination(
                id="__".join(id_parts) or "variant",
                label=" · ".join(label_parts),
                metadata=meta,
                field_selectors=field_selectors,
                recipe=recipe,
                url_params=url_params,
            )
        )
    return combinations


def apply_field_overrides(
    fields: list[dict[str, Any]], combo: VariantCombination
) -> list[dict[str, Any]]:
    """Clone selected fields, replacing selectors per the combo's deterministic
    overrides. Field identity is matched on user_label/label/name."""
    if not combo.field_selectors:
        return [dict(f) for f in fields]
    out: list[dict[str, Any]] = []
    for f in fields:
        clone = dict(f)
        for key in ("user_label", "label", "name"):
            name = f.get(key)
            if name is not None and str(name) in combo.field_selectors:
                clone["selector"] = combo.field_selectors[str(name)]
                break
        out.append(clone)
    return out


def metadata_columns(profile: dict[str, Any] | None) -> list[str]:
    """Ordered metadata column names a variant export carries (group order)."""
    cols: list[str] = [META_VARIANT_ID, META_VARIANT_LABEL]
    if not isinstance(profile, dict):
        return cols
    for group in _selected_groups(profile):
        key = str(group.get("metadata_key") or sanitize_metadata_key(group.get("label", "")))
        if key not in cols:
            cols.append(key)
    return cols


def tag_record_metadata(
    record: dict[str, Any], combo: VariantCombination
) -> dict[str, Any]:
    """Return ``record`` augmented with this combo's metadata columns."""
    enriched = dict(record)
    enriched[META_VARIANT_ID] = combo.id
    enriched[META_VARIANT_LABEL] = combo.label
    for key, value in combo.metadata.items():
        enriched[key] = value
    return enriched
