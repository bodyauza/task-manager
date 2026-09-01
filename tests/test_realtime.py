"""Юнит-тесты src.realtime: ConnectionManager (транспорт) и broadcast_task_event
(форма доменных событий).

До выноса в отдельный модуль эта логика жила внутри routers/tasks.py вперемешку
с CRUD-обработчиками и не имела отдельных тестов вовсе. Теперь ConnectionManager
и broadcast_task_event — независимые единицы, которые тестируются без поднятия
приложения, БД или реального WS-соединения: ConnectionManager — через фейковый
объект вместо starlette.WebSocket, а broadcast_task_event — через фейковую
реализацию протокола Broadcaster (см. DIP в src/realtime/events.py).
"""

import json

from src.realtime.events import broadcast_task_event
from src.realtime.connection_manager import ConnectionManager


class FakeWebSocket:
    """Минимальная замена starlette.WebSocket для тестов ConnectionManager."""

    def __init__(self, fail: bool = False):
        self.sent: list[str] = []
        self.fail = fail  # True имитирует разорванное соединение

    async def send_text(self, data: str) -> None:
        if self.fail:
            raise RuntimeError("connection closed")
        self.sent.append(data)


class FakeBroadcaster:
    """Реализация протокола Broadcaster для проверки broadcast_task_event
    без обращения к ConnectionManager — демонстрирует, что events.py
    зависит от абстракции, а не от конкретного класса (DIP)."""

    def __init__(self):
        self.calls: list[tuple[dict, int | None]] = []

    async def broadcast(self, payload: dict, exclude_user_id: int | None = None) -> None:
        self.calls.append((payload, exclude_user_id))


# ── ConnectionManager: регистрация ──────────────────────────────────────────

async def test_register_stores_connection():
    manager = ConnectionManager()
    ws = FakeWebSocket()

    manager.register(1, ws, "alice@example.com")

    assert manager.get(1) == {ws}
    assert manager.get_email(1) == "alice@example.com"


async def test_register_adds_second_connection_for_same_user():
    """Несколько вкладок/устройств одного пользователя — оба соединения
    остаются зарегистрированными одновременно, ни одно не вытесняется."""
    manager = ConnectionManager()
    ws_tab1, ws_tab2 = FakeWebSocket(), FakeWebSocket()

    manager.register(1, ws_tab1, "alice@example.com")
    manager.register(1, ws_tab2, "alice@example.com")  # вторая вкладка

    assert manager.get(1) == {ws_tab1, ws_tab2}
    assert manager.get_email(1) == "alice@example.com"


async def test_register_same_websocket_twice_is_idempotent():
    manager = ConnectionManager()
    ws = FakeWebSocket()

    manager.register(1, ws, "alice@example.com")
    manager.register(1, ws, "alice@example.com")

    assert manager.get(1) == {ws}


def test_get_returns_none_for_unknown_user():
    manager = ConnectionManager()
    assert manager.get(999) is None


def test_get_email_returns_none_for_unknown_user():
    manager = ConnectionManager()
    assert manager.get_email(999) is None


# ── ConnectionManager: снятие с регистрации ─────────────────────────────────

async def test_unregister_removes_matching_websocket():
    manager = ConnectionManager()
    ws = FakeWebSocket()
    manager.register(1, ws, "alice@example.com")

    manager.unregister(1, ws)

    assert manager.get(1) is None
    assert manager.get_email(1) is None  # email тоже очищен — соединений не осталось


async def test_unregister_removes_only_specified_connection():
    """Снятие с регистрации одной вкладки не должно трогать остальные
    активные соединения того же пользователя."""
    manager = ConnectionManager()
    ws_tab1, ws_tab2 = FakeWebSocket(), FakeWebSocket()
    manager.register(1, ws_tab1, "alice@example.com")
    manager.register(1, ws_tab2, "alice@example.com")

    manager.unregister(1, ws_tab1)  # закрылась только первая вкладка

    assert manager.get(1) == {ws_tab2}
    assert manager.get_email(1) == "alice@example.com"  # email жив, пока жива хоть одна вкладка


async def test_unregister_unknown_websocket_is_noop():
    """discard() не бросает исключение для сокета, которого уже нет в наборе —
    безопасно при повторном/запоздалом вызове (например, после того как
    broadcast уже удалил то же мёртвое соединение)."""
    manager = ConnectionManager()
    ws_registered, ws_unknown = FakeWebSocket(), FakeWebSocket()
    manager.register(1, ws_registered, "alice@example.com")

    manager.unregister(1, ws_unknown)

    assert manager.get(1) == {ws_registered}


# ── ConnectionManager: рассылка ──────────────────────────────────────────────

