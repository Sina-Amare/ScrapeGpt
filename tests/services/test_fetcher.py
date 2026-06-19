"""Tests for the HTTP fetcher service."""

from types import SimpleNamespace

import pytest

from app.services.fetcher import (
    FetchError,
    FetchResult,
    RenderModeUsed,
    _ACCEPT_ENCODING,
    _decode_response,
    _supported_accept_encoding,
    fetch_url,
)


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
    "FLARESOLVERR_URL": "",
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


class _PeerStream:
    def __init__(self, ip: str):
        self._ip = ip

    def get_extra_info(self, key):
        return (self._ip, 80) if key == "server_addr" else None


@pytest.mark.asyncio
async def test_static_fetch_blocks_rebound_private_peer_ip(monkeypatch):
    """A4: if the connection actually lands on a private IP (DNS rebinding),
    the static path rejects it via the connected-peer-IP check."""
    resp = _FakeResponse()
    resp.extensions = {"network_stream": _PeerStream("10.0.0.5")}

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(resp))
    monkeypatch.setattr("app.services.fetcher.settings", _FAKE_SETTINGS)
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": False})(),
    )

    with pytest.raises(FetchError) as exc_info:
        await fetch_url("http://example.com/", render_mode="STATIC")
    assert exc_info.value.error_code == "URL_BLOCKED"


@pytest.mark.asyncio
async def test_static_fetch_allows_public_peer_ip(monkeypatch):
    """A4: a public connected peer IP passes the rebinding check."""
    resp = _FakeResponse()
    resp.extensions = {"network_stream": _PeerStream("93.184.216.34")}

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(resp))
    monkeypatch.setattr("app.services.fetcher.settings", _FAKE_SETTINGS)
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": False})(),
    )

    result = await fetch_url("http://example.com/", render_mode="STATIC")
    assert result.status_code == 200


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
    fake_page.add_init_script = AsyncMock()
    fake_page.wait_for_load_state = AsyncMock()

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
    fake_page.add_init_script = AsyncMock()
    fake_page.wait_for_load_state = AsyncMock()

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
    fake_page.add_init_script = AsyncMock()
    fake_page.wait_for_load_state = AsyncMock()

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
        lambda url, cookies=None: expected,
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

    from app.services.fetcher import FetchError, FetchResult, RenderModeUsed

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
        httpx, "AsyncClient",
        lambda **kw: _FakeClient(_FakeResponse(body=_CF_CHALLENGE_HTML)),
    )
    monkeypatch.setattr("app.services.fetcher.settings", _FAKE_SETTINGS)
    # Camoufox is not available in unit tests — skip straight to Playwright
    monkeypatch.setattr(
        "app.services.fetcher._camoufox_fetch",
        AsyncMock(
            side_effect=FetchError("camoufox not installed", "BROWSER_UNAVAILABLE")
        ),
    )
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
async def test_cloudflare_challenge_browser_unavailable_falls_back_to_static(
    monkeypatch,
):
    """If browser is unavailable, fall back to the static CF challenge page."""
    import httpx
    from unittest.mock import AsyncMock

    from app.services.fetcher import FetchError

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: _FakeClient(_FakeResponse(body=_CF_CHALLENGE_HTML)),
    )
    monkeypatch.setattr("app.services.fetcher.settings", _FAKE_SETTINGS)
    monkeypatch.setattr(
        "app.services.fetcher._camoufox_fetch",
        AsyncMock(
            side_effect=FetchError("camoufox not installed", "BROWSER_UNAVAILABLE")
        ),
    )
    monkeypatch.setattr(
        "app.services.fetcher._browser_fetch",
        AsyncMock(side_effect=FetchError("no playwright", "BROWSER_UNAVAILABLE")),
    )

    result = await fetch_url("https://example.com/", render_mode="AUTO")

    assert result.render_mode_used == RenderModeUsed.STATIC
    assert result.fetch_metadata["browser_fallback_skipped"] is True
    assert result.fetch_metadata["challenge_type"] == "cloudflare_challenge"


