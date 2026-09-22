"""Юнит-тесты src.realtime.chat_history: append_event/get_history_page на
Redis List.

FakeRedis — самодельная замена redis.asyncio.Redis поверх обычного Python
list/int (по аналогии с FakeWebSocket/FakeBroadcaster в tests/test_realtime.py),
а не сторонний мок-пакет (fakeredis) и не настоящий Redis: нужны только пять
команд (INCR/GET/RPUSH/LTRIM/LRANGE) с точной семантикой отрицательных
индексов Redis, которую проверяют сами тесты пагинации.
"""

import json
from unittest.mock import patch

import pytest

from src.config import settings
from src.realtime import chat_history


def _resolve_range(n: int, start: int, stop: int):
    """Нормализует (start, stop) как Redis LRANGE/LTRIM: отрицательные индексы
    считаются от конца списка, диапазон включает оба конца. Возвращает (s, e)
    или None, если диапазон пуст."""
    s = start if start >= 0 else n + start
    e = stop if stop >= 0 else n + stop
    s = max(s, 0)
    if n == 0 or s >= n:
        return None
    e = min(e, n - 1)
    if e < s:
        return None
    return s, e


class FakeRedis:
    def __init__(self):
        self.lists: dict[str, list[bytes]] = {}
        self.counters: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def get(self, key: str):
        if key not in self.counters:
            return None
        return str(self.counters[key]).encode()

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value.encode())
        return len(self.lists[key])

    async def ltrim(self, key: str, start: int, stop: int) -> bool:
        lst = self.lists.get(key, [])
        resolved = _resolve_range(len(lst), start, stop)
        self.lists[key] = lst[resolved[0]:resolved[1] + 1] if resolved else []
        return True

    async def lrange(self, key: str, start: int, stop: int) -> list[bytes]:
        lst = self.lists.get(key, [])
        resolved = _resolve_range(len(lst), start, stop)
        return lst[resolved[0]:resolved[1] + 1] if resolved else []


@pytest.fixture
def fake_redis():
    fake = FakeRedis()
    with patch("src.realtime.chat_history._get_redis", return_value=fake):
        yield fake


async def _append_chat(sender_user_id: int, sender_email: str, text: str) -> dict:
    """Хелпер — append_event() теперь принимает готовый payload целиком (см.
    src/realtime/events.py::broadcast_task_event для CRUD-событий), а не
    отдельные позиционные аргументы sender_user_id/sender_email/text; тесты
    ниже проверяют именно логику Redis List/пагинации, не форму конкретного
    payload, поэтому используют этот тонкий wrapper вместо повторения dict
    в каждом вызове."""
    return await chat_history.append_event({
        "type": "chat", "sender_user_id": sender_user_id, "sender": sender_email, "text": text,
    })


# ── append_event ──────────────────────────────────────────────────────────

async def test_append_event_assigns_increasing_ids(fake_redis):
    first = await _append_chat(1, "alice@example.com", "привет")
    second = await _append_chat(1, "alice@example.com", "как дела?")

    assert first["id"] == 1
    assert second["id"] == 2


async def test_append_event_stores_sender_and_text(fake_redis):
    entry = await _append_chat(7, "bob@example.com", "hello")

    assert entry["sender_user_id"] == 7
    assert entry["sender"] == "bob@example.com"
    assert entry["text"] == "hello"
    assert "created_at" in entry


async def test_append_event_preserves_arbitrary_payload_fields(fake_redis):
    """append_event ничего не знает о форме payload — что передали, то и
    сохранилось (плюс id/created_at). Проверяем на форме CRUD-события задачи,
    а не чат-сообщения — ровно то, что теперь тоже проходит через этот путь
    (src/realtime/events.py::broadcast_task_event)."""
    entry = await chat_history.append_event({
        "type": "task_created", "title": "Демо-задача", "sender": "alice@example.com",
        "actor_id": 1,
    })

    assert entry["type"] == "task_created"
    assert entry["title"] == "Демо-задача"
    assert entry["actor_id"] == 1
    assert "id" in entry and "created_at" in entry


async def test_append_event_trims_history_to_configured_max_len(fake_redis):
    with patch.object(settings, "CHAT_HISTORY_MAX_LEN", 3):
        for i in range(5):
            await _append_chat(1, "alice@example.com", f"msg{i}")

        raw = fake_redis.lists["chat:history"]
        assert len(raw) == 3
        kept_ids = [json.loads(item)["id"] for item in raw]
        assert kept_ids == [3, 4, 5]  # только 3 последних сообщения


# ── get_history_page ─────────────────────────────────────────────────────

async def test_get_history_page_without_cursor_returns_latest_page(fake_redis):
    for i in range(5):
        await _append_chat(1, "alice@example.com", f"msg{i}")

    page = await chat_history.get_history_page(None, limit=3)

    assert [entry["text"] for entry in page] == ["msg2", "msg3", "msg4"]  # старые → новые


async def test_get_history_page_empty_history_returns_empty_list(fake_redis):
    page = await chat_history.get_history_page(None, limit=10)

    assert page == []


async def test_get_history_page_with_cursor_returns_older_messages(fake_redis):
    for i in range(5):
        await _append_chat(1, "alice@example.com", f"msg{i}")
    # Клиент уже отрисовал msg2..msg4 (id 3..5) — курсор указывает на самое старое из них.
    first_page = await chat_history.get_history_page(None, limit=3)
    cursor = first_page[0]["id"]

    older_page = await chat_history.get_history_page(cursor, limit=10)

    assert [entry["text"] for entry in older_page] == ["msg0", "msg1"]


async def test_get_history_page_cursor_at_beginning_returns_empty_list(fake_redis):
    await _append_chat(1, "alice@example.com", "msg0")
    first_page = await chat_history.get_history_page(None, limit=10)
    oldest_id = first_page[0]["id"]

    page = await chat_history.get_history_page(oldest_id, limit=10)

    assert page == []


async def test_get_history_page_cursor_beyond_trimmed_history_returns_empty_list(fake_redis):
    """before_id, указывающий на сообщение, которое LTRIM уже вытеснил из
    списка, — не ошибка."""
    with patch.object(settings, "CHAT_HISTORY_MAX_LEN", 2):
        for i in range(5):
            await _append_chat(1, "alice@example.com", f"msg{i}")

    page = await chat_history.get_history_page(before_id=1, limit=10)

    assert page == []


async def test_get_history_page_limit_is_clamped_to_max_page_size(fake_redis):
    for i in range(3):
        await _append_chat(1, "alice@example.com", f"msg{i}")

    page = await chat_history.get_history_page(None, limit=chat_history.MAX_PAGE_SIZE + 1000)

    assert len(page) == 3  # запрошенный лимит клампится, но реально сообщений всего 3
