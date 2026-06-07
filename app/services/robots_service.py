"""robots.txt checking with in-process cache."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_ROBOTS_CACHE_TTL_SECONDS = 300  # 5-minute TTL
_USER_AGENT = settings.USER_AGENT

# origin -> (fetched_at, RobotFileParser | None)
_cache: dict[str, tuple[float, RobotFileParser | None]] = {}


class RobotsResult(str, Enum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class RobotsCheck:
    result: RobotsResult
    crawl_delay: float | None = None
    reason: str | None = None


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _robots_url(url: str) -> str:
    return f"{_origin(url)}/robots.txt"


async def _fetch_robots(robots_url: str) -> RobotFileParser | None:
    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(robots_url)

        if resp.status_code == 404:
            # No robots.txt means everything is allowed
            rp = RobotFileParser()
            rp.set_url(robots_url)
            rp.parse([])
            return rp

        if resp.status_code != 200:
            return None

        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(resp.text.splitlines())
        return rp

    except Exception as exc:
        logger.warning("robots.fetch_failed", extra={"url": robots_url, "error": str(exc)})
        return None


async def check_robots(url: str) -> RobotsCheck:
    """
    Check whether url is fetchable according to robots.txt.

    Caches parsed robots rules per origin for TTL seconds.
    On fetch failure, applies settings.ROBOTS_FAILURE_POLICY (deny | allow).
    """
    origin = _origin(url)
    now = time.monotonic()

    cached = _cache.get(origin)
    if cached is None or now - cached[0] > _ROBOTS_CACHE_TTL_SECONDS:
        rp = await _fetch_robots(_robots_url(url))
        _cache[origin] = (now, rp)
    else:
        rp = cached[1]

    if rp is None:
        if settings.ROBOTS_FAILURE_POLICY == "allow":
            return RobotsCheck(result=RobotsResult.ALLOWED, reason="robots unavailable, policy=allow")
        return RobotsCheck(
            result=RobotsResult.UNAVAILABLE,
            reason="robots.txt could not be fetched",
        )

    allowed = rp.can_fetch(_USER_AGENT, url)
    if not allowed:
        return RobotsCheck(result=RobotsResult.BLOCKED, reason="robots.txt disallows this path")

    crawl_delay: float | None = None
    try:
        d = rp.crawl_delay(_USER_AGENT)
        if d is not None:
            crawl_delay = float(d)
    except Exception:
        pass

    return RobotsCheck(result=RobotsResult.ALLOWED, crawl_delay=crawl_delay)


def clear_robots_cache() -> None:
    """Clear the in-memory robots cache (for testing)."""
    _cache.clear()
