"""Password reset by emailed 6-digit code.

Two operations, both designed to avoid leaking whether an email is registered:

* ``request_password_reset`` — always returns without error. If the email maps
  to an active user, it invalidates any prior unconsumed codes, generates a new
  6-digit code, stores only its bcrypt hash, and emails it (or, in development
  without SMTP, logs it for local testing).
* ``confirm_password_reset`` — validates the newest unconsumed code (exists,
  not expired, attempts remaining, matches), then updates the password,
  consumes the code, and stamps ``user.password_changed_at`` so existing
  access/refresh tokens are invalidated.

Codes are low entropy (10^6), so brute force is bounded by three layers: the
per-code attempt cap, the short TTL, and the endpoint rate limit. The stored
hash uses the same bcrypt context as account passwords (slow by design).
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models.password_reset import PasswordResetCode
from app.models.user import User
from app.services.email import send_email
from app.services.email_templates import password_reset_email

logger = logging.getLogger(__name__)

MAX_RESET_ATTEMPTS = 5
CODE_LENGTH = 6


class PasswordResetError(Exception):
    """Raised when a reset confirmation is invalid.

    The endpoint maps every variant to one generic client message so it does
    not reveal whether the email exists or why the code failed.
    """

    def __init__(self, code: str = "INVALID_OR_EXPIRED_CODE") -> None:
        self.code = code
        super().__init__(code)


def _generate_code() -> str:
    """Return a zero-padded 6-digit code from a CSPRNG."""
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def request_password_reset(db: AsyncSession, email: str) -> None:
    """Issue a reset code for ``email`` if it maps to an active user.

    Always returns None and never reveals whether the email is registered.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        logger.info("auth.password_reset_requested", extra={"matched": False})
        return

    now = datetime.now(timezone.utc)

    # Invalidate any prior unconsumed codes for this user (one live code at a time).
    await db.execute(
        update(PasswordResetCode)
        .where(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )

    code = _generate_code()
    db.add(
        PasswordResetCode(
            user_id=user.id,
            code_hash=hash_password(code),
            expires_at=now + timedelta(minutes=settings.PASSWORD_RESET_CODE_TTL_MINUTES),
        )
    )
    await db.commit()

    logger.info("auth.password_reset_requested", extra={"matched": True, "user_id": user.id})

    subject, text, html = password_reset_email(
        code, settings.PASSWORD_RESET_CODE_TTL_MINUTES
    )
    sent = await send_email(user.email, subject, text, html)
    if not sent and settings.is_development:
        # Dev-only fallback so the flow is usable locally without SMTP. The
        # code is logged at WARNING. Never reached in production (where the
        # feature is gated off unless SMTP is configured).
        logger.warning(
            "auth.password_reset_dev_code",
            extra={"user_id": user.id, "dev_code": code},
        )


async def confirm_password_reset(
    db: AsyncSession, email: str, code: str, new_password: str
) -> None:
    """Validate the code and reset the password, or raise ``PasswordResetError``."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        raise PasswordResetError()

    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(PasswordResetCode)
        .where(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.consumed_at.is_(None),
        )
        .order_by(PasswordResetCode.created_at.desc())
    )
    reset_code = result.scalars().first()
    if reset_code is None:
        raise PasswordResetError()

    if _as_utc(reset_code.expires_at) < now:
        raise PasswordResetError()

    if reset_code.attempt_count >= MAX_RESET_ATTEMPTS:
        reset_code.consumed_at = now
        await db.commit()
        raise PasswordResetError("TOO_MANY_ATTEMPTS")

    if not verify_password(code, reset_code.code_hash):
        reset_code.attempt_count += 1
        if reset_code.attempt_count >= MAX_RESET_ATTEMPTS:
            reset_code.consumed_at = now  # burn the code after too many misses
        await db.commit()
        logger.warning("auth.password_reset_failed", extra={"user_id": user.id})
        raise PasswordResetError()

    user.hashed_password = hash_password(new_password)
    user.password_changed_at = now
    reset_code.consumed_at = now
    await db.commit()
    logger.info("auth.password_reset_succeeded", extra={"user_id": user.id})
