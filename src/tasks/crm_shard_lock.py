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

_LOCK_TIMEOUT_SECONDS = 300  # с запасом над ХУДШИМ последовательным сценарием
                            # httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0,
                            # write=120.0, read=120.0, pool=30.0)) (src/crm/client.py),
                            # а не над одним таймаутом целиком (частая ошибка — см.
                            # историю этой константы ниже): один float в timeout=
                            # httpx разворачивает в ЧЕТЫРЕ независимых таймаута —
                            # connect/write/read/pool, каждый применяется отдельно
                            # (httpcore/_backends/anyio.py: anyio.fail_after(timeout)
                            # вокруг каждого read()/write()). Успешный (не упавший по
                            # таймауту) запрос может честно занять connect(<30) +
                            # write(<120) + read(<120) последовательно — до ~270 секунд,
                            # если каждая фаза лишь чуть-чуть не дотягивает до лимита.
                            # pool-таймаут не считаем: воркер обрабатывает CRM-вызовы
                            # строго последовательно (--pool=solo, свой asyncio.run() на
                            # задачу), конкуренции за пул соединений внутри процесса нет.
                            #
                            # Это особенно реально для файловых операций — shard_lock
                            # оборачивает ЛЮБОЙ обработчик одинаково, включая
                            # _do_sync_files_task/_do_sync_files_subtask (см.
                            # src/tasks/crm_outbox_tasks.py, общий диспетчер), а не
                            # только текстовые create/update. TaskManager.update_task/
                            # SubtaskManager.update_subtask с other_file_abs_paths могут
                            # отправить в CRM до MAX_OTHER_FILES=10 файлов по
                            # MAX_FILE_SIZE=100 МБ (src/utils/file_utils.py) — до ~1 ГБ
                            # сырых данных, ~1.33 ГБ после base64 (_file_to_crm,
                            # src/crm/client.py) одним JSON-телом: httpx отдаёт body
                            # write() ОДНИМ куском (ByteStream.__aiter__ — один yield,
                            # httpx/_content.py), то есть вся передача ограничена
                            # write-таймаутом как единым целым, и большой файл повышает
                            # шанс реально упереться в этот потолок, а не просто "долго
                            # обрабатывается". Внутренних ретраев в CRMClient._call() нет
                            # (ровно один client.post(...)) — умножать 270 секунд не на что.
                            #
                            # История: было 30 (TTL лока == таймауту httpx один в один —
                            # при недоступном CRM оба истекали одновременно, release()
                            # ловил LockNotOwnedError, см. git history), затем 45 (запас
                            # над ОДНИМ httpx-таймаутом — ошибочная модель: считали
                            # timeout=30.0 одним общим дедлайном на весь вызов, а не
                            # четырьмя независимыми), затем 120 (запас ~30 с сверху над
                            # честным ~90-секундным потолком connect(<30)+write(<30)+
                            # read(<30) — модель верная, но сами 30/30/30 оказались малы
                            # для настоящего большого файла: 93.6 МБ ТЗ в реальной задаче
                            # ловил "CRM request timed out" — httpx падал на фазе write/
                            # read задолго до истечения самого лока, см. src/crm/client.py).
                            # 300 — client.py поднял write/read до 120 каждый, запас
                            # (~30 с на чтение файлов с диска, сериализацию JSON, запись
                            # в БД) поверх нового потолка connect(<30)+write(<120)+
                            # read(<120)=270.


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
