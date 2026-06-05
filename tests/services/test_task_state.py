import pytest

import app.services.task_state as task_state
from app.models.scrape_task import ScrapeTask, TaskState


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, task):
        self.task = task
        self.executions = 0
        self.flushes = 0
        self.refreshes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def begin(self):
        return FakeTransaction()

    async def get(self, _model, _task_id):
        return self.task

    async def execute(self, _statement, _params=None):
        self.executions += 1
        raise AssertionError("unexpected execute")

    async def flush(self):
        self.flushes += 1

    async def refresh(self, _task):
        self.refreshes += 1


def _task(state: TaskState, user_id: int = 1) -> ScrapeTask:
    return ScrapeTask(
        id=1,
        user_id=user_id,
        state=state,
        url="https://example.com",
    )


@pytest.mark.asyncio
async def test_transition_to_failed_skips_when_expected_state_changed(monkeypatch):
    task = _task(TaskState.SCRAPING)
    session = FakeSession(task)
    monkeypatch.setattr(task_state, "async_session_factory", lambda: session)

    result = await task_state.transition_to_failed(
        task_id=task.id,
        error_message="Watchdog: Pipeline did not start within 3m",
        expected_states={TaskState.PERMISSION_GRANTED},
    )

    assert result.success is False
    assert result.error == "Task state changed concurrently"
    assert task.state == TaskState.SCRAPING
    assert task.error is None


@pytest.mark.asyncio
async def test_transition_to_failed_fails_when_expected_state_matches(monkeypatch):
    task = _task(TaskState.SCRAPING)
    session = FakeSession(task)
    monkeypatch.setattr(task_state, "async_session_factory", lambda: session)

    result = await task_state.transition_to_failed(
        task_id=task.id,
        error_message="Watchdog: Stuck in SCRAPING for >5m",
        expected_states={TaskState.SCRAPING},
    )

    assert result.success is True
    assert task.state == TaskState.FAILED
    assert task.error == "Watchdog: Stuck in SCRAPING for >5m"


@pytest.mark.asyncio
async def test_transition_to_llm_processing_ownership_mismatch_does_not_mutate_task(
    monkeypatch,
):
    task = _task(TaskState.SCRAPED, user_id=1)
    session = FakeSession(task)
    monkeypatch.setattr(task_state, "async_session_factory", lambda: session)

    result = await task_state.transition_to_llm_processing(task_id=task.id, user_id=2)

    assert result.success is False
    assert result.error == "Task ownership mismatch"
    assert task.state == TaskState.SCRAPED
    assert task.error is None
    assert session.executions == 0
    assert session.flushes == 0
