"""Tests for deterministic extraction, focused on the table-structure fallback
that lets weak/wrong AI selectors still extract."""

from types import SimpleNamespace

from app.models.job import ExtractionMode
from app.services.extractor import extract_records_from_html


def _spec(fields):
    return SimpleNamespace(
        fields=fields,
        mode=ExtractionMode.STRUCTURED,
        content_config={},
    )


def _project(analysis=None):
    return SimpleNamespace(analysis=analysis or {})


_TABLE_HTML = """
<html><body>
  <table class="totally-unexpected-class">
    <thead><tr><th>Food</th><th>Serving</th><th>Calories</th><th>kJ</th></tr></thead>
    <tbody>
      <tr><td>Beef Filet</td><td>100 g</td><td>143</td><td>598</td></tr>
      <tr><td>Veal Cutlet</td><td>100 g</td><td>215</td><td>900</td></tr>
    </tbody>
  </table>
</body></html>
"""


def test_table_fallback_extracts_when_selectors_miss():
    """AI selectors match nothing -> we still read the table by header text."""
    fields = [
        {"selected": True, "label": "Food Name", "type": "string", "selector": ".nope-food"},
        {"selected": True, "label": "Serving Size", "type": "string", "selector": ".nope-serv"},
        {"selected": True, "label": "Calories (kcal)", "type": "number", "selector": ".nope-cal"},
        {"selected": True, "label": "Kilojoules (kJ)", "type": "number", "selector": ".nope-kj"},
    ]
    records = extract_records_from_html(
        _TABLE_HTML, source_url="https://x.test/", project=_project(), spec=_spec(fields)
    )
    assert len(records) == 2
    first = records[0].normalized_data
    assert first["Food Name"] == "Beef Filet"
    assert first["Serving Size"] == "100 g"
    assert first["Calories (kcal)"] == 143  # coerced to number by header match
    assert first["Kilojoules (kJ)"] == 598
    second = records[1].normalized_data
    assert second["Food Name"] == "Veal Cutlet"
    assert second["Calories (kcal)"] == 215


def test_relaxed_selectors_drops_only_bare_text_wrappers():
    from app.services.extractor import relaxed_selectors

    # Trailing bare wrapper tag is dropped to read the parent cell text.
    assert relaxed_selectors("td:nth-child(3) p") == [
        "td:nth-child(3) p",
        "td:nth-child(3)",
    ]
    # Single token: nothing to relax.
    assert relaxed_selectors("a") == ["a"]
    # A trailing link/value element is NEVER dropped (would change the data).
    assert relaxed_selectors("p.title.is-5 a") == ["p.title.is-5 a"]
    # Stops at a non-bare token: ".x" keeps the selector from collapsing to "div".
    assert relaxed_selectors("div .x span") == ["div .x span", "div .x"]
    # Chained bare wrappers all relax.
    assert relaxed_selectors("td p span") == ["td p span", "td p", "td"]


def test_per_field_selector_relaxation_recovers_missing_descendant():
    """A field whose selector over-specifies a descendant absent in some cells
    (e.g. 'td:nth-child(3) p' where the value is direct <td> text, as on
    calories.info) is recovered by relaxing to the cell — not left empty while
    sibling fields extract fine (which would suppress the table fallback)."""
    html = """
    <html><body><table><tbody>
      <tr><td><p>Beef</p></td><td><p>100 g</p></td><td>156 Cal</td></tr>
      <tr><td><p>Pork</p></td><td><p>100 g</p></td><td>242 Cal</td></tr>
    </tbody></table></body></html>
    """
    fields = [
        {"selected": True, "label": "Food", "type": "string", "selector": "td:nth-child(1) p"},
        {"selected": True, "label": "Serving", "type": "string", "selector": "td:nth-child(2) p"},
        {"selected": True, "label": "Calories", "type": "number", "selector": "td:nth-child(3) p"},
    ]
    records = extract_records_from_html(
        html,
        source_url="https://x.test/",
        project=_project({"repeated_item_selector": "tbody tr"}),
        spec=_spec(fields),
    )
    assert len(records) == 2
    assert records[0].normalized_data["Food"] == "Beef"
    assert records[0].normalized_data["Serving"] == "100 g"
    # Recovered despite the missing <p> in the calorie cell.
    assert records[0].normalized_data["Calories"] == 156
    assert records[1].normalized_data["Calories"] == 242


def test_table_fallback_positional_without_headers():
    html = """
    <html><body><table>
      <tr><td>Apple</td><td>52</td></tr>
      <tr><td>Banana</td><td>89</td></tr>
    </table></body></html>
    """
    fields = [
        {"selected": True, "label": "Name", "type": "string", "selector": ".x"},
        {"selected": True, "label": "Cal", "type": "number", "selector": ".y"},
    ]
    records = extract_records_from_html(
        html, source_url="https://x.test/", project=_project(), spec=_spec(fields)
    )
    assert len(records) == 2
    assert records[0].normalized_data["Name"] == "Apple"
    assert records[0].normalized_data["Cal"] == 52


