"""Best-effort detection of page-variant controls from static HTML.

Scans the already-fetched seed HTML (no browser needed) for toggle / segmented /
radio / select control groups that switch the displayed values — e.g. "Metric /
Imperial" or "per 100 g / per serving" — and proposes them as **interactive**
variant groups. The currently-active option becomes the no-click *baseline*
(extracted from the static HTML, no browser); the other options carry click
recipes (need a browser).

Safety first: controls that submit, authenticate, pay, navigate, paginate, or
leave the site are excluded. Detection never marks anything ``deterministic``
(that requires confirming the alternate values are already in the DOM, which is
configured explicitly) — so there is no silent downgrade path.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from app.services.extractor import sample_selector_values
from app.services.interaction_profile import (
    EXECUTION_DETERMINISTIC,
    EXECUTION_INTERACTIVE,
    EXECUTION_MIXED,
    sanitize_metadata_key,
)

# Option/label text that must never be treated as a benign variant toggle.
_UNSAFE_TEXT = re.compile(
    r"\b(submit|search|log\s?in|sign\s?in|sign\s?up|log\s?out|register|"
    r"buy|cart|checkout|pay|order|delete|remove|subscribe|download|upload|"
    r"next|previous|prev|continue|confirm|cancel|apply|reset|save|edit)\b",
    re.I,
)
_MAX_GROUPS = 4
_MIN_OPTIONS = 2
_MAX_OPTIONS = 6

# Generic page-section labels. A small group of these that all jump to on-page
# anchors (href="#…") is in-page section navigation (e.g. "Charts" /
# "More information"), not a data-variant toggle. Kept tight so real toggles
# (Metric/Imperial, per-100g/per-serving, …) are never matched.
_SECTION_WORDS = {
    "chart", "charts", "more information", "more info", "details", "detail",
    "overview", "description", "descriptions", "review", "reviews",
    "specification", "specifications", "specs", "comment", "comments",
    "related", "about", "info", "information", "summary", "gallery",
    "photos", "images", "map", "nutrition facts", "ingredients",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _is_unsafe(label: str) -> bool:
    label = label.strip()
    if not label or len(label) > 40:
        return True
    if label.isdigit():  # pagination page numbers
        return True
    return bool(_UNSAFE_TEXT.search(label))


def _is_section_word(label: str) -> bool:
    """True if *label* is a generic page-section word (see ``_SECTION_WORDS``)."""
    return _clean(label).lower() in _SECTION_WORDS


def _css_value(value: str) -> str:
    return value.replace('"', '\\"')


def _group_from_select(select: Tag) -> dict[str, Any] | None:
    name = select.get("name") or select.get("id")
    if not name:
        return None
    options = [
        o for o in select.find_all("option")
        if _clean(o.get_text()) and not _is_unsafe(_clean(o.get_text()))
    ]
    if not (_MIN_OPTIONS <= len(options) <= _MAX_OPTIONS):
        return None
    if select.get("id"):
        sel = f'select#{select.get("id")}'
    else:
        sel = f'select[name="{_css_value(str(select.get("name")))}"]'
    default_idx = next(
        (i for i, o in enumerate(options) if o.has_attr("selected")), 0
    )
    built: list[dict[str, Any]] = []
    for i, o in enumerate(options):
        label = _clean(o.get_text())
        recipe = (
            [] if i == default_idx
            else [{"action": "select", "by": "selector",
                   "value": f"{sel}::{label}"}]
        )
        built.append({"label": label, "recipe": recipe})
    group_label = _control_label(select) or "Option"
    return _assemble_group(group_label, built)


def _group_from_radios(radios: list[Tag]) -> dict[str, Any] | None:
    options = []
    default_idx = 0
    for i, r in enumerate(radios):
        label = _radio_label(r)
        if not label or _is_unsafe(label):
            return None
        name = r.get("name")
        value = r.get("value")
        if name and value is not None:
            sel = (
                f'input[name="{_css_value(str(name))}"]'
                f'[value="{_css_value(str(value))}"]'
            )
        elif r.get("id"):
            sel = f'input#{r.get("id")}'
        else:
            return None
        if r.has_attr("checked"):
            default_idx = i
        options.append((label, sel))
    if not (_MIN_OPTIONS <= len(options) <= _MAX_OPTIONS):
        return None
    built = []
    for i, (label, sel) in enumerate(options):
        recipe = [] if i == default_idx else [
            {"action": "click", "by": "selector", "value": sel}
        ]
        built.append({"label": label, "recipe": recipe})
    name = radios[0].get("name") or "Option"
    return _assemble_group(str(name).replace("_", " ").title(), built)


def _group_from_segmented(container: Tag) -> dict[str, Any] | None:
    """A container whose direct children are a small set of toggle buttons/links."""
    pairs = [
        (c, _clean(c.get_text()))
        for c in container.find_all(["button", "a"], recursive=False)
        if isinstance(c, Tag) and _clean(c.get_text())
    ]
    candidates = [c for c, _ in pairs]
    labels = [lbl for _, lbl in pairs]
    if not (_MIN_OPTIONS <= len(labels) <= _MAX_OPTIONS):
        return None
    if len(set(labels)) != len(labels):  # duplicates -> not a clean toggle
        return None
    if any(_is_unsafe(lbl) for lbl in labels):
        return None
    # Must look like a compact toggle, not a nav bar: short labels only.
    if any(len(lbl) > 24 for lbl in labels):
        return None
    # External / navigation links disqualify the group.
    for c in candidates:
        href = str(c.get("href") or "")
        if href and (href.startswith("http") or href.startswith("//")):
            return None
    # In-page section navigation masquerading as a toggle: a small group whose
    # EVERY label is a generic section word (e.g. "Charts" / "More information").
    # These switch on-page content sections — often MUI/Bootstrap tabs rendered
    # as buttons OR anchors jumping to a fragment — not data variants.
    # Conservative: real data toggles (Metric/Imperial, per-100g/per-serving,
    # Small/Large, Map/List) are never *all* generic section words, so they are
    # unaffected (see survival tests). Gated on the curated ``_SECTION_WORDS``
    # set, never a site-specific selector.
    if all(_is_section_word(lbl) for lbl in labels):
        return None
    default_idx = next(
        (i for i, c in enumerate(candidates) if _is_active(c)), 0
    )
    built = []
    for i, label in enumerate(labels):
        recipe = [] if i == default_idx else [
            {"action": "click", "by": "text", "value": label}
        ]
        built.append({"label": label, "recipe": recipe})
    return _assemble_group(_control_label(container) or "Option", built)


def _is_active(el: Tag) -> bool:
    if el.get("aria-pressed") == "true" or el.get("aria-selected") == "true":
        return True
    cls = " ".join(el.get("class") or []).lower()
    return any(tok in cls for tok in ("active", "selected", "current"))


def _control_label(el: Tag) -> str | None:
    for attr in ("aria-label", "data-label", "title"):
        if el.get(attr):
            return _clean(str(el.get(attr)))
    if el.get("id"):
        lab = el.find_parent().find("label") if el.find_parent() else None
        if lab and _clean(lab.get_text()):
            return _clean(lab.get_text())
    return None


def _radio_label(radio: Tag) -> str:
    rid = radio.get("id")
    if rid:
        soup = radio.find_parent()
        while soup is not None and not isinstance(soup, BeautifulSoup):
            soup = soup.parent
        if soup is not None:
            lab = soup.find("label", attrs={"for": rid})
            if lab and _clean(lab.get_text()):
                return _clean(lab.get_text())
    parent_label = radio.find_parent("label")
    if parent_label and _clean(parent_label.get_text()):
        return _clean(parent_label.get_text())
    return _clean(str(radio.get("value") or ""))


def _infer_metadata_key(label: str, option_labels: list[str]) -> str:
    """Prefer a meaningful group label; otherwise infer from the option texts."""
    key = sanitize_metadata_key(label)
    if key not in ("option", "variant"):
        return key
    text = " ".join(option_labels).lower()
    if "imperial" in text or "metric" in text:
        return "unit_system"
    if "serving" in text or "per 100" in text or "100 g" in text or "100g" in text:
        return "serving_basis"
    return sanitize_metadata_key("_".join(option_labels[:2])) or "variant"


def _assemble_group(label: str, options: list[dict[str, Any]]) -> dict[str, Any]:
    option_labels = [o["label"] for o in options]
    return {
        "label": label,
        "metadata_key": _infer_metadata_key(label, option_labels),
        "execution": EXECUTION_INTERACTIVE,
        "options": [
            {
                "id": sanitize_metadata_key(o["label"]) or f"opt{i}",
                "label": o["label"],
                "selected": True,
                "field_selectors": {},
                "recipe": o["recipe"],
            }
            for i, o in enumerate(options)
        ],
    }


def _in_auth_form(el: Tag) -> bool:
    form = el.find_parent("form")
    if form is None:
        return False
    return form.find("input", attrs={"type": "password"}) is not None


# --- Global-navigation / chrome exclusion ---------------------------------
# Toggle-like controls that live in site navigation (category menus, header /
# footer link bars, breadcrumbs) are NOT data-variant controls. Any candidate
# that is, or is nested within, such a region is skipped. Detection is by
# semantic landmark tag, ARIA role, or a tight nav class/id token set — never a
# site-specific selector — so real in-content toggles (serving basis,
# metric/imperial, …) are preserved.
_NAV_LANDMARK_TAGS = {"nav", "header", "footer"}
_NAV_ROLES = {"navigation", "menubar", "menu", "banner", "contentinfo"}
_NAV_IDENT_TOKENS = {
    "nav", "navbar", "navigation", "topnav", "mainnav", "sitenav",
    "globalnav", "navmenu", "megamenu", "breadcrumb", "breadcrumbs",
}


def _ident_tokens(el: Tag) -> list[str]:
    raw = " ".join(el.get("class") or []) + " " + str(el.get("id") or "")
    return [t for t in re.split(r"[-_\s]+", raw.lower()) if t]


def _in_navigation_region(el: Tag) -> bool:
    """True if *el* is, or is nested within, a global-navigation/chrome region."""
    node: Tag | None = el
    while node is not None and isinstance(node, Tag):
        if (node.name or "").lower() in _NAV_LANDMARK_TAGS:
            return True
        if str(node.get("role") or "").lower() in _NAV_ROLES:
            return True
        if any(tok in _NAV_IDENT_TOKENS for tok in _ident_tokens(node)):
            return True
        node = node.parent
    return False


def detect_interaction_groups(html: str) -> list[dict[str, Any]]:
    """Return a list of proposed interactive variant groups (possibly empty)."""
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    groups: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def _add(group: dict[str, Any] | None) -> None:
        if not group:
            return
        key = group["metadata_key"]
        labels = tuple(o["label"] for o in group["options"])
        sig = (key, labels)
        if sig in seen_keys or key in {g["metadata_key"] for g in groups}:
            return
        seen_keys.add(sig)
        groups.append(group)

    # 1. Native <select> dropdowns.
    for select in soup.find_all("select"):
        if (
            isinstance(select, Tag)
            and not _in_auth_form(select)
            and not _in_navigation_region(select)
        ):
            _add(_group_from_select(select))

    # 2. Radio-button groups (by shared name).
    radios_by_name: dict[str, list[Tag]] = {}
    for r in soup.find_all("input", attrs={"type": "radio"}):
        if (
            isinstance(r, Tag)
            and r.get("name")
            and not _in_auth_form(r)
            and not _in_navigation_region(r)
        ):
            radios_by_name.setdefault(str(r.get("name")), []).append(r)
    for radios in radios_by_name.values():
        _add(_group_from_radios(radios))

    # 3. Segmented button / tab toggles: containers of 2-4 button/anchor toggles.
    # ``nav`` is intentionally NOT scanned, and any candidate inside a
    # navigation/header/footer region is skipped — site menus are not variants.
    for container in soup.find_all(["div", "ul", "ol", "span", "fieldset"]):
        if not isinstance(container, Tag):
            continue
        if _in_auth_form(container) or _in_navigation_region(container):
            continue
        _add(_group_from_segmented(container))
        if len(groups) >= _MAX_GROUPS:
            break

    return groups[:_MAX_GROUPS]


# --- Parallel-column qualifier vocabulary (CLOSED on purpose) ---------------
# A field label carries a "variant qualifier" only when it matches one of a
# small, fixed set of patterns. This is deliberately NOT an open-ended
# "any trailing word" rule — that would collapse unrelated fields (e.g. "Price
# USD" / "Price EUR" are different data, not the same column). Recognized:
#   * trailing integer:        "Calories 1" / "Calories 2"
#   * ordinal word:            "Secondary serving size" / "Primary serving size"
#   * parenthetical qualifier: "Calories (per 100 g)" / "Calories (per serving)",
#                              "Weight (metric)" / "Weight (imperial)"
_NUM_SUFFIX_RE = re.compile(r"^(.*?)[\s_:#-]*(\d+)$")
_PAREN_RE = re.compile(r"^(.*?)\s*[\(\[]\s*(.+?)\s*[\)\]]\s*$")
_ORDINAL_WORDS = {
    "primary": "Primary",
    "secondary": "Secondary",
    "tertiary": "Tertiary",
    "quaternary": "Quaternary",
    "first": "First",
    "second": "Second",
    "third": "Third",
    "fourth": "Fourth",
    "fifth": "Fifth",
}
# Ordinal word embedded anywhere in a phrase, e.g. "(first reported serving)".
_ORDINAL_RE = re.compile(
    r"\b(" + "|".join(_ORDINAL_WORDS) + r")\b", re.I
)
# Parenthetical inner text accepted as a variant qualifier (basis or unit).
_QUALIFIER_PAREN_RE = re.compile(r"^(?:per\s+.+|metric|imperial)$", re.I)


def _find_ordinal(text: str) -> str | None:
    """Return the first recognized ordinal word (lowercased) in *text*, or None."""
    m = _ORDINAL_RE.search(text or "")
    return m.group(1).lower() if m else None


def _sentence_case(text: str) -> str:
    text = (text or "").strip()
    return text[:1].upper() + text[1:] if text else text


def _split_variant_qualifier(
    label: str,
) -> tuple[str, str | None, str | None, bool]:
    """Split a field label into ``(base, variant_key, option_label, strong)``.

    ``variant_key`` is None for a plain field. ``strong`` marks a key from the
    CLOSED, semantically-meaningful vocabulary (per/metric/imperial, ordinals,
    numbers) vs a WEAK key — an arbitrary parenthetical like "(alternate
    column)". A strong key can form a column set on its own; a weak key only
    collapses with structural backup (>=2 base families sharing it), because the
    analyzer can't always name the second column (the per-serving label isn't in
    the static DOM), so it falls back to "(alternate column)" etc. Examples::

        "Calories 1"                  -> ("Calories", "num:1", "Variant 1", True)
        "Calories (per 100 g)"        -> ("Calories", "per 100 g", "per 100 g", True)
        "Serving Size (alternate column)" -> ("Serving Size", "q:alternate column",
                                              "alternate column", False)
        "Food"                        -> ("Food", None, None, False)
    """
    text = (label or "").strip()
    if not text:
        return "", None, None, False

    # 1. Parenthetical suffix: "Calories (per 100 g)", an ordinal phrase, or any
    #    other distinguishing parenthetical (weak).
    m = _PAREN_RE.match(text)
    if m and m.group(1).strip():
        base = m.group(1).strip()
        inner = _clean(m.group(2))
        if _QUALIFIER_PAREN_RE.match(inner):
            return base, inner.lower(), inner, True
        if inner.lower() in _ORDINAL_WORDS:
            return base, inner.lower(), _ORDINAL_WORDS[inner.lower()], True
        ord_word = _find_ordinal(inner)
        if ord_word is not None:
            return base, ord_word, _sentence_case(inner), True
        if 0 < len(inner) <= 30:
            return base, "q:" + inner.lower(), inner, False

    # 2. Leading / trailing ordinal word: "Secondary serving size".
    tokens = text.split()
    if len(tokens) >= 2:
        first = tokens[0].lower().strip(":-")
        last = tokens[-1].lower().strip(":-")
        if first in _ORDINAL_WORDS:
            return " ".join(tokens[1:]).strip(), first, _ORDINAL_WORDS[first], True
        if last in _ORDINAL_WORDS:
            return " ".join(tokens[:-1]).strip(), last, _ORDINAL_WORDS[last], True

    # 3. Trailing integer: "Calories 1".
    m = _NUM_SUFFIX_RE.match(text)
    if m and m.group(1).strip():
        n = int(m.group(2))
        return m.group(1).strip(), f"num:{n}", f"Variant {n}", True

    return text, None, None, False


def _field_label(field: dict[str, Any]) -> str:
    return str(field.get("user_label") or field.get("label") or field.get("name") or "")


def detect_column_variants(
    fields: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Detect parallel in-DOM columns expressed as qualified sibling fields.

    Analyzers model pages like calories.info as flat fields with a per-column
    qualifier — ``Calories 1`` / ``Calories 2``, ``Calories (per 100 g)`` /
    ``Calories (per serving)``, ``Primary serving size`` / ``Secondary serving
    size``. This collapses each qualified family into a single base field
    (``Calories``) and returns a **deterministic** variant group whose options
    override those base fields to each column's analyzer-provided selector — no
    browser needed, since the alternate values are already in the static DOM.

    Returns ``(new_fields, group)`` or ``(fields, None)`` when there is no such
    pattern. Tightly bounded: only the closed qualifier vocabulary is
    recognized, and every collapsible family must expose the **same** set of
    variant keys (otherwise the columns are not parallel and nothing collapses).
    The per-option selectors come straight from the analyzer, never synthesized.
    """
    families: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    key_label: dict[str, str] = {}
    key_order: list[str] = []  # first-appearance order across the field list
    key_strong: dict[str, bool] = {}
    for f in fields:
        base, key, opt_label, strong = _split_variant_qualifier(_field_label(f))
        if key is None or not base:
            continue
        families.setdefault(base, []).append((key, f))
        if key not in key_label:
            key_label[key] = opt_label or key
            key_order.append(key)
        key_strong[key] = key_strong.get(key, False) or strong

    # Keep only families that carry >=2 distinct variant keys.
    families = {
        b: members
        for b, members in families.items()
        if len({k for k, _ in members}) >= 2
    }
    if not families:
        return fields, None

    # Every collapsible family must expose the SAME set of variant keys —
    # otherwise the columns are not parallel and we must not collapse them.
    key_sets = {frozenset(k for k, _ in members) for members in families.values()}
    if len(key_sets) != 1:
        return fields, None
    shared_keys = next(iter(key_sets))
    keys = [k for k in key_order if k in shared_keys]
    if len(keys) < 2:
        return fields, None

    # A WEAK (arbitrary-parenthetical) key set needs structural backup: >=2 base
    # families sharing it. A fully-strong key set (per/metric/ordinal/numbered)
    # is semantically clear enough to collapse a single family.
    if not all(key_strong.get(k, False) for k in keys) and len(families) < 2:
        return fields, None

    options: list[dict[str, Any]] = []
    for key in keys:
        field_selectors: dict[str, str] = {}
        for base, members in families.items():
            sel = next(
                (fld.get("selector") for (k, fld) in members if k == key), None
            )
            if sel:
                field_selectors[base] = str(sel)
        if field_selectors:
            label = key_label[key]
            options.append({
                "id": sanitize_metadata_key(label) or f"set_{len(options)}",
                "label": label,
                "selected": True,
                "field_selectors": field_selectors,
                "recipe": [],
            })
    if len(options) < 2:
        return fields, None

    group = {
        "label": "Column set",
        "metadata_key": "column_set",
        "execution": EXECUTION_DETERMINISTIC,
        "options": options,
    }

    # Collapse each family to one base field (keeping the first member's spot),
    # defaulting its selector to the first variant; drop the other members.
    member_ids = {id(fld) for members in families.values() for _, fld in members}
    done: set[str] = set()
    new_fields: list[dict[str, Any]] = []
    for f in fields:
        if id(f) not in member_ids:
            new_fields.append(dict(f))
            continue
        base, _key, _opt, _strong = _split_variant_qualifier(_field_label(f))
        if base in families and base not in done:
            done.add(base)
            collapsed = dict(f)
            collapsed["name"] = base
            collapsed["label"] = base
            collapsed["user_label"] = base
            collapsed["selector"] = options[0]["field_selectors"].get(
                base, f.get("selector")
            )
            collapsed["selected"] = True
            new_fields.append(collapsed)
        # subsequent members of the same family are dropped
    return new_fields, group


