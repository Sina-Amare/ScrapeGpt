"""Tests for robots.txt service."""

import pytest

from app.services.robots_service import (
    RobotsResult,
    check_robots,
    clear_robots_cache,
)


def _make_robots_text(disallow: list[str], user_agent: str = "*") -> str:
    lines = [f"User-agent: {user_agent}"]
    for path in disallow:
        lines.append(f"Disallow: {path}")
    return "\n".join(lines)


@pytest.fixture(autouse=True)
def clear_cache():
    clear_robots_cache()
    yield
    clear_robots_cache()


@pytest.mark.asyncio
async def test_allows_permitted_url(monkeypatch):
    async def mock_get(self, url, **kwargs):
        class Resp:
            status_code = 200
            is_redirect = False
            text = _make_robots_text(["/private/"])
        return Resp()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await check_robots("http://example.com/public/page")
    assert result.result == RobotsResult.ALLOWED


@pytest.mark.asyncio
async def test_blocks_disallowed_path(monkeypatch):
    async def mock_get(self, url, **kwargs):
        class Resp:
            status_code = 200
            is_redirect = False
            text = _make_robots_text(["/private/"])
        return Resp()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await check_robots("http://example.com/private/secret")
    assert result.result == RobotsResult.BLOCKED


@pytest.mark.asyncio
async def test_allows_when_no_robots_txt(monkeypatch):
    async def mock_get(self, url, **kwargs):
        class Resp:
            status_code = 404
            is_redirect = False
            text = ""
        return Resp()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await check_robots("http://example.com/any/path")
    assert result.result == RobotsResult.ALLOWED


@pytest.mark.asyncio
async def test_deny_policy_on_fetch_failure(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get",
                        lambda self, url, **kw: (_ for _ in ()).throw(Exception("Network error")))
    monkeypatch.setattr(
        "app.services.robots_service.settings",
        type("S", (), {"ROBOTS_FAILURE_POLICY": "deny", "USER_AGENT": "TestBot"})(),
    )
    result = await check_robots("http://example.com/page")
    assert result.result == RobotsResult.UNAVAILABLE


@pytest.mark.asyncio
async def test_allow_policy_on_fetch_failure(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get",
                        lambda self, url, **kw: (_ for _ in ()).throw(Exception("timeout")))
    monkeypatch.setattr(
        "app.services.robots_service.settings",
        type("S", (), {"ROBOTS_FAILURE_POLICY": "allow", "USER_AGENT": "TestBot"})(),
    )
    result = await check_robots("http://example.com/page")
    assert result.result == RobotsResult.ALLOWED


@pytest.mark.asyncio
async def test_cache_is_used_on_second_call(monkeypatch):
    call_count = 0

    async def mock_get(self, url, **kwargs):
        nonlocal call_count
        call_count += 1
        class Resp:
            status_code = 200
            is_redirect = False
            text = _make_robots_text([])
        return Resp()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    await check_robots("http://example.com/a")
    await check_robots("http://example.com/b")
    # Both calls hit the same origin → only one fetch
    assert call_count == 1


@pytest.mark.asyncio
async def test_redirect_treated_as_unavailable_deny_policy(monkeypatch):
    """robots.txt redirect must never be followed (SSRF risk).

    A 3xx from robots.txt is treated as unavailable, not followed.
    With deny policy the result is UNAVAILABLE.
    """
    async def mock_get(self, url, **kwargs):
        class Resp:
            status_code = 301
            is_redirect = True
            headers = {"location": "http://192.168.1.1/evil"}
            text = ""
        return Resp()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    monkeypatch.setattr(
        "app.services.robots_service.settings",
        type("S", (), {"ROBOTS_FAILURE_POLICY": "deny", "USER_AGENT": "TestBot"})(),
    )
    result = await check_robots("http://example.com/page")
    assert result.result == RobotsResult.UNAVAILABLE


@pytest.mark.asyncio
async def test_redirect_with_allow_policy_returns_allowed(monkeypatch):
    """With allow policy, a robots.txt redirect still yields ALLOWED (not BLOCKED)."""
    async def mock_get(self, url, **kwargs):
        class Resp:
            status_code = 302
            is_redirect = True
            headers = {"location": "http://internal.corp/robots.txt"}
            text = ""
        return Resp()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    monkeypatch.setattr(
        "app.services.robots_service.settings",
        type("S", (), {"ROBOTS_FAILURE_POLICY": "allow", "USER_AGENT": "TestBot"})(),
    )
    result = await check_robots("http://example.com/page")
    assert result.result == RobotsResult.ALLOWED