def test_no_table_returns_empty():
    html = "<html><body><p>No tabular data here at all.</p></body></html>"
    fields = [{"selected": True, "label": "Name", "type": "string", "selector": ".x"}]
    records = extract_records_from_html(
        html, source_url="https://x.test/", project=_project(), spec=_spec(fields)
    )
    assert records == []


def test_working_selectors_take_precedence_over_table_fallback():
    """If field-index selectors match, we use them — not the table fallback."""
    html = """
    <html><body>
      <span class="title">Real Title A</span>
      <span class="title">Real Title B</span>
      <table><tr><td>ignore</td><td>me</td></tr></table>
    </body></html>
    """
    fields = [{"selected": True, "label": "Title", "type": "string", "selector": ".title"}]
    records = extract_records_from_html(
        html, source_url="https://x.test/", project=_project(), spec=_spec(fields)
    )
    titles = {r.normalized_data["Title"] for r in records}
    assert titles == {"Real Title A", "Real Title B"}


def _rows(html_rows: str, headers: str = "") -> str:
    head = f"<thead><tr>{headers}</tr></thead>" if headers else ""
    return f"<html><body><table>{head}<tbody>{html_rows}</tbody></table></body></html>"


def test_table_exact_header_beats_substring():
    """field 'ID' must map to the exact 'ID' column, not 'Video ID' by substring."""
    html = _rows(
        "<tr><td>V123</td><td>42</td></tr>",
        headers="<th>Video ID</th><th>ID</th>",
    )
    fields = [{"selected": True, "label": "ID", "type": "string", "selector": ".x"}]
    records = extract_records_from_html(
        html, source_url="https://x.test/", project=_project(), spec=_spec(fields)
    )
    assert records[0].normalized_data["ID"] == "42"  # exact 'ID' column, not 'V123'


def test_table_does_not_swap_columns_on_substring():
    """'title' is a substring of 'subtitle' — exact matches must prevent a swap."""
    html = _rows(
        "<tr><td>MyTitle</td><td>MySub</td></tr>",
        headers="<th>Title</th><th>Subtitle</th>",
    )
    fields = [
        {"selected": True, "label": "Subtitle", "type": "string", "selector": ".a"},
        {"selected": True, "label": "Title", "type": "string", "selector": ".b"},
    ]
    records = extract_records_from_html(
        html, source_url="https://x.test/", project=_project(), spec=_spec(fields)
    )
    rec = records[0].normalized_data
    assert rec["Title"] == "MyTitle"
    assert rec["Subtitle"] == "MySub"


def test_table_alias_maps_kcal_column_to_calories_field():
    html = _rows(
        "<tr><td>Beef</td><td>143</td></tr>",
        headers="<th>Food</th><th>kcal</th>",
    )
    fields = [
        {"selected": True, "label": "Food", "type": "string", "selector": ".a"},
        {"selected": True, "label": "Calories", "type": "number", "selector": ".b"},
    ]
    records = extract_records_from_html(
        html, source_url="https://x.test/", project=_project(), spec=_spec(fields)
    )
    assert records[0].normalized_data["Calories"] == 143


def test_table_short_header_does_not_falsely_match_long_field():
    """Short header 'cal' is a substring of 'physical' but must NOT confidently match it."""
    html = _rows(
        "<tr><td>Walking</td><td>5</td></tr>",
        headers="<th>Physical Activity</th><th>cal</th>",
    )
    fields = [
        {"selected": True, "label": "Physical Activity", "type": "string", "selector": ".a"},
    ]
    records = extract_records_from_html(
        html, source_url="https://x.test/", project=_project(), spec=_spec(fields)
    )
    # Only one field: it should map to the first/best column ('Physical Activity'),
    # not be dragged to the 'cal' column by a loose substring.
    assert records[0].normalized_data["Physical Activity"] == "Walking"


# --- CONTENT mode hardening -------------------------------------------------

import pytest  # noqa: E402

_LONG = (
    "Beef is a rich source of protein and iron and is eaten across many cuisines "
    "worldwide, commonly grilled, roasted, or stewed. A 100 gram serving has "
    "around 250 calories depending on the cut and preparation method used."
)


def _content_spec(primary_selector=None, fields=None):
    cfg = {"primary_selector": primary_selector} if primary_selector else {}
    return SimpleNamespace(
        fields=fields or [], mode=ExtractionMode.CONTENT, content_config=cfg,
    )


def test_content_strips_chrome_and_picks_main():
    html = (
        "<html><body>"
        "<nav>Home Products About Contact LOGIN SIGNUP</nav>"
        "<header>Big Site Title And Menu Bar</header>"
        f"<main><h1>Beef Nutrition</h1><p>{_LONG}</p></main>"
        "<footer>Copyright 2026 All rights reserved Privacy Terms Cookies</footer>"
        "</body></html>"
    )
    records = extract_records_from_html(
        html, source_url="https://e.com/x", project=_project(),
        spec=_content_spec(), max_records=10,
    )
    assert len(records) == 1
    content = records[0].normalized_data["content"]
    assert "Beef Nutrition" in content and "250 calories" in content
    assert "Home Products" not in content   # nav chrome stripped
    assert "Copyright" not in content       # footer chrome stripped


