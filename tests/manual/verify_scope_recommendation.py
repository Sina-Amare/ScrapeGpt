"""Manual real-URL verification for Phase 1 evidence-based scope recommendation.

Fetches a handful of real pages and asserts that ``recommend_scope`` picks the
right crawl mode against the LIVE HTML (decoded through the project fetcher, so
zstd/br/gzip are handled). Run manually (needs network):

    python -m tests.manual.verify_scope_recommendation

Not part of the pytest suite (it hits the network). Logging uses the project
config so no secrets/PII are emitted.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.logging_config import configure_logging
from app.services.crawl_scope import recommend_scope
from app.services.fetcher import fetch_url

configure_logging()
logger = logging.getLogger(__name__)

# (url, expected_mode, note). expected_mode None means "just report".
CASES = [
    ("https://www.calories.info/food/beef-veal", "COLLECTION",
     "sibling /food/* category pages, no pagination"),
    ("https://books.toscrape.com/", "PAGINATION",
     "real next-page pagination"),
    ("https://quotes.toscrape.com/", "PAGINATION",
     "real next-page pagination"),
    ("https://www.scrapethissite.com/pages/forms/", "PAGINATION",
     "paginated hockey-team table"),
]


async def main() -> int:
    failures = 0
    for url, expected, note in CASES:
        try:
            fetched = await fetch_url(url, "AUTO")
        except Exception as exc:  # noqa: BLE001 - manual diagnostic script
            logger.error("fetch_failed url=%s error=%s", url, type(exc).__name__)
            failures += 1
            continue
        rec = recommend_scope({}, fetched.html, fetched.final_url)
        mode = rec["recommended_mode"]
        ok = expected is None or mode == expected
        status = "OK " if ok else "FAIL"
        logger.info(
            "%s url=%s -> %s (expected %s; %s) patterns=%s evidence=%s",
            status, url, mode, expected, note,
            rec.get("suggested_include_patterns"), rec.get("evidence"),
        )
        if not ok:
            failures += 1
    logger.info("verify_scope_recommendation done failures=%s", failures)
    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
