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


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _is_unsafe(label: str) -> bool:
    label = label.strip()
    if not label or len(label) > 40:
        return True
    if label.isdigit():  # pagination page numbers
        return True
    return bool(_UNSAFE_TEXT.search(label))


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


_SUFFIX_RE = re.compile(r"^(.*?)[\s_:#-]*(\d+)$")


def _split_suffix(label: str) -> tuple[str, int | None]:
    """('Calories 1' -> ('Calories', 1)); ('Food' -> ('Food', None))."""
    m = _SUFFIX_RE.match((label or "").strip())
    if m and m.group(1).strip():
        return m.group(1).strip(), int(m.group(2))
    return (label or "").strip(), None


def _field_label(field: dict[str, Any]) -> str:
    return str(field.get("user_label") or field.get("label") or field.get("name") or "")


def detect_column_variants(
    fields: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Detect parallel in-DOM columns expressed as numbered sibling fields.

    Analyzers model pages like calories.info as flat fields — ``Calories 1`` /
    ``Calories 2``, ``Serving Size 1`` / ``Serving Size 2`` — one per parallel
    column. This collapses each numbered family into a single base field
    (``Calories``) and returns a **deterministic** variant group whose options
    override those base fields to each column's selector. Returns
    ``(new_fields, group)`` or ``(fields, None)`` when there is no such pattern.
    No browser needed: the alternate values are already in the static DOM.
    """
    families: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for f in fields:
        base, idx = _split_suffix(_field_label(f))
        if idx is not None and base:
            families.setdefault(base, []).append((idx, f))
    # Keep only families with >=2 distinct indices.
    families = {
        b: sorted(v, key=lambda t: t[0])
        for b, v in families.items()
        if len({i for i, _ in v}) >= 2
    }
    if not families:
        return fields, None

    indices = sorted({i for v in families.values() for i, _ in v})
    options: list[dict[str, Any]] = []
    for i in indices:
        field_selectors: dict[str, str] = {}
        for base, members in families.items():
            sel = next(
                (fld.get("selector") for (idx, fld) in members if idx == i), None
            )
            if sel:
                field_selectors[base] = str(sel)
        if field_selectors:
            options.append({
                "id": f"set_{i}",
                "label": f"Variant {i}",
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
        base, _ = _split_suffix(_field_label(f))
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


def detect_interaction_profile(
    html: str, fields: list[dict[str, Any]] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    """Build a draft (disabled) interaction_profile.

    Combines static-HTML control detection (interactive groups) with numbered
    parallel-column detection over the spec ``fields`` (a deterministic group,
    listed first). Returns ``(profile, new_fields_or_None)`` — ``new_fields`` is
    the collapsed field list the caller should persist when a column-variant
    group was found, else ``None``.
    """
    groups = detect_interaction_groups(html)
    new_fields: list[dict[str, Any]] | None = None
    if fields:
        collapsed, col_group = detect_column_variants(fields)
        if col_group is not None:
            groups = [col_group, *groups]
            new_fields = collapsed
    return (
        {
            "enabled": False,
            "merge_variants": False,
            "max_variant_combinations": 12,
            "groups": groups,
        },
        new_fields,
    )
