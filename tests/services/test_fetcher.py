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
async def test_browser_mode_unavailable_raises_fetch_error(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright.async_api":
            raise ImportError("playwright intentionally unavailable in this test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(FetchError) as exc_info:
        await fetch_url("http://example.com/", render_mode="BROWSER")
    assert exc_info.value.error_code == "BROWSER_UNAVAILABLE"


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
    fake_context.add_init_script = AsyncMock()
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
            # Real Playwright throws when route.abort() is called — simulate that.
            if fake_route.abort.called:
                raise Exception("net::ERR_BLOCKED_BY_CLIENT")
        return MagicMock(status=200)

    fake_page.goto = mock_goto

    fake_context = MagicMock()
    fake_context.route = capture_route
    fake_context.add_init_script = AsyncMock()
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


@pytest.mark.asyncio
async def test_browser_fetch_blank_exception_has_actionable_message(monkeypatch):
    """Blank Playwright exceptions must not persist as 'Browser fetch failed: '."""
    try:
        import playwright.async_api as pw_api
    except ImportError:
        pytest.skip("playwright not installed")

    from unittest.mock import AsyncMock, MagicMock

    fake_page = MagicMock()
    fake_page.goto = AsyncMock(side_effect=Exception())

    fake_context = MagicMock()
    fake_context.route = AsyncMock()
    fake_context.add_init_script = AsyncMock()
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
            "SCRAPE_TIMEOUT": 30,
            "USER_AGENT": "Test/1.0",
            "MAX_FETCH_BYTES": 2 * 1024 * 1024,
            "ALLOW_PRIVATE_NETWORK_URLS": True,
        })(),
    )

    from app.services.fetcher import FetchError as FE, _browser_fetch

    with pytest.raises(FE) as exc_info:
        await _browser_fetch("https://example.com")

    assert exc_info.value.error_code == "FETCH_FAILED"
    assert str(exc_info.value) == "Browser fetch failed: Exception"


@pytest.mark.asyncio
async def test_browser_fetch_uses_threaded_path_when_required(monkeypatch):
    expected = FetchResult(
        html="<html></html>",
        content_hash="0" * 64,
        final_url="https://example.com/",
        render_mode_used=RenderModeUsed.BROWSER,
        status_code=200,
        elapsed_ms=1,
        fetch_metadata={"threaded": True},
    )

    monkeypatch.setattr(
        "app.services.fetcher._should_use_threaded_browser_fetch",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.fetcher._browser_fetch_sync",
        lambda url: expected,
    )

    from app.services.fetcher import _browser_fetch

    result = await _browser_fetch("https://example.com/")
    assert result is expected


# ---------------------------------------------------------------------------
# Cloudflare challenge auto-retry
# ---------------------------------------------------------------------------

# HTML must exceed _is_sparse() threshold (500 stripped chars) so challenge
# detection runs rather than the sparse-content browser fallback taking priority.
_PADDING = b"x" * 600

_CF_CHALLENGE_HTML = (
    b"<html><head><title>Just a moment...</title></head>"
    b"<body><p>Cloudflare</p><script src='/cdn-cgi/challenge-platform/h/g/orchestrate'></script>"
    b"<!--" + _PADDING + b"--></body></html>"
)
_CF_TURNSTILE_HTML = (
    b"<html><body>"
    b"<div class='cf-turnstile' data-sitekey='xxx'></div>"
    b"<script src='https://challenges.cloudflare.com/turnstile/v0/api.js'></script>"
    b"<!--" + _PADDING + b"--></body></html>"
)
_REAL_PAGE_HTML = (
    b"<html><body><p>Actual page content here.</p></body></html>"
)


@pytest.mark.asyncio
async def test_cloudflare_js_challenge_triggers_browser_retry(monkeypatch):
    """AUTO mode must retry with browser when static fetch returns a CF JS challenge."""
    import httpx
    from unittest.mock import AsyncMock

    from app.services.fetcher import FetchResult, RenderModeUsed

    browser_result = FetchResult(
        html=_REAL_PAGE_HTML.decode(),
        content_hash="abc" * 21 + "d",
        final_url="https://example.com/",
        render_mode_used=RenderModeUsed.BROWSER,
        status_code=200,
        elapsed_ms=500,
        fetch_metadata={},
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse(body=_CF_CHALLENGE_HTML))
    )
    monkeypatch.setattr("app.services.fetcher.settings", _FAKE_SETTINGS)
    monkeypatch.setattr(
        "app.services.fetcher._browser_fetch",
        AsyncMock(return_value=browser_result),
    )

    result = await fetch_url("https://example.com/", render_mode="AUTO")

    assert result.render_mode_used == RenderModeUsed.BROWSER
    assert "Actual page content" in result.html


@pytest.mark.asyncio
async def test_cloudflare_challenge_not_retried_in_static_mode(monkeypatch):
    """STATIC mode must never retry with browser, even on a CF challenge."""
    import httpx
    from unittest.mock import AsyncMock

    browser_called = []
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse(body=_CF_CHALLENGE_HTML))
    )
    monkeypatch.setattr("app.services.fetcher.settings", _FAKE_SETTINGS)
    monkeypatch.setattr(
        "app.services.fetcher._browser_fetch",
        AsyncMock(side_effect=lambda url: browser_called.append(url)),
    )

    result = await fetch_url("https://example.com/", render_mode="STATIC")

    assert result.render_mode_used == RenderModeUsed.STATIC
    assert browser_called == []


@pytest.mark.asyncio
async def test_cloudflare_turnstile_not_retried_with_browser(monkeypatch):
    """Turnstile (interactive CAPTCHA) must NOT trigger browser retry."""
    import httpx
    from unittest.mock import AsyncMock

    browser_called = []
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse(body=_CF_TURNSTILE_HTML))
    )
    monkeypatch.setattr("app.services.fetcher.settings", _FAKE_SETTINGS)
    monkeypatch.setattr(
        "app.services.fetcher._browser_fetch",
        AsyncMock(side_effect=lambda url: browser_called.append(url)),
    )

    result = await fetch_url("https://example.com/", render_mode="AUTO")

    assert result.render_mode_used == RenderModeUsed.STATIC
    assert browser_called == []


@pytest.mark.asyncio
async def test_cloudflare_challenge_browser_unavailable_falls_back_to_static(monkeypatch):
    """If browser is unavailable, fall back to the static CF challenge page and record it."""
    import httpx
    from unittest.mock import AsyncMock

    from app.services.fetcher import FetchError

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse(body=_CF_CHALLENGE_HTML))
    )
    monkeypatch.setattr("app.services.fetcher.settings", _FAKE_SETTINGS)
    monkeypatch.setattr(
        "app.services.fetcher._browser_fetch",
        AsyncMock(side_effect=FetchError("no playwright", "BROWSER_UNAVAILABLE")),
    )

    result = await fetch_url("https://example.com/", render_mode="AUTO")

    assert result.render_mode_used == RenderModeUsed.STATIC
    assert result.fetch_metadata["browser_fallback_skipped"] is True
    assert result.fetch_metadata["challenge_type"] == "cloudflare_challenge"
