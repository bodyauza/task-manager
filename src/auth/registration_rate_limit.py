"""Лимит `POST /auth/register/request-code` по IP.

Дополняет лимит «1 запрос на email раз в 60 с», который не мешает анонимному клиенту запрашивать коды
на разные адреса (email-бомбинг). Фиксированное окно: INCR + `EXPIRE ... NX` (как в src/tasks/crm_rate_limit.py).
Клиент Redis — module-level singleton, потому что веб-процесс живёт в одном event loop.
"""

import redis.asyncio as redis

from src.config import settings

# 5 запросов кода с одного IP за 10 минут — независимо от email (per-email лимит отдельный, 60 с).
MAX_REQUESTS_PER_WINDOW = 5
WINDOW_SECONDS = 600

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL)
    return _redis_client


async def acquire_request_code_slot(client_ip: str) -> bool:
    """True — лимит окна не исчерпан; False — вызывающий код должен ответить 429, не отправляя письмо и не хешируя."""
    client = _get_redis()
    key = f"reg_code_rate_limit:{client_ip}"
    count = await client.incr(key)
    await client.expire(key, WINDOW_SECONDS, nx=True)
    return count <= MAX_REQUESTS_PER_WINDOW
