"""
Background scheduler for periodic tasks.

Handles:
- Watchdog cleanup of stuck tasks

"""

import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.services.watchdog import run_watchdog_once


logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = AsyncIOScheduler(timezone="UTC")


async def _timed_watchdog() -> None:
    """Wrap watchdog execution with scheduler timing logs."""
    logger.debug("scheduler.job_started", extra={"job_name": "watchdog_cleanup"})
    start = time.monotonic()
    await run_watchdog_once()
    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.debug(
        "scheduler.job_completed",
        extra={"job_name": "watchdog_cleanup", "duration_ms": duration_ms},
    )


def configure_scheduler() -> None:
    """Configure all scheduled jobs."""

    # Watchdog: every 60 seconds
    scheduler.add_job(
        _timed_watchdog,
        trigger=IntervalTrigger(seconds=60),
        id="watchdog_cleanup",
        name="Clean up stuck tasks",
        replace_existing=True,
    )

    logger.info("scheduler.configured", extra={"jobs": 1})


def start_scheduler() -> None:
    """
    Start the background scheduler.

    """
    configure_scheduler()
    scheduler.start()
    logger.info("scheduler.started")


def stop_scheduler() -> None:
    """Stop the background scheduler gracefully."""
    scheduler.shutdown(wait=False)
    logger.info("scheduler.stopped")
