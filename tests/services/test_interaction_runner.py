"""P2a: the interaction runner cascades camoufox -> Playwright (not camoufox-only)."""

from __future__ import annotations

import pytest

from app.services import fetcher
from app.services.fetcher import FetchError


@pytest.mark.asyncio
async def test_runner_falls_back_to_playwright_when_camoufox_absent(monkeypatch):
    async def camoufox_missing(url, recipes, cookies):
        raise ImportError("no camoufox")

    async def playwright_ok(url, recipes, cookies):
        return {rid: "<html>pw</html>" for rid in recipes}

    monkeypatch.setattr(fetcher, "_apply_interactions_camoufox", camoufox_missing)
    monkeypatch.setattr(fetcher, "_apply_interactions_playwright", playwright_ok)

    out = await fetcher._apply_interactions_async("https://x/", {"v1": []})
    assert out == {"v1": "<html>pw</html>"}


@pytest.mark.asyncio
async def test_runner_prefers_camoufox_when_available(monkeypatch):
    async def camoufox_ok(url, recipes, cookies):
        return {rid: "<html>cf</html>" for rid in recipes}

    async def playwright_unused(url, recipes, cookies):  # pragma: no cover
        raise AssertionError("Playwright should not be called when camoufox works")

    monkeypatch.setattr(fetcher, "_apply_interactions_camoufox", camoufox_ok)
    monkeypatch.setattr(fetcher, "_apply_interactions_playwright", playwright_unused)

    out = await fetcher._apply_interactions_async("https://x/", {"v1": []})
    assert out == {"v1": "<html>cf</html>"}


@pytest.mark.asyncio
async def test_runner_falls_back_on_browser_unavailable_fetcherror(monkeypatch):
    async def camoufox_unavailable(url, recipes, cookies):
        raise FetchError("camoufox not installed", "BROWSER_UNAVAILABLE")

    async def playwright_ok(url, recipes, cookies):
        return {rid: "<html>pw</html>" for rid in recipes}

    monkeypatch.setattr(fetcher, "_apply_interactions_camoufox", camoufox_unavailable)
    monkeypatch.setattr(fetcher, "_apply_interactions_playwright", playwright_ok)

    out = await fetcher._apply_interactions_async("https://x/", {"v1": []})
    assert out == {"v1": "<html>pw</html>"}


@pytest.mark.asyncio
async def test_runner_browser_unavailable_when_neither_backend(monkeypatch):
    async def missing(url, recipes, cookies):
        raise ImportError("absent")

    monkeypatch.setattr(fetcher, "_apply_interactions_camoufox", missing)
    monkeypatch.setattr(fetcher, "_apply_interactions_playwright", missing)

    with pytest.raises(FetchError) as exc:
        await fetcher._apply_interactions_async("https://x/", {"v1": []})
    assert exc.value.error_code == "BROWSER_UNAVAILABLE"