# --- #5: bounded reconciliation of redundant interactive groups -------------
# When the deterministic column group already extracts a variant axis from the
# static DOM, an interactive toggle for that SAME axis is redundant: it would
# only re-introduce a (flaky) browser for data we already have. We DROP such a
# group. This is pure suppression — it never synthesizes a selector and never
# downgrades a toggle that has no matching column group. Matching is on the same
# CLOSED vocabulary as #4, by canonical axis token, so it stays conservative:
# when unsure, the interactive group is kept exactly as-is.
_AXIS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("metric", re.compile(r"\bmetric\b", re.I)),
    ("imperial", re.compile(r"\bimperial\b", re.I)),
    ("per_100g", re.compile(r"per\s*100|\b100\s*g\b", re.I)),
    ("per_serving", re.compile(r"per\s+serving|per\s+portion|\bserving\b|\bportion\b", re.I)),
    ("primary", re.compile(r"\bprimary\b", re.I)),
    ("secondary", re.compile(r"\bsecondary\b", re.I)),
    ("tertiary", re.compile(r"\btertiary\b", re.I)),
]


def _axis_token(label: str) -> str | None:
    for token, rx in _AXIS_PATTERNS:
        if rx.search(label or ""):
            return token
    return None


def _axis_tokens(labels: list[str]) -> frozenset[str]:
    return frozenset(t for t in (_axis_token(lbl) for lbl in labels) if t)


