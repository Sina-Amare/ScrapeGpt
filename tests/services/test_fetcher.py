"""Tests for the HTTP fetcher service."""

import pytest

from app.services.fetcher import FetchError, FetchResult, RenderModeUsed, fetch_url


def _make_html(body: str = "<p>Hello</p>") -> bytes:
    return f"<html><head></head><body>{body}</body></html>".encode("utf-8")


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        content_type: str = "text/html; charset=utf-8",
        body: bytes = b"<html><body><p>Hello world content here for testing.</p></body></html>",
        is_redirect: bool = False,
        location: str = "",
    ):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        if location:
            self.headers["location"] = location
        self.is_redirect = is_redirect
        self._body = body

    async def aread(self) -> bytes:
        return self._body


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, **kwargs):
        return self._response


@pytest.mark.asyncio
async def test_static_fetch_success(monkeypatch):
    resp = _FakeResponse()

    import httpx
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: _FakeClient(resp),
    )
    monkeypatch.setattr(
        "app.services.fetcher.settings",
        type("S", (), {
            "SCRAPE_TIMEOUT": 30, "USER_AGENT": "Test/1.0",
            "MAX_REDIRECTS": 5, "MAX_FETCH_BYTES": 2 * 1024 * 1024,
            "ALLOW_PRIVATE_NETWORK_URLS": True,
        })(),
    )

    result = await fetch_url("http://example.com/", render_mode="STATIC")
    assert isinstance(result, FetchResult)
    assert result.render_mode_used == RenderModeUsed.STATIC
    assert result.status_code == 200
    assert "Hello" in result.html
    assert len(result.content_hash) == 64


@pytest.mark.asyncio
async def test_rejects_non_html_content_type(monkeypatch):
    resp = _FakeResponse(content_type="application/pdf")

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(resp))
    monkeypatch.setattr(
        "app.services.fetcher.settings",
        type("S", (), {
            "SCRAPE_TIMEOUT": 30, "USER_AGENT": "Test/1.0",
            "MAX_REDIRECTS": 5, "MAX_FETCH_BYTES": 2 * 1024 * 1024,
            "ALLOW_PRIVATE_NETWORK_URLS": True,
        })(),
    )

    with pytest.raises(FetchError) as exc_info:
        await fetch_url("http://example.com/doc.pdf", render_mode="STATIC")
    assert exc_info.value.error_code == "UNSUPPORTED_CONTENT_TYPE"


@pytest.mark.asyncio
async def test_enforces_max_fetch_bytes(monkeypatch):
    big_body = b"A" * 10_000

    resp = _FakeResponse(body=big_body)

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(resp))
    monkeypatch.setattr(
        "app.services.fetcher.settings",
        type("S", (), {
            "SCRAPE_TIMEOUT": 30, "USER_AGENT": "Test/1.0",
            "MAX_REDIRECTS": 5, "MAX_FETCH_BYTES": 100,
            "ALLOW_PRIVATE_NETWORK_URLS": True,
        })(),
    )

    result = await fetch_url("http://example.com/", render_mode="STATIC")
    # Body truncated at MAX_FETCH_BYTES
    assert len(result.html) <= 100


@pytest.mark.asyncio
async def test_browser_mode_unavailable_raises_fetch_error():
    import sys
    # Remove playwright from sys.modules if present to simulate unavailability
    playwright_mod = sys.modules.pop("playwright", None)
    playwright_async = sys.modules.pop("playwright.async_api", None)

    try:
        with pytest.raises(FetchError) as exc_info:
            await fetch_url("http://example.com/", render_mode="BROWSER")
        assert exc_info.value.error_code == "BROWSER_UNAVAILABLE"
    finally:
        if playwright_mod is not None:
            sys.modules["playwright"] = playwright_mod
        if playwright_async is not None:
            sys.modules["playwright.async_api"] = playwright_async
