"""Phase 2.5 extraction-quality tests (preview + extraction paths)."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.extraction_quality import (
    WARN_DUPLICATE_COLUMN_VALUES,
    WARN_FIELD_LOW_SUCCESS_RATE,
    WARN_NO_RECORDS_EXTRACTED,
    WARN_REQUIRED_FIELD_MISSING,
    compute_extraction_quality,
    compute_preview_quality,
    detect_duplicate_column_warnings,
)


def _record(raw, normalized=None):
    return SimpleNamespace(raw_data=raw, normalized_data=normalized or raw, warnings=[])


def _spec(*names, required=()):
    fields = [
        {"name": n, "label": n, "user_label": n, "selected": True, "required": n in required}
        for n in names
    ]
    return SimpleNamespace(
        fields=fields,
        crawl_scope={"mode": "FULL_SITE", "status": "USER_CONFIRMED"},
    )


# ---- compute_extraction_quality ----


def test_extraction_quality_good_when_all_fields_present():
    rs = [
        _record({"Title": "A", "Price": "10"}),
        _record({"Title": "B", "Price": "20"}),
        _record({"Title": "C", "Price": "30"}),
    ]
    s = compute_extraction_quality(rs, _spec("Title", "Price"))
    assert s["overall"] == "good"
    assert s["field_success_rates"]["Title"] == 1.0
    assert s["field_success_rates"]["Price"] == 1.0
    assert s["missing_field_rates"]["Title"] == 0.0
    assert s["warnings"] == []


def test_extraction_quality_flags_low_success_rate_field():
    rs = [
        _record({"Title": "A", "Seller": "x"}),
        _record({"Title": "B", "Seller": ""}),
        _record({"Title": "C"}),
        _record({"Title": "D"}),
    ]
    spec = SimpleNamespace(
        fields=[{"name": "Title", "selected": True}, {"name": "Seller", "selected": True}],
        crawl_scope={"mode": "PAGINATION", "status": "USER_CONFIRMED"},
    )
    s = compute_extraction_quality(rs, spec)
    assert s["overall"] == "needs_review"
    seller_warn = next(
        w for w in s["warnings"] if w["code"] == WARN_FIELD_LOW_SUCCESS_RATE and w["field"] == "Seller"
    )
    assert 0.0 < seller_warn["success_rate"] < 0.7


def test_extraction_quality_flags_required_field_missing():
    rs = [_record({"Title": "A"}), _record({"Title": "B"})]
    s = compute_extraction_quality(rs, _spec("Title", "Price", required=("Price",)))
    assert any(w["code"] == WARN_REQUIRED_FIELD_MISSING for w in s["warnings"])
    msg = next(w for w in s["warnings"] if w["code"] == WARN_REQUIRED_FIELD_MISSING)["message"]
    assert "Price" in msg


def test_extraction_quality_no_records_emits_warning_and_unknown():
    s = compute_extraction_quality([], _spec("Title"))
    assert s["overall"] == "unknown"
    assert s["field_success_rates"] == {}
    assert any(w["code"] == WARN_NO_RECORDS_EXTRACTED for w in s["warnings"])


def test_extraction_quality_many_pages_failed_warns_but_not_yet_risky():
    rs = [_record({"Title": "A"}), _record({"Title": "B"})]
    s = compute_extraction_quality(rs, _spec("Title"), pages_attempted=10, pages_failed=4)
    assert any(w["code"] == "MANY_PAGES_FAILED" for w in s["warnings"])
    assert s["overall"] != "risky"


def test_extraction_quality_page_failure_above_50_pct_is_risky():
    rs = [_record({"Title": "A"})]
    s = compute_extraction_quality(rs, _spec("Title"), pages_attempted=10, pages_failed=7)
    assert s["overall"] == "risky"


def test_extraction_quality_full_site_low_success_is_risky():
    rs = [
        _record({"Title": "A", "Price": "10"}),
        _record({"Title": "B"}),
        _record({"Title": "C"}),
        _record({"Title": "D"}),
    ]
    spec = SimpleNamespace(
        fields=[{"name": "Title", "selected": True}, {"name": "Price", "selected": True}],
        crawl_scope={"mode": "FULL_SITE", "status": "USER_CONFIRMED"},
    )
    s = compute_extraction_quality(rs, spec)
    assert s["overall"] == "risky"


# ---- compute_preview_quality ----


def test_preview_quality_reports_missing_field_in_preview():
    rs = [_record({"Title": "A"}), _record({"Title": "B"})]
    s = compute_preview_quality(selected_fields=["Title", "Price"], sample_records=rs)
    assert any(w["code"] == "FIELD_MISSING_IN_PREVIEW" for w in s["warnings"])
    msg = next(w for w in s["warnings"] if w["code"] == "FIELD_MISSING_IN_PREVIEW")["message"]
    assert "Price" in msg


def test_preview_quality_reports_low_success_rate():
    rs = [
        _record({"Title": "A", "Price": "10"}),
        _record({"Title": "B"}),
        _record({"Title": "C"}),
    ]
    s = compute_preview_quality(selected_fields=["Title", "Price"], sample_records=rs)
    assert any(w["code"] == WARN_FIELD_LOW_SUCCESS_RATE for w in s["warnings"])


def test_preview_quality_good_with_full_samples():
    rs = [
        _record({"Title": "A", "Price": "10"}),
        _record({"Title": "B", "Price": "20"}),
    ]
    s = compute_preview_quality(selected_fields=["Title", "Price"], sample_records=rs)
    assert s["overall"] == "good"
    assert s["warnings"] == []


# ---- #7: duplicate-column warning ----


def test_duplicate_column_warning_flags_identical_columns():
    rows = [
        {"Calories": "52", "Energy": "52"},
        {"Calories": "89", "Energy": "89"},
    ]
    warns = detect_duplicate_column_warnings(["Calories", "Energy"], rows)
    assert len(warns) == 1
    w = warns[0]
    assert w["code"] == WARN_DUPLICATE_COLUMN_VALUES
    assert sorted(w["fields"]) == ["Calories", "Energy"]


def test_duplicate_column_warning_ignores_differing_columns():
    rows = [
        {"Calories": "52", "Protein": "0.3"},
        {"Calories": "89", "Protein": "1.1"},
    ]
    assert detect_duplicate_column_warnings(["Calories", "Protein"], rows) == []


def test_duplicate_column_warning_ignores_all_empty_columns():
    rows = [{"A": "", "B": None}, {"A": "", "B": ""}]
    # Both empty everywhere -> not a duplicate signal (missing-field warns cover it).
    assert detect_duplicate_column_warnings(["A", "B"], rows) == []


def test_duplicate_column_warning_is_whitespace_insensitive():
    rows = [{"A": " 52 ", "B": "52"}, {"A": "89", "B": " 89"}]
    warns = detect_duplicate_column_warnings(["A", "B"], rows)
    assert len(warns) == 1


def test_duplicate_column_warning_needs_two_fields_and_rows():
    assert detect_duplicate_column_warnings(["A"], [{"A": "x"}]) == []
    assert detect_duplicate_column_warnings(["A", "B"], []) == []
