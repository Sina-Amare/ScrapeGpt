"""Phase 2 unit tests: static-HTML detection of page-variant controls."""

from __future__ import annotations

from app.services.interaction_detect import (
    detect_interaction_groups,
    detect_interaction_profile,
)


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


def test_detect_interaction_profile_is_disabled_draft():
    html = '<div><button class="active">Metric</button><button>Imperial</button></div>'
    profile = detect_interaction_profile(html)
    assert profile["enabled"] is False
    assert profile["max_variant_combinations"] == 12
    assert len(profile["groups"]) == 1


def test_detect_empty_html():
    assert detect_interaction_groups("") == []
