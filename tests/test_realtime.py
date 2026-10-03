"""Юнит-тесты src.realtime: ConnectionManager (транспорт) и broadcast_task_event (форма доменных событий).

Тестируются без приложения, БД и реального WS: ConnectionManager — через фейковый сокет, broadcast_task_event — через
фейковую реализацию протокола Broadcaster.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.realtime.events import broadcast_task_event
from src.realtime.connection_manager import ConnectionManager, connection_manager


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
    """Реализация протокола Broadcaster для проверки broadcast_task_event без ConnectionManager."""

    def __init__(self):
        self.calls: list[tuple[dict, int | None]] = []

    async def broadcast(self, payload: dict, exclude_user_id: int | None = None) -> None:
        self.calls.append((payload, exclude_user_id))


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
    """discard() не бросает исключение для отсутствующего сокета — безопасно при повторном вызове."""
    manager = ConnectionManager()
    ws_registered, ws_unknown = FakeWebSocket(), FakeWebSocket()
    manager.register(1, ws_registered, "alice@example.com")

    manager.unregister(1, ws_unknown)

    assert manager.get(1) == {ws_registered}


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


# ConnectionManager: Redis Pub/Sub (рассылка между воркерами).
#
# mock_realtime_redis (autouse) патчит _get_redis; как параметр он запрашивается, когда нужна ссылка на фейковый клиент (publish).

async def test_broadcast_publishes_to_redis_with_origin_and_payload(mock_realtime_redis):
    manager = ConnectionManager()

    await manager.broadcast({"type": "ping"}, exclude_user_id=7)

    mock_realtime_redis.publish.assert_called_once()
    channel, message = mock_realtime_redis.publish.call_args.args
    assert channel == "task_events"
    assert json.loads(message) == {
        "origin": manager._origin,
        "payload": {"type": "ping"},
        "exclude_user_id": 7,
    }


async def test_broadcast_survives_redis_publish_failure():
    """Локальная доставка уже произошла: сбой Redis не должен ронять broadcast() у инициатора."""
    manager = ConnectionManager()
    ws = FakeWebSocket()
    manager.register(1, ws, "alice@example.com")

    with patch(
        "src.realtime.connection_manager._get_redis",
        side_effect=RuntimeError("Redis unreachable"),
    ):
        await manager.broadcast({"type": "ping"})  # не должно бросить исключение

    assert json.loads(ws.sent[0]) == {"type": "ping"}  # локальная доставка всё равно произошла


async def test_pubsub_message_from_other_origin_delivers_locally():
    manager = ConnectionManager()
    ws = FakeWebSocket()
    manager.register(1, ws, "alice@example.com")

    message = {
        "type": "message",
        "data": json.dumps({"origin": "some-other-process", "payload": {"type": "ping"}, "exclude_user_id": None}),
    }
    await manager._handle_pubsub_message(message)

    assert json.loads(ws.sent[0]) == {"type": "ping"}


async def test_pubsub_message_from_own_origin_is_ignored():
    """Redis рассылает публикацию и публикующему: без проверки origin событие доставилось бы дважды."""
    manager = ConnectionManager()
    ws = FakeWebSocket()
    manager.register(1, ws, "alice@example.com")

    message = {
        "type": "message",
        "data": json.dumps({"origin": manager._origin, "payload": {"type": "ping"}, "exclude_user_id": None}),
    }
    await manager._handle_pubsub_message(message)

    assert ws.sent == []


async def test_pubsub_message_ignores_non_message_type():
    """redis-py отдаёт через listen() и служебные события подписки
    (type='subscribe') — не доменные события, доставлять их не нужно."""
    manager = ConnectionManager()
    ws = FakeWebSocket()
    manager.register(1, ws, "alice@example.com")

    await manager._handle_pubsub_message({"type": "subscribe", "data": 1})

    assert ws.sent == []


async def test_pubsub_message_respects_exclude_user_id():
    manager = ConnectionManager()
    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    manager.register(1, ws1, "alice@example.com")
    manager.register(2, ws2, "bob@example.com")

    message = {
        "type": "message",
        "data": json.dumps({"origin": "other", "payload": {"type": "ping"}, "exclude_user_id": 1}),
    }
    await manager._handle_pubsub_message(message)

    assert ws1.sent == []
    assert json.loads(ws2.sent[0]) == {"type": "ping"}


# broadcast_task_event: форма payload и подмена broadcaster.
#
# Персистируемые типы дополняются id/created_at из chat_history.append_event (mock_chat_history_redis) до broadcaster.

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
    assert payload["type"] == "task_updated"
    assert payload["title"] == "Задача №1"
    assert payload["sender"] == "alice@example.com"
    assert payload["task_id"] == 87
    assert "id" in payload and "created_at" in payload
    assert exclude_user_id == 7


async def test_broadcast_task_event_defaults_are_empty():
    fake = FakeBroadcaster()

    await broadcast_task_event("task_created", "Задача №2", broadcaster=fake)

    payload, exclude_user_id = fake.calls[0]
    assert payload["type"] == "task_created"
    assert payload["title"] == "Задача №2"
    assert payload["sender"] == ""
    assert "id" in payload and "created_at" in payload
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

    sent = json.loads(ws.sent[0])
    assert sent["type"] == "task_deleted"
    assert sent["title"] == "Задача №3"
    assert sent["sender"] == "carol@example.com"
    assert "id" in sent and "created_at" in sent


async def test_broadcast_task_event_persists_all_crud_and_file_types(mock_chat_history_redis):
    fake = FakeBroadcaster()
    crud_types = (
        "task_created", "task_updated", "task_deleted",
        "subtask_created", "subtask_updated", "subtask_deleted",
        "task_files_updated", "subtask_files_updated",
    )
    for event_type in crud_types:
        await broadcast_task_event(event_type, "Title", broadcaster=fake, sender_email="a@b.c")

    assert mock_chat_history_redis.rpush.call_count == len(crud_types)
    for payload, _ in fake.calls:
        assert "id" in payload and "created_at" in payload


async def test_broadcast_task_event_does_not_persist_unknown_event(mock_chat_history_redis):
    """Тип вне _PERSISTED_EVENT_TYPES остаётся эфемерным."""
    fake = FakeBroadcaster()

    await broadcast_task_event("something_else", "Title", broadcaster=fake, sender_email="a@b.c")

    mock_chat_history_redis.rpush.assert_not_called()
    for payload, _ in fake.calls:
        assert "id" not in payload  # не персистировано — id/created_at не добавляются


# _publish_chat_message: персист в chat_history и broadcast.
#
# append_event мокается напрямую (логика Redis List — в test_chat_history.py); проверяем оркестрацию: payload persist
# и дополнение broadcast-payload id/created_at.

async def test_publish_chat_message_persists_then_broadcasts_with_id_and_created_at():
    from src.realtime.router import _publish_chat_message
    from src.realtime.connection_manager import connection_manager

    sender_ws, other_ws = FakeWebSocket(), FakeWebSocket()
    connection_manager.register(1, sender_ws, "alice@example.com")
    connection_manager.register(2, other_ws, "bob@example.com")
    try:
        with patch(
            "src.realtime.router.chat_history.append_event",
            AsyncMock(return_value={"id": 42, "created_at": "2026-01-01T00:00:00+00:00"}),
        ) as mock_append:
            await _publish_chat_message(1, "hello")
    finally:
        connection_manager.unregister(1, sender_ws)
        connection_manager.unregister(2, other_ws)

    mock_append.assert_called_once_with(
        {"type": "chat", "sender": "alice@example.com", "text": "hello", "sender_user_id": 1}
    )
    base = {
        "type": "chat",
        "sender": "alice@example.com",
        "text": "hello",
        "id": 42,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    # Отправитель получает эхо (рисуется как «You: ...»), остальные — то же
    # сообщение с is_own=False; внутренний sender_user_id клиентам не уходит.
    assert json.loads(sender_ws.sent[0]) == {**base, "is_own": True}
    assert json.loads(other_ws.sent[0]) == {**base, "is_own": False}


async def test_publish_chat_message_skips_persistence_when_sender_not_registered():
    """sender_email is None (соединение уже закрылось): персистентность не вызывается, как и broadcast()."""
    from src.realtime.router import _publish_chat_message

    with patch("src.realtime.router.chat_history.append_event", AsyncMock()) as mock_append:
        await _publish_chat_message(999, "hello")

    mock_append.assert_not_called()


async def test_deliver_local_sets_is_own_per_recipient_and_strips_sender_user_id():
    manager = ConnectionManager()
    author_tab1, author_tab2, other = FakeWebSocket(), FakeWebSocket(), FakeWebSocket()
    manager.register(1, author_tab1, "alice@example.com")
    manager.register(1, author_tab2, "alice@example.com")
    manager.register(2, other, "bob@example.com")

    await manager._deliver_local(
        {"type": "chat", "sender": "alice@example.com", "text": "hi", "sender_user_id": 1}
    )

    frames = [json.loads(ws.sent[0]) for ws in (author_tab1, author_tab2, other)]
    assert [f["is_own"] for f in frames] == [True, True, False]   # обе вкладки автора — «свои»
    for frame in frames:
        assert "sender_user_id" not in frame                       # внутренний id клиентам не отдаётся
        assert frame["text"] == "hi" and frame["sender"] == "alice@example.com"


async def test_deliver_local_without_sender_user_id_sends_payload_unchanged():
    manager = ConnectionManager()
    ws = FakeWebSocket()
    manager.register(1, ws, "alice@example.com")

    await manager._deliver_local({"type": "task_created", "title": "T", "actor_id": 1})

    assert json.loads(ws.sent[0]) == {"type": "task_created", "title": "T", "actor_id": 1}


async def test_pubsub_message_from_other_worker_tailors_is_own_for_local_sockets():
    """Сообщение чата от другого воркера доставляется своим сокетам с is_own на получателя и без sender_user_id."""
    manager = ConnectionManager()
    author_ws, other_ws = FakeWebSocket(), FakeWebSocket()
    manager.register(1, author_ws, "alice@example.com")   # вторая вкладка автора — на ЭТОМ воркере
    manager.register(2, other_ws, "bob@example.com")

    message = {
        "type": "message",
        "data": json.dumps({
            "origin": "some-other-process",
            "payload": {"type": "chat", "sender": "alice@example.com", "text": "hi", "sender_user_id": 1},
            "exclude_user_id": None,
        }),
    }
    await manager._handle_pubsub_message(message)

    assert json.loads(author_ws.sent[0])["is_own"] is True
    assert json.loads(other_ws.sent[0])["is_own"] is False
    assert "sender_user_id" not in author_ws.sent[0] and "sender_user_id" not in other_ws.sent[0]


async def test_broadcast_publishes_sender_user_id_through_redis_for_other_workers(mock_realtime_redis):
    """Через Redis payload идёт С sender_user_id (каждый воркер сам решает
    is_own для своих сокетов) — клиентам локального воркера он не уходит."""
    manager = ConnectionManager()
    ws = FakeWebSocket()
    manager.register(1, ws, "alice@example.com")

    await manager.broadcast({"type": "chat", "text": "hi", "sender_user_id": 1})

    published = json.loads(mock_realtime_redis.publish.call_args.args[1])
    assert published["payload"]["sender_user_id"] == 1
    assert "sender_user_id" not in ws.sent[0]


async def test_broadcast_task_event_survives_append_event_failure():
    fake = FakeBroadcaster()
    with patch(
        "src.realtime.events.chat_history.append_event",
        AsyncMock(side_effect=ConnectionError("redis down")),
    ):
        await broadcast_task_event("task_created", "T", broadcaster=fake, sender_email="a@b.c")

    assert len(fake.calls) == 1                       # живая рассылка всё равно состоялась
    assert "id" not in fake.calls[0][0]               # событие не персистировано


async def test_broadcast_task_event_survives_broadcast_failure(mock_chat_history_redis):
    broken = MagicMock()
    broken.broadcast = AsyncMock(side_effect=ConnectionError("redis down"))

    await broadcast_task_event("task_created", "T", broadcaster=broken, sender_email="a@b.c")

    broken.broadcast.assert_awaited_once()


async def test_publish_chat_message_survives_redis_failures():
    from src.realtime.router import _publish_chat_message

    ws = FakeWebSocket()
    connection_manager.register(1, ws, "alice@example.com")
    try:
        with patch(
            "src.realtime.router.chat_history.append_event",
            AsyncMock(side_effect=ConnectionError("redis down")),
        ), patch.object(
            connection_manager, "broadcast", AsyncMock(side_effect=ConnectionError("redis down"))
        ) as mock_broadcast:
            await _publish_chat_message(1, "hello")
    finally:
        connection_manager.unregister(1, ws)

    mock_broadcast.assert_awaited_once()
    assert "id" not in mock_broadcast.await_args.args[0]
