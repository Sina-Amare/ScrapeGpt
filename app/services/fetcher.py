"""HTTP fetcher: static httpx + stealth browser cascade.

Fetch cascade (AUTO mode):
  1. Static httpx — fastest, no JS, no fingerprint
  2. Camoufox  — stealth Firefox, best CF-JS-challenge evasion  (optional install)
  3. Playwright + stealth patches — headless Chrome with anti-fingerprint patches
  4. FlareSolverr — Docker service that runs a stealth browser externally (optional)

BROWSER mode always starts at step 2 and falls through to 4.
STATIC mode skips all browser steps.

Install stealth backends (all optional, graceful fallback if missing):
  pip install playwright && python -m playwright install chromium
  pip install playwright-stealth
  pip install "camoufox[geoip]" && python -m camoufox fetch
  docker run -d -p 8191:8191 flaresolverr/flaresolverr:latest  # then set FLARESOLVERR_URL
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from app.core.config import settings
from app.services.anti_bot import AUTO_SOLVABLE_CHALLENGES, anti_bot_challenge_reason
from app.services.dom_summary import assess_html_quality
from app.services.url_validator import (
    URLValidationError,
    check_ip,
    validate_redirect_target,
    validate_url,
)

logger = logging.getLogger(__name__)

_CONTENT_TYPE_ALLOWLIST = ("text/html", "text/plain", "application/xhtml+xml")


def _supported_accept_encoding() -> str:
    """Build an `Accept-Encoding` header advertising only what httpx can decode.

    Advertising `br`/`zstd` without the matching decoder installed makes httpx
    return the still-compressed body, which we then mis-read as binary garbage
    (the bug this fixes). gzip/deflate are always available via stdlib zlib; br
    and zstd are added only when their decoders are actually present.

    `httpx._decoders.SUPPORTED_DECODERS` is private, so we use it defensively and
    fall back to probing the optional libraries directly if it ever moves.
    """
    encodings = ["gzip", "deflate"]
    available: set[str] = set()
    try:  # authoritative: httpx's own decoder registry
        from httpx._decoders import SUPPORTED_DECODERS

        available = set(SUPPORTED_DECODERS.keys())
    except Exception:  # private API moved — probe the optional libs instead
        import importlib.util

        if importlib.util.find_spec("brotli") or importlib.util.find_spec("brotlicffi"):
            available.add("br")
        if importlib.util.find_spec("zstandard"):
            available.add("zstd")
    for enc in ("br", "zstd"):
        if enc in available:
            encodings.append(enc)
    return ", ".join(encodings)


# Computed once at import: advertise only decoders we can actually use.
_ACCEPT_ENCODING = _supported_accept_encoding()


def _decode_response(body: bytes, resp: httpx.Response) -> str:
    """Decode response bytes to text using the response's charset, not a fixed codec.

    Prefers the charset declared in Content-Type, then charset detection
    (charset-normalizer, already a dependency), then UTF-8. `errors="replace"`
    only ever applies to genuinely undecodable bytes — which the caller's
    quality check then flags rather than silently accepting.
    """
    # charset_encoding is the charset from the Content-Type header, or None — unlike
    # resp.encoding, it does not silently default to utf-8, so we can fall through
    # to real detection when the server didn't declare one.
    encoding = getattr(resp, "charset_encoding", None)
    if not encoding:
        try:
            from charset_normalizer import from_bytes

            match = from_bytes(body).best()
            if match is not None:
                encoding = match.encoding
        except Exception:
            encoding = None
    try:
        return body.decode(encoding or "utf-8", errors="replace")
    except (LookupError, TypeError):
        # Unknown/invalid codec name — fall back to UTF-8.
        return body.decode("utf-8", errors="replace")

# AUTO_SOLVABLE_CHALLENGES (imported from anti_bot) lists challenge types a
# stealth browser can resolve by executing the JS.  Turnstile / CAPTCHA need
# a solving service — waiting on them is pointless.

# Full browser-like headers for static requests.
_STATIC_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": _ACCEPT_ENCODING,
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# Extra HTTP headers for Playwright contexts (no Accept-Encoding — browser handles it).
_BROWSER_HEADERS = {
    k: v for k, v in _STATIC_HEADERS.items() if k not in ("Accept-Encoding",)
}

_BROWSER_SETTLE_MS = 15_000

# Built-in stealth init script injected into every Playwright Chromium page.
# Removes the most obvious automation fingerprints without any external deps.
# playwright-stealth (if installed) adds deeper patches on top of this.
_STEALTH_INIT_SCRIPT = r"""
() => {
    // Remove navigator.webdriver — the single biggest tell
    try {
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true,
        });
    } catch (_) {}

    // Inject a realistic window.chrome object (absent in headless Chrome)
    if (!window.chrome) {
        window.chrome = {
            app: { isInstalled: false },
            runtime: {
                id: undefined,
                connect: () => {},
                sendMessage: () => {},
                PlatformOs: { WIN: 'win', MAC: 'mac', LINUX: 'linux', ANDROID: 'android', CROS: 'cros' },
                PlatformArch: { ARM: 'arm', ARM64: 'arm64', X86_32: 'x86-32', X86_64: 'x86-64' },
            },
            loadTimes: () => ({}),
            csi: () => ({}),
        };
    }

    // Headless Chrome reports 0 plugins; real browsers expose at least three.
    if (!navigator.plugins || navigator.plugins.length === 0) {
        const PLUGINS = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
        ];
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = Object.assign([], PLUGINS);
                arr.refresh = () => {};
                arr.item = (i) => arr[i] ?? null;
                arr.namedItem = (n) => arr.find(p => p.name === n) ?? null;
                return arr;
            },
        });
    }

    // Realistic MIME types
    if (!navigator.mimeTypes || navigator.mimeTypes.length === 0) {
        Object.defineProperty(navigator, 'mimeTypes', {
            get: () => {
                const arr = Object.assign([], [
                    { type: 'application/pdf', description: 'Portable Document Format', suffixes: 'pdf' },
                    { type: 'text/pdf', description: '', suffixes: 'pdf' },
                ]);
                arr.item = (i) => arr[i] ?? null;
                arr.namedItem = (n) => arr.find(m => m.type === n) ?? null;
                return arr;
            },
        });
    }

    // Languages — headless often returns an empty array
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

    // Permissions API — override so notifications check looks real
    if (window.Permissions && window.Permissions.prototype.query) {
        const _orig = window.Permissions.prototype.query.bind(window.Permissions.prototype);
        window.Permissions.prototype.query = function (params) {
            if (params.name === 'notifications') {
                return Promise.resolve({ state: (typeof Notification !== 'undefined' ? Notification.permission : 'default') });
            }
            return _orig(params);
        };
    }

    // Hardware & screen — match the viewport (1920×1080) and a typical desktop
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    Object.defineProperty(screen, 'width', { get: () => 1920 });
    Object.defineProperty(screen, 'height', { get: () => 1080 });
    Object.defineProperty(screen, 'availWidth', { get: () => 1920 });
    Object.defineProperty(screen, 'availHeight', { get: () => 1040 });
    Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
    Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
}
"""


def _format_browser_exception(exc: Exception) -> str:
    detail = str(exc).strip()
    if detail:
        return f"Browser fetch failed: {detail}"
    return f"Browser fetch failed: {exc.__class__.__name__}"


class FetchError(Exception):
    def __init__(self, message: str, error_code: str = "FETCH_FAILED") -> None:
        super().__init__(message)
        self.error_code = error_code


# Driver/transport-level browser crash signatures. These mean the browser
# process or its IPC pipe died mid-operation (not a page-level error such as a
# bad selector), so a single retry on a fresh browser usually succeeds. Matched
# case-insensitively as substrings of ``str(exc)`` — backend-agnostic
# (Playwright + camoufox share the same driver transport wording).
_BROWSER_CRASH_SIGNATURES = (
    "connection closed while reading from the driver",
    "target closed",
    "target page, context or browser has been closed",
    "browser has been closed",
    "browser closed unexpectedly",
    "connection lost",
    "websocket connection closed",
    "the browser process exited",
    "browser process exited",
    "pipe closed",
)


def _is_browser_driver_crash(exc: Exception) -> bool:
    """True when *exc* looks like a transient browser-driver/transport crash."""
    text = str(exc).lower()
    return any(sig in text for sig in _BROWSER_CRASH_SIGNATURES)


def _browser_exception_to_fetch_error(exc: Exception) -> FetchError:
    """Map a raw browser exception to a FetchError with a stable error code.

    Transient driver/transport crashes get ``BROWSER_DRIVER_CRASHED`` (the
    caller retries these once); everything else collapses to ``FETCH_FAILED`` as
    before. No raw driver strings leak past this boundary as an unknown code.
    """
    if _is_browser_driver_crash(exc):
        detail = str(exc).strip() or exc.__class__.__name__
        return FetchError(
            f"The browser closed unexpectedly while loading the page ({detail}).",
            "BROWSER_DRIVER_CRASHED",
        )
    return FetchError(_format_browser_exception(exc), "FETCH_FAILED")


async def _retry_once_on_driver_crash(op: Any, *args: Any, **kwargs: Any) -> Any:
    """Await ``op(*args, **kwargs)``; retry exactly once on a transient crash.

    Only ``BROWSER_DRIVER_CRASHED`` is retried — every other FetchError (bad
    selector, blocked URL, browser unavailable, timeout) is re-raised
    immediately. The retry runs the op from scratch, which for our backends
    launches a brand-new browser process, so a dead driver from the first
    attempt cannot poison the second.
    """
    try:
        return await op(*args, **kwargs)
    except FetchError as exc:
        if exc.error_code != "BROWSER_DRIVER_CRASHED":
            raise
        logger.warning(
            "fetcher.browser_driver_crash_retry",
            extra={"detail": str(exc)},
        )
        return await op(*args, **kwargs)


class RenderModeUsed(str, Enum):
    STATIC = "STATIC"
    BROWSER = "BROWSER"
    CAMOUFOX = "CAMOUFOX"
    FLARESOLVERR = "FLARESOLVERR"


@dataclass
class FetchResult:
    html: str
    content_hash: str
    final_url: str
    render_mode_used: RenderModeUsed
    status_code: int
    elapsed_ms: int
    fetch_metadata: dict = field(default_factory=dict)


def _browser_result_from_html(
    *,
    html_raw: bytes,
    final_url: str,
    status: int,
    elapsed_ms: int,
    render_mode: RenderModeUsed = RenderModeUsed.BROWSER,
) -> FetchResult:
    original_bytes = len(html_raw)
    truncated = original_bytes > settings.MAX_FETCH_BYTES
    if truncated:
        html_raw = html_raw[: settings.MAX_FETCH_BYTES]
    analyzed_bytes = len(html_raw)
    html = html_raw.decode("utf-8", errors="replace")
    content_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()

    return FetchResult(
        html=html,
        content_hash=content_hash,
        final_url=final_url,
        render_mode_used=render_mode,
        status_code=status,
        elapsed_ms=elapsed_ms,
        fetch_metadata={
            "original_bytes": original_bytes,
            "analyzed_bytes": analyzed_bytes,
            "truncated": truncated,
            "elapsed_ms": elapsed_ms,
        },
    )


# ---------------------------------------------------------------------------
# Static fetch
# ---------------------------------------------------------------------------

async def _static_fetch(url: str) -> FetchResult:
    """Fetch with httpx, manually validating each redirect hop."""
    current_url = url
    hops = 0
    t0 = time.monotonic()

    async with httpx.AsyncClient(
        timeout=settings.SCRAPE_TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": settings.USER_AGENT, **_STATIC_HEADERS},
    ) as client:
        while True:
            try:
                resp = await client.get(current_url)
            except httpx.TimeoutException as exc:
                raise FetchError(
                    f"Request timed out for {current_url}", "FETCH_TIMEOUT"
                ) from exc
            except httpx.RequestError as exc:
                raise FetchError(f"Network error: {exc}", "FETCH_FAILED") from exc

            # DNS-rebinding defense: validate the IP we ACTUALLY connected to,
            # not just the one resolved during pre-fetch validation. This closes
            # the TOCTOU window for the static path even if the hostname rebinds
            # to an internal address at connect time. Browser backends are not
            # pinned this way and still rely on egress controls (documented).
            stream = (getattr(resp, "extensions", None) or {}).get(
                "network_stream"
            )
            if stream is not None:
                server_addr = stream.get_extra_info("server_addr")
                if server_addr:
                    try:
                        check_ip(str(server_addr[0]))
                    except URLValidationError as exc:
                        raise FetchError(
                            f"Connection blocked ({exc})", "URL_BLOCKED"
                        ) from exc

            if resp.is_redirect:
                if hops >= settings.MAX_REDIRECTS:
                    raise FetchError(
                        f"Too many redirects (>{settings.MAX_REDIRECTS})",
                        "TOO_MANY_REDIRECTS",
                    )
                location = resp.headers.get("location", "")
                if not location:
                    raise FetchError("Redirect with no Location header", "FETCH_FAILED")
                try:
                    current_url = validate_redirect_target(location, current_url)
                except URLValidationError as exc:
                    raise FetchError(
                        f"Redirect target blocked: {exc}", "URL_BLOCKED"
                    ) from exc
                hops += 1
                continue

            status = resp.status_code
            ct = resp.headers.get("content-type", "")
            if not any(allowed in ct for allowed in _CONTENT_TYPE_ALLOWLIST):
                raise FetchError(
                    f"Unsupported content-type: {ct}", "UNSUPPORTED_CONTENT_TYPE"
                )

            try:
                body = await resp.aread()
            except Exception as exc:
                raise FetchError(f"Failed to read response body: {exc}") from exc

            original_bytes = len(body)
            truncated = original_bytes > settings.MAX_FETCH_BYTES
            if truncated:
                body = body[: settings.MAX_FETCH_BYTES]

            html = _decode_response(body, resp)
            content_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
            elapsed = int((time.monotonic() - t0) * 1000)

            return FetchResult(
                html=html,
                content_hash=content_hash,
                final_url=current_url,
                render_mode_used=RenderModeUsed.STATIC,
                status_code=status,
                elapsed_ms=elapsed,
                fetch_metadata={
                    "hops": hops,
                    "content_type": ct,
                    "original_bytes": original_bytes,
                    "analyzed_bytes": len(body),
                    "truncated": truncated,
                    "elapsed_ms": elapsed,
                },
            )


# ---------------------------------------------------------------------------
# Windows event loop helpers
# ---------------------------------------------------------------------------

def _should_use_threaded_browser_fetch() -> bool:
    if sys.platform != "win32":
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    return "Selector" in loop.__class__.__name__


def _ensure_windows_proactor_policy_for_playwright() -> None:
    if sys.platform != "win32":
        return
    policy_factory = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if policy_factory is None:
        return
    if "Proactor" not in asyncio.get_event_loop_policy().__class__.__name__:
        asyncio.set_event_loop_policy(policy_factory())


# ---------------------------------------------------------------------------
# SSRF route handler factory (shared by Playwright + camoufox)
# ---------------------------------------------------------------------------

def _make_route_handler(blocked: list[FetchError], is_async: bool):  # type: ignore[return]
    if is_async:
        async def _async_handler(route: Any) -> None:
            req_url = route.request.url
            if not req_url.startswith(("http://", "https://")):
                await route.continue_()
                return
            try:
                validate_url(req_url)
                await route.continue_()
            except URLValidationError as exc:
                if not blocked:
                    blocked.append(FetchError(f"Browser blocked URL: {exc}", "BROWSER_URL_BLOCKED"))
                await route.abort("blockedbyclient")
        return _async_handler
    else:
        def _sync_handler(route: Any) -> None:
            req_url = route.request.url
            if not req_url.startswith(("http://", "https://")):
                route.continue_()
                return
            try:
                validate_url(req_url)
                route.continue_()
            except URLValidationError as exc:
                if not blocked:
                    blocked.append(FetchError(f"Browser blocked URL: {exc}", "BROWSER_URL_BLOCKED"))
                route.abort("blockedbyclient")
        return _sync_handler


# ---------------------------------------------------------------------------
# Playwright Chromium fetch (with built-in + optional playwright-stealth patches)
# ---------------------------------------------------------------------------

def _browser_fetch_sync(
    url: str,
    cookies: list[dict] | None = None,
) -> FetchResult:
    """Playwright Chromium + stealth patches, sync API for Windows selector loops."""
    _ensure_windows_proactor_policy_for_playwright()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError(
            "Playwright not installed. "
            "Install: pip install playwright && python -m playwright install chromium",
            "BROWSER_UNAVAILABLE",
        ) from exc

    t0 = time.monotonic()
    blocked: list[FetchError] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=settings.USER_AGENT,
                    java_script_enabled=True,
                    viewport={"width": 1920, "height": 1080},
                    extra_http_headers=_BROWSER_HEADERS,
                )
                if cookies:
                    context.add_cookies(cookies)
                try:
                    context.route("**", _make_route_handler(blocked, is_async=False))
                    page = context.new_page()
                    # Built-in stealth init script (always applied)
                    page.add_init_script(_STEALTH_INIT_SCRIPT)
                    # Deeper stealth via playwright-stealth v2 if installed
                    try:
                        from playwright_stealth import Stealth
                        Stealth().apply_stealth_sync(page)
                    except ImportError:
                        pass

                    response = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=settings.SCRAPE_TIMEOUT * 1000,
                    )
                    if blocked:
                        raise blocked[0]
                    if response is None:
                        raise FetchError("Browser got no response", "FETCH_FAILED")

                    final_url = page.url
                    try:
                        validate_url(final_url)
                    except URLValidationError as exc:
                        raise FetchError(f"Final URL blocked: {exc}", "BROWSER_URL_BLOCKED") from exc

                    try:
                        page.wait_for_load_state("networkidle", timeout=_BROWSER_SETTLE_MS)
                    except Exception:
                        pass

                    # If an auto-solvable CF challenge is still showing, wait
                    _chk = anti_bot_challenge_reason(page.content(), page.url)
                    if _chk and _chk in AUTO_SOLVABLE_CHALLENGES:
                        _wait_for_cf_challenge_sync(page)

                    html_raw = page.content().encode("utf-8")
                    final_url = page.url
                    elapsed = int((time.monotonic() - t0) * 1000)
                    return _browser_result_from_html(
                        html_raw=html_raw,
                        final_url=final_url,
                        status=response.status,
                        elapsed_ms=elapsed,
                    )
                finally:
                    context.close()
            finally:
                browser.close()
    except FetchError:
        raise
    except Exception as exc:
        if blocked:
            raise blocked[0]
        raise _browser_exception_to_fetch_error(exc) from exc


async def _browser_fetch_async(
    url: str,
    cookies: list[dict] | None = None,
) -> FetchResult:
    """Playwright Chromium + stealth patches, async API."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise FetchError(
            "Playwright not installed. "
            "Install: pip install playwright && python -m playwright install chromium",
            "BROWSER_UNAVAILABLE",
        ) from exc

    t0 = time.monotonic()
    blocked: list[FetchError] = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent=settings.USER_AGENT,
                    java_script_enabled=True,
                    viewport={"width": 1920, "height": 1080},
                    extra_http_headers=_BROWSER_HEADERS,
                )
                if cookies:
                    await context.add_cookies(cookies)
                try:
                    await context.route("**", _make_route_handler(blocked, is_async=True))
                    page = await context.new_page()
                    # Built-in stealth init script (always applied)
                    await page.add_init_script(_STEALTH_INIT_SCRIPT)
                    # Deeper stealth via playwright-stealth v2 if installed
                    try:
                        from playwright_stealth import Stealth
                        await Stealth().apply_stealth_async(page)
                    except ImportError:
                        pass

                    response = await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=settings.SCRAPE_TIMEOUT * 1000,
                    )
                    if blocked:
                        raise blocked[0]
                    if response is None:
                        raise FetchError("Browser got no response", "FETCH_FAILED")

                    status = response.status
                    final_url = page.url
                    try:
                        validate_url(final_url)
                    except URLValidationError as exc:
                        raise FetchError(f"Final URL blocked: {exc}", "BROWSER_URL_BLOCKED") from exc

                    try:
                        await page.wait_for_load_state("networkidle", timeout=_BROWSER_SETTLE_MS)
                    except Exception:
                        pass

                    # If an auto-solvable CF challenge is still showing, wait
                    check = anti_bot_challenge_reason(await page.content(), page.url)
                    if check and check in AUTO_SOLVABLE_CHALLENGES:
                        await _wait_for_cf_challenge(page)

                    html_raw = (await page.content()).encode("utf-8")
                    final_url = page.url
                finally:
                    await context.close()
            finally:
                await browser.close()
    except FetchError:
        raise
    except Exception as exc:
        if blocked:
            raise blocked[0]
        raise _browser_exception_to_fetch_error(exc) from exc

    elapsed = int((time.monotonic() - t0) * 1000)
    return _browser_result_from_html(
        html_raw=html_raw,
        final_url=final_url,
        status=status,
        elapsed_ms=elapsed,
    )


