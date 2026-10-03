"""Лимит `POST /auth/register/request-code` по IP.

Дополняет лимит «1 запрос на email раз в 60 с», который не мешает анонимному клиенту запрашивать коды
на разные адреса (email-бомбинг). Фиксированное окно: INCR + `EXPIRE ... NX` (как в src/tasks/crm_rate_limit.py).
Клиент Redis — module-level singleton, потому что веб-процесс живёт в одном event loop.

Fail-closed: при недоступности Redis запрос отклоняется с 503 RATE_LIMITER_UNAVAILABLE, а не пропускается и не
превращается в необработанный 500. Без счётчика лимит по IP не работает, и пропуск запросов на время сбоя открыл бы
email-бомбинг; регистрация на это время недоступна осознанно, с понятной ошибкой для клиента.
"""

import logging

import redis.asyncio as redis
from fastapi import HTTPException

from src.config import settings

logger = logging.getLogger(__name__)

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
    """True — лимит окна не исчерпан; False — вызывающий код должен ответить 429, не отправляя письмо и не хешируя.

    Raises:
        HTTPException(503, "RATE_LIMITER_UNAVAILABLE"): Redis недоступен (fail-closed).
    """
    key = f"reg_code_rate_limit:{client_ip}"
    try:
        client = _get_redis()
        count = await client.incr(key)
        await client.expire(key, WINDOW_SECONDS, nx=True)
    except Exception as exc:
        logger.error("Лимит request-code по IP недоступен (Redis): %s — запрос отклонён (fail-closed)", exc)
        raise HTTPException(status_code=503, detail="RATE_LIMITER_UNAVAILABLE") from exc
    return count <= MAX_REQUESTS_PER_WINDOW
