"""Запуск и остановка приложения: src/main.py (create_initial_roles, lifespan) и
фоновая подписка ConnectionManager на Redis Pub/Sub (start_listening/
stop_listening/_pubsub_loop).

httpx.ASGITransport не запускает lifespan, а conftest сам вставляет роли перед
каждым тестом, поэтому раньше этот код не выполнялся ни разу: идемпотентность
создания ролей при старте, порядок «роли → подписка → …→ отписка → закрытие
CRM-клиента» и сама подписка на канал были не проверены.
"""

import asyncio
import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select, text
from starlette.testclient import TestClient

from src.auth.user_models import Role, User
from src.database import async_session_maker
from src.main import app, create_initial_roles, lifespan
from src.realtime.connection_manager import ConnectionManager, connection_manager


async def _clear_roles() -> None:
    async with async_session_maker() as session:
        await session.execute(text("TRUNCATE TABLE role RESTART IDENTITY CASCADE"))
        await session.commit()


async def _roles() -> dict[int, str]:
    async with async_session_maker() as session:
        return {r.id: r.name for r in (await session.execute(select(Role))).scalars().all()}


# ── create_initial_roles ─────────────────────────────────────────────────────

async def test_create_initial_roles_creates_user_and_admin_on_empty_table():
    await _clear_roles()
    assert await _roles() == {}

    await create_initial_roles()

    assert await _roles() == {1: "user", 2: "admin"}


async def test_create_initial_roles_is_idempotent(caplog):
    """Повторный запуск приложения не дублирует записи и не падает. Ошибки внутри
    create_initial_roles глотаются (только лог), поэтому одной проверки итогового
    состояния мало: попытка повторной вставки тоже оставила бы 2 роли — отсутствие
    записи об ошибке и «уже существуют» в логе доказывает, что вставки не было."""
    await _clear_roles()
    await create_initial_roles()

    with caplog.at_level(logging.INFO):
        await create_initial_roles()
        await create_initial_roles()

    assert await _roles() == {1: "user", 2: "admin"}
    assert "Ошибка" not in caplog.text
    assert caplog.text.count("Базовые роли уже существуют") == 2


async def test_create_initial_roles_adds_only_missing_role_and_keeps_existing():
    await _clear_roles()
    async with async_session_maker() as session:
        session.add(Role(id=1, name="user"))
        await session.commit()

    await create_initial_roles()

    assert await _roles() == {1: "user", 2: "admin"}


async def test_create_initial_roles_does_not_overwrite_existing_role_names():
    """Уже существующая запись с тем же id не перезаписывается (даже с другим именем)."""
    await _clear_roles()
    async with async_session_maker() as session:
        session.add_all([Role(id=1, name="custom_user"), Role(id=2, name="custom_admin")])
        await session.commit()

    await create_initial_roles()

    assert await _roles() == {1: "custom_user", 2: "custom_admin"}


async def test_create_initial_roles_logs_error_instead_of_crashing_startup(caplog):
    """Ошибка БД при создании ролей логируется, но не роняет запуск приложения
    (текущее поведение: except Exception в create_initial_roles)."""
    def _broken_session_maker():
        raise ConnectionError("db down")

    with patch("src.main.async_session_maker", _broken_session_maker), caplog.at_level(logging.ERROR):
        await create_initial_roles()      # не должно бросить

    assert "Ошибка при создании базовых ролей" in caplog.text
    assert "db down" in caplog.text


async def test_registration_fails_clearly_when_roles_were_never_created(client, mock_smtp):
    """Без ролей регистрация не может выдать роль по умолчанию — это и есть причина,
    по которой create_initial_roles обязана отработать при старте."""
    await _clear_roles()
    await client.post("/auth/register/request-code", json={"email": "norole@example.com"})
    code = mock_smtp["norole@example.com"]
    await client.post("/auth/register/verify-code", json={"email": "norole@example.com", "code": code})

    with pytest.raises(RuntimeError, match="Роль 'user' не найдена"):
        await client.post("/auth/register/complete", json={
            "firstname": "No", "lastname": "Role", "password": "Password1!",
        })

    async with async_session_maker() as session:
        assert (await session.execute(select(User))).scalars().all() == []   # пользователь не создан

    await create_initial_roles()     # роль появляется только благодаря create_initial_roles
    assert await _roles() == {1: "user", 2: "admin"}


# ── lifespan ─────────────────────────────────────────────────────────────────