async def _browser_fetch(
    url: str,
    cookies: list[dict] | None = None,
) -> FetchResult:
    async def _op() -> FetchResult:
        if _should_use_threaded_browser_fetch():
            return await asyncio.to_thread(_browser_fetch_sync, url, cookies)
        return await _browser_fetch_async(url, cookies)

    return await _retry_once_on_driver_crash(_op)


# ---------------------------------------------------------------------------
# Camoufox fetch — stealth Firefox, best detection evasion (Tier 1 best)
# ---------------------------------------------------------------------------

async def _camoufox_fetch_async(
    url: str,
    cookies: list[dict] | None = None,
) -> FetchResult:
    """Fetch with camoufox stealth Firefox. Best evasion for Cloudflare JS challenges."""
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError as exc:
        raise FetchError(
            "camoufox not installed. "
            "Install: pip install \"camoufox[geoip]\" && python -m camoufox fetch",
            "BROWSER_UNAVAILABLE",
        ) from exc

    t0 = time.monotonic()
    blocked: list[FetchError] = []
    try:
        async with AsyncCamoufox(
            headless=True,
            geoip=True,
            humanize=True,
        ) as browser:
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                extra_http_headers=_BROWSER_HEADERS,
            )
            if cookies:
                await context.add_cookies(cookies)
            try:
                await context.route("**", _make_route_handler(blocked, is_async=True))
                page = await context.new_page()

                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=settings.SCRAPE_TIMEOUT * 1000,
                )
                if blocked:
                    raise blocked[0]
                if response is None:
                    raise FetchError("camoufox got no response", "FETCH_FAILED")

                status = response.status
                final_url = page.url
                try:
                    validate_url(final_url)
                except URLValidationError as exc:
                    raise FetchError(f"Final URL blocked: {exc}", "BROWSER_URL_BLOCKED") from exc

                try:
                    await page.wait_for_load_state("networkidle", timeout=_BROWSER_SETTLE_MS)
                except Exception:
                    pass

                # If CF challenge still showing, wait for it to auto-solve
                check = anti_bot_challenge_reason(await page.content(), page.url)
                if check:
                    await _wait_for_cf_challenge(page)

                html_raw = (await page.content()).encode("utf-8")
                final_url = page.url
            finally:
                await context.close()
    except FetchError:
        raise
    except Exception as exc:
        if blocked:
            raise blocked[0]
        raise _browser_exception_to_fetch_error(exc) from exc

    elapsed = int((time.monotonic() - t0) * 1000)
    return _browser_result_from_html(
        html_raw=html_raw,
        final_url=final_url,
        status=status,
        elapsed_ms=elapsed,
        render_mode=RenderModeUsed.CAMOUFOX,
    )