async def test_broadcast_sends_to_all_connected_users():
    manager = ConnectionManager()
    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    manager.register(1, ws1, "alice@example.com")
    manager.register(2, ws2, "bob@example.com")

    await manager.broadcast({"type": "ping"})

    assert json.loads(ws1.sent[0]) == {"type": "ping"}
    assert json.loads(ws2.sent[0]) == {"type": "ping"}


async def test_broadcast_sends_to_every_tab_of_same_user():
    """Ключевой сценарий этого фикса: у одного пользователя открыто
    несколько вкладок — событие должно дойти до каждой из них."""
    manager = ConnectionManager()
    ws_tab1, ws_tab2 = FakeWebSocket(), FakeWebSocket()
    manager.register(1, ws_tab1, "alice@example.com")
    manager.register(1, ws_tab2, "alice@example.com")

    await manager.broadcast({"type": "ping"})

    assert json.loads(ws_tab1.sent[0]) == {"type": "ping"}
    assert json.loads(ws_tab2.sent[0]) == {"type": "ping"}


async def test_broadcast_excludes_given_user_id():
    manager = ConnectionManager()
    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    manager.register(1, ws1, "alice@example.com")
    manager.register(2, ws2, "bob@example.com")

    await manager.broadcast({"type": "ping"}, exclude_user_id=1)

    assert ws1.sent == []
    assert json.loads(ws2.sent[0]) == {"type": "ping"}


async def test_broadcast_excludes_all_tabs_of_excluded_user():
    """exclude_user_id исключает пользователя целиком — все его вкладки,
    а не только одну из них."""
    manager = ConnectionManager()
    ws_tab1, ws_tab2, ws_other = FakeWebSocket(), FakeWebSocket(), FakeWebSocket()
    manager.register(1, ws_tab1, "alice@example.com")
    manager.register(1, ws_tab2, "alice@example.com")
    manager.register(2, ws_other, "bob@example.com")

    await manager.broadcast({"type": "ping"}, exclude_user_id=1)

    assert ws_tab1.sent == []
    assert ws_tab2.sent == []
    assert json.loads(ws_other.sent[0]) == {"type": "ping"}


async def test_broadcast_removes_dead_connection_after_send_failure():
    manager = ConnectionManager()
    dead, alive = FakeWebSocket(fail=True), FakeWebSocket()
    manager.register(1, dead, "dead@example.com")
    manager.register(2, alive, "alive@example.com")

    await manager.broadcast({"type": "ping"})

    assert manager.get(1) is None        # мёртвое соединение удалено из реестра
    assert manager.get(2) is not None    # живое соединение не затронуто
    assert json.loads(alive.sent[0]) == {"type": "ping"}


async def test_broadcast_removes_only_dead_tab_keeps_other_tab_alive():
    """Если у пользователя из двух вкладок одна отвалилась — вторая должна
    остаться в реестре и продолжать получать события."""
    manager = ConnectionManager()
    dead_tab, alive_tab = FakeWebSocket(fail=True), FakeWebSocket()
    manager.register(1, dead_tab, "alice@example.com")
    manager.register(1, alive_tab, "alice@example.com")

    await manager.broadcast({"type": "ping"})

    assert manager.get(1) == {alive_tab}
    assert json.loads(alive_tab.sent[0]) == {"type": "ping"}


# ── broadcast_task_event: форма payload и DIP-подмена broadcaster ──────────

async def test_broadcast_task_event_builds_expected_payload():
    fake = FakeBroadcaster()

    await broadcast_task_event(
        "task_updated", "Задача №1",
        exclude_user_id=7, sender_email="alice@example.com",
        broadcaster=fake,
        task_id=87,  # попадает в payload через **extra
    )

    assert len(fake.calls) == 1
    payload, exclude_user_id = fake.calls[0]
    assert payload == {
        "type": "task_updated",
        "title": "Задача №1",
        "sender": "alice@example.com",
        "task_id": 87,
    }
    assert exclude_user_id == 7


async def test_broadcast_task_event_defaults_are_empty():
    fake = FakeBroadcaster()

    await broadcast_task_event("task_created", "Задача №2", broadcaster=fake)

    payload, exclude_user_id = fake.calls[0]
    assert payload == {"type": "task_created", "title": "Задача №2", "sender": ""}
    assert exclude_user_id is None


async def test_broadcast_task_event_uses_connection_manager_by_default():
    """Без явного broadcaster= функция реально доставляет сообщение через
    process-wide connection_manager — проверяем интеграцию с дефолтным DI."""
    from src.realtime.connection_manager import connection_manager

    ws = FakeWebSocket()
    connection_manager.register(42, ws, "carol@example.com")
    try:
        await broadcast_task_event("task_deleted", "Задача №3", sender_email="carol@example.com")
    finally:
        connection_manager.unregister(42, ws)

    assert json.loads(ws.sent[0]) == {
        "type": "task_deleted",
        "title": "Задача №3",
        "sender": "carol@example.com",
    }
