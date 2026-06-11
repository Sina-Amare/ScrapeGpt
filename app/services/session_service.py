"""Browser session service: encrypted cookie storage for user-owned sessions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.browser_session import BrowserSession
from app.models.user import User

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    return Fernet(settings.PROVIDER_KEY_ENCRYPTION_SECRET.encode("utf-8"))


def _encrypt_cookies(cookies: list[dict]) -> bytes:
    return _fernet().encrypt(json.dumps(cookies).encode("utf-8"))


def _decrypt_cookies(data: bytes) -> list[dict]:
    return json.loads(_fernet().decrypt(data).decode("utf-8"))


def _bare_domain(url_or_domain: str) -> str:
    """Return bare hostname from a URL or already-bare domain string."""
    if "://" in url_or_domain:
        return urlparse(url_or_domain).hostname or url_or_domain
    return url_or_domain.lstrip(".")


async def create_session(
    db: AsyncSession,
    user: User,
    *,
    name: str,
    domain: str,
    cookies: list[dict],
    user_agent: str | None = None,
    expires_at: datetime | None = None,
) -> BrowserSession:
    bare = _bare_domain(domain)
    session = BrowserSession(
        user_id=user.id,
        name=name,
        domain=bare,
        cookies_encrypted=_encrypt_cookies(cookies),
        user_agent=user_agent,
        expires_at=expires_at,
        is_active=True,
    )
    db.add(session)
    await db.flush()
    logger.info(
        "browser_session.created",
        extra={
            "session_id": session.id,
            "user_id": user.id,
            "domain": bare,
        },
    )
    return session


async def list_sessions(
    db: AsyncSession, user: User
) -> list[BrowserSession]:
    result = await db.execute(
        select(BrowserSession)
        .where(BrowserSession.user_id == user.id)
        .order_by(BrowserSession.created_at.desc())
    )
    return list(result.scalars().all())


async def get_session(
    db: AsyncSession, user: User, session_id: int
) -> BrowserSession | None:
    session = await db.get(BrowserSession, session_id)
    if session is None or session.user_id != user.id:
        return None
    return session


async def delete_session(
    db: AsyncSession, user: User, session_id: int
) -> bool:
    session = await get_session(db, user, session_id)
    if session is None:
        return False
    await db.delete(session)
    logger.info(
        "browser_session.deleted",
        extra={"session_id": session_id, "user_id": user.id},
    )
    return True


async def get_cookies_for_session(
    db: AsyncSession,
    session_id: int,
    *,
    owner_user_id: int,
) -> list[dict] | None:
    """Decrypt cookies for use in a Playwright context.

    Returns None when the session does not exist, belongs to a different user,
    or is inactive/expired. Never logs cookie values.
    """
    session = await db.get(BrowserSession, session_id)
    if session is None or session.user_id != owner_user_id:
        return None
    if not session.is_active:
        return None
    if session.expires_at is not None:
        now = datetime.now(timezone.utc)
        if session.expires_at.tzinfo is None:
            # Treat naive datetimes as UTC.
            from datetime import timezone as _tz
            aware = session.expires_at.replace(tzinfo=_tz.utc)
        else:
            aware = session.expires_at
        if now > aware:
            logger.info(
                "browser_session.expired",
                extra={"session_id": session_id, "user_id": owner_user_id},
            )
            return None
    try:
        return _decrypt_cookies(session.cookies_encrypted)
    except Exception:
        logger.warning(
            "browser_session.decrypt_failed",
            extra={"session_id": session_id, "user_id": owner_user_id},
        )
        return None