def _camoufox_fetch_in_thread(
    url: str,
    cookies: list[dict] | None = None,
) -> FetchResult:
    """Run camoufox in a worker thread with its own ProactorEventLoop (Windows compat)."""
    _ensure_windows_proactor_policy_for_playwright()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_camoufox_fetch_async(url, cookies))
    finally:
        loop.close()


async def _camoufox_fetch(
    url: str,
    cookies: list[dict] | None = None,
) -> FetchResult:
    async def _op() -> FetchResult:
        if _should_use_threaded_browser_fetch():
            return await asyncio.to_thread(_camoufox_fetch_in_thread, url, cookies)
        return await _camoufox_fetch_async(url, cookies)

    return await _retry_once_on_driver_crash(_op)


# ---------------------------------------------------------------------------
# Interaction runner — apply per-variant click/select recipes in a browser
# ---------------------------------------------------------------------------

async def _run_interaction_steps(page, steps: list[dict]) -> None:
    """Apply one variant's ordered recipe to an already-loaded page.

    Supported steps (kept deliberately small and safe):
      * {"action":"click","by":"selector","value":"css"}      -> page.click(css)
      * {"action":"click","by":"text","value":"Imperial"}     -> click by text
      * {"action":"select","by":"selector","value":"css::Opt"} -> select_option
      * {"action":"wait","value":"500"} (ms) or a CSS selector to wait for
    """
    for step in steps or []:
        action = str(step.get("action") or "click")
        by = str(step.get("by") or "selector")
        value = str(step.get("value") or "").strip()
        if not value and action != "wait":
            continue
        if action == "click":
            if by == "text":
                await page.get_by_text(value, exact=False).first.click(
                    timeout=_BROWSER_SETTLE_MS
                )
            else:
                await page.click(value, timeout=_BROWSER_SETTLE_MS)
        elif action == "select":
            selector, _, option = value.partition("::")
            if option:
                await page.select_option(selector, label=option, timeout=_BROWSER_SETTLE_MS)
            else:
                await page.select_option(selector, index=0, timeout=_BROWSER_SETTLE_MS)
        elif action == "wait":
            if value.isdigit():
                await page.wait_for_timeout(int(value))
            elif value:
                await page.wait_for_selector(value, timeout=_BROWSER_SETTLE_MS)
            else:
                await page.wait_for_timeout(_BROWSER_SETTLE_MS)
        # Let the DOM settle after each step.
        try:
            await page.wait_for_load_state("networkidle", timeout=_BROWSER_SETTLE_MS)
        except Exception:
            pass


