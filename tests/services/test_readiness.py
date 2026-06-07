import asyncio
import time

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

import app.services.readiness as readiness
from app.services.readiness import check_db_ready


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, effects=None):
        self.effects = list(effects or [])
        self.calls = 0
        self.statements = []

    async def execute(self, statement):
        self.calls += 1
        self.statements.append(str(statement))
        if self.effects:
            effect = self.effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
            return effect
        return FakeResult(1)


@pytest.mark.asyncio
async def test_check_db_ready_healthy_returns_ok():
    session = FakeSession()

    result = await check_db_ready(session, timeout_seconds=1.0)

    assert result.ready is True
    assert result.code == "ok"
    assert session.calls == 7  # SELECT 1 + 5 table probes + 1 new (jobs + analysis_cache)
    assert any("provider_configs" in statement for statement in session.statements)
    assert any("jobs" in statement for statement in session.statements)
    assert any("analysis_cache" in statement for statement in session.statements)
    assert not any("system_state" in statement for statement in session.statements)


@pytest.mark.asyncio
async def test_check_db_ready_operational_error_returns_db_unreachable():
    session = FakeSession(
        effects=[OperationalError("SELECT 1", None, Exception("db unavailable"))]
    )

    result = await check_db_ready(session, timeout_seconds=1.0)

    assert result.ready is False
    assert result.code == "db_unreachable"


@pytest.mark.asyncio
async def test_check_db_ready_programming_error_returns_schema_incompatible():
    session = FakeSession(
        effects=[
            FakeResult(1),
            FakeResult("004"),
            ProgrammingError("SELECT ... FROM users", None, Exception("missing col")),
        ]
    )

    result = await check_db_ready(session, timeout_seconds=1.0)

    assert result.ready is False
    assert result.code == "schema_incompatible"


@pytest.mark.asyncio
async def test_check_db_ready_missing_alembic_row_returns_schema_incompatible():
    session = FakeSession(effects=[FakeResult(1), FakeResult(None)])

    result = await check_db_ready(session, timeout_seconds=1.0)

    assert result.ready is False
    assert result.code == "schema_incompatible"


@pytest.mark.asyncio
async def test_check_db_ready_generic_failure_returns_query_failed():
    session = FakeSession(effects=[RuntimeError("unexpected failure")])

    result = await check_db_ready(session, timeout_seconds=1.0)

    assert result.ready is False
    assert result.code == "query_failed"


@pytest.mark.asyncio
async def test_check_db_ready_timeout_returns_timeout_and_is_bounded(monkeypatch):
    async def slow_probe(_db):
        await asyncio.sleep(0.2)

    monkeypatch.setattr(readiness, "_run_probe", slow_probe)

    started = time.perf_counter()
    result = await check_db_ready(FakeSession(), timeout_seconds=0.05)
    elapsed = time.perf_counter() - started

    assert result.ready is False
    assert result.code == "timeout"
    assert elapsed < 0.3


@pytest.mark.asyncio
async def test_check_db_ready_sanitizes_exception_details():
    secret = "postgresql://user:supersecret@db.internal/prod password=topsecret"
    session = FakeSession(effects=[RuntimeError(secret)])

    result = await check_db_ready(session, timeout_seconds=1.0)

    assert result.ready is False
    assert result.code == "query_failed"
    assert secret not in str(result)
    assert secret not in repr(result)
