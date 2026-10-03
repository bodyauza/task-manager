"""Redlock-мьютекс на шард — страховка от ошибки деплоя (два воркера на один шард).

Порядок внутри шарда обеспечивает топология (один celery-worker-shard-N, --pool=solo, concurrency=1), а не этот модуль.
При одном Redis redis.asyncio.Redis.lock() (SET NX PX + токен) и есть практическая реализация Redlock.
"""

from contextlib import asynccontextmanager

import redis.asyncio as redis

from src.config import settings

_LOCK_TIMEOUT_SECONDS = 300
                            # С запасом над худшим последовательным сценарием: httpx.Timeout(connect=30, write=120, read=120, pool=30) (src/crm/client.py)
                            # применяется к каждой фазе отдельно, и запрос может занять connect + write + read ≈ 270 с (pool не считаем: воркер
                            # последовательный). Это реально для файловых операций (до ~133 МБ base64 одним телом). Внутренних ретраев в
                            # CRMClient._call() нет. +30 с на чтение файлов, сериализацию и запись в БД. При смене таймаутов httpx пересчитать.


@asynccontextmanager
async def shard_lock(shard: str):
    """async with shard_lock("shard_0"): ... — держит Redlock на время блока.

    blocking_timeout=10: если лок занят (штатно не бывает), ждём до 10 с и поднимаем TimeoutError — строка останется
    'pending', её подберёт reconcile_pending_outbox.

    Клиент создаётся и закрывается в этом же вызове: задачи идут в отдельных asyncio.run(), и соединение redis-py
    старого loop падает с «Event loop is closed».
    """
    client = redis.from_url(settings.REDIS_URL)
    lock = client.lock(f"crm_shard_lock:{shard}", timeout=_LOCK_TIMEOUT_SECONDS, blocking_timeout=10)
    try:
        acquired = await lock.acquire()
        if not acquired:
            raise TimeoutError(f"Не удалось получить Redlock на {shard} за 10 сек")
        try:
            yield
        finally:
            await lock.release()
    finally:
        await client.aclose()