def _covers_same_axis(col_group: dict[str, Any], inter_group: dict[str, Any]) -> bool:
    """True if the deterministic *col_group* already covers *inter_group*'s axis.

    Requires the same option count AND identical, non-empty canonical axis
    token sets. A numbered column group (``Variant 1`` / ``Variant 2``) has no
    axis tokens, so it never suppresses anything — exactly the conservative
    behavior we want.
    """
    col_opts = col_group.get("options") or []
    inter_opts = inter_group.get("options") or []
    if len(col_opts) != len(inter_opts):
        return False
    col_tokens = _axis_tokens([str(o.get("label", "")) for o in col_opts])
    inter_tokens = _axis_tokens([str(o.get("label", "")) for o in inter_opts])
    return bool(col_tokens) and col_tokens == inter_tokens


_SERVING_RE = re.compile(r"\b(serving|portion)\b", re.I)


def _is_serving_ordinal_column_set(col_group: dict[str, Any]) -> bool:
    """A deterministic column set whose every option is serving/portion-related
    (e.g. ``First reported serving`` / ``Second reported serving``).

    These cover the same axis as a ``serving_basis`` interactive toggle, but the
    ordinal option labels don't share the toggle's per-100g/per-serving tokens,
    so ``_covers_same_axis`` can't see it — this catches that case by category.
    """
    opts = col_group.get("options") or []
    if len(opts) < 2:
        return False
    labels = [str(o.get("label", "")) for o in opts]
    return all(_SERVING_RE.search(lbl) for lbl in labels)