async def _content_len(page) -> int | None:
    try:
        return len(await page.content())
    except Exception:
        return None


async def _wait_for_dom_settle(
    page,
    *,
    baseline_len: int | None = None,
    interval_ms: int = 250,
    stable_reads: int = 2,
    max_ms: int = 10_000,
) -> None:
    """Wait for a client-side re-render to land, then for the DOM to stabilize.

    Client-side toggles (e.g. a React/MUI ToggleButtonGroup re-rendering a table)
    do NO network I/O, so ``wait_for_load_state("networkidle")`` returns before
    the re-render lands and ``page.content()`` would capture the stale, pre-toggle
    DOM (the flaky "shows the default value" bug). Two phases, no site knowledge:

      1. If ``baseline_len`` (the pre-interaction size) is known, wait until the
         content size CHANGES — otherwise we could "stabilize" on the unchanged
         pre-toggle DOM that simply has not updated yet.
      2. Then wait until the size stops changing for ``stable_reads`` reads.
    """
    elapsed = 0
    if baseline_len is not None:
        while elapsed < max_ms:
            size = await _content_len(page)
            if size is None:
                return
            if size != baseline_len:
                break
            try:
                await page.wait_for_timeout(interval_ms)
            except Exception:
                return
            elapsed += interval_ms

    prev: int | None = None
    stable = 0
    while elapsed < max_ms:
        size = await _content_len(page)
        if size is None:
            return
        if prev is not None and size == prev:
            stable += 1
            if stable >= stable_reads:
                return
        else:
            stable = 0
        prev = size
        try:
            await page.wait_for_timeout(interval_ms)
        except Exception:
            return
        elapsed += interval_ms


