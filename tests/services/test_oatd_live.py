"""Opt-in live checks for OATD anti-bot behavior.

Run with:
    RUN_OATD_LIVE=1 venv\\Scripts\\python.exe -m pytest tests/services/test_oatd_live.py -q -s

These tests hit the real OATD website and are skipped by default.
They verify:
1. STATIC fetch classifies the Cloudflare challenge correctly.
2. BROWSER fetch (Playwright) also detects or passes the challenge.
3. CHALLENGE_MESSAGES contains an actionable message for the detected reason.
"""

import os

import pytest

from app.services.anti_bot import CHALLENGE_MESSAGES, anti_bot_challenge_reason
from app.services.fetcher import fetch_url

OATD_URL = (
    "https://www.oatd.org/oatd/search?"
    "q=statistics&form=basic&last2yr=y&level.facet=doctoral&start=241"
)

_LIVE = pytest.mark.skipif(
    os.environ.get("RUN_OATD_LIVE") != "1",
    reason="Opt-in live OATD check; requires RUN_OATD_LIVE=1",
)


@pytest.mark.asyncio
@_LIVE
async def test_oatd_static_challenge_classified():
    """Static fetch of OATD: if Cloudflare blocks, anti_bot detects it."""
    result = await fetch_url(OATD_URL, "STATIC")
    reason = anti_bot_challenge_reason(result.html, result.final_url)

    print(
        "\n--- STATIC ---\n"
        f"  status: {result.status_code}\n"
        f"  final_url: {result.final_url}\n"
        f"  anti_bot_reason: {reason}\n"
        f"  bytes: {len(result.html)}\n"
    )

    # If the site returned a challenge page, reason must be set and
    # CHALLENGE_MESSAGES must have an actionable entry for it.
    if result.status_code in {401, 403, 429} or (
        reason and "cloudflare" in reason
    ):
        assert reason is not None, "Bot challenge detected but reason is None"
        assert reason in CHALLENGE_MESSAGES, (
            f"No CHALLENGE_MESSAGES entry for reason '{reason}'"
        )
        msg = CHALLENGE_MESSAGES[reason]
        assert len(msg) > 20, "Challenge message is too short to be useful"
        print(f"  challenge_message: {msg}")
    else:
        # Site allowed the request — verify we got real HTML content.
        assert len(result.html) > 1000, (
            "Unexpected: no challenge but very little HTML returned"
        )
        print("  Site allowed static request (no CF challenge this run)")


@pytest.mark.asyncio
@_LIVE
async def test_oatd_browser_challenge_classified():
    """Browser (Playwright) fetch of OATD: challenge is detected or resolved."""
    result = await fetch_url(OATD_URL, "BROWSER")
    reason = anti_bot_challenge_reason(result.html, result.final_url)

    print(
        "\n--- BROWSER ---\n"
        f"  status: {result.status_code}\n"
        f"  final_url: {result.final_url}\n"
        f"  anti_bot_reason: {reason}\n"
        f"  bytes: {len(result.html)}\n"
    )

    if reason:
        # Browser could not pass the challenge — verify message is available.
        assert reason in CHALLENGE_MESSAGES, (
            f"No CHALLENGE_MESSAGES entry for reason '{reason}'"
        )
        print(f"  challenge_message: {CHALLENGE_MESSAGES[reason]}")
    else:
        # Browser passed CF JS challenge — we should have real content.
        assert len(result.html) > 2000, (
            "Browser fetch returned very little HTML — may be a partial page"
        )
        print("  Browser fetch succeeded (CF JS challenge resolved or absent)")


@pytest.mark.asyncio
@_LIVE
async def test_oatd_auto_mode_emits_challenge_or_content():
    """AUTO mode: should end with either real content or a detected challenge."""
    result = await fetch_url(OATD_URL, "AUTO")
    reason = anti_bot_challenge_reason(result.html, result.final_url)

    print(
        "\n--- AUTO ---\n"
        f"  render_mode_used: {result.render_mode_used}\n"
        f"  status: {result.status_code}\n"
        f"  anti_bot_reason: {reason}\n"
        f"  bytes: {len(result.html)}\n"
    )

    # In AUTO mode: either we have real content (long HTML, no reason)
    # or the challenge was detected (reason set, message available).
    if reason:
        assert reason in CHALLENGE_MESSAGES
    else:
        assert len(result.html) > 1000