def _toggle_dependent_fields(
    col_group: dict[str, Any],
    html: str,
    repeated_item_selector: str | None,
) -> set[str]:
    """Base fields whose static columns show IDENTICAL PRESENT values across
    options — the real per-variant value lives only behind a browser toggle.

    Requires, on at least one row, every option's value to be present (non-empty)
    AND equal. Empty / no-match selectors are NOT toggle-dependent (that's a
    broken or absent field, not a duplicated one). Axis-agnostic (any field)."""
    opts = col_group.get("options") or []
    if len(opts) < 2:
        return set()
    bases = {base for o in opts for base in (o.get("field_selectors") or {})}
    dependent: set[str] = set()
    for base in bases:
        per_opt = [
            sample_selector_values(
                html,
                repeated_item_selector=repeated_item_selector,
                selector=str((o.get("field_selectors") or {}).get(base) or ""),
            )
            for o in opts
        ]
        n = max((len(v) for v in per_opt), default=0)
        for i in range(n):
            row = [v[i] if i < len(v) else None for v in per_opt]
            if all(x not in (None, "") for x in row) and len(set(row)) == 1:
                dependent.add(base)
                break
    return dependent


def _axis_overlap(
    col_group: dict[str, Any],
    toggle: dict[str, Any],
    dep_fields: set[str],
) -> int:
    """Axis-token overlap between (column option labels + toggle-dependent field
    names) and the toggle's option labels — the generic signal that a column set
    and a toggle are on the SAME axis. No per-axis hardcoding; uses the shared
    ``_AXIS_PATTERNS`` vocabulary, so it works for serving basis, units, etc."""
    col_tokens = _axis_tokens(
        [str(o.get("label", "")) for o in col_group.get("options") or []]
    )
    col_tokens |= _axis_tokens([str(b) for b in dep_fields])
    tog_tokens = _axis_tokens(
        [str(o.get("label", "")) for o in toggle.get("options") or []]
    )
    return len(col_tokens & tog_tokens)