# ---------------------------------------------------------------------------
# Decode / Accept-Encoding (Phase 1: br/zstd + charset handling)
# ---------------------------------------------------------------------------


def test_accept_encoding_only_advertises_decodable_encodings():
    """We must never advertise an encoding httpx cannot decode (the zstd/br bug)."""
    from httpx._decoders import SUPPORTED_DECODERS

    advertised = [e.strip() for e in _supported_accept_encoding().split(",")]
    assert "gzip" in advertised and "deflate" in advertised
    # Every advertised encoding has a real decoder behind it.
    for enc in advertised:
        assert enc in SUPPORTED_DECODERS, f"advertised {enc!r} but httpx can't decode it"
    # In this environment brotli + zstandard are installed.
    assert "br" in advertised and "zstd" in advertised
    assert _ACCEPT_ENCODING == _supported_accept_encoding()


def test_decode_response_uses_header_charset():
    """A non-UTF-8 body is decoded with the charset from Content-Type, not utf-8."""
    text = "Café Münchën pâté"
    body = text.encode("iso-8859-1")
    resp = SimpleNamespace(charset_encoding="iso-8859-1")
    assert _decode_response(body, resp) == text


def test_decode_response_detects_charset_without_header():
    """With no declared charset, fall back to detection rather than mangling bytes."""
    text = "Résumé naïve coöperation — façade"
    body = text.encode("utf-8")
    resp = SimpleNamespace(charset_encoding=None)
    decoded = _decode_response(body, resp)
    assert "�" not in decoded
    assert "sum" in decoded and "facade" in decoded.replace("ç", "c")


def test_decode_response_falls_back_to_utf8_on_unknown_codec():
    body = "plain ascii".encode("utf-8")
    resp = SimpleNamespace(charset_encoding="totally-not-a-codec")
    assert _decode_response(body, resp) == "plain ascii"


# ---------------------------------------------------------------------------
# Tier 1 #1: browser-driver crash classification + retry-once
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg",
    [
        "Connection closed while reading from the driver",
        "Target page, context or browser has been closed",
        "Target closed",
        "Browser has been closed",
        "Browser closed unexpectedly",
        "The browser process exited",
    ],
)
def test_driver_crash_signatures_are_classified(msg):
    from app.services.fetcher import _is_browser_driver_crash

    assert _is_browser_driver_crash(Exception(msg)) is True


@pytest.mark.parametrize(
    "msg",
    [
        "Timeout 30000ms exceeded",
        "net::ERR_NAME_NOT_RESOLVED",
        "Selector did not resolve to any element",
        "",
    ],
)
def test_non_crash_exceptions_are_not_classified(msg):
    from app.services.fetcher import _is_browser_driver_crash

    assert _is_browser_driver_crash(Exception(msg)) is False


def test_browser_exception_to_fetch_error_assigns_stable_codes():
    from app.services.fetcher import _browser_exception_to_fetch_error

    crash = _browser_exception_to_fetch_error(
        Exception("Connection closed while reading from the driver")
    )
    assert crash.error_code == "BROWSER_DRIVER_CRASHED"
    assert "browser closed unexpectedly" in str(crash).lower()

    other = _browser_exception_to_fetch_error(Exception("boom"))
    assert other.error_code == "FETCH_FAILED"
    assert str(other) == "Browser fetch failed: boom"


@pytest.mark.asyncio
async def test_retry_once_succeeds_on_second_attempt():
    from app.services.fetcher import _retry_once_on_driver_crash

    calls = []

    async def op():
        calls.append(1)
        if len(calls) == 1:
            raise FetchError("driver died", "BROWSER_DRIVER_CRASHED")
        return "ok"

    assert await _retry_once_on_driver_crash(op) == "ok"
    assert len(calls) == 2  # original + exactly one retry


@pytest.mark.asyncio
async def test_retry_once_gives_up_after_a_single_retry():
    from app.services.fetcher import _retry_once_on_driver_crash

    calls = []

    async def op():
        calls.append(1)
        raise FetchError("driver died again", "BROWSER_DRIVER_CRASHED")

    with pytest.raises(FetchError) as ei:
        await _retry_once_on_driver_crash(op)
    assert ei.value.error_code == "BROWSER_DRIVER_CRASHED"
    assert len(calls) == 2  # never more than one retry


