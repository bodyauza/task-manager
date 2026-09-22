"""Redlock-мьютекс на шард (src/tasks/crm_shard_lock.py::shard_lock).

В остальных тестах (test_crm_outbox.py) shard_lock подменён заглушкой, поэтому
само тело — получение лока, таймаут, освобождение, закрытие клиента — раньше не
выполнялось ни разу. Здесь redis-клиент заменён самодельным FakeRedis, который
моделирует ровно то, что использует shard_lock: client.lock(...), acquire(),
release(), aclose(). Семантика настоящего redis-py Lock (SET NX PX, токен) этими
тестами НЕ проверяется — для неё нужен живой Redis.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src.database import async_session_maker
from src.task_logic.models import CrmOutbox
from src.tasks.crm_shard_lock import _LOCK_TIMEOUT_SECONDS, shard_lock


class _FakeLockError(Exception):
    """Аналог redis.exceptions.LockError: release() без удержания."""


class FakeRedisServer:
    """«Сервер»: набор удерживаемых имён локов, общий для всех клиентов."""

    def __init__(self) -> None:
        self.held: set[str] = set()
        self.clients: list["FakeRedisClient"] = []


class FakeLock:
    def __init__(self, server: FakeRedisServer, name: str, timeout, blocking_timeout) -> None:
        self.server, self.name = server, name
        self.timeout, self.blocking_timeout = timeout, blocking_timeout
        self.released = False

    async def acquire(self) -> bool:
        # Занят — сразу «истёк blocking_timeout» (реальный Lock ждал бы до 10 с).
        if self.name in self.server.held:
            return False
        self.server.held.add(self.name)
        return True

    async def release(self) -> None:
        if self.name not in self.server.held:
            raise _FakeLockError("Cannot release an unlocked lock")
        self.server.held.discard(self.name)
        self.released = True


class FakeRedisClient:
    def __init__(self, server: FakeRedisServer) -> None:
        self.server = server
        self.locks: list[FakeLock] = []
        self.closed = False
        server.clients.append(self)

    def lock(self, name: str, timeout=None, blocking_timeout=None) -> FakeLock:
        lock = FakeLock(self.server, name, timeout, blocking_timeout)
        self.locks.append(lock)
        return lock

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def redis_server():
    server = FakeRedisServer()
    with patch("src.tasks.crm_shard_lock.redis.from_url", side_effect=lambda url: FakeRedisClient(server)):
        yield server


async def test_shard_lock_holds_named_lock_with_ttl_and_blocking_timeout(redis_server):
    async with shard_lock("shard_2"):
        assert redis_server.held == {"crm_shard_lock:shard_2"}
        lock = redis_server.clients[0].locks[0]
        assert lock.timeout == _LOCK_TIMEOUT_SECONDS           # TTL защищает от упавшего воркера
        assert lock.blocking_timeout == 10                      # ждём не бесконечно

    assert redis_server.held == set()                           # освобождён после блока
    assert redis_server.clients[0].locks[0].released is True


async def test_shard_lock_releases_and_closes_client_when_block_raises(redis_server):
    with pytest.raises(RuntimeError, match="boom"):
        async with shard_lock("shard_0"):
            raise RuntimeError("boom")

    assert redis_server.held == set()                           # лок не «застрял» после сбоя
    assert redis_server.clients[0].closed is True


async def test_shard_lock_raises_timeout_when_shard_is_busy_and_does_not_run_block(redis_server):
    """Два воркера на один шард (ошибка в манифесте деплоя): второй не должен
    работать параллельно — ждёт и получает TimeoutError."""
    redis_server.held.add("crm_shard_lock:shard_1")
    ran = []

    with pytest.raises(TimeoutError, match="shard_1"):
        async with shard_lock("shard_1"):
            ran.append(True)

    assert ran == []                                            # тело не выполнено
    assert redis_server.clients[0].closed is True               # клиент закрыт даже при таймауте
    assert redis_server.clients[0].locks[0].released is False   # чужой лок не освобождался
    assert redis_server.held == {"crm_shard_lock:shard_1"}      # чужое удержание не тронуто


async def test_shard_lock_nested_same_shard_is_rejected(redis_server):
    async with shard_lock("shard_0"):
        with pytest.raises(TimeoutError):
            async with shard_lock("shard_0"):
                pytest.fail("вторая попытка взять тот же шард не должна пройти")
    assert redis_server.held == set()


async def test_shard_lock_different_shards_do_not_block_each_other(redis_server):
    async with shard_lock("shard_0"):
        async with shard_lock("shard_1"):
            assert redis_server.held == {"crm_shard_lock:shard_0", "crm_shard_lock:shard_1"}
    assert redis_server.held == set()


async def test_shard_lock_creates_and_closes_a_new_client_per_call(redis_server):
    """Клиент не хранится между вызовами (module-level singleton): каждая Celery-задача
    выполняется в СВОЁМ asyncio.run(), а соединение redis-py, открытое в одном
    event loop, при переиспользовании в следующем падает «Event loop is closed»
    (баг, найденный живым прогоном docker compose)."""
    async with shard_lock("shard_0"):
        pass
    async with shard_lock("shard_0"):
        pass

    assert len(redis_server.clients) == 2
    assert redis_server.clients[0] is not redis_server.clients[1]
    assert all(client.closed for client in redis_server.clients)


# ── Использование в обработчике outbox ───────────────────────────────────────

async def _pending_update_row(shard: str = "shard_1") -> int:
    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="update", status="pending", shard=shard,
            payload={"crm_task_id": 42, "title": "X", "description": None, "completed": None, "project": None},
        )
        session.add(row)
        await session.commit()
        return row.id


async def test_outbox_handler_runs_inside_the_row_shard_lock(redis_server):
    """CRM-вызов выполняется под локом шарда СТРОКИ (crm_shard_lock:<row.shard>) и лок
    освобождается после обработки."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    outbox_id = await _pending_update_row(shard="shard_3")
    held_during_crm_call: list[set[str]] = []

    fake_manager = AsyncMock()

    async def _update_task(**kwargs):
        held_during_crm_call.append(set(redis_server.held))

    fake_manager.update_task.side_effect = _update_task
    with patch("src.tasks.crm_outbox_tasks.acquire_slot", AsyncMock(return_value=True)), \
         patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_manager):
        await _process_outbox_row_async(outbox_id)

    assert held_during_crm_call == [{"crm_shard_lock:shard_3"}]
    assert redis_server.held == set()
    async with async_session_maker() as session:
        assert (await session.get(CrmOutbox, outbox_id)).status == "done"


async def test_outbox_row_stays_pending_and_crm_untouched_when_shard_lock_is_busy(redis_server):
    """Другой воркер уже держит шард: строка не уходит в CRM и остаётся pending
    (её позже подберёт reconcile), причина сохранена в last_error."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    outbox_id = await _pending_update_row(shard="shard_1")
    redis_server.held.add("crm_shard_lock:shard_1")
    fake_manager = AsyncMock()

    with patch("src.tasks.crm_outbox_tasks.acquire_slot", AsyncMock(return_value=True)), \
         patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_manager):
        await _process_outbox_row_async(outbox_id)

    fake_manager.update_task.assert_not_called()
    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
    assert row.status == "pending"
    assert "shard_1" in row.last_error and row.last_error.startswith("TimeoutError")
    assert redis_server.held == {"crm_shard_lock:shard_1"}      # чужой лок не тронут