def test_content_emits_markdown_headings_and_paragraphs():
    html = (
        "<html><body><article>"
        "<h1>Title</h1><p>First paragraph.</p><p>Second paragraph.</p>"
        "</article></body></html>"
    )
    records = extract_records_from_html(
        html, source_url="https://e.com/x", project=_project(),
        spec=_content_spec(), max_records=10,
    )
    content = records[0].normalized_data["content"]
    # CONTENT mode now emits Markdown: an ATX heading + blank-line-separated paras.
    assert "# Title" in content
    assert "First paragraph." in content
    assert "Second paragraph." in content


def test_content_markdown_preserves_code_blocks_and_strips_pilcrows():
    """Documentation pages are the headline case: syntax-highlighted code wrapped
    in per-token <span>s must come back as ONE fenced block (not shredded one
    token per line), the heading-anchor pilcrow must be gone, and the code
    language detected from the ``language-*`` class."""
    html = (
        "<html><body><article>"
        '<h1>Guide<a class="headerlink" href="#g" title="Permanent link">¶</a></h1>'
        f"<p>{_LONG}</p>"
        '<div class="language-python highlight"><pre><span></span><code>'
        '<span class="kn">from</span> <span class="nn">fastapi</span> '
        '<span class="kn">import</span> <span class="n">FastAPI</span>\n\n'
        '<span class="n">app</span> <span class="o">=</span> '
        '<span class="n">FastAPI</span><span class="p">()</span>'
        "</code></pre></div>"
        "</article></body></html>"
    )
    records = extract_records_from_html(
        html, source_url="https://e.com/x", project=_project(),
        spec=_content_spec(), max_records=10,
    )
    content = records[0].normalized_data["content"]
    assert "# Guide" in content
    assert "¶" not in content  # pilcrow heading-anchor stripped
    assert "```python" in content  # fenced block + language detected
    assert "from fastapi import FastAPI" in content  # NOT shredded per token
    assert "app = FastAPI()" in content


def test_content_markdown_keeps_links_and_lists():
    html = (
        "<html><body><article>"
        f"<p>{_LONG}</p>"
        "<ul><li>Alpha item one here</li><li>Beta item two here</li></ul>"
        '<p>See <a href="https://example.com/docs">the docs</a> for details.</p>'
        "</article></body></html>"
    )
    records = extract_records_from_html(
        html, source_url="https://e.com/x", project=_project(),
        spec=_content_spec(), max_records=10,
    )
    content = records[0].normalized_data["content"]
    assert "- Alpha item one here" in content
    assert "[the docs](https://example.com/docs)" in content


def test_content_ignores_tiny_primary_selector_and_uses_density():
    html = (
        "<html><body>"
        '<div class="tag">x</div>'
        f"<main><p>{_LONG}</p></main>"
        "</body></html>"
    )
    records = extract_records_from_html(
        html, source_url="https://e.com/x", project=_project(),
        spec=_content_spec(primary_selector="div.tag"), max_records=10,
    )
    content = records[0].normalized_data["content"]
    # The tiny primary-selector match is ignored in favor of the dense <main>.
    assert "250 calories" in content


def test_content_empty_returns_no_records():
    html = (
        "<html><body><nav>Menu Home About</nav>"
        "<script>var x = 1;</script></body></html>"
    )
    records = extract_records_from_html(
        html, source_url="https://e.com/x", project=_project(),
        spec=_content_spec(), max_records=10,
    )
    assert records == []


@pytest.mark.asyncio
async def test_content_mode_skips_variant_fanout():
    """Even with an enabled interaction profile, CONTENT mode yields ONE content
    record (no per-variant fan-out / browser requirement)."""
    from app.services.interaction_extraction import extract_records_with_variants

    html = f"<html><body><main><p>{_LONG}</p></main></body></html>"
    profile = {
        "enabled": True,
        "max_variant_combinations": 12,
        "groups": [{
            "metadata_key": "unit_system",
            "execution": "interactive",
            "options": [
                {"id": "metric", "label": "Metric", "selected": True, "recipe": []},
                {"id": "imperial", "label": "Imperial", "selected": True,
                 "recipe": [{"action": "click", "by": "text", "value": "Imperial"}]},
            ],
        }],
    }
    spec = SimpleNamespace(
        mode=ExtractionMode.CONTENT, content_config={}, fields=[],
        interaction_profile=profile,
    )
    records, warnings = await extract_records_with_variants(
        base_html=html, source_url="https://e.com/x",
        project=SimpleNamespace(analysis={}), spec=spec, max_records=10,
        fetch_variant_htmls=None,
    )
    assert len(records) == 1
    assert "serving_basis" not in records[0].normalized_data
    assert "unit_system" not in records[0].normalized_data
