"""История WS-панели (чат и события задач/подзадач) в Redis List `chat:history`.

RPUSH + LTRIM до settings.CHAT_HISTORY_MAX_LEN последних записей. Форму payload модуль не разбирает.
"""

import json
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis

from src.config import settings

_HISTORY_KEY = "chat:history"
_SEQ_KEY = "chat:history:next_id"

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL)
    return _redis_client


async def append_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Сохраняет запись, добавляя id и created_at, и возвращает её целиком.

    INCR + RPUSH + LTRIM — не атомарно: редкая гонка порядка между воркерами допустима.
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
    """До `limit` записей старше before_id (None — самая свежая страница) в хронологическом порядке.

    Вытесненный LTRIM курсор даёт пустую или короткую страницу — это не ошибка.
    """
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    r = _get_redis()
    if before_id is None:
        raw = await r.lrange(_HISTORY_KEY, -limit, -1)
        return [json.loads(item) for item in raw]

    last_id_raw = await r.get(_SEQ_KEY)
    last_id = int(last_id_raw) if last_id_raw is not None else 0
    # skip — сколько записей с id >= before_id пропустить с конца, чтобы читать строго старше курсора.
    skip = last_id - before_id + 1
    if skip <= 0:
        return []
    raw = await r.lrange(_HISTORY_KEY, -(skip + limit), -(skip + 1))
    return [json.loads(item) for item in raw]
