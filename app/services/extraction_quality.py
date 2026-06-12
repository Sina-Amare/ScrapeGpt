"""Extraction-quality computation (Workstream E, behavior layer).

Pure functions that turn a list of ``ExtractedRecord`` rows plus a spec
into a quality summary. No DB, no LLM, no HTTP.

The quality shape is intentionally small in v1: per-field success rate
and missing rate, plus a coarse ``overall`` field that the frontend can
render without writing its own thresholds. The reason codes are
stable strings the frontend can localise.

This module does not attempt selector repair, drift detection, or
schema healing. Those are explicitly out of Phase 2.5 scope.
"""

from __future__ import annotations

from typing import Any, Iterable


# Warning reason codes the plan calls for. Centralised so the frontend
# can map them to user-facing copy without stringly-typed duplication.
WARN_FIELD_MISSING_IN_PREVIEW = "FIELD_MISSING_IN_PREVIEW"
WARN_FIELD_LOW_SUCCESS_RATE = "FIELD_LOW_SUCCESS_RATE"
WARN_REQUIRED_FIELD_MISSING = "REQUIRED_FIELD_MISSING"
WARN_NO_RECORDS_EXTRACTED = "NO_RECORDS_EXTRACTED"
WARN_MANY_PAGES_FAILED = "MANY_PAGES_FAILED"
WARN_SCOPE_NOT_CONFIRMED = "SCOPE_NOT_CONFIRMED"
WARN_FULL_SITE_SCOPE_WARNING = "FULL_SITE_SCOPE_WARNING"
WARN_FRONTIER_HAS_MANY_EXCLUSIONS = "FRONTIER_HAS_MANY_EXCLUSIONS"


_DEFAULT_FIELD_SUCCESS_THRESHOLD = 0.7
_DEFAULT_PAGE_FAILURE_THRESHOLD = 0.25
_DEFAULT_FULL_SITE_RISKY_SUCCESS = 0.8


def compute_extraction_quality(
    records: Iterable[Any],
    spec: Any | None,
    *,
    field_success_threshold: float = _DEFAULT_FIELD_SUCCESS_THRESHOLD,
    page_failure_ratio: float | None = None,
    pages_attempted: int | None = None,
    pages_failed: int | None = None,
) -> dict[str, Any]:
    """Compute a small quality summary from records and a spec.

    ``records`` is any iterable of objects with ``.raw_data`` and
    ``.normalized_data`` and ``.warnings``. ``spec`` is the
    ``ExtractionSpec`` (or ``None``). The function does not write to
    the DB; the caller is responsible for persisting the result.
    """
    records = list(records)
    field_success_rates, missing_field_rates = _per_field_rates(records)
    warnings: list[dict[str, Any]] = []
    selected_fields = _selected_field_names(spec) if spec is not None else []

    # Per-field low success rate warning.
    for field, rate in field_success_rates.items():
        if rate < field_success_threshold:
            warnings.append(
                {
                    "code": WARN_FIELD_LOW_SUCCESS_RATE,
                    "field": field,
                    "success_rate": rate,
                    "message": f"Field '{field}' succeeded on {rate:.0%} of records.",
                }
            )

    # Required-field-missing warning.
    total_records = len(records)
    if total_records > 0:
        for field in selected_fields:
            if field and field not in field_success_rates:
                rate = missing_field_rates.get(field, 1.0)
            else:
                rate = missing_field_rates.get(field, 0.0)
            if rate >= 1.0 and field in selected_fields:
                warnings.append(
                    {
                        "code": WARN_REQUIRED_FIELD_MISSING,
                        "field": field,
                        "message": f"Required field '{field}' was missing on every record.",
                    }
                )

    if total_records == 0:
        warnings.append(
            {
                "code": WARN_NO_RECORDS_EXTRACTED,
                "message": "No records were extracted from any crawled page.",
            }
        )

    if (
        page_failure_ratio is None
        and pages_attempted is not None
        and pages_failed is not None
        and pages_attempted > 0
    ):
        page_failure_ratio = pages_failed / pages_attempted
    if (
        page_failure_ratio is not None
        and page_failure_ratio > _DEFAULT_PAGE_FAILURE_THRESHOLD
    ):
        warnings.append(
            {
                "code": WARN_MANY_PAGES_FAILED,
                "ratio": page_failure_ratio,
                "message": (
                    f"{page_failure_ratio:.0%} of crawled pages failed."
                ),
            }
        )

    overall = _overall_label(
        field_success_rates,
        field_success_threshold=field_success_threshold,
        full_site_risky_success=_DEFAULT_FULL_SITE_RISKY_SUCCESS,
        has_full_site_scope=_is_full_site_scope(spec),
        page_failure_ratio=page_failure_ratio,
    )

    return {
        "overall": overall,
        "field_success_rates": field_success_rates,
        "missing_field_rates": missing_field_rates,
        "warnings": warnings,
    }


