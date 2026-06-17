"""Phase 2 unit tests: static-HTML detection of page-variant controls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.job import ExtractionMode
from app.services.interaction_detect import (
    detect_column_variants,
    detect_interaction_groups,
    detect_interaction_profile,
    repair_parallel_column_selectors,
)
from app.services.interaction_extraction import extract_records_with_variants


def _by_key(groups):
    return {g["metadata_key"]: g for g in groups}


def test_detect_segmented_buttons_toggle():
    html = """
    <div class="toggle">
      <button class="active">Metric</button>
      <button>Imperial</button>
    </div>
    """
    groups = detect_interaction_groups(html)
    assert len(groups) == 1
    g = groups[0]
    assert g["metadata_key"] == "unit_system"
    assert g["execution"] == "interactive"
    labels = [o["label"] for o in g["options"]]
    assert labels == ["Metric", "Imperial"]
    # Active option is the no-click baseline; the other carries a click recipe.
    assert g["options"][0]["recipe"] == []
    assert g["options"][1]["recipe"][0]["action"] == "click"
    assert g["options"][1]["recipe"][0]["value"] == "Imperial"


def test_detect_radio_group_uses_checked_as_baseline():
    html = """
    <form>
      <label><input type="radio" name="basis" value="100g" checked> per 100 g</label>
      <label><input type="radio" name="basis" value="serving"> per serving</label>
    </form>
    """
    groups = detect_interaction_groups(html)
    assert len(groups) == 1
    g = groups[0]
    assert g["execution"] == "interactive"
    assert g["options"][0]["recipe"] == []  # checked baseline
    assert g["options"][1]["recipe"][0]["action"] == "click"


def test_detect_select_dropdown():
    html = """
    <select name="size">
      <option selected>Small</option>
      <option>Large</option>
    </select>
    """
    groups = detect_interaction_groups(html)
    assert len(groups) == 1
    g = groups[0]
    assert [o["label"] for o in g["options"]] == ["Small", "Large"]
    assert g["options"][1]["recipe"][0]["action"] == "select"
    assert g["options"][1]["recipe"][0]["value"].endswith("::Large")


def test_unsafe_controls_excluded():
    html = """
    <div><button>Submit</button><button>Search</button></div>
    <div class="pager"><a>1</a><a>2</a><a>3</a></div>
    <form>
      <input type="password" name="pw">
      <input type="radio" name="remember" value="y" checked> Yes
      <input type="radio" name="remember" value="n"> No
    </form>
    """
    groups = detect_interaction_groups(html)
    # submit/search excluded (unsafe), pagination numbers excluded, and the radio
    # group inside the password form is excluded.
    assert groups == []


def test_external_nav_links_not_a_variant_group():
    html = """
    <nav>
      <a href="https://other.com/a">Alpha</a>
      <a href="https://other.com/b">Bravo</a>
    </nav>
    """
    assert detect_interaction_groups(html) == []


# --- Global-navigation / chrome regions are not data-variant controls --------


def test_internal_nav_menu_is_not_a_variant_group():
    """A <nav> of internal, short, toggle-like category links is site nav, not a
    data variant — even though it would otherwise pass the segmented checks."""
    html = """
    <nav class="site-nav">
      <a href="/food/meat" class="active">Meat</a>
      <a href="/food/fish">Fish</a>
      <a href="/food/fruit">Fruit</a>
    </nav>
    """
    assert detect_interaction_groups(html) == []


def test_header_menu_region_is_excluded():
    html = """
    <header>
      <div class="menu">
        <a href="/a" class="active">Home</a>
        <a href="/b">About</a>
        <a href="/c">Contact</a>
      </div>
    </header>
    """
    assert detect_interaction_groups(html) == []


def test_navbar_class_region_is_excluded():
    html = """
    <div class="navbar">
      <a href="/x" class="active">One</a>
      <a href="/y">Two</a>
    </div>
    """
    assert detect_interaction_groups(html) == []


def test_aria_menubar_region_is_excluded():
    html = """
    <div role="menubar">
      <button class="active">Tab A</button>
      <button>Tab B</button>
    </div>
    """
    assert detect_interaction_groups(html) == []


def test_content_toggles_preserved_alongside_navigation():
    """The real fix: site nav is ignored, but genuine in-content toggles
    (serving basis + metric/imperial) are still detected."""
    html = """
    <header><nav class="main-nav">
      <a href="/food/meat" class="active">Meat</a>
      <a href="/food/fish">Fish</a>
      <a href="/food/fruit">Fruit</a>
    </nav></header>
    <main>
      <div class="unit-toggle">
        <button class="active">Metric</button><button>Imperial</button>
      </div>
      <div class="basis-toggle">
        <button class="active">Show per 100 g</button>
        <button>Show per serving</button>
      </div>
    </main>
    """
    keys = {g["metadata_key"] for g in detect_interaction_groups(html)}
    assert keys == {"unit_system", "serving_basis"}


# --- #8: in-page section anchors are not data-variant toggles ----------------


def test_section_anchor_group_is_dropped():
    """Generic section words that jump to on-page anchors are in-page nav, not a
    variant toggle."""
    html = """
    <main>
      <div class="tabs">
        <a href="#charts" class="active">Charts</a>
        <a href="#more-information">More information</a>
      </div>
    </main>
    """
    assert detect_interaction_groups(html) == []


def test_section_tab_buttons_are_dropped():
    """The real calories.info leftover: MUI tab BUTTONS 'Charts' /
    'More Information' (no href) are in-page section nav, not a data toggle."""
    html = """
    <main>
      <div class="MuiTabs-flexContainer">
        <button class="active">Charts</button>
        <button>More Information</button>
      </div>
    </main>
    """
    assert detect_interaction_groups(html) == []


def test_real_button_toggle_survives_alongside_section_tabs():
    """SURVIVAL GATE: dropping section tabs must NOT drop genuine data toggles
    (buttons AND fragment-anchor toggles with non-generic labels)."""
    html = """
    <main>
      <div class="tabs">
        <button class="active">Charts</button><button>More Information</button>
      </div>
      <div class="unit-toggle">
        <button class="active">Metric</button><button>Imperial</button>
      </div>
      <div class="basis-toggle">
        <a href="#per-100g" class="active">Per 100 g</a>
        <a href="#per-serving">Per serving</a>
      </div>
    </main>
    """
    keys = {g["metadata_key"] for g in detect_interaction_groups(html)}
    assert keys == {"unit_system", "serving_basis"}  # section tabs dropped


def test_group_with_one_non_section_label_survives():
    """If even one label is not a generic section word, the group is NOT a pure
    section-nav and survives (e.g. a Map/List view toggle)."""
    html = """
    <main><div class="tabs">
      <button class="active">Map</button><button>List</button>
    </div></main>
    """
    # "map" is a section word but "list" is not -> not all-section -> kept.
    groups = detect_interaction_groups(html)
    assert len(groups) == 1


def test_detect_interaction_profile_is_disabled_draft():
    html = '<div><button class="active">Metric</button><button>Imperial</button></div>'
    profile, new_fields = detect_interaction_profile(html)
    assert profile["enabled"] is False
    assert profile["max_variant_combinations"] == 12
    assert len(profile["groups"]) == 1
    assert new_fields is None  # no fields passed -> no collapse


def test_detect_empty_html():
    assert detect_interaction_groups("") == []


# --- Deterministic parallel-column detection (numbered sibling fields) --------


def _f(label, selector):
    return {"name": label, "label": label, "user_label": label,
            "selector": selector, "type": "string", "selected": True}


def test_detect_column_variants_collapses_numbered_fields():
    fields = [
        _f("Food", "td:nth-child(1) p"),
        _f("Serving Size 1", "td:nth-child(2) p"),
        _f("Calories 1", "td:nth-child(3) p"),
        _f("Serving Size 2", "td:nth-child(4) p"),
        _f("Calories 2", "td:nth-child(5) p"),
    ]
    new_fields, group = detect_column_variants(fields)
    # Calories 1/2 and Serving Size 1/2 collapse to single base fields.
    labels = [f["label"] for f in new_fields]
    assert labels == ["Food", "Serving Size", "Calories"]
    assert group is not None
    assert group["execution"] == "deterministic"
    assert [o["label"] for o in group["options"]] == ["Variant 1", "Variant 2"]
    v1, v2 = group["options"]
    assert v1["field_selectors"]["Calories"] == "td:nth-child(3) p"
    assert v2["field_selectors"]["Calories"] == "td:nth-child(5) p"
    assert v2["field_selectors"]["Serving Size"] == "td:nth-child(4) p"


def test_detect_column_variants_noop_without_numbered_fields():
    fields = [_f("Title", ".t"), _f("Price", ".p")]
    new_fields, group = detect_column_variants(fields)
    assert group is None
    assert new_fields == fields


def test_detect_interaction_profile_includes_column_group_first():
    fields = [
        _f("Food", "td:nth-child(1) p"),
        _f("Calories 1", "td:nth-child(3) p"),
        _f("Calories 2", "td:nth-child(5) p"),
    ]
    profile, new_fields = detect_interaction_profile("", fields)
    assert new_fields is not None
    assert profile["groups"][0]["metadata_key"] == "column_set"


# --- #4: generalized deterministic column detection (closed vocabulary) -------


def test_detect_column_variants_collapses_ordinal_fields():
    fields = [
        _f("Food", "td:nth-child(1) p"),
        _f("Primary Serving Size", "td:nth-child(2) p"),
        _f("Secondary Serving Size", "td:nth-child(3) p"),
        _f("Primary Calories", "td:nth-child(4) p"),
        _f("Secondary Calories", "td:nth-child(5) p"),
    ]
    new_fields, group = detect_column_variants(fields)
    assert group is not None
    assert group["execution"] == "deterministic"
    assert [o["label"] for o in group["options"]] == ["Primary", "Secondary"]
    assert [f["label"] for f in new_fields] == ["Food", "Serving Size", "Calories"]
    v1, v2 = group["options"]
    assert v1["field_selectors"]["Serving Size"] == "td:nth-child(2) p"
    assert v2["field_selectors"]["Serving Size"] == "td:nth-child(3) p"
    # Deterministic: every option has an EMPTY recipe (no browser click).
    assert all(o["recipe"] == [] for o in group["options"])


def test_detect_column_variants_collapses_parenthetical_basis():
    fields = [
        _f("Food", "td:nth-child(1) p"),
        _f("Serving Size (per 100 g)", "td:nth-child(2) p"),
        _f("Calories (per 100 g)", "td:nth-child(3) p"),
        _f("Serving Size (per serving)", "td:nth-child(4) p"),
        _f("Calories (per serving)", "td:nth-child(5) p"),
    ]
    new_fields, group = detect_column_variants(fields)
    assert group is not None
    assert group["execution"] == "deterministic"
    assert [o["label"] for o in group["options"]] == ["per 100 g", "per serving"]
    assert [f["label"] for f in new_fields] == ["Food", "Serving Size", "Calories"]
    v1, v2 = group["options"]
    assert v1["field_selectors"]["Calories"] == "td:nth-child(3) p"
    assert v2["field_selectors"]["Calories"] == "td:nth-child(5) p"
    assert all(o["recipe"] == [] for o in group["options"])


def test_detect_column_variants_collapses_metric_imperial():
    fields = [
        _f("Item", ".n"),
        _f("Weight (metric)", ".wm"),
        _f("Weight (imperial)", ".wi"),
        _f("Height (metric)", ".hm"),
        _f("Height (imperial)", ".hi"),
    ]
    _new_fields, group = detect_column_variants(fields)
    assert group is not None
    assert [o["label"] for o in group["options"]] == ["metric", "imperial"]


def test_unrecognized_trailing_word_does_not_collapse():
    """Closed vocabulary: 'USD'/'EUR' are different data, not a parallel column —
    they must NOT be collapsed (guards against open-ended over-collapse)."""
    fields = [_f("Price USD", ".usd"), _f("Price EUR", ".eur")]
    new_fields, group = detect_column_variants(fields)
    assert group is None
    assert new_fields == fields


def test_mismatched_qualifier_families_do_not_collapse():
    """Families that expose DIFFERENT variant-key sets are not parallel columns
    and must not collapse (ordinal family vs parenthetical-basis family)."""
    fields = [
        _f("Primary Serving Size", ".a"),
        _f("Secondary Serving Size", ".b"),
        _f("Calories (per 100 g)", ".c"),
        _f("Calories (per serving)", ".d"),
    ]
    new_fields, group = detect_column_variants(fields)
    assert group is None
    assert new_fields == fields


def test_single_qualified_family_member_does_not_collapse():
    """A lone qualified field (no sibling with another key) is left alone."""
    fields = [_f("Food", ".f"), _f("Calories (per serving)", ".c")]
    new_fields, group = detect_column_variants(fields)
    assert group is None
    assert new_fields == fields


@pytest.mark.asyncio
async def test_generalized_variants_extract_without_browser():
    """The real functional payoff: a static parenthetical-basis table extracts
    BOTH variants with NO browser callback invoked."""
    html = (
        "<table><tbody>"
        "<tr>"
        "<td><p>Apple</p></td>"
        "<td><p>100 g</p></td><td><p>52</p></td>"
        "<td><p>1 cup</p></td><td><p>65</p></td>"
        "</tr>"
        "</tbody></table>"
    )
    fields = [
        _f("Food", "td:nth-child(1) p"),
        _f("Serving Size (per 100 g)", "td:nth-child(2) p"),
        _f("Calories (per 100 g)", "td:nth-child(3) p"),
        _f("Serving Size (per serving)", "td:nth-child(4) p"),
        _f("Calories (per serving)", "td:nth-child(5) p"),
    ]
    new_fields, group = detect_column_variants(fields)
    assert group is not None and group["execution"] == "deterministic"

    profile = {"enabled": True, "max_variant_combinations": 12, "groups": [group]}
    project = SimpleNamespace(analysis={"repeated_item_selector": "tbody tr"})
    spec = SimpleNamespace(
        mode=ExtractionMode.STRUCTURED,
        content_config={},
        fields=new_fields,
        interaction_profile=profile,
    )

    called = {"browser": False}

    async def _no_browser(_recipes):
        called["browser"] = True
        raise AssertionError("a browser must not launch for static variants")

    records, _warnings = await extract_records_with_variants(
        base_html=html,
        source_url="https://example.com",
        project=project,
        spec=spec,
        max_records=50,
        fetch_variant_htmls=_no_browser,
    )

    assert called["browser"] is False
    by_variant = {
        str(r.normalized_data.get("column_set")): r.normalized_data.get("Calories")
        for r in records
    }
    assert by_variant.get("per 100 g") == "52"
    assert by_variant.get("per serving") == "65"


# --- #5: bounded reconciliation of redundant interactive groups --------------


def test_reconciliation_drops_redundant_interactive_axis():
    """An interactive toggle whose axis a deterministic column group already
    covers is dropped; a toggle on a DIFFERENT axis is kept."""
    html = """
    <main>
      <div class="basis">
        <button class="active">Show per 100 g</button>
        <button>Show per serving</button>
      </div>
      <div class="unit">
        <button class="active">Metric</button><button>Imperial</button>
      </div>
    </main>
    """
    fields = [
        _f("Calories (per 100 g)", ".a"),
        _f("Calories (per serving)", ".b"),
        _f("Protein (per 100 g)", ".c"),
        _f("Protein (per serving)", ".d"),
    ]
    profile, _new = detect_interaction_profile(html, fields)
    keys = [g["metadata_key"] for g in profile["groups"]]
    assert "column_set" in keys
    assert "serving_basis" not in keys  # covered by column_set -> dropped
    assert "unit_system" in keys        # different axis -> kept


def test_reconciliation_keeps_interactive_without_column_group():
    """With no fields there is no deterministic group, so nothing is dropped."""
    html = """
    <main><div class="unit">
      <button class="active">Metric</button><button>Imperial</button>
    </div></main>
    """
    profile, _new = detect_interaction_profile(html)
    keys = [g["metadata_key"] for g in profile["groups"]]
    assert keys == ["unit_system"]


def test_reconciliation_numbered_columns_do_not_suppress_interactive():
    """A numbered column group carries no axis tokens, so it must never suppress
    an interactive toggle (ambiguous -> keep)."""
    html = """
    <main><div class="unit">
      <button class="active">Metric</button><button>Imperial</button>
    </div></main>
    """
    fields = [_f("Calories 1", ".a"), _f("Calories 2", ".b")]
    profile, _new = detect_interaction_profile(html, fields)
    keys = [g["metadata_key"] for g in profile["groups"]]
    assert "column_set" in keys and "unit_system" in keys


def test_full_profile_path_keeps_uncolumned_axis_interactive():
    """Scope guard (finding #3): detect_interaction_profile makes browser-free
    ONLY the axes the analyzer columned. A toggle axis with no matching column
    group stays INTERACTIVE — it still needs a browser. This documents that a
    page is not automatically 'all variants browser-free'; only covered axes are."""
    html = """
    <main><div class="unit">
      <button class="active">Metric</button><button>Imperial</button>
    </div></main>
    """
    # Analyzer columned the BASIS axis only (numbered parallel columns).
    fields = [_f("Calories 1", ".a"), _f("Calories 2", ".b")]
    profile, new_fields = detect_interaction_profile(html, fields)
    groups = {g["metadata_key"]: g for g in profile["groups"]}
    assert new_fields is not None
    # The columned axis is deterministic (browser-free)...
    assert groups["column_set"]["execution"] == "deterministic"
    # ...but the un-columned unit toggle is still interactive (needs a browser).
    assert groups["unit_system"]["execution"] == "interactive"


@pytest.mark.asyncio
async def test_reconciliation_makes_single_axis_page_browser_free():
    """calories.info-style: a basis column group + a redundant basis toggle ->
    the toggle is dropped and extraction launches ZERO browsers."""
    html = (
        "<main>"
        "<div class='basis'><button class='active'>Show per 100 g</button>"
        "<button>Show per serving</button></div>"
        "<table><tbody><tr>"
        "<td><p>Apple</p></td><td><p>52</p></td><td><p>65</p></td>"
        "</tr></tbody></table>"
        "</main>"
    )
    fields = [
        _f("Food", "td:nth-child(1) p"),
        _f("Calories (per 100 g)", "td:nth-child(2) p"),
        _f("Calories (per serving)", "td:nth-child(3) p"),
    ]
    profile, new_fields = detect_interaction_profile(html, fields)
    assert [g["metadata_key"] for g in profile["groups"]] == ["column_set"]
    assert new_fields is not None

    enabled = {**profile, "enabled": True}
    project = SimpleNamespace(analysis={"repeated_item_selector": "tbody tr"})
    spec = SimpleNamespace(
        mode=ExtractionMode.STRUCTURED,
        content_config={},
        fields=new_fields,
        interaction_profile=enabled,
    )
    called = {"browser": False}

    async def _no_browser(_recipes):
        called["browser"] = True
        raise AssertionError("no browser for a fully reconciled static page")

    records, _w = await extract_records_with_variants(
        base_html=html,
        source_url="https://example.com",
        project=project,
        spec=spec,
        max_records=50,
        fetch_variant_htmls=_no_browser,
    )
    assert called["browser"] is False
    cals = {
        str(r.normalized_data.get("column_set")): r.normalized_data.get("Calories")
        for r in records
    }
    assert cals.get("per 100 g") == "52"
    assert cals.get("per serving") == "65"


# --- "first/second reported serving" label parsing ---------------------------


def test_reported_serving_phrases_share_ordinal_key():
    fields = [
        _f("Food", "td:nth-child(1)"),
        _f("Serving size (first reported serving)", "td:nth-child(2)"),
        _f("Calories (first reported serving)", "td:nth-child(3)"),
        _f("Serving size (second reported serving)", "td:nth-child(4)"),
        _f("Calories (second reported serving)", "td:nth-child(5)"),
    ]
    new_fields, group = detect_column_variants(fields)
    assert group is not None
    assert [o["label"] for o in group["options"]] == [
        "First reported serving",
        "Second reported serving",
    ]
    assert [f["label"] for f in new_fields] == ["Food", "Serving size", "Calories"]


def test_bare_first_second_ordinals_collapse():
    fields = [
        _f("First serving size", ".a"),
        _f("Second serving size", ".b"),
        _f("First calories", ".c"),
        _f("Second calories", ".d"),
    ]
    _new, group = detect_column_variants(fields)
    assert group is not None
    assert [o["label"] for o in group["options"]] == ["First", "Second"]


# --- #2: strict, verified parallel-column selector repair --------------------


_REPAIR_HTML = (
    "<table><tbody>"
    "<tr><td>Beef</td><td>100 g</td><td>156</td>"
    "<td>1 portion (170 g)</td><td>265</td></tr>"
    "<tr><td>Veal</td><td>100 g</td><td>140</td>"
    "<td>1 cutlet (50 g)</td><td>70</td></tr>"
    "</tbody></table>"
)


def _broken_serving_fields():
    return [
        _f("Food", "td:nth-child(1)"),
        _f("Serving size (first reported serving)", "td:nth-child(2)"),
        _f("Calories (first reported serving)", "td:nth-child(3)"),
        _f("Serving size (second reported serving)", "td:nth-child(2)"),  # dup
        _f("Calories (second reported serving)", "td:nth-child(5)"),
    ]


def _sel_for(fields, label):
    return next(f["selector"] for f in fields if f["label"] == label)


def test_repair_infers_missing_column_from_sibling_spacing():
    """serving second wrongly = col 2; calories spacing (3->5, +2) implies
    serving second = col 4, verified against the HTML."""
    fields = _broken_serving_fields()
    repaired = repair_parallel_column_selectors(
        fields, _REPAIR_HTML, repeated_item_selector="tbody tr"
    )
    assert _sel_for(repaired, "Serving size (second reported serving)") == "td:nth-child(4)"
    # untouched columns stay put
    assert _sel_for(repaired, "Serving size (first reported serving)") == "td:nth-child(2)"
    assert _sel_for(repaired, "Calories (second reported serving)") == "td:nth-child(5)"


def test_repair_noop_without_donor_family():
    """Only the broken family present -> no spacing evidence -> no repair."""
    fields = [
        _f("Food", "td:nth-child(1)"),
        _f("Serving size (first reported serving)", "td:nth-child(2)"),
        _f("Serving size (second reported serving)", "td:nth-child(2)"),
    ]
    repaired = repair_parallel_column_selectors(
        fields, _REPAIR_HTML, repeated_item_selector="tbody tr"
    )
    assert repaired is fields  # unchanged


def test_repair_noop_when_selector_shape_differs():
    """Non-table-cell selectors are never repaired (shape mismatch)."""
    fields = [
        _f("Serving size (first reported serving)", ".serv"),
        _f("Serving size (second reported serving)", ".serv"),  # dup, but class
        _f("Calories (first reported serving)", "td:nth-child(3)"),
        _f("Calories (second reported serving)", "td:nth-child(5)"),
    ]
    repaired = repair_parallel_column_selectors(
        fields, _REPAIR_HTML, repeated_item_selector="tbody tr"
    )
    assert repaired is fields


def test_repair_noop_when_candidate_matches_no_values():
    """If the inferred column doesn't exist in the HTML, keep current behavior."""
    three_col = (
        "<table><tbody>"
        "<tr><td>Beef</td><td>100 g</td><td>156</td></tr>"
        "</tbody></table>"
    )
    fields = _broken_serving_fields()
    repaired = repair_parallel_column_selectors(
        fields, three_col, repeated_item_selector="tbody tr"
    )
    # col 4 has no value -> verification fails -> duplicate left in place
    assert _sel_for(repaired, "Serving size (second reported serving)") == "td:nth-child(2)"


def test_repair_noop_when_inferred_values_not_distinct():
    """If the inferred column repeats the duplicate's value, don't accept it."""
    same_col = (
        "<table><tbody>"
        "<tr><td>Beef</td><td>100 g</td><td>156</td><td>100 g</td><td>265</td></tr>"
        "</tbody></table>"
    )
    fields = _broken_serving_fields()
    repaired = repair_parallel_column_selectors(
        fields, same_col, repeated_item_selector="tbody tr"
    )
    assert _sel_for(repaired, "Serving size (second reported serving)") == "td:nth-child(2)"


# --- #3 + acceptance: full detect + reconcile + correct distinct extraction --


def test_reported_serving_basis_toggle_is_reconciled_away():
    html = (
        "<main>"
        "<div class='basis'><button class='active'>Per 100 g</button>"
        "<button>Per serving</button></div>"
        + _REPAIR_HTML
        + "<div class='unit'><button class='active'>Metric</button>"
        "<button>Imperial</button></div></main>"
    )
    profile, _new = detect_interaction_profile(
        html, _broken_serving_fields(), repeated_item_selector="tbody tr"
    )
    keys = [g["metadata_key"] for g in profile["groups"]]
    assert "column_set" in keys
    assert "serving_basis" not in keys  # ordinal serving set covers this axis
    assert "unit_system" in keys        # unrelated toggle kept interactive


@pytest.mark.asyncio
async def test_serving_basis_merged_when_static_serving_values_identical():
    """calories.info reality: the per-serving serving size is NOT in the static
    DOM (both columns read '100 g'); only calories differ. The column set
    (static calories) and the serving_basis toggle (browser serving size) are
    MERGED into one 'mixed' axis: static calories per option + a browser recipe
    for the toggle-only serving size."""
    base_html = (
        "<main>"
        "<div class='basis'><button class='active'>Show per 100 g</button>"
        "<button>Show per serving</button></div>"
        "<table><tbody>"
        "<tr><td>Beef</td><td><p>100 g</p></td><td>156</td>"
        "<td><p>100 g</p></td><td>265</td></tr>"
        "</tbody></table></main>"
    )
    # Clicking "Show per serving" makes BOTH serving columns render the real size.
    perserving_html = base_html.replace("<p>100 g</p>", "<p>1 portion (170 g)</p>")
    fields = [
        _f("Food", "td:nth-child(1)"),
        _f("Serving size (first reported serving)", "td:nth-child(2) p"),
        _f("Calories (first reported serving)", "td:nth-child(3)"),
        _f("Serving size (second reported serving)", "td:nth-child(4) p"),
        _f("Calories (second reported serving)", "td:nth-child(5)"),
    ]
    profile, new_fields = detect_interaction_profile(
        base_html, fields, repeated_item_selector="tbody tr"
    )
    assert [g["metadata_key"] for g in profile["groups"]] == ["serving_basis"]
    merged = profile["groups"][0]
    assert merged["execution"] == "mixed"  # static selectors + browser recipe
    assert [o["label"] for o in merged["options"]] == [
        "Show per 100 g",
        "Show per serving",
    ]
    assert [f["label"] for f in new_fields] == ["Food", "Serving size", "Calories"]

    async def browser(recipes):
        out = {}
        for rid, recipe in recipes.items():
            clicked = any(s.get("value") == "Show per serving" for s in recipe)
            out[rid] = perserving_html if clicked else base_html
        return out

    spec = SimpleNamespace(
        mode=ExtractionMode.STRUCTURED, content_config={},
        fields=new_fields, interaction_profile={**profile, "enabled": True},
    )
    records, _w = await extract_records_with_variants(
        base_html=base_html, source_url="https://x",
        project=SimpleNamespace(analysis={"repeated_item_selector": "tbody tr"}),
        spec=spec, max_records=50, fetch_variant_htmls=browser,
    )
    by_basis = {
        str(r.normalized_data.get("serving_basis")): r.normalized_data
        for r in records
    }
    # per-100g: static serving + static per-100g calories (no browser needed)
    assert by_basis["Show per 100 g"]["Serving size"] == "100 g"
    assert by_basis["Show per 100 g"]["Calories"] == "156"
    # per-serving: browser-rendered serving + static per-serving calories
    assert by_basis["Show per serving"]["Serving size"] == "1 portion (170 g)"
    assert by_basis["Show per serving"]["Calories"] == "265"


@pytest.mark.asyncio
async def test_repaired_columns_extract_distinct_values_browser_free():
    """The headline fix: after repair the two serving columns extract DIFFERENT
    values (not both '100 g'), with no browser launched."""
    profile, new_fields = detect_interaction_profile(
        _REPAIR_HTML, _broken_serving_fields(), repeated_item_selector="tbody tr"
    )
    assert [g["metadata_key"] for g in profile["groups"]] == ["column_set"]

    enabled = {**profile, "enabled": True}
    project = SimpleNamespace(analysis={"repeated_item_selector": "tbody tr"})
    spec = SimpleNamespace(
        mode=ExtractionMode.STRUCTURED,
        content_config={},
        fields=new_fields,
        interaction_profile=enabled,
    )
    called = {"browser": False}

    async def _no_browser(_recipes):
        called["browser"] = True
        raise AssertionError("no browser for a repaired static column set")

    records, _w = await extract_records_with_variants(
        base_html=_REPAIR_HTML,
        source_url="https://example.com",
        project=project,
        spec=spec,
        max_records=50,
        fetch_variant_htmls=_no_browser,
    )
    assert called["browser"] is False
    # Key by (food, variant) — _REPAIR_HTML has two rows, so a variant-only dict
    # would collapse them. The point is first != second for the SAME food.
    serving = {
        (r.normalized_data.get("Food"), str(r.normalized_data.get("column_set"))):
        r.normalized_data.get("Serving size")
        for r in records
    }
    assert serving[("Beef", "First reported serving")] == "100 g"
    assert serving[("Beef", "Second reported serving")] == "1 portion (170 g)"
    assert serving[("Veal", "Second reported serving")] == "1 cutlet (50 g)"


# --- inconsistent analyzer labels: structural (label-independent) handling ----


def test_inconsistent_parenthetical_labels_collapse_with_two_families():
    """The analyzer can't always name the 2nd column (per-serving label isn't in
    the static DOM), so it falls back to '(alternate column)'. Two base families
    sharing the qualifier set still collapse structurally."""
    fields = [
        _f("Food", "td:nth-child(1)"),
        _f("Serving Size (per 100 g)", "td:nth-child(2)"),
        _f("Calories (per 100 g)", "td:nth-child(3)"),
        _f("Serving Size (alternate column)", "td:nth-child(4)"),
        _f("Calories (alternate column)", "td:nth-child(5)"),
    ]
    new_fields, group = detect_column_variants(fields)
    assert group is not None
    assert [f["label"] for f in new_fields] == ["Food", "Serving Size", "Calories"]
    assert [o["label"] for o in group["options"]] == ["per 100 g", "alternate column"]


def test_single_family_weak_parenthetical_does_not_collapse():
    """A lone family with arbitrary parenthetical keys has no structural backup
    (only one base) -> no collapse (guards against over-collapse)."""
    fields = [_f("Price (USD)", ".usd"), _f("Price (EUR)", ".eur")]
    new_fields, group = detect_column_variants(fields)
    assert group is None
    assert new_fields == fields


def test_two_families_weak_parenthetical_collapse():
    """Two families sharing weak (arbitrary) keys DO collapse — the shared
    qualifier set across bases is the structural signal."""
    fields = [
        _f("Revenue (2024)", ".r24"), _f("Revenue (2023)", ".r23"),
        _f("Profit (2024)", ".p24"), _f("Profit (2023)", ".p23"),
    ]
    _new, group = detect_column_variants(fields)
    assert group is not None
    assert [o["label"] for o in group["options"]] == ["2024", "2023"]


@pytest.mark.asyncio
async def test_merge_triggers_with_inconsistent_alternate_column_labels():
    """Structural merge: '(per 100 g)' / '(alternate column)' labels + a
    serving_basis toggle + identical static serving values -> merged 'mixed'
    group using the meaningful TOGGLE labels, with the real per-serving size
    coming from the browser."""
    base_html = (
        "<main>"
        "<div class='basis'><button class='active'>Show per 100 g</button>"
        "<button>Show per serving</button></div>"
        "<table><tbody>"
        "<tr><td>Beef</td><td><p>100 g</p></td><td>156</td>"
        "<td><p>100 g</p></td><td>265</td></tr>"
        "</tbody></table></main>"
    )
    perserving = base_html.replace("<p>100 g</p>", "<p>1 portion (170 g)</p>")
    fields = [
        _f("Food", "td:nth-child(1)"),
        _f("Serving Size (per 100 g)", "td:nth-child(2) p"),
        _f("Calories (per 100 g)", "td:nth-child(3)"),
        _f("Serving Size (alternate column)", "td:nth-child(4) p"),
        _f("Calories (alternate column)", "td:nth-child(5)"),
    ]
    profile, new_fields = detect_interaction_profile(
        base_html, fields, repeated_item_selector="tbody tr"
    )
    assert [g["metadata_key"] for g in profile["groups"]] == ["serving_basis"]
    merged = profile["groups"][0]
    assert merged["execution"] == "mixed"
    assert [o["label"] for o in merged["options"]] == [
        "Show per 100 g",
        "Show per serving",
    ]

    async def browser(recipes):
        return {
            rid: (
                perserving
                if any(s.get("value") == "Show per serving" for s in recipe)
                else base_html
            )
            for rid, recipe in recipes.items()
        }

    spec = SimpleNamespace(
        mode=ExtractionMode.STRUCTURED, content_config={},
        fields=new_fields, interaction_profile={**profile, "enabled": True},
    )
    records, _w = await extract_records_with_variants(
        base_html=base_html, source_url="https://x",
        project=SimpleNamespace(analysis={"repeated_item_selector": "tbody tr"}),
        spec=spec, max_records=50, fetch_variant_htmls=browser,
    )
    by = {str(r.normalized_data.get("serving_basis")): r.normalized_data
          for r in records}
    assert by["Show per 100 g"]["Serving Size"] == "100 g"
    assert by["Show per 100 g"]["Calories"] == "156"
    assert by["Show per serving"]["Serving Size"] == "1 portion (170 g)"
    assert by["Show per serving"]["Calories"] == "265"


@pytest.mark.asyncio
async def test_merge_generalizes_to_non_serving_axis():
    """The merge is axis-agnostic (NOT hardcoded to serving): a metric/imperial
    column set whose Weight column is toggle-dependent merges with the
    unit_system toggle — static columns for the distinct field (Height) + the
    browser recipe for the toggle-only field (Weight)."""
    base_html = (
        "<main>"
        "<div class='u'><button class='active'>Metric</button>"
        "<button>Imperial</button></div>"
        "<table><tbody>"
        # Weight identical statically (toggle-only); Height distinct (10/20).
        "<tr><td>Item</td><td><p>n/a</p></td><td>10</td>"
        "<td><p>n/a</p></td><td>20</td></tr>"
        "</tbody></table></main>"
    )
    imperial_html = base_html.replace("<p>n/a</p>", "<p>2.2 lb</p>")
    fields = [
        _f("Name", "td:nth-child(1)"),
        _f("Weight (metric)", "td:nth-child(2) p"),
        _f("Height (metric)", "td:nth-child(3)"),
        _f("Weight (imperial)", "td:nth-child(4) p"),
        _f("Height (imperial)", "td:nth-child(5)"),
    ]
    profile, new_fields = detect_interaction_profile(
        base_html, fields, repeated_item_selector="tbody tr"
    )
    assert [g["metadata_key"] for g in profile["groups"]] == ["unit_system"]
    merged = profile["groups"][0]
    assert merged["execution"] == "mixed"
    assert [o["label"] for o in merged["options"]] == ["Metric", "Imperial"]
    assert [f["label"] for f in new_fields] == ["Name", "Weight", "Height"]

    async def browser(recipes):
        return {
            rid: (
                imperial_html
                if any(s.get("value") == "Imperial" for s in recipe)
                else base_html
            )
            for rid, recipe in recipes.items()
        }

    spec = SimpleNamespace(
        mode=ExtractionMode.STRUCTURED, content_config={},
        fields=new_fields, interaction_profile={**profile, "enabled": True},
    )
    records, _w = await extract_records_with_variants(
        base_html=base_html, source_url="https://x",
        project=SimpleNamespace(analysis={"repeated_item_selector": "tbody tr"}),
        spec=spec, max_records=50, fetch_variant_htmls=browser,
    )
    by = {str(r.normalized_data.get("unit_system")): r.normalized_data
          for r in records}
    assert by["Metric"]["Weight"] == "n/a"      # static identical placeholder
    assert by["Metric"]["Height"] == "10"
    assert by["Imperial"]["Weight"] == "2.2 lb"  # from the browser toggle
    assert by["Imperial"]["Height"] == "20"