def _find_mergeable_toggle(
    col_group: dict[str, Any],
    interactive_groups: list[dict[str, Any]],
    html: str,
    repeated_item_selector: str | None,
) -> dict[str, Any] | None:
    """Pick the interactive toggle to MERGE with *col_group* — axis-agnostic.

    Requires the column set to have a toggle-dependent field (identical static
    values). Among interactive toggles with the same option count, chooses the
    one with the highest axis-token overlap (>=1); if none share a token, only a
    UNIQUE candidate is accepted (avoids guessing among several). Works for any
    axis the token vocabulary recognizes (serving basis, metric/imperial, …),
    not just serving. Returns the toggle group or None.
    """
    n = len(col_group.get("options") or [])
    candidates = [
        g for g in interactive_groups
        if g.get("execution") == EXECUTION_INTERACTIVE
        and len(g.get("options") or []) == n
    ]
    if not candidates:
        return None
    dep = _toggle_dependent_fields(col_group, html, repeated_item_selector)
    if not dep:
        return None
    best = max(candidates, key=lambda g: _axis_overlap(col_group, g, dep))
    if _axis_overlap(col_group, best, dep) >= 1:
        return best
    return candidates[0] if len(candidates) == 1 else None


def _serving_axis_values_distinct(
    col_group: dict[str, Any],
    html: str,
    repeated_item_selector: str | None,
) -> bool:
    """True only if the column set's serving-related base field yields DIFFERENT
    values across options on the actual page.

    Guards the serving-basis reconciliation: on pages where the per-serving
    serving size is NOT in the static DOM (it only appears after the browser
    toggle, e.g. calories.info), both static serving columns read the same
    value. Suppressing the toggle there would hide the only path to the real
    data — so we keep it unless the static columns genuinely differ.
    """
    opts = col_group.get("options") or []
    if len(opts) < 2 or not html:
        return False
    bases = {
        base
        for o in opts
        for base in (o.get("field_selectors") or {})
        if _SERVING_RE.search(str(base))
    }
    if not bases:
        return False
    return any(
        _base_values_distinct(opts, base, html, repeated_item_selector)
        for base in bases
    )


