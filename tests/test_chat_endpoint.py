"""HTTP-тесты GET /chat/history (src/realtime/router.py) — постраничная
подгрузка истории WS-чата.

mock_chat_history_redis (tests/conftest.py, autouse) уже патчит
src.realtime.chat_history._get_redis для ВСЕГО файла — здесь он запрашивается
как параметр там, где тесту нужно настроить конкретный ответ Redis
(lrange.return_value), не только избежать реального сетевого соединения.
Сама арифметика пагинации (курсор before_id, LTRIM-обрезка) уже проверена
в tests/test_chat_history.py — здесь проверяется только HTTP-контракт
эндпоинта (аутентификация, форма ответа, валидация query-параметров).
"""

import json

from httpx import AsyncClient

from tests.conftest import register_and_login

EMAIL    = "alice@example.com"
PASSWORD = "Password1!"


async def _register_login(client: AsyncClient, mock_smtp: dict) -> None:
    await register_and_login(client, mock_smtp, EMAIL, PASSWORD)


async def test_chat_history_requires_authentication(client: AsyncClient):
    response = await client.get("/chat/history")
    assert response.status_code == 401


async def test_chat_history_empty_by_default(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)

    response = await client.get("/chat/history")

    assert response.status_code == 200
    assert response.json() == []


async def test_chat_history_returns_stored_messages_without_sender_user_id(
    client: AsyncClient, mock_smtp: dict, mock_chat_history_redis,
):
    await _register_login(client, mock_smtp)
    entry = {
        "id": 1,
        "type": "chat",
        "sender_user_id": 424242,   # заведомо не id текущего пользователя
        "sender": "bob@example.com",
        "text": "hello",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    mock_chat_history_redis.lrange.return_value = [json.dumps(entry).encode()]

    response = await client.get("/chat/history")

    assert response.status_code == 200
    data = response.json()
    # Без строгого response_model (эндпоинт отдаёт разные наборы полей для
    # чата и для CRUD-событий задач/подзадач, см. router.py) — created_at
    # проходит как есть, без изменения формата.
    assert data == [{
        "id": 1,
        "type": "chat",
        "sender": "bob@example.com",
        "text": "hello",
        "is_own": False,
        "created_at": "2026-01-01T00:00:00+00:00",
    }]
    assert "sender_user_id" not in data[0]  # внутреннее поле не входит в публичный контракт


async def test_chat_history_returns_stored_task_event_entries(
    client: AsyncClient, mock_smtp: dict, mock_chat_history_redis,
):
    """Персистированные CRUD-события задач/подзадач (task_created и т.п.,
    src/realtime/events.py) отдаются через тот же эндпоинт, что и чат —
    единая история панели WS."""
    await _register_login(client, mock_smtp)
    entry = {
        "id": 2,
        "type": "task_created",
        "title": "Демо-задача",
        "sender": "bob@example.com",
        "actor_id": 7,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    mock_chat_history_redis.lrange.return_value = [json.dumps(entry).encode()]

    response = await client.get("/chat/history")

    assert response.status_code == 200
    assert response.json() == [entry]


async def test_chat_history_forwards_before_id_and_limit_to_redis(
    client: AsyncClient, mock_smtp: dict, mock_chat_history_redis,
):
    await _register_login(client, mock_smtp)
    mock_chat_history_redis.get.return_value = b"10"  # текущий "chat:history:next_id"

    response = await client.get("/chat/history", params={"before_id": 7, "limit": 5})

    assert response.status_code == 200
    # skip = last_id(10) - before_id(7) + 1 = 4 → LRANGE(-(4+5), -(4+1)) = LRANGE(-9, -5)
    mock_chat_history_redis.lrange.assert_called_once_with("chat:history", -9, -5)


async def test_chat_history_rejects_before_id_below_one(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)

    response = await client.get("/chat/history", params={"before_id": 0})

    assert response.status_code == 422


async def test_chat_history_rejects_limit_above_max_page_size(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)

    response = await client.get("/chat/history", params={"limit": 201})

    assert response.status_code == 422


async def test_chat_history_marks_own_messages_by_sender_user_id(
    client: AsyncClient, mock_smtp: dict, mock_chat_history_redis,
):
    """Свои сообщения (sender_user_id == id текущего пользователя) получают
    is_own=True — клиент рисует «You: ...» вместо email; id наружу не уходит."""
    await _register_login(client, mock_smtp)
    mine = {"id": 1, "type": "chat", "sender_user_id": 1, "sender": "someone@example.com",
            "text": "mine", "created_at": "2026-01-01T00:00:00+00:00"}
    theirs = {"id": 2, "type": "chat", "sender_user_id": 999, "sender": "bob@example.com",
              "text": "theirs", "created_at": "2026-01-01T00:00:01+00:00"}
    mock_chat_history_redis.lrange.return_value = [json.dumps(mine).encode(), json.dumps(theirs).encode()]

    data = (await client.get("/chat/history")).json()

    assert [row["is_own"] for row in data] == [True, False]
    assert all("sender_user_id" not in row for row in data)


async def test_chat_history_marks_legacy_own_messages_by_sender_email(
    client: AsyncClient, mock_smtp: dict, mock_chat_history_redis,
):
    """Записи, сохранённые до появления sender_user_id, сопоставляются по email
    отправителя."""
    await _register_login(client, mock_smtp)
    mine = {"id": 1, "type": "chat", "sender": EMAIL, "text": "old own",
            "created_at": "2026-01-01T00:00:00+00:00"}
    theirs = {"id": 2, "type": "chat", "sender": "bob@example.com", "text": "old other",
              "created_at": "2026-01-01T00:00:01+00:00"}
    mock_chat_history_redis.lrange.return_value = [json.dumps(mine).encode(), json.dumps(theirs).encode()]

    data = (await client.get("/chat/history")).json()

    assert [row["is_own"] for row in data] == [True, False]


async def test_chat_history_does_not_add_is_own_to_action_events(
    client: AsyncClient, mock_smtp: dict, mock_chat_history_redis,
):
    """События действий (task_*/subtask_*, в т.ч. файловые) несут actor_id и
    отдаются как есть — is_own только у сообщений чата."""
    await _register_login(client, mock_smtp)
    entry = {"id": 3, "type": "task_files_updated", "title": "T", "sender": "bob@example.com",
             "actor_id": 7, "action": "uploaded", "task_id": 1, "created_at": "2026-01-01T00:00:00+00:00"}
    mock_chat_history_redis.lrange.return_value = [json.dumps(entry).encode()]

    assert (await client.get("/chat/history")).json() == [entry]