@pytest.mark.asyncio
async def test_retry_once_does_not_retry_other_fetch_errors():
    from app.services.fetcher import _retry_once_on_driver_crash

    calls = []

    async def op():
        calls.append(1)
        raise FetchError("bad selector", "FETCH_FAILED")

    with pytest.raises(FetchError) as ei:
        await _retry_once_on_driver_crash(op)
    assert ei.value.error_code == "FETCH_FAILED"
    assert len(calls) == 1  # non-crash codes are not retried


@pytest.mark.asyncio
async def test_browser_fetch_retries_once_on_driver_crash(monkeypatch):
    """A transient driver crash on the first attempt is retried once, then
    succeeds on a brand-new browser — wiring of classification + retry."""
    try:
        import playwright.async_api as pw_api
    except ImportError:
        pytest.skip("playwright not installed")

    from unittest.mock import AsyncMock, MagicMock

    attempts = {"n": 0}

    def make_pw():
        idx = attempts["n"]
        attempts["n"] += 1

        fake_page = MagicMock()
        fake_page.url = "https://example.com"
        fake_page.content = AsyncMock(
            return_value="<html><body>ok content</body></html>"
        )
        fake_page.add_init_script = AsyncMock()
        fake_page.wait_for_load_state = AsyncMock()
        if idx == 0:
            fake_page.goto = AsyncMock(
                side_effect=Exception(
                    "Connection closed while reading from the driver"
                )
            )
        else:
            fake_page.goto = AsyncMock(return_value=MagicMock(status=200))

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
        return fake_pw

    monkeypatch.setattr(pw_api, "async_playwright", make_pw)
    monkeypatch.setattr(
        "app.services.fetcher.settings",
        type("S", (), {
            "SCRAPE_TIMEOUT": 30, "USER_AGENT": "Test/1.0",
            "MAX_FETCH_BYTES": 2 * 1024 * 1024, "ALLOW_PRIVATE_NETWORK_URLS": True,
        })(),
    )

    from app.services.fetcher import _browser_fetch

    result = await _browser_fetch("https://example.com")
    assert "ok content" in result.html
    assert attempts["n"] == 2  # crashed once, retried once, succeeded


# ---------------------------------------------------------------------------
# Camoufox driver crash -> fall through to Playwright Chromium
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_cascade_falls_through_on_camoufox_crash(monkeypatch):
    """A camoufox DRIVER CRASH must cascade to Chromium (its Firefox driver
    crashes on some pages Chromium renders fine)."""
    from unittest.mock import AsyncMock
    import app.services.fetcher as fx

    chromium_result = FetchResult(
        html="<html>chromium</html>", content_hash="0" * 64,
        final_url="https://x", render_mode_used=RenderModeUsed.BROWSER,
        status_code=200, elapsed_ms=1, fetch_metadata={},
    )
    monkeypatch.setattr(
        fx, "_camoufox_fetch",
        AsyncMock(side_effect=FetchError("driver died", "BROWSER_DRIVER_CRASHED")),
    )
    monkeypatch.setattr(fx, "_browser_fetch", AsyncMock(return_value=chromium_result))

    result = await fx._stealth_browser_fetch("https://x")
    assert result is chromium_result


@pytest.mark.asyncio
async def test_fetch_cascade_propagates_non_crash_camoufox_error(monkeypatch):
    """A non-crash camoufox error (e.g. blocked URL) must NOT fall through."""
    from unittest.mock import AsyncMock
    import app.services.fetcher as fx

    monkeypatch.setattr(
        fx, "_camoufox_fetch",
        AsyncMock(side_effect=FetchError("blocked", "BROWSER_URL_BLOCKED")),
    )
    chromium = AsyncMock()
    monkeypatch.setattr(fx, "_browser_fetch", chromium)

    with pytest.raises(FetchError) as ei:
        await fx._stealth_browser_fetch("https://x")
    assert ei.value.error_code == "BROWSER_URL_BLOCKED"
    chromium.assert_not_called()