def _base_values_distinct(
    options: list[dict[str, Any]],
    base: str,
    html: str,
    repeated_item_selector: str | None,
) -> bool:
    """True if *base*'s per-option selectors yield different values on some row."""
    per_opt = [
        sample_selector_values(
            html,
            repeated_item_selector=repeated_item_selector,
            selector=str((o.get("field_selectors") or {}).get(base) or ""),
        )
        for o in options
    ]
    n = max((len(v) for v in per_opt), default=0)
    for i in range(n):
        present = [v[i] for v in per_opt if i < len(v) and v[i] not in (None, "")]
        if len(present) >= 2 and len(set(present)) >= 2:
            return True
    return False


def _merge_column_set_with_toggle(
    col_group: dict[str, Any],
    toggle_group: dict[str, Any],
    html: str,
    repeated_item_selector: str | None,
) -> dict[str, Any] | None:
    """Merge a deterministic column set with the interactive toggle on the SAME
    axis into one ``mixed`` group.

    The page exposes the per-100g/per-serving axis twice: as static columns
    (correct for some fields, e.g. calories) AND as a browser toggle (the only
    source of the rest, e.g. the per-serving serving size). Each merged option
    carries the toggle's browser **recipe** plus per-field **selectors**:
      * a field whose static columns genuinely differ -> its per-option column
        (read statically / from the rendered HTML);
      * a field whose static columns are identical (toggle-dependent) -> a single
        column, read from the browser-rendered HTML after the recipe runs.

    Options are paired by order (default/first column ↔ default/first toggle
    option). Returns None when the shapes don't line up (then the caller keeps
    its conservative behavior).
    """
    col_opts = col_group.get("options") or []
    tog_opts = toggle_group.get("options") or []
    if len(col_opts) < 2 or len(col_opts) != len(tog_opts):
        return None

    bases: set[str] = set()
    for o in col_opts:
        bases.update((o.get("field_selectors") or {}).keys())
    if not bases:
        return None

    distinct = {
        base: _base_values_distinct(col_opts, base, html, repeated_item_selector)
        for base in bases
    }
    # Nothing to merge if every field is already statically distinct (the plain
    # deterministic column set is correct on its own).
    if all(distinct.values()):
        return None

    merged_options: list[dict[str, Any]] = []
    for col_o, tog_o in zip(col_opts, tog_opts):
        field_selectors: dict[str, str] = {}
        for base in bases:
            source = col_o if distinct[base] else col_opts[0]
            sel = (source.get("field_selectors") or {}).get(base)
            if sel:
                field_selectors[str(base)] = str(sel)
        label = str(tog_o.get("label") or col_o.get("label") or "Option")
        merged_options.append({
            "id": tog_o.get("id") or sanitize_metadata_key(label),
            "label": label,
            "selected": True,
            "field_selectors": field_selectors,
            "recipe": tog_o.get("recipe") or [],
        })

    return {
        "label": toggle_group.get("label") or col_group.get("label") or "Variant",
        "metadata_key": (
            toggle_group.get("metadata_key")
            or sanitize_metadata_key(str(toggle_group.get("label", "")))
            or "variant"
        ),
        "execution": EXECUTION_MIXED,
        "options": merged_options,
    }


