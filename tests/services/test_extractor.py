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
