import asyncio
import logging

from app.core.config import settings
from app.core.logging_config import configure_logging
from app.services.email import send_email

# Use the project logging setup (formatter + SecretRedactingFilter) instead of
# logging.basicConfig, so this entrypoint follows the same redaction rules as
# the app. configure_logging() is idempotent.
configure_logging()
logger = logging.getLogger(__name__)

async def main():
    if not settings.smtp_configured:
        logger.error("SMTP is not configured in .env. Please check your settings.")
        return

    # Log only non-sensitive connection config. Email addresses (username,
    # from-address, recipient) are PII and are never logged — see the rule in
    # app/services/email.py ("Recipient addresses are not logged").
    logger.info(f"Using SMTP Host: {settings.SMTP_HOST}")
    logger.info(f"Using SMTP Port: {settings.SMTP_PORT}")

    # Send a test email to the same address used as the username.
    to_email = settings.SMTP_USERNAME
    subject = "ScrapeGPT Enterprise - SMTP Test"
    body = "If you are reading this, your SMTP configuration is working perfectly!"
    html = "<h3>Success!</h3><p>Your ScrapeGPT Enterprise SMTP setup is fully operational.</p>"

    logger.info("Sending test email...")

    success = await send_email(to_email, subject, body, html)
    
    if success:
        logger.info("Test email sent successfully! Check your inbox.")
    else:
        logger.error("Failed to send test email. Check the logs above for errors.")

if __name__ == "__main__":
    asyncio.run(main())