# --- Strict, verified repair of duplicate parallel-column selectors ----------
# The analyzer sometimes gives two columns of a parallel table the SAME selector
# (e.g. both serving-size columns -> "td:nth-child(2)"), so the two variants
# extract identical values. When OTHER field families in the same variant set DO
# have distinct, evenly-spaced ``td:nth-child(n)`` selectors, we infer the
# missing column index by preserving that spacing, then VERIFY it against the
# fetched HTML before accepting. Anything ambiguous or unverifiable -> change
# nothing (the duplicate-column warning still fires). No site-specific logic;
# only simple table-cell selectors with corroborating cross-family evidence are
# ever touched.
_TABLE_CELL_RE = re.compile(
    r"^(?P<prefix>.*?)\btd:nth-(?P<func>child|of-type)\(\s*(?P<idx>\d+)\s*\)"
    r"(?P<suffix>.*)$"
)
_TABLE_CELL_TOKEN_RE = re.compile(r"td:nth-(?:child|of-type)\(")


def _parse_table_cell_selector(
    selector: str,
) -> tuple[str, str, int, str] | None:
    """Parse ``td:nth-child(N) p`` into ``(prefix, func, index, suffix)``.

    Returns None unless the selector contains exactly ONE simple table-cell
    token (zero or several are both rejected as too complex to repair safely).
    """
    s = (selector or "").strip()
    if not s or len(_TABLE_CELL_TOKEN_RE.findall(s)) != 1:
        return None
    m = _TABLE_CELL_RE.match(s)
    if not m:
        return None
    return m.group("prefix"), m.group("func"), int(m.group("idx")), m.group("suffix")


def _build_table_cell_selector(
    prefix: str, func: str, index: int, suffix: str
) -> str:
    return f"{prefix}td:nth-{func}({index}){suffix}"


def _ordered_variant_keys(
    fields: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, dict[str, Any]]] | None, list[str] | None]:
    """Group fields into families ``{base: {variant_key: field}}`` with a shared
    ordered key set, mirroring ``detect_column_variants`` gating. Returns
    ``(None, None)`` when there is no clean parallel-column structure."""
    families: dict[str, dict[str, dict[str, Any]]] = {}
    key_order: list[str] = []
    for f in fields:
        base, key, _label, _strong = _split_variant_qualifier(_field_label(f))
        if key is None or not base:
            continue
        families.setdefault(base, {}).setdefault(key, f)
        if key not in key_order:
            key_order.append(key)
    families = {b: m for b, m in families.items() if len(m) >= 2}
    if not families:
        return None, None
    key_sets = {frozenset(m) for m in families.values()}
    if len(key_sets) != 1:
        return None, None
    shared = next(iter(key_sets))
    keys = [k for k in key_order if k in shared]
    if len(keys) < 2:
        return None, None
    return families, keys


def _repair_values_ok(values_for: dict[str, list[str | None]], keys: list[str]) -> bool:
    """Verification gate: every key extracts a present value on >=1 row, AND on
    at least one row two keys yield DIFFERENT non-empty values (so the repair
    actually breaks the duplication rather than re-pointing at the same data)."""
    for k in keys:
        if not any(v not in (None, "") for v in values_for.get(k, [])):
            return False
    n = max((len(values_for.get(k, [])) for k in keys), default=0)
    for i in range(n):
        present = []
        for k in keys:
            vs = values_for.get(k, [])
            v = vs[i] if i < len(vs) else None
            if v not in (None, ""):
                present.append(v)
        if len(present) >= 2 and len(set(present)) >= 2:
            return True
    return False


