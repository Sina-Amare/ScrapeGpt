import asyncio
import logging

from app.core.config import settings
from app.services.email import send_email

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    if not settings.smtp_configured:
        logger.error("SMTP is not configured in .env. Please check your settings.")
        return

    logger.info(f"Using SMTP Host: {settings.SMTP_HOST}")
    logger.info(f"Using SMTP Port: {settings.SMTP_PORT}")
    logger.info(f"Using SMTP Username: {settings.SMTP_USERNAME}")
    logger.info(f"Using SMTP From Email: {settings.SMTP_FROM_EMAIL}")

    # Send a test email to the same address used as the username
    to_email = settings.SMTP_USERNAME
    subject = "ScrapeGPT Enterprise - SMTP Test"
    body = "If you are reading this, your SMTP configuration is working perfectly!"
    html = "<h3>Success!</h3><p>Your ScrapeGPT Enterprise SMTP setup is fully operational.</p>"

    logger.info(f"Sending test email to: {to_email}")
    
    success = await send_email(to_email, subject, body, html)
    
    if success:
        logger.info("Test email sent successfully! Check your inbox.")
    else:
        logger.error("Failed to send test email. Check the logs above for errors.")

if __name__ == "__main__":
    asyncio.run(main())