async def _capture_recipes_on_context(
    context, url: str, recipes: dict[str, list[dict]], blocked: list[FetchError]
) -> dict[str, str]:
    """Shared per-recipe loop: re-navigate, apply steps, capture HTML.

    Works on any Playwright-compatible context (camoufox or Playwright), since
    both expose the same page API. Re-goto per recipe resets to the base state.
    """
    out: dict[str, str] = {}
    stalled: list[str] = []  # stepped recipes whose interaction never changed the DOM
    page = await context.new_page()
    for recipe_id, steps in recipes.items():
        # A click recipe SHOULD change the DOM. Browser drivers occasionally drop
        # a click on a freshly-hydrated control (no re-render), leaving the stale
        # pre-toggle DOM (the flaky "shows the default value" bug). Retry the whole
        # navigate+interact a few times until the content actually changes.
        attempts = 3 if steps else 1
        last_html = ""
        changed = False
        for attempt in range(attempts):
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=settings.SCRAPE_TIMEOUT * 1000,
            )
            if blocked:
                raise blocked[0]
            if response is None:
                raise FetchError("Browser got no response", "FETCH_FAILED")
            try:
                validate_url(page.url)
            except URLValidationError as exc:
                raise FetchError(
                    f"Final URL blocked: {exc}", "BROWSER_URL_BLOCKED"
                ) from exc
            try:
                await page.wait_for_load_state(
                    "networkidle", timeout=_BROWSER_SETTLE_MS
                )
            except Exception:
                pass
            if not steps:
                last_html = await page.content()
                break
            baseline_len = await _content_len(page)
            await _run_interaction_steps(page, steps)
            # Wait for the client-side re-render to land + stabilize.
            await _wait_for_dom_settle(page, baseline_len=baseline_len)
            last_html = await page.content()
            if baseline_len is None or len(last_html) != baseline_len:
                changed = True
                break  # interaction took effect — done
        out[recipe_id] = last_html
        if steps and not changed:
            stalled.append(recipe_id)

    # Some drivers (notably the stealth Firefox/camoufox build) run without
    # crashing yet never register the click, so retrying in the SAME driver stays
    # stuck on the pre-toggle DOM. Signal a retryable driver failure so the caller
    # cascades to the next backend (Chromium clicks these reliably). If the next
    # backend also can't move the DOM, the caller degrades gracefully to static.
    if stalled:
        raise FetchError(
            f"Interaction produced no DOM change for: {', '.join(stalled)}",
            "BROWSER_DRIVER_CRASHED",
        )
    return out