@pytest.mark.asyncio
async def test_interaction_cascade_falls_through_on_camoufox_crash(monkeypatch):
    """The interaction capture path must also cascade camoufox crash -> Chromium."""
    from unittest.mock import AsyncMock
    import app.services.fetcher as fx

    monkeypatch.setattr(
        fx, "_apply_interactions_camoufox",
        AsyncMock(side_effect=FetchError("driver died", "BROWSER_DRIVER_CRASHED")),
    )
    monkeypatch.setattr(
        fx, "_apply_interactions_playwright",
        AsyncMock(return_value={"v": "<html>ok</html>"}),
    )
    monkeypatch.setattr(fx, "_should_use_threaded_browser_fetch", lambda: False)

    out = await fx.apply_interactions_and_capture(
        "https://x", {"v": [{"action": "click", "by": "text", "value": "X"}]}
    )
    assert out == {"v": "<html>ok</html>"}


@pytest.mark.asyncio
async def test_auto_falls_back_to_browser_on_binary_static(monkeypatch):
    """Undecodable/garbled static HTML must trigger the stealth cascade in AUTO."""
    from unittest.mock import AsyncMock

    # A long, non-sparse, status-200 body with no challenge markers: the only
    # branch that can trigger a fallback here is the binary-content one.
    big_body = ("<html><body>" + ("content " * 100) + "</body></html>").encode("utf-8")
    resp = _FakeResponse(body=big_body)

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(resp))
    monkeypatch.setattr("app.services.fetcher.settings", _FAKE_SETTINGS)

    # Force the quality verdict to "binary" so we exercise the wiring, not the heuristic.
    from app.services.dom_summary import HtmlQuality
    monkeypatch.setattr(
        "app.services.fetcher.assess_html_quality",
        lambda html: HtmlQuality("binary", 0.9, 0, 0, ["forced"]),
    )
    # Browser tiers unavailable -> falls back to the static result, flagged.
    monkeypatch.setattr(
        "app.services.fetcher._camoufox_fetch",
        AsyncMock(side_effect=FetchError("no camoufox", "BROWSER_UNAVAILABLE")),
    )
    monkeypatch.setattr(
        "app.services.fetcher._browser_fetch",
        AsyncMock(side_effect=FetchError("no playwright", "BROWSER_UNAVAILABLE")),
    )

    result = await fetch_url("https://example.com/", render_mode="AUTO")
    assert result.fetch_metadata["browser_fallback_skipped"] is True


@pytest.mark.asyncio
async def test_browser_interactions_are_serialized(monkeypatch):
    """BROWSER_INTERACTION_CONCURRENCY serializes concurrent variant captures.

    Regression: under a concurrent crawl, launching several headless browsers at
    once to drive a click/toggle crashes the drivers, which silently degrades a
    variant to the page's STATIC values (e.g. a per-serving size stuck at the
    per-100g default on every crawled child page). Serializing the captures makes
    the camoufox->Chromium cascade recover reliably.
    """
    import asyncio

    import app.services.fetcher as fetcher

    # Async (non-threaded) path + a fresh semaphore for this loop
    # (default BROWSER_INTERACTION_CONCURRENCY == 1).
    monkeypatch.setattr(fetcher, "_should_use_threaded_browser_fetch", lambda: False)
    fetcher._interaction_semaphore = None
    fetcher._interaction_semaphore_loop = None

    active = 0
    peak = 0

    async def _fake_async(url, recipes, cookies):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {rid: f"<html>{rid}</html>" for rid in recipes}

    monkeypatch.setattr(fetcher, "_apply_interactions_async", _fake_async)

    results = await asyncio.gather(*[
        fetcher.apply_interactions_and_capture(
            f"https://example.com/{i}", {"r": [{"action": "click"}]}
        )
        for i in range(6)
    ])

    assert peak == 1, f"browser interactions not serialized (peak concurrency {peak})"
    assert all(r == {"r": "<html>r</html>"} for r in results)
