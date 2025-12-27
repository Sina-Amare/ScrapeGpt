"""
Task executor with always-finalize guarantee.

Orchestrates the scrape pipeline with exception handling.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import async_session_factory
from app.models.scrape_task import ScrapeTask, TaskState
from app.services.scraper import scrape_url, ScrapeError
from app.services.llm_processor import process_with_llm, LLMError
from app.services.task_state import (
    transition_to_scraping,
    transition_to_scraped,
    transition_to_llm_processing,
    transition_to_completed,
    transition_to_failed,
)


logger = logging.getLogger(__name__)


async def execute_scrape_pipeline(task_id: int, user_id: int) -> None:
    """
    Execute the full scrape pipeline for a task.

    Always-finalize guarantee: Every execution path ends in
    COMPLETED or FAILED state.

    This runs as a background task, separate from HTTP request.

    Args:
        task_id: ID of task to process
        user_id: ID of task owner (for credit deduction)
    """
    logger.info(
        "pipeline.started",
        extra={"task_id": task_id, "user_id": user_id},
    )

    try:
        async with async_session_factory() as db:
            # Get URL from task
            task = await db.get(ScrapeTask, task_id)
            if not task:
                logger.error("pipeline.task_not_found", extra={"task_id": task_id})
                return

            url = task.url

            # Phase 1: Transition to SCRAPING
            result = await transition_to_scraping(task_id, db)
            if not result.success:
                logger.error(
                    "pipeline.transition_failed",
                    extra={"task_id": task_id, "error": result.error},
                )
                return

            # Phase 2: Scrape URL
            try:
                content = await scrape_url(url)
            except ScrapeError as e:
                await transition_to_failed(task_id, f"Scraping failed: {e.message}", db)
                return

            # Phase 3: Transition to SCRAPED
            result = await transition_to_scraped(task_id, content, db)
            if not result.success:
                await transition_to_failed(task_id, result.error, db)
                return

            # Phase 4: Transition to LLM_PROCESSING (with credit deduction)
            result = await transition_to_llm_processing(task_id, user_id, db)
            if not result.success:
                # Already marked FAILED in transition if no credits
                return

            # Phase 5: LLM Processing
            try:
                llm_result = await process_with_llm(content)
            except LLMError as e:
                await transition_to_failed(
                    task_id,
                    f"LLM processing failed: {str(e)}",
                    db,
                )
                return

            # Phase 6: Complete
            result = await transition_to_completed(task_id, llm_result, db)
            if not result.success:
                await transition_to_failed(task_id, result.error, db)
                return

            logger.info("pipeline.completed", extra={"task_id": task_id})

    except Exception as e:
        # Catch-all: ensure task is marked FAILED
        logger.exception(
            "pipeline.unexpected_error",
            extra={"task_id": task_id, "error": str(e)},
        )
        try:
            async with async_session_factory() as db:
                await transition_to_failed(task_id, f"Unexpected error: {str(e)}", db)
        except Exception:
            logger.exception("pipeline.failed_to_mark_failed", extra={"task_id": task_id})
