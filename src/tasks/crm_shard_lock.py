"""Redlock-мьютекс на шард — дополнительная страховка от человеческой ошибки
в манифесте деплоя («подняли двух воркеров на один и тот же шард»).

Порядок обработки строк одного шарда в этой схеме обеспечивается не этим
модулем, а топологией деплоя (docker-compose: один celery-worker-shard-N
процесс на шард, --pool=solo, concurrency=1 — см. src/docker-compose.yml) —
Redlock здесь не более чем сетевая страховка НА СЛУЧАЙ, если эта топология
будет нарушена (например, вручную запустят второй воркер на тот же -Q).

При одном Redis-инстансе в проекте redis.asyncio.Redis.lock() (SET NX PX +
токен для безопасного release, встроено в redis-py) — это и есть практическая
реализация Redlock: полноценный многоузловой алгоритм с кворумом применим
только при нескольких независимых Redis-инстансах, что не тот случай.
"""

from contextlib import asynccontextmanager

import redis.asyncio as redis

from src.config import settings

_LOCK_TIMEOUT_SECONDS = 30  # чуть больше типичной длительности одной обработки
                            # строки outbox (сетевой вызов CRM + запись в БД) —
                            # достаточно, чтобы не отпустить лок посреди работы,
                            # но не настолько долго, чтобы упавший воркер держал
                            # чужой шард заблокированным неоправданно долго
                            # (redis-py lock — самоуничтожается по истечении TTL,
                            # а не требует явного unlock от упавшего процесса).


@asynccontextmanager
async def shard_lock(shard: str):
    """async with shard_lock("shard_0"): ... — держит Redlock на время блока.

    blocking_timeout=10: если лок уже занят (что структурно не должно
    происходить при штатной топологии деплоя — см. докстринг модуля), ждём
    до 10 сек, затем поднимаем TimeoutError, а не блокируемся навсегда —
    строка останется 'pending' и её позже подхватит reconcile_pending_outbox,
    как и при любом другом сбое обработки.

    Клиент создаётся и закрывается В ПРЕДЕЛАХ ЭТОГО ЖЕ вызова, а не хранится
    как module-level singleton между вызовами (как было раньше — тот же
    приём, что и _get_shared_http_client() в src/crm/client.py, но здесь он
    не подходит): эта функция вызывается из Celery-задач, каждая из которых
    выполняется через src.database.run_isolated() в СВОЁМ собственном
    asyncio.run(). Соединение redis-py, открытое в одном event loop, при
    попытке переиспользовать его в следующем asyncio.run() (новый loop)
    падает "RuntimeError: Event loop is closed" при закрытии — обнаружено
    живым прогоном docker compose up (юнит-тесты не ловят: не выполняют два
    реальных asyncio.run() подряд в одном процессе поверх одного клиента).
    Тот же класс проблемы, что и с SQLAlchemy engine — см. докстринг
    run_isolated() и src/tasks/crm_rate_limit.py, где применён тот же фикс.
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
