"""Unit tests for app.services.session_service."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.session import parse_cookies_raw
from app.services.session_service import (
    _decrypt_cookies,
    _encrypt_cookies,
    get_cookies_for_session,
)


# ---------------------------------------------------------------------------
# Cookie encryption round-trip
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_round_trip():
    """Cookies encrypt and decrypt back to the original list."""
    cookies = [
        {"name": "cf_clearance", "value": "abc123", "domain": ".oatd.org"},
        {"name": "session_id", "value": "xyz", "domain": "oatd.org"},
    ]
    encrypted = _encrypt_cookies(cookies)
    assert isinstance(encrypted, bytes)
    # Ciphertext must not contain plaintext cookie values.
    assert b"abc123" not in encrypted
    assert b"xyz" not in encrypted

    decrypted = _decrypt_cookies(encrypted)
    assert decrypted == cookies


def test_encrypt_produces_different_ciphertext_each_call():
    """Fernet uses a random IV so two encryptions of the same data differ."""
    cookies = [{"name": "x", "value": "y"}]
    enc1 = _encrypt_cookies(cookies)
    enc2 = _encrypt_cookies(cookies)
    assert enc1 != enc2  # Different ciphertext, same plaintext.


# ---------------------------------------------------------------------------
# get_cookies_for_session: security guards
# ---------------------------------------------------------------------------


def _fake_session(
    *,
    session_id: int = 1,
    user_id: int = 42,
    is_active: bool = True,
    expires_at: datetime | None = None,
    cookies: list[dict] | None = None,
) -> MagicMock:
    cookies = cookies or [{"name": "token", "value": "secret"}]
    s = MagicMock()
    s.id = session_id
    s.user_id = user_id
    s.is_active = is_active
    s.expires_at = expires_at
    s.cookies_encrypted = _encrypt_cookies(cookies)
    return s


@pytest.mark.asyncio
async def test_get_cookies_returns_none_when_session_missing():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    result = await get_cookies_for_session(db, session_id=1, owner_user_id=42)
    assert result is None


@pytest.mark.asyncio
async def test_get_cookies_returns_none_when_wrong_owner():
    """Session exists but belongs to a different user — must return None."""
    session = _fake_session(session_id=1, user_id=99)  # user 99 owns it
    db = AsyncMock()
    db.get = AsyncMock(return_value=session)
    result = await get_cookies_for_session(db, session_id=1, owner_user_id=42)
    assert result is None  # user 42 must not see user 99's cookies


@pytest.mark.asyncio
async def test_get_cookies_returns_none_when_inactive():
    session = _fake_session(is_active=False)
    db = AsyncMock()
    db.get = AsyncMock(return_value=session)
    result = await get_cookies_for_session(db, session_id=1, owner_user_id=42)
    assert result is None


@pytest.mark.asyncio
async def test_get_cookies_returns_none_when_expired():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    session = _fake_session(expires_at=past)
    db = AsyncMock()
    db.get = AsyncMock(return_value=session)
    result = await get_cookies_for_session(db, session_id=1, owner_user_id=42)
    assert result is None


@pytest.mark.asyncio
async def test_get_cookies_returns_decrypted_cookies_when_valid():
    cookies = [{"name": "cf_clearance", "value": "abc", "domain": ".oatd.org"}]
    session = _fake_session(cookies=cookies)
    db = AsyncMock()
    db.get = AsyncMock(return_value=session)
    result = await get_cookies_for_session(db, session_id=1, owner_user_id=42)
    assert result == cookies


@pytest.mark.asyncio
async def test_get_cookies_valid_non_expired():
    future = datetime.now(timezone.utc) + timedelta(days=7)
    cookies = [{"name": "x", "value": "y"}]
    session = _fake_session(expires_at=future, cookies=cookies)
    db = AsyncMock()
    db.get = AsyncMock(return_value=session)
    result = await get_cookies_for_session(db, session_id=1, owner_user_id=42)
    assert result == cookies


# ---------------------------------------------------------------------------
# parse_cookies_raw
# ---------------------------------------------------------------------------


def test_parse_cookies_raw_json_array():
    raw = json.dumps([
        {"name": "cf_clearance", "value": "abc", "domain": ".oatd.org", "path": "/"},
    ])
    cookies = parse_cookies_raw(raw)
    assert cookies[0]["name"] == "cf_clearance"
    assert cookies[0]["value"] == "abc"


def test_parse_cookies_raw_simple_string():
    raw = "session_id=abc123; token=xyz; "
    cookies = parse_cookies_raw(raw)
    assert {"name": "session_id", "value": "abc123"} in cookies
    assert {"name": "token", "value": "xyz"} in cookies


def test_parse_cookies_raw_invalid_json_raises():
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_cookies_raw("[bad json")


def test_parse_cookies_raw_missing_name_raises():
    with pytest.raises(ValueError, match="'name'"):
        parse_cookies_raw(json.dumps([{"value": "abc"}]))


def test_parse_cookies_raw_empty_raises():
    with pytest.raises(ValueError):
        parse_cookies_raw("")


def test_parse_cookies_raw_no_valid_pairs_raises():
    with pytest.raises(ValueError, match="No valid cookies"):
        parse_cookies_raw("   ;  ;  ")