def repair_parallel_column_selectors(
    fields: list[dict[str, Any]],
    html: str,
    *,
    repeated_item_selector: str | None = None,
) -> list[dict[str, Any]]:
    """Return *fields* with duplicate parallel-column selectors repaired where a
    clear, VERIFIED table-column pattern exists; otherwise return *fields*
    unchanged. See the module note above for the strict rules.
    """
    if not fields or not html:
        return fields
    families, keys = _ordered_variant_keys(fields)
    if not families or not keys:
        return fields

    # Parse every member into a simple table-cell shape. A family is usable only
    # if ALL its members share one prefix/func/suffix shape.
    shaped: dict[str, dict[str, int]] = {}
    shape_of: dict[str, tuple[str, str, str]] = {}
    for base, members in families.items():
        idx_by_key: dict[str, int] = {}
        shapes: set[tuple[str, str, str]] = set()
        ok = True
        for key in keys:
            fld = members.get(key)
            parsed = (
                _parse_table_cell_selector(str(fld.get("selector") or ""))
                if fld else None
            )
            if parsed is None:
                ok = False
                break
            prefix, func, idx, suffix = parsed
            idx_by_key[key] = idx
            shapes.add((prefix, func, suffix))
        if ok and len(shapes) == 1:
            shaped[base] = idx_by_key
            shape_of[base] = next(iter(shapes))
    if len(shaped) < 2:
        return fields  # need >=1 donor + >=1 repair target

    # Donors: indices strictly increasing in key order. All donors must AGREE on
    # the consecutive deltas (consensus spacing) or we abort.
    delta_seqs: set[tuple[int, ...]] = set()
    for idx_by_key in shaped.values():
        idxs = [idx_by_key[k] for k in keys]
        if len(set(idxs)) == len(idxs) and all(
            idxs[i] < idxs[i + 1] for i in range(len(idxs) - 1)
        ):
            delta_seqs.add(tuple(idxs[i + 1] - idxs[i] for i in range(len(idxs) - 1)))
    if len(delta_seqs) != 1:
        return fields
    deltas = next(iter(delta_seqs))
    prefix_sum = [0]
    for d in deltas:
        prefix_sum.append(prefix_sum[-1] + d)
    pos = {k: prefix_sum[i] for i, k in enumerate(keys)}

    # Columns occupied by ANY simple-table-cell field (including non-variant
    # fields like "Food" at column 1). An inferred column must never land on one
    # of these — that's what stops a wrong anchor "verifying" against an
    # unrelated column (e.g. the food-name column reading as "distinct").
    occupied: set[int] = set()
    for f in fields:
        parsed = _parse_table_cell_selector(str(f.get("selector") or ""))
        if parsed is not None:
            occupied.add(parsed[2])

    repaired = [dict(f) for f in fields]
    repaired_by_id = {id(orig): cp for orig, cp in zip(fields, repaired)}
    changed = False

    for base, idx_by_key in shaped.items():
        idxs = [idx_by_key[k] for k in keys]
        if len(set(idxs)) == len(idxs):
            continue  # not broken (no duplicate column)
        prefix, func, suffix = shape_of[base]
        # Other fields' columns (everything occupied except this family's own).
        other_indices = occupied - set(idx_by_key.values())

        accepted: dict[str, int] | None = None
        for anchor in keys:
            inferred = {k: idx_by_key[anchor] + (pos[k] - pos[anchor]) for k in keys}
            ivals = [inferred[k] for k in keys]
            if any(v <= 0 for v in ivals):
                continue
            if any(ivals[i] >= ivals[i + 1] for i in range(len(ivals) - 1)):
                continue  # parallel columns are strictly increasing L->R
            if len(set(ivals)) != len(ivals):
                continue
            if set(ivals) & other_indices:
                continue  # would collide with another field's column
            values_for = {
                k: sample_selector_values(
                    html,
                    repeated_item_selector=repeated_item_selector,
                    selector=_build_table_cell_selector(
                        prefix, func, inferred[k], suffix
                    ),
                    field_type=str(
                        (families[base].get(k) or {}).get("type") or "string"
                    ),
                )
                for k in keys
            }
            if not _repair_values_ok(values_for, keys):
                continue
            if accepted is not None and accepted != inferred:
                accepted = None  # two different anchors verify -> ambiguous
                break
            accepted = inferred

        if accepted is not None:
            for k in keys:
                fld = families[base].get(k)
                cp = repaired_by_id.get(id(fld)) if fld is not None else None
                if cp is not None:
                    cp["selector"] = _build_table_cell_selector(
                        prefix, func, accepted[k], suffix
                    )
            changed = True

    return repaired if changed else fields


def detect_interaction_profile(
    html: str,
    fields: list[dict[str, Any]] | None = None,
    *,
    repeated_item_selector: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    """Build a draft (disabled) interaction_profile.

    Combines static-HTML control detection (interactive groups) with parallel-
    column detection over the spec ``fields`` (a deterministic group, listed
    first). When a deterministic column group is found, any interactive toggle
    whose variant axis it already covers is dropped (#5 reconciliation) so the
    page stays browser-free for that axis. Returns ``(profile, new_fields_or
    _None)`` — ``new_fields`` is the collapsed field list the caller should
    persist when a column-variant group was found, else ``None``.
    """
    interactive_groups = detect_interaction_groups(html)
    new_fields: list[dict[str, Any]] | None = None
    col_group: dict[str, Any] | None = None
    if fields:
        # Repair duplicate parallel-column selectors (verified against the page)
        # BEFORE collapsing, so the column set carries correct per-variant
        # selectors instead of two columns pointing at the same cell.
        repaired = repair_parallel_column_selectors(
            fields, html, repeated_item_selector=repeated_item_selector
        )
        collapsed, col_group = detect_column_variants(repaired)
        if col_group is not None:
            new_fields = collapsed

    if col_group is not None:
        # Axis-agnostic: find a toggle on the SAME axis whose values the column
        # set is missing (a toggle-dependent field), and MERGE them — static
        # columns for the distinct fields + the browser recipe for the rest.
        merge_toggle = _find_mergeable_toggle(
            col_group, interactive_groups, html, repeated_item_selector
        )
        merged = (
            _merge_column_set_with_toggle(
                col_group, merge_toggle, html, repeated_item_selector
            )
            if merge_toggle is not None
            else None
        )

        if merged is not None:
            others = [
                g for g in interactive_groups
                if g is not merge_toggle and not _covers_same_axis(merged, g)
            ]
            groups = [merged, *others]
        else:
            kept: list[dict[str, Any]] = []
            for g in interactive_groups:
                if _covers_same_axis(col_group, g):
                    continue  # complete column set already covers this axis
                if (
                    _is_serving_ordinal_column_set(col_group)
                    and g.get("metadata_key") == "serving_basis"
                    and len(g.get("options") or []) == len(col_group.get("options") or [])
                    and _serving_axis_values_distinct(
                        col_group, html, repeated_item_selector
                    )
                ):
                    continue  # serving column set already covers this axis
                kept.append(g)
            groups = [col_group, *kept]
    else:
        groups = interactive_groups

    return (
        {
            "enabled": False,
            "merge_variants": False,
            "max_variant_combinations": 12,
            "groups": groups,
        },
        new_fields,
    )