async def _apply_interactions_camoufox(
    url: str, recipes: dict[str, list[dict]], cookies: list[dict] | None
) -> dict[str, str]:
    from camoufox.async_api import AsyncCamoufox  # may ImportError -> caller cascades

    blocked: list[FetchError] = []
    try:
        async with AsyncCamoufox(headless=True, geoip=True, humanize=True) as browser:
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                extra_http_headers=_BROWSER_HEADERS,
            )
            if cookies:
                await context.add_cookies(cookies)
            try:
                await context.route("**", _make_route_handler(blocked, is_async=True))
                return await _capture_recipes_on_context(context, url, recipes, blocked)
            finally:
                await context.close()
    except FetchError:
        raise
    except Exception as exc:
        if blocked:
            raise blocked[0]
        raise _browser_exception_to_fetch_error(exc) from exc


async def _apply_interactions_playwright(
    url: str, recipes: dict[str, list[dict]], cookies: list[dict] | None
) -> dict[str, str]:
    from playwright.async_api import async_playwright  # may ImportError

    blocked: list[FetchError] = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent=settings.USER_AGENT,
                    java_script_enabled=True,
                    viewport={"width": 1920, "height": 1080},
                    extra_http_headers=_BROWSER_HEADERS,
                )
                if cookies:
                    await context.add_cookies(cookies)
                try:
                    await context.route("**", _make_route_handler(blocked, is_async=True))
                    return await _capture_recipes_on_context(context, url, recipes, blocked)
                finally:
                    await context.close()
            finally:
                await browser.close()
    except FetchError:
        raise
    except Exception as exc:
        if blocked:
            raise blocked[0]
        raise _browser_exception_to_fetch_error(exc) from exc


