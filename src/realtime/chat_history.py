"""Хранилище истории панели WS (чат + CRUD-события задач/подзадач) — Redis List.

RPUSH добавляет новую запись в конец списка "chat:history", LTRIM
обрезает список до settings.CHAT_HISTORY_MAX_LEN самых свежих записей —
история заведомо ограничена, не бесконечный архив (docs/
chat_history_redis_list_guide.md, §1.4, решение №3).

Персистируются два рода событий: сообщения чата (тип "chat", из
src.realtime.router::_publish_chat_message; запись несёт внутренний
sender_user_id, который /chat/history превращает в is_own и не отдаёт наружу)
и события действий над задачами/подзадачами — CRUD (task_created/updated/
deleted, subtask_created/updated/deleted) и файловые (task_files_updated/
subtask_files_updated) из src.realtime.events::broadcast_task_event
(_PERSISTED_EVENT_TYPES там же).
Модуль здесь не разбирает форму payload — append_event() принимает уже
готовый dict целиком, откуда бы он ни пришёл.

Отдельный module-level singleton _get_redis(), как и в
src.realtime.connection_manager/src.crm.client/src.tasks.crm_rate_limit —
свой клиент на каждый модуль, использующий Redis, чтобы тесты могли
патчить _get_redis() каждого модуля независимо (см. mock_realtime_redis в
tests/conftest.py для того же приёма в connection_manager.py; для этого
модуля — отдельная фикстура, см. tests/test_chat_history.py).
"""

import json
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis

from src.config import settings

_HISTORY_KEY = "chat:history"
_SEQ_KEY = "chat:history:next_id"

# Константы запроса, не Settings — защита от чрезмерного limit в самом
# запросе (docs/chat_history_redis_list_guide.md §1.4, решение №8), не
# параметр деплоя.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL)
    return _redis_client


async def append_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Персистирует одну запись (чат-сообщение ИЛИ CRUD-событие задачи/
    подзадачи — см. докстринг модуля) поверх уже готового payload, добавляя
    к нему id/created_at. Возвращает сохранённую запись целиком — вызывающий
    код (router.py::_publish_chat_message, events.py::broadcast_task_event)
    использует её, чтобы подмешать те же id/created_at в payload live-рассылки.

    INCR + RPUSH + LTRIM — три отдельные команды, не атомарный Lua-скрипт:
    осознанный компромисс, см. docs/chat_history_redis_list_guide.md §1.4,
    решение №4 (крайне редкая гонка порядка двух записей при конкурентной
    отправке с разных uvicorn-воркеров — приемлемо для этой истории).
    """
    message_id = await _get_redis().incr(_SEQ_KEY)
    entry: dict[str, Any] = {
        **payload,
        "id": message_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await _get_redis().rpush(_HISTORY_KEY, json.dumps(entry))
    await _get_redis().ltrim(_HISTORY_KEY, -settings.CHAT_HISTORY_MAX_LEN, -1)
    return entry


async def get_history_page(before_id: int | None, limit: int) -> list[dict[str, Any]]:
    """До `limit` сообщений старше before_id, в хронологическом порядке
    (старые → новые — порядок, удобный клиенту для вставки в НАЧАЛО
    #messages без разворота массива на JS-стороне).

    before_id=None — самая свежая страница (открытие страницы/переподключение).
    before_id=<id> — сообщения строго старше этого id (пролистывание вверх;
    курсор — минимальный id уже отрисованных на клиенте сообщений).

    before_id, указывающий на сообщение, которое уже вытеснено LTRIM в
    append_event, — не ошибка: возвращается [] или укороченная страница,
    клиент трактует это как «дальше истории нет» (docs/
    chat_history_redis_list_guide.md §1.4, решение №3).
    """
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    r = _get_redis()
    if before_id is None:
        raw = await r.lrange(_HISTORY_KEY, -limit, -1)
        return [json.loads(item) for item in raw]

    last_id_raw = await r.get(_SEQ_KEY)
    last_id = int(last_id_raw) if last_id_raw is not None else 0
    # skip — сколько сообщений с id >= before_id нужно пропустить с конца списка,
    # чтобы дальше читать именно то, что СТРОГО СТАРШЕ курсора (+1, а не только
    # last_id - before_id: иначе сообщение с id == before_id само попадало бы
    # в страницу как "старое", хотя это ровно та граница, которую клиент уже видел).
    skip = last_id - before_id + 1
    if skip <= 0:
        return []  # курсор новее самого свежего сообщения — не штатный случай при обычной работе клиента
    raw = await r.lrange(_HISTORY_KEY, -(skip + limit), -(skip + 1))
    return [json.loads(item) for item in raw]
