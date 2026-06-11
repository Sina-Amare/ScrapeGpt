"""Opt-in live checks for OATD anti-bot behavior.

Run with:
    RUN_OATD_LIVE=1 venv\\Scripts\\python.exe -m pytest tests/services/test_oatd_live.py -q -s
"""

import os

import pytest

from app.services.anti_bot import anti_bot_challenge_reason
from app.services.fetcher import fetch_url


OATD_URL = (
    "https://www.oatd.org/oatd/search?"
    "q=statistics&form=basic&last2yr=y&level.facet=doctoral&start=31"
)


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("RUN_OATD_LIVE") != "1",
    reason="Opt-in live OATD check; site behavior and network reputation drift.",
)
async def test_oatd_live_response_is_classified_if_challenged():
    result = await fetch_url(OATD_URL, "STATIC")
    reason = anti_bot_challenge_reason(result.html, result.final_url)

    print(
        {
            "status_code": result.status_code,
            "final_url": result.final_url,
            "anti_bot_reason": reason,
            "bytes": len(result.html),
        }
    )

    if result.status_code in {401, 403, 429}:
        assert reason is not None
