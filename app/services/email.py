"""Outbound email via stdlib smtplib.

Deliberately small: one ``send_email`` helper, used by the password-reset flow.
The blocking SMTP call runs in a worker thread so it never blocks the event
loop, and the function never raises to its caller — failures are logged and
reported via the boolean return, so the reset endpoint can keep its generic,
enumeration-safe response regardless of mail-server health.

No new dependency: uses ``smtplib`` + ``email.message`` from the standard
library. Recipient addresses are not logged (PII).
"""

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_sync(to_email: str, subject: str, body: str, html: str | None = None) -> None:
    message = EmailMessage()
    message["From"] = settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME
    message["To"] = to_email
    message["Subject"] = subject
    # Plain-text part is the fallback; the HTML alternative renders when supported.
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
        if settings.SMTP_USE_TLS:
            smtp.starttls()
        if settings.SMTP_USERNAME:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        smtp.send_message(message)


async def send_email(
    to_email: str, subject: str, body: str, html: str | None = None
) -> bool:
    """Send an email (optionally with an HTML part). Returns True on success,
    False on failure. Never raises."""
    if not settings.smtp_configured:
        logger.warning("email.skipped", extra={"reason": "smtp_unconfigured"})
        return False
    try:
        await asyncio.to_thread(_send_sync, to_email, subject, body, html)
        logger.info("email.sent")
        return True
    except Exception as exc:
        logger.error("email.send_failed", extra={"error_type": type(exc).__name__})
        return False