async def test_lifespan_orders_startup_and_shutdown_steps():
    calls: list[str] = []

    async def _roles_step():
        calls.append("roles")

    async def _stop():
        calls.append("stop_listening")

    async def _close_http():
        calls.append("close_http_client")

    with patch("src.main.create_initial_roles", _roles_step), \
         patch.object(connection_manager, "start_listening", lambda: calls.append("start_listening")), \
         patch.object(connection_manager, "stop_listening", _stop), \
         patch("src.main.aclose_http_client", _close_http):
        async with lifespan(app):
            calls.append("running")

    assert calls == ["roles", "start_listening", "running", "stop_listening", "close_http_client"]


def test_app_is_wired_to_run_lifespan_on_startup_and_shutdown():
    """Настоящий запуск через ASGI lifespan (TestClient как контекстный менеджер):
    подтверждает, что create_app() подключил именно lifespan."""
    calls: list[str] = []

    async def _roles_step():
        calls.append("roles")

    async def _stop():
        calls.append("stop_listening")

    async def _close_http():
        calls.append("close_http_client")

    with patch("src.main.create_initial_roles", _roles_step), \
         patch.object(connection_manager, "start_listening", lambda: calls.append("start_listening")), \
         patch.object(connection_manager, "stop_listening", _stop), \
         patch("src.main.aclose_http_client", _close_http):
        with TestClient(app):
            assert calls == ["roles", "start_listening"]      # старт выполнен до приёма запросов
        assert calls == ["roles", "start_listening", "stop_listening", "close_http_client"]


# ── ConnectionManager: подписка на Redis Pub/Sub ─────────────────────────────

class FakePubSub:
    """Модель redis.asyncio.PubSub: подписка, поток сообщений, отписка, закрытие."""

    def __init__(self, messages: list[dict]) -> None:
        self.messages = messages
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False
        self.ready = asyncio.Event()

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)
        self.ready.set()

    async def listen(self):
        for message in self.messages:
            yield message
        await asyncio.Event().wait()      # дальше сообщений нет — ждём отмены задачи

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def aclose(self) -> None:
        self.closed = True


class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


def _redis_with(pubsub: FakePubSub) -> MagicMock:
    client = MagicMock()
    client.pubsub.return_value = pubsub        # pubsub() у redis-py — обычный (не async) метод
    return client


async def _wait_until(predicate, timeout: float = 3.0) -> None:
    waited = 0.0
    while not predicate() and waited < timeout:
        await asyncio.sleep(0.02)
        waited += 0.02
    assert predicate(), "условие не выполнилось за отведённое время"


async def test_start_listening_subscribes_delivers_messages_and_stop_unsubscribes():
    manager = ConnectionManager()
    socket = _FakeSocket()
    manager.register(1, socket, "alice@example.com")
    foreign = {"type": "message", "data": json.dumps(
        {"origin": "another-worker", "payload": {"type": "ping"}, "exclude_user_id": None}
    )}
    own = {"type": "message", "data": json.dumps(
        {"origin": manager._origin, "payload": {"type": "own-echo"}, "exclude_user_id": None}
    )}
    pubsub = FakePubSub([{"type": "subscribe", "data": 1}, foreign, own])

    with patch("src.realtime.connection_manager._get_redis", return_value=_redis_with(pubsub)):
        manager.start_listening()
        await asyncio.wait_for(pubsub.ready.wait(), 3)
        await _wait_until(lambda: len(socket.sent) >= 1)
        await manager.stop_listening()

    assert pubsub.subscribed == ["task_events"]
    # Событие другого воркера доставлено, своё эхо и служебное «subscribe» — нет.
    assert [json.loads(frame) for frame in socket.sent] == [{"type": "ping"}]
    assert pubsub.unsubscribed == ["task_events"]      # корректная отписка при остановке
    assert pubsub.closed is True
    assert manager._pubsub_task is None


async def test_start_listening_is_idempotent():
    manager = ConnectionManager()
    pubsub = FakePubSub([])

    with patch("src.realtime.connection_manager._get_redis", return_value=_redis_with(pubsub)):
        manager.start_listening()
        first_task = manager._pubsub_task
        manager.start_listening()                       # повторный вызов не создаёт вторую подписку
        assert manager._pubsub_task is first_task
        await asyncio.wait_for(pubsub.ready.wait(), 3)
        await manager.stop_listening()

    assert pubsub.subscribed == ["task_events"]          # подписка одна


async def test_stop_listening_without_start_is_a_noop():
    manager = ConnectionManager()
    await manager.stop_listening()                       # не должно бросить
    assert manager._pubsub_task is None
