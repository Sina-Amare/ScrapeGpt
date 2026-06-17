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

from app.services.interaction_profile import (
    EXECUTION_DETERMINISTIC,
    EXECUTION_INTERACTIVE,
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
}
# Parenthetical inner text accepted as a variant qualifier (basis or unit).
_QUALIFIER_PAREN_RE = re.compile(r"^(?:per\s+.+|metric|imperial)$", re.I)


def _split_variant_qualifier(
    label: str,
) -> tuple[str, str | None, str | None]:
    """Split a field label into ``(base, variant_key, option_label)``.

    Returns ``variant_key=None`` (a plain field) unless the label matches the
    CLOSED qualifier vocabulary above. ``variant_key`` is a grouping identity;
    ``option_label`` is what the variant option is shown as. Examples::

        "Calories 1"            -> ("Calories", "num:1", "Variant 1")
        "Secondary serving size"-> ("serving size", "secondary", "Secondary")
        "Calories (per 100 g)"  -> ("Calories", "per 100 g", "per 100 g")
        "Food"                  -> ("Food", None, None)
    """
    text = (label or "").strip()
    if not text:
        return "", None, None

    # 1. Parenthetical qualifier suffix: "Calories (per 100 g)".
    m = _PAREN_RE.match(text)
    if m and m.group(1).strip():
        inner = _clean(m.group(2))
        if _QUALIFIER_PAREN_RE.match(inner):
            return m.group(1).strip(), inner.lower(), inner
        if inner.lower() in _ORDINAL_WORDS:
            return m.group(1).strip(), inner.lower(), _ORDINAL_WORDS[inner.lower()]

    # 2. Leading / trailing ordinal word: "Secondary serving size".
    tokens = text.split()
    if len(tokens) >= 2:
        first = tokens[0].lower().strip(":-")
        last = tokens[-1].lower().strip(":-")
        if first in _ORDINAL_WORDS:
            return " ".join(tokens[1:]).strip(), first, _ORDINAL_WORDS[first]
        if last in _ORDINAL_WORDS:
            return " ".join(tokens[:-1]).strip(), last, _ORDINAL_WORDS[last]

    # 3. Trailing integer: "Calories 1".
    m = _NUM_SUFFIX_RE.match(text)
    if m and m.group(1).strip():
        n = int(m.group(2))
        return m.group(1).strip(), f"num:{n}", f"Variant {n}"

    return text, None, None


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
    for f in fields:
        base, key, opt_label = _split_variant_qualifier(_field_label(f))
        if key is None or not base:
            continue
        families.setdefault(base, []).append((key, f))
        if key not in key_label:
            key_label[key] = opt_label or key
            key_order.append(key)

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
        base, _key, _opt = _split_variant_qualifier(_field_label(f))
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


def detect_interaction_profile(
    html: str, fields: list[dict[str, Any]] | None = None
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
        collapsed, col_group = detect_column_variants(fields)
        if col_group is not None:
            new_fields = collapsed

    if col_group is not None:
        kept = [
            g for g in interactive_groups
            if not _covers_same_axis(col_group, g)
        ]
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
