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


_FAKE_SETTINGS = type("S", (), {
    "SCRAPE_TIMEOUT": 30,
    "USER_AGENT": "Test/1.0",
    "MAX_REDIRECTS": 5,
    "MAX_FETCH_BYTES": 2 * 1024 * 1024,
    "ALLOW_PRIVATE_NETWORK_URLS": True,
})()


@pytest.mark.asyncio
async def test_static_fetch_success(monkeypatch):
    resp = _FakeResponse()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(resp))
    monkeypatch.setattr("app.services.fetcher.settings", _FAKE_SETTINGS)

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
    monkeypatch.setattr("app.services.fetcher.settings", _FAKE_SETTINGS)

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
    assert len(result.html) <= 100


@pytest.mark.asyncio
async def test_static_fetch_truncation_metadata(monkeypatch):
    """Truncated static fetch must expose original_bytes, analyzed_bytes, truncated."""
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
    meta = result.fetch_metadata
    assert meta["truncated"] is True
    assert meta["original_bytes"] == 10_000
    assert meta["analyzed_bytes"] == 100
    assert len(result.html.encode("utf-8")) <= 100


@pytest.mark.asyncio
async def test_static_fetch_no_truncation_metadata(monkeypatch):
    """Non-truncated fetch must have truncated=False with matching byte counts."""
    body = b"X" * 50

    resp = _FakeResponse(body=body)

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(resp))
    monkeypatch.setattr(
        "app.services.fetcher.settings",
        type("S", (), {
            "SCRAPE_TIMEOUT": 30, "USER_AGENT": "Test/1.0",
            "MAX_REDIRECTS": 5, "MAX_FETCH_BYTES": 1000,
            "ALLOW_PRIVATE_NETWORK_URLS": True,
        })(),
    )

    result = await fetch_url("http://example.com/", render_mode="STATIC")
    meta = result.fetch_metadata
    assert meta["truncated"] is False
    assert meta["original_bytes"] == 50
    assert meta["analyzed_bytes"] == 50


@pytest.mark.asyncio
async def test_browser_mode_unavailable_raises_fetch_error():
    import sys
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


@pytest.mark.asyncio
async def test_browser_fetch_enforces_max_fetch_bytes(monkeypatch):
    """Browser page.content() must be capped at MAX_FETCH_BYTES."""
    try:
        import playwright.async_api as pw_api
    except ImportError:
        pytest.skip("playwright not installed")

    from unittest.mock import AsyncMock, MagicMock

    big_html = "X" * 100_000
    MAX = 200

    fake_page = MagicMock()
    fake_page.url = "https://example.com"
    fake_page.goto = AsyncMock(return_value=MagicMock(status=200))
    fake_page.content = AsyncMock(return_value=big_html)

    fake_context = MagicMock()
    fake_context.route = AsyncMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.close = AsyncMock()

    fake_browser = MagicMock()
    fake_browser.new_context = AsyncMock(return_value=fake_context)
    fake_browser.close = AsyncMock()

    fake_chromium = MagicMock()
    fake_chromium.launch = AsyncMock(return_value=fake_browser)

    fake_pw = MagicMock()
    fake_pw.chromium = fake_chromium
    fake_pw.__aenter__ = AsyncMock(return_value=fake_pw)
    fake_pw.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(pw_api, "async_playwright", lambda: fake_pw)
    monkeypatch.setattr(
        "app.services.fetcher.settings",
        type("S", (), {
            "SCRAPE_TIMEOUT": 30, "USER_AGENT": "Test/1.0",
            "MAX_FETCH_BYTES": MAX, "ALLOW_PRIVATE_NETWORK_URLS": True,
        })(),
    )

    from app.services.fetcher import _browser_fetch
    result = await _browser_fetch("https://example.com")
    assert len(result.html.encode("utf-8")) <= MAX
    assert result.fetch_metadata["truncated"] is True
    assert result.fetch_metadata["original_bytes"] > MAX
    assert result.fetch_metadata["analyzed_bytes"] == MAX


@pytest.mark.asyncio
async def test_browser_fetch_blocks_private_ip_via_route(monkeypatch):
    """Route interception must raise FetchError(BROWSER_URL_BLOCKED) for private IPs."""
    try:
        import playwright.async_api as pw_api
    except ImportError:
        pytest.skip("playwright not installed")

    from unittest.mock import AsyncMock, MagicMock

    captured_handler: list = []

    async def capture_route(pattern, handler):
        captured_handler.append(handler)

    fake_page = MagicMock()
    fake_page.url = "https://example.com"
    fake_page.content = AsyncMock(return_value="<html></html>")

    async def mock_goto(url, **kwargs):
        # Simulate Playwright calling the route handler for a request to a private IP
        if captured_handler:
            fake_route = MagicMock()
            fake_route.request = MagicMock()
            fake_route.request.url = "http://192.168.1.1/steal"
            fake_route.abort = AsyncMock()
            fake_route.continue_ = AsyncMock()
            await captured_handler[0](fake_route)
        return MagicMock(status=200)

    fake_page.goto = mock_goto

    fake_context = MagicMock()
    fake_context.route = capture_route
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.close = AsyncMock()

    fake_browser = MagicMock()
    fake_browser.new_context = AsyncMock(return_value=fake_context)
    fake_browser.close = AsyncMock()

    fake_chromium = MagicMock()
    fake_chromium.launch = AsyncMock(return_value=fake_browser)

    fake_pw = MagicMock()
    fake_pw.chromium = fake_chromium
    fake_pw.__aenter__ = AsyncMock(return_value=fake_pw)
    fake_pw.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(pw_api, "async_playwright", lambda: fake_pw)
    monkeypatch.setattr(
        "app.services.fetcher.settings",
        type("S", (), {
            "SCRAPE_TIMEOUT": 30, "USER_AGENT": "Test/1.0",
            "MAX_FETCH_BYTES": 2 * 1024 * 1024, "ALLOW_PRIVATE_NETWORK_URLS": False,
        })(),
    )
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": False})(),
    )

    from app.services.fetcher import FetchError as FE, _browser_fetch
    with pytest.raises(FE) as exc_info:
        await _browser_fetch("https://example.com")
    assert exc_info.value.error_code == "BROWSER_URL_BLOCKED"