async def _apply_interactions_async(
    url: str,
    recipes: dict[str, list[dict]],
    cookies: list[dict] | None = None,
) -> dict[str, str]:
    """Capture one HTML snapshot per recipe by re-navigating and applying steps.

    Mirrors the fetch stealth cascade: try camoufox (best evasion), then fall
    back to stealth Playwright — the documented browser backend. Returns
    {recipe_id: html}. Raises FetchError("BROWSER_UNAVAILABLE") only when neither
    backend is installed — never a partial/guessed result.
    """
    try:
        return await _apply_interactions_camoufox(url, recipes, cookies)
    except ImportError:
        pass
    except FetchError as exc:
        # Fall through to Playwright Chromium when camoufox is missing OR its
        # driver crashed. Camoufox's Firefox driver crashes on some pages (e.g.
        # an uncaught page-level JS error), and Chromium handles those fine — so
        # a camoufox driver crash must not abandon the whole interaction.
        if exc.error_code not in ("BROWSER_UNAVAILABLE", "BROWSER_DRIVER_CRASHED"):
            raise
        logger.info(
            "fetcher.camoufox_interaction_fallback_playwright",
            extra={"error_code": exc.error_code},
        )
    try:
        return await _apply_interactions_playwright(url, recipes, cookies)
    except ImportError as exc:
        raise FetchError(
            "No browser backend available for interactive variants. Install "
            "camoufox (\"pip install camoufox[geoip]\") or Playwright "
            "(\"pip install playwright && python -m playwright install chromium\").",
            "BROWSER_UNAVAILABLE",
        ) from exc


def _apply_interactions_in_thread(
    url: str,
    recipes: dict[str, list[dict]],
    cookies: list[dict] | None = None,
) -> dict[str, str]:
    _ensure_windows_proactor_policy_for_playwright()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            _apply_interactions_async(url, recipes, cookies)
        )
    finally:
        loop.close()


async def apply_interactions_and_capture(
    url: str,
    recipes: dict[str, list[dict]],
    *,
    cookies: list[dict] | None = None,
) -> dict[str, str]:
    """Public entry: {recipe_id: [steps]} -> {recipe_id: html} via a browser.

    Raises FetchError("BROWSER_UNAVAILABLE") when no browser is installed so the
    caller can surface INTERACTION_BROWSER_REQUIRED instead of silently dropping
    an interactive variant.
    """
    if not recipes:
        return {}

    async def _op() -> dict[str, str]:
        if _should_use_threaded_browser_fetch():
            return await asyncio.to_thread(
                _apply_interactions_in_thread, url, recipes, cookies
            )
        return await _apply_interactions_async(url, recipes, cookies)

    return await _retry_once_on_driver_crash(_op)


# ---------------------------------------------------------------------------
# FlareSolverr fetch — Docker-based CF challenge solver (Tier 2, nuclear option)
# ---------------------------------------------------------------------------

async def _flaresolverr_fetch(url: str) -> FetchResult:
    """Submit URL to a FlareSolverr instance and return the solved HTML.

    FlareSolverr runs a stealth browser in a Docker container and returns the
    page content after solving any Cloudflare JS challenge. Configure via
    FLARESOLVERR_URL in .env (e.g. http://localhost:8191).
    """
    base = settings.FLARESOLVERR_URL.rstrip("/")
    timeout_ms = settings.FLARESOLVERR_TIMEOUT * 1000
    t0 = time.monotonic()

    try:
        async with httpx.AsyncClient(
            timeout=settings.FLARESOLVERR_TIMEOUT + 15,
        ) as client:
            resp = await client.post(
                f"{base}/v1",
                json={"cmd": "request.get", "url": url, "maxTimeout": timeout_ms},
            )
    except httpx.RequestError as exc:
        raise FetchError(
            f"FlareSolverr unreachable at {base}: {exc}", "FETCH_FAILED"
        ) from exc

    try:
        data = resp.json()
    except Exception as exc:
        raise FetchError(f"FlareSolverr returned non-JSON response", "FETCH_FAILED") from exc

    if data.get("status") != "ok":
        msg = data.get("message") or data.get("error") or "unknown error"
        raise FetchError(f"FlareSolverr failed: {msg}", "FETCH_FAILED")

    solution = data.get("solution", {})
    html = solution.get("response", "")
    if not html:
        raise FetchError("FlareSolverr returned empty response body", "FETCH_FAILED")

    html_bytes = html.encode("utf-8")
    if len(html_bytes) > settings.MAX_FETCH_BYTES:
        html_bytes = html_bytes[: settings.MAX_FETCH_BYTES]
    html = html_bytes.decode("utf-8", errors="replace")

    final_url = solution.get("url") or url
    status = solution.get("status") or 200
    elapsed = int((time.monotonic() - t0) * 1000)

    logger.info(
        "fetcher.flaresolverr_success",
        extra={"url": url, "status": status, "elapsed_ms": elapsed},
    )

    return FetchResult(
        html=html,
        content_hash=hashlib.sha256(html.encode("utf-8")).hexdigest(),
        final_url=final_url,
        render_mode_used=RenderModeUsed.FLARESOLVERR,
        status_code=status,
        elapsed_ms=elapsed,
        fetch_metadata={"via": "flaresolverr", "elapsed_ms": elapsed},
    )


# ---------------------------------------------------------------------------
# Stealth cascade: camoufox → stealth Playwright → FlareSolverr
# ---------------------------------------------------------------------------

