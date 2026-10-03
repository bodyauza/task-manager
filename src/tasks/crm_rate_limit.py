"""Ограничитель скорости запросов к CRM: не больше N в секунду на один общий ключ crm_rate_limit.

Нужен при разборе backlog после простоя CRM: reconcile_pending_outbox возвращает в очередь сотни строк, и несколько
шардов выстрелили бы пачкой в один CRM. Фиксированное окно (INCR + EXPIRE); лимит общий для всех шардов.
"""

import redis.asyncio as redis

from src.config import settings
from src.crm.crm_config import crm_settings


async def acquire_slot() -> bool:
    """True — запрос к CRM можно выполнять; False — лимит текущей секунды исчерпан, вызывающий код откладывает попытку.

    `EXPIRE ... NX` ставит TTL только если его нет: если процесс упал между INCR и EXPIRE, ключ иначе остался бы
    бессрочным и навсегда блокировал CRM-вызовы. NX не сдвигает уже выставленный TTL — окно не скользит.

    Клиент создаётся и закрывается в этом же вызове: задачи идут через run_celery_task() в отдельных asyncio.run(), и соединение
    redis-py старого loop падает с «Event loop is closed».
    """
    client = redis.from_url(settings.REDIS_URL)
    try:
        count = await client.incr("crm_rate_limit")
        await client.expire("crm_rate_limit", 1, nx=True)
        return count <= crm_settings.RATE_LIMIT_PER_SECOND
    finally:
        await client.aclose()
