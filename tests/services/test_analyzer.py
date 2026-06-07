"""Tests for the analyzer service."""

import pytest

from app.models.job import ExtractionMode
from app.schemas.job import ContentAnalysis, StructuredAnalysis


# ---------------------------------------------------------------------------
# Schema validation — ensure the locked schemas accept valid data
# ---------------------------------------------------------------------------


def _valid_structured() -> dict:
    return {
        "page_type": "listing",
        "repeated_item_selector": ".product-card",
        "candidate_fields": [
            {
                "name": "title",
                "label": "Title",
                "selector": ".product-card h3",
                "data_type": "string",
                "required": True,
                "confidence": 0.95,
                "sample_values": ["Widget A"],
            }
        ],
        "detail_link_selector": ".product-card a",
        "pagination_selector": ".next",
        "estimated_pages": 10,
        "warnings": [],
        "confidence": 0.88,
    }


def _valid_content() -> dict:
    return {
        "content_type": "article",
        "primary_content_selector": ".article-body",
        "estimated_pages": 1,
        "avg_content_length": 3000,
        "recommended_chunking": "paragraph",
        "metadata_fields": [
            {
                "name": "title",
                "label": "Title",
                "selector": "h1",
                "confidence": 0.99,
                "sample_values": ["My Article"],
            }
        ],
        "warnings": [],
        "confidence": 0.92,
    }


def test_structured_analysis_schema_accepts_valid_data():
    obj = StructuredAnalysis.model_validate(_valid_structured())
    assert obj.confidence == 0.88
    assert obj.page_type == "listing"
    assert len(obj.candidate_fields) == 1
    assert obj.candidate_fields[0].name == "title"


def test_structured_analysis_requires_confidence():
    data = _valid_structured()
    del data["confidence"]
    with pytest.raises(Exception):
        StructuredAnalysis.model_validate(data)


def test_structured_analysis_requires_candidate_fields():
    data = _valid_structured()
    del data["candidate_fields"]
    with pytest.raises(Exception):
        StructuredAnalysis.model_validate(data)


def test_content_analysis_schema_accepts_valid_data():
    obj = ContentAnalysis.model_validate(_valid_content())
    assert obj.confidence == 0.92
    assert obj.content_type == "article"
    assert len(obj.metadata_fields) == 1


def test_content_analysis_requires_primary_content_selector():
    data = _valid_content()
    del data["primary_content_selector"]
    with pytest.raises(Exception):
        ContentAnalysis.model_validate(data)


def test_candidate_field_requires_all_fields():
    data = _valid_structured()
    # Remove a required field from the first candidate
    del data["candidate_fields"][0]["confidence"]
    with pytest.raises(Exception):
        StructuredAnalysis.model_validate(data)


# ---------------------------------------------------------------------------
# Cache lookup — verify cache key components
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_page_uses_cache_on_hit(monkeypatch):
    """Cache hit must avoid calling the provider."""
    from app.services import analyzer

    cached_result = _valid_structured()
    call_count = 0

    async def fake_lookup(db, content_hash, extraction_mode, provider, model):
        return cached_result

    async def fake_call(*args, **kwargs):
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr(analyzer, "_lookup_cache", fake_lookup)
    monkeypatch.setattr(analyzer, "call_json_model", fake_call)

    # Fake provider config
    class FakeProvider:
        provider = "openai"
        model = "gpt-4o"
        api_key_encrypted = "x"

    result = await analyzer.analyze_page(
        provider_config=FakeProvider(),
        dom_summary="Title: Example",
        extraction_mode=ExtractionMode.STRUCTURED,
        content_hash="abc123",
    )

    assert result == cached_result
    assert call_count == 0  # Provider was NOT called


@pytest.mark.asyncio
async def test_analyze_page_calls_provider_on_cache_miss(monkeypatch):
    """Cache miss must call the provider and store the result."""
    from app.services import analyzer
    from app.schemas.job import StructuredAnalysis

    valid = _valid_structured()
    stored = {}
    provider_called = []

    async def fake_lookup(db, *args):
        return None  # cache miss

    async def fake_store(db, content_hash, extraction_mode, provider, model, result, normalized_url):
        stored["result"] = result

    class FakeJSONResult:
        data = StructuredAnalysis.model_validate(valid)

    async def fake_call_json_model(provider_config, messages, schema, max_retries=3):
        provider_called.append(True)
        return FakeJSONResult()

    monkeypatch.setattr(analyzer, "_lookup_cache", fake_lookup)
    monkeypatch.setattr(analyzer, "_store_cache", fake_store)
    monkeypatch.setattr(analyzer, "call_json_model", fake_call_json_model)

    class FakeProvider:
        provider = "openai"
        model = "gpt-4o"
        api_key_encrypted = "x"

    result = await analyzer.analyze_page(
        provider_config=FakeProvider(),
        dom_summary="Title: Example",
        extraction_mode=ExtractionMode.STRUCTURED,
        content_hash="abc456",
    )

    assert result["page_type"] == "listing"
    assert len(provider_called) == 1
    assert stored["result"]["page_type"] == "listing"