def compute_preview_quality(
    selected_fields: list[str] | None,
    sample_records: Iterable[Any],
    *,
    field_success_threshold: float = _DEFAULT_FIELD_SUCCESS_THRESHOLD,
) -> dict[str, Any]:
    """Quality summary for a single-page preview.

    Used by ``project_preview`` and surfaced to the user as
    ``preview_results.quality_summary``. Reuses the same
    overall-label logic as ``compute_extraction_quality``.
    """
    sample_records = list(sample_records or [])
    field_success_rates, missing_field_rates = _per_field_rates(sample_records)
    warnings: list[dict[str, Any]] = []
    for field, rate in field_success_rates.items():
        if rate < field_success_threshold:
            warnings.append(
                {
                    "code": WARN_FIELD_LOW_SUCCESS_RATE,
                    "field": field,
                    "success_rate": rate,
                    "message": f"Field '{field}' was missing on {1 - rate:.0%} of sampled records.",
                }
            )
    for field in selected_fields or []:
        if field and field not in field_success_rates:
            warnings.append(
                {
                    "code": WARN_FIELD_MISSING_IN_PREVIEW,
                    "field": field,
                    "message": f"Field '{field}' had no value in any sample record.",
                }
            )
    overall = _overall_label(
        field_success_rates,
        field_success_threshold=field_success_threshold,
        full_site_risky_success=_DEFAULT_FULL_SITE_RISKY_SUCCESS,
        has_full_site_scope=False,
        page_failure_ratio=None,
    )
    return {
        "overall": overall,
        "field_success_rates": field_success_rates,
        "missing_field_rates": missing_field_rates,
        "warnings": warnings,
    }


# Internal helpers


def _per_field_rates(
    records: list[Any],
) -> tuple[dict[str, float], dict[str, float]]:
    """Return (success rate, missing rate) per field name across records."""
    if not records:
        return {}, {}
    success_counts: dict[str, int] = {}
    missing_counts: dict[str, int] = {}
    field_set: set[str] = set()
    for record in records:
        data = _record_data(record)
        for key in data.keys():
            field_set.add(str(key))
        for field in field_set:
            success_counts.setdefault(field, 0)
            missing_counts.setdefault(field, 0)
        for field in list(field_set):
            value = data.get(field)
            if _is_present_value(value):
                success_counts[field] += 1
            else:
                missing_counts[field] += 1
    total = len(records)
    success_rates = {f: success_counts.get(f, 0) / total for f in field_set}
    missing_rates = {f: missing_counts.get(f, 0) / total for f in field_set}
    return success_rates, missing_rates


def _record_data(record: Any) -> dict[str, Any]:
    """Return the most-useful data dict for a record (normalized > raw)."""
    if record is None:
        return {}
    if hasattr(record, "normalized_data") and record.normalized_data:
        return dict(record.normalized_data)
    if hasattr(record, "raw_data") and record.raw_data:
        return dict(record.raw_data)
    if isinstance(record, dict):
        return dict(record)
    return {}


def _is_present_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _selected_field_names(spec: Any) -> list[str]:
    """Return selected field names from an ExtractionSpec-like object."""
    fields = getattr(spec, "fields", None) or []
    out: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        if not field.get("selected", True):
            continue
        name = field.get("user_label") or field.get("label") or field.get("name")
        if name:
            out.append(str(name))
    return out


def _is_full_site_scope(spec: Any | None) -> bool:
    if spec is None:
        return False
    scope = getattr(spec, "crawl_scope", None)
    if not isinstance(scope, dict):
        return False
    return scope.get("mode") == "FULL_SITE"


def _overall_label(
    field_success_rates: dict[str, float],
    *,
    field_success_threshold: float,
    full_site_risky_success: float,
    has_full_site_scope: bool,
    page_failure_ratio: float | None,
) -> str:
    if not field_success_rates:
        return "unknown"
    if page_failure_ratio is not None and page_failure_ratio > 0.5:
        return "risky"
    if (
        has_full_site_scope
        and min(field_success_rates.values(), default=1.0) < full_site_risky_success
    ):
        return "risky"
    if any(rate < field_success_threshold for rate in field_success_rates.values()):
        return "needs_review"
    return "good"
