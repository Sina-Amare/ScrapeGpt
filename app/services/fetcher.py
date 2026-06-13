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
from app.services.url_validator import (
    URLValidationError,
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
        raise FetchError(_format_browser_exception(exc), "FETCH_FAILED") from exc


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
        raise FetchError(_format_browser_exception(exc), "FETCH_FAILED") from exc

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
    if _should_use_threaded_browser_fetch():
        return await asyncio.to_thread(_browser_fetch_sync, url, cookies)
    return await _browser_fetch_async(url, cookies)


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
        raise FetchError(_format_browser_exception(exc), "FETCH_FAILED") from exc

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
    if _should_use_threaded_browser_fetch():
        return await asyncio.to_thread(_camoufox_fetch_in_thread, url, cookies)
    return await _camoufox_fetch_async(url, cookies)


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
        if exc.error_code != "BROWSER_UNAVAILABLE":
            raise
        logger.info("fetcher.camoufox_unavailable_fallback_playwright", extra={"url": url})

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
