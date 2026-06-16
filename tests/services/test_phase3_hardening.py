"""Phase 3 hardening tests: provider 429 backoff, metrics, CPU offload."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from app.services import provider_service
from app.services.provider_service import (
    ProviderCallError,
    _completion,
    _is_rate_limit_error,
    _retry_after_seconds,
)


class _RateLimit(Exception):
    status_code = 429


def _provider():
    return SimpleNamespace(model="gpt", provider="openai")


def _ok_response(text="hi"):
    return {"choices": [{"message": {"content": text}}]}


@pytest.fixture(autouse=True)
def _instant_sleep(monkeypatch):
    async def _nosleep(_seconds):
        return None

    monkeypatch.setattr(provider_service.asyncio, "sleep", _nosleep)
    monkeypatch.setattr(provider_service.random, "uniform", lambda a, b: 0.0)


def _fake_litellm(seq):
    """A fake litellm module whose acompletion yields from `seq` (raises if the
    item is an Exception, else returns it)."""
    calls = {"n": 0}

    async def acompletion(**kwargs):
        item = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    return SimpleNamespace(acompletion=acompletion), calls


# --- rate-limit detection helpers -------------------------------------------


def test_is_rate_limit_error_detects_status_and_text():
    assert _is_rate_limit_error(_RateLimit("nope")) is True
    assert _is_rate_limit_error(Exception("HTTP 429 Too Many Requests")) is True
    assert _is_rate_limit_error(Exception("rate limit exceeded")) is True
    assert _is_rate_limit_error(Exception("connection reset")) is False


def test_retry_after_parsing():
    exc = Exception("x")
    exc.response = SimpleNamespace(headers={"retry-after": "3"})
    assert _retry_after_seconds(exc) == 3.0
    assert _retry_after_seconds(Exception("no header")) is None


# --- _completion backoff -----------------------------------------------------


@pytest.mark.asyncio
async def test_completion_retries_then_succeeds(monkeypatch):
    fake, calls = _fake_litellm([_RateLimit("429"), _RateLimit("429"), _ok_response("done")])
    monkeypatch.setitem(sys.modules, "litellm", fake)
    out = await _completion(_provider(), "key", [{"role": "user", "content": "hi"}])
    assert out == "done"
    assert calls["n"] == 3  # two 429s + one success


@pytest.mark.asyncio
async def test_completion_exhausts_and_raises_rate_limited(monkeypatch):
    fake, _ = _fake_litellm([_RateLimit("429")])  # always 429
    monkeypatch.setitem(sys.modules, "litellm", fake)
    with pytest.raises(ProviderCallError) as exc:
        await _completion(_provider(), "key", [{"role": "user", "content": "hi"}])
    assert exc.value.rate_limited is True


@pytest.mark.asyncio
async def test_completion_non_rate_limit_does_not_retry(monkeypatch):
    fake, calls = _fake_litellm([Exception("auth failed"), _ok_response()])
    monkeypatch.setitem(sys.modules, "litellm", fake)
    with pytest.raises(ProviderCallError) as exc:
        await _completion(_provider(), "key", [{"role": "user", "content": "hi"}])
    assert exc.value.rate_limited is False
    assert calls["n"] == 1  # no retry on non-rate-limit errors


@pytest.mark.asyncio
async def test_rate_limit_records_metric(monkeypatch):
    counter = {"n": 0}
    monkeypatch.setattr(
        provider_service, "record_rate_limit_retry", lambda: counter.__setitem__("n", counter["n"] + 1)
    )
    fake, _ = _fake_litellm([_RateLimit("429"), _ok_response("ok")])
    monkeypatch.setitem(sys.modules, "litellm", fake)
    await _completion(_provider(), "key", [{"role": "user", "content": "hi"}])
    assert counter["n"] == 1


# --- metrics module ----------------------------------------------------------


def test_metrics_helpers_never_raise_and_render():
    from app.core import metrics

    metrics.record_run_state("completed")
    metrics.record_page_outcome("extracted")
    metrics.record_rate_limit_retry()
    metrics.observe_run_duration(2.0)
    out = metrics.render_latest()
    assert isinstance(out, bytes)


# --- CPU offload -------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_offloads_parsing_to_thread(monkeypatch):
    """extract_records_with_variants must run the (CPU-bound) extractor via
    asyncio.to_thread so a big page can't block the event loop."""
    from app.services import interaction_extraction
    from app.models.job import ExtractionMode

    used = {"to_thread": 0}
    real_to_thread = asyncio.to_thread

    async def tracking_to_thread(func, *args, **kwargs):
        used["to_thread"] += 1
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(interaction_extraction.asyncio, "to_thread", tracking_to_thread)

    spec = SimpleNamespace(
        mode=ExtractionMode.STRUCTURED, content_config={},
        fields=[{"name": "T", "selector": "td", "type": "string", "selected": True}],
        interaction_profile={},  # disabled -> single passthrough extraction
    )
    project = SimpleNamespace(analysis={})
    html = "<table><tr><td>a</td></tr></table>"
    records, _ = await interaction_extraction.extract_records_with_variants(
        base_html=html, source_url="u", project=project, spec=spec, max_records=10,
    )
    assert used["to_thread"] >= 1