async def _stealth_browser_fetch(
    url: str,
    cookies: list[dict] | None = None,
) -> FetchResult:
    """Try stealth backends in order of effectiveness.

    1. camoufox (stealth Firefox — best, requires separate install)
    2. Playwright Chromium + stealth patches (always available if playwright installed)

    If both raise BROWSER_UNAVAILABLE it means neither is installed.
    """
    # Try camoufox first — best fingerprint evasion
    try:
        result = await _camoufox_fetch(url, cookies)
        logger.info("fetcher.camoufox_success", extra={"url": url})
        return result
    except FetchError as exc:
        # Fall through to Chromium when camoufox is missing OR its driver
        # crashed (its Firefox driver crashes on some pages that Chromium
        # renders fine). Any other error is a real failure and propagates.
        if exc.error_code not in ("BROWSER_UNAVAILABLE", "BROWSER_DRIVER_CRASHED"):
            raise
        logger.info(
            "fetcher.camoufox_fallback_playwright",
            extra={"url": url, "error_code": exc.error_code},
        )

    # Fall back to stealth Playwright Chromium
    return await _browser_fetch(url, cookies)


async def _best_fetch_with_fallback(
    url: str,
    cookies: list[dict] | None = None,
    *,
    log_event: str,
    challenge: str | None,
) -> FetchResult:
    """Run the stealth browser cascade, then try FlareSolverr if still blocked."""
    logger.info(log_event, extra={"url": url, "challenge": challenge})

    try:
        result = await _stealth_browser_fetch(url, cookies)
    except FetchError as exc:
        if exc.error_code == "BROWSER_UNAVAILABLE":
            logger.info("fetcher.all_browsers_unavailable", extra={"url": url})
            # If FlareSolverr is configured, try it even without a local browser
            if settings.FLARESOLVERR_URL:
                return await _flaresolverr_fetch(url)
            raise
        raise

    # Check if the stealth browser still got blocked
    remaining_challenge = anti_bot_challenge_reason(result.html, result.final_url)
    if remaining_challenge and settings.FLARESOLVERR_URL:
        logger.info(
            "fetcher.stealth_still_blocked_trying_flaresolverr",
            extra={"url": url, "challenge": remaining_challenge},
        )
        try:
            fs_result = await _flaresolverr_fetch(url)
            # Only use FlareSolverr result if it actually broke through
            fs_challenge = anti_bot_challenge_reason(fs_result.html, fs_result.final_url)
            if not fs_challenge:
                return fs_result
            logger.info(
                "fetcher.flaresolverr_also_blocked",
                extra={"url": url, "challenge": fs_challenge},
            )
        except FetchError as exc:
            logger.warning(
                "fetcher.flaresolverr_error",
                extra={"url": url, "error": str(exc)},
            )

    return result


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

# Status codes that commonly mean "bot detected, try stealth browser".
_BLOCKED_STATUS_CODES = frozenset({401, 403, 407, 429, 503})


def _is_sparse(html: str) -> bool:
    stripped = html.replace(" ", "").replace("\n", "")
    return len(stripped) < 500


async def _wait_for_cf_challenge(page: Any, *, timeout_s: float = 25.0) -> None:
    """Poll until the page is no longer a CF challenge page, or timeout.

    CF JS challenges auto-solve and redirect in ~3-8 s when the browser
    passes fingerprint checks.  We poll every 2 s rather than using
    wait_for_navigation because CF redirects back to the same URL.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        try:
            html = await page.content()
        except Exception:
            return
        if not anti_bot_challenge_reason(html, page.url):
            return


def _wait_for_cf_challenge_sync(page: Any, *, timeout_s: float = 25.0) -> None:
    """Sync version of _wait_for_cf_challenge."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(2)
        try:
            html = page.content()
        except Exception:
            return
        if not anti_bot_challenge_reason(html, page.url):
            return


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

async def fetch_url(
    url: str,
    render_mode: str = "AUTO",
    browser_session_cookies: list[dict] | None = None,
) -> FetchResult:
    """Fetch a URL with the appropriate strategy.

    AUTO  — static first; if sparse or CF-JS-challenge detected, run the full
            stealth cascade (camoufox → stealth Playwright → FlareSolverr).
    BROWSER — stealth cascade directly (skip static).
    STATIC  — static httpx only.
    """
    if render_mode == "BROWSER":
        return await _best_fetch_with_fallback(
            url,
            browser_session_cookies,
            log_event="fetcher.browser_mode_stealth_fetch",
            challenge=None,
        )

    result = await _static_fetch(url)

    if render_mode != "AUTO":
        return result

    challenge: str | None = None
    if _is_sparse(result.html):
        log_event = "fetcher.sparse_content_stealth_fallback"
    elif assess_html_quality(result.html).is_binary:
        # Undecodable/garbled body (e.g. a compression we couldn't decode). A real
        # browser handles content negotiation itself, so retry via the cascade.
        log_event = "fetcher.garbled_content_stealth_fallback"
    elif result.status_code in _BLOCKED_STATUS_CODES:
        # 403/429/503 from a static fetch almost always means bot detection.
        # Try the stealth cascade regardless of what the body looks like.
        log_event = "fetcher.blocked_status_stealth_fallback"
    else:
        challenge = anti_bot_challenge_reason(result.html, result.final_url)
        if challenge in AUTO_SOLVABLE_CHALLENGES:
            log_event = "fetcher.challenge_stealth_retry"
        else:
            return result

    try:
        result = await _best_fetch_with_fallback(
            url,
            browser_session_cookies,
            log_event=log_event,
            challenge=challenge,
        )
    except FetchError as exc:
        if exc.error_code == "BROWSER_UNAVAILABLE":
            logger.info(
                "fetcher.browser_unavailable_fallback_static",
                extra={"url": url, "challenge": challenge},
            )
            result.fetch_metadata["browser_fallback_skipped"] = True
            if challenge:
                result.fetch_metadata["challenge_type"] = challenge
        else:
            raise

    return result
