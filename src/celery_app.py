"""Celery-приложение проекта — единственный инстанс на деплой.

Celery + Redis решают три задачи: outbox-синхронизацию с CRM (src/tasks/crm_outbox_tasks.py), периодическую
синхронизацию таблицы project (src/tasks/global_lists_tasks.py) и WebSocket-рассылку между воркерами через Redis Pub/Sub.
Все используют один Redis (settings.REDIS_URL) и один celery_app. Подключение к Redis ленивое, поэтому
lifespan-хук в main.py не нужен.
"""

import logging
from typing import Awaitable, TypeVar

from celery import Celery
from celery.schedules import crontab
from celery.signals import task_failure

from src.config import settings
from src.crm.client import aclose_http_client
from src.crm.crm_config import crm_settings
from src.database import run_isolated
# Регистрирует все ORM-модели (включая User/Role) в Base.metadata до первого запроса любой задачи:
# Task.owner ссылается на "User" строкой, а воркер иначе не импортирует src.auth.user_models, и configure_mappers() падает
# с InvalidRequestError.
import src.models  # noqa: F401,E402

celery_app = Celery("task_manager", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

# Надёжность доставки: дополняет outbox (он защищает факт постановки), эти флаги — от потери задачи уже в очереди Celery.
celery_app.conf.update(
    # ack после выполнения: при падении воркера (OOM, kill -9) задача возвращается в очередь, а не пропадает.
    task_acks_late=True,
    # В паре с task_acks_late: задача потерянного воркера переставляется, а не пропадает молча (reconcile подбирает только crm_outbox).
    task_reject_on_worker_lost=True,
    # Повторять подключение к брокеру вместо падения при старте, пока Redis не готов (depends_on гарантирует лишь порядок старта).
    broker_connection_retry_on_startup=True,
    # События для Flower: без них он видит воркеры, но не задачи. task_send_sent_event добавляет стадию «поставлена в очередь».
    worker_send_task_events=True,
    task_send_sent_event=True,
    # dispatch_outbox_row() и sync_project_table.delay() вызывают apply_async/delay в потоке (to_thread), но сам поток
    # зависает на таймауте kombu (~4 с) с повторами. Глобальные таймауты ниже сокращают это сразу в обоих местах.
    broker_connection_timeout=2,  # вместо дефолтных ~4 с
    task_publish_retry_policy={
        "max_retries": 2,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 0.5,
    },
)

logger = logging.getLogger(__name__)


# task_failure срабатывает на любой необработанной задаче — одна подписка на все задачи проекта. Закрывает разрыв
# в наблюдаемости: код проекта иначе логировал только сбой CRM-вызова внутри _process_outbox_row_async (он там же
# перехватывается, и сигнал не дублирует лог). Подписка не снимает зависимость от --loglevel, но даёт отдельный
# логгер src.celery_app.
@task_failure.connect
def _log_task_failure(sender=None, task_id=None, exception=None, einfo=None, **_kwargs) -> None:
    logger.error(
        "Celery-задача %s (task_id=%s) завершилась необработанным исключением: %r\n%s",
        getattr(sender, "name", sender), task_id, exception,
        einfo.traceback if einfo is not None else "",
    )


_T = TypeVar("_T")


def run_celery_task(coro: Awaitable[_T]) -> _T:
    """Точка входа для тела каждой Celery-задачи: вместо голого asyncio.run().

    run_isolated() освобождает пул SQLAlchemy, а общий httpx.AsyncClient (src/crm/client.py), созданный под одним
    event loop, ломается в следующем asyncio.run(), поэтому клиент закрывается здесь. aclose_http_client() безопасен:
    следующий CRM-вызов лениво создаст новый клиент.
    """
    async def _wrapper() -> _T:
        try:
            return await coro
        finally:
            await aclose_http_client()

    return run_isolated(_wrapper())

# task_always_eager сознательно не включаем: задачи — sync-обёртки над asyncio.run(), и eager-выполнение .delay() из-под
# работающего loop pytest-asyncio упало бы с RuntimeError. Тесты мокируют .delay() напрямую.
celery_app.conf.beat_schedule = {
    "sync-project-table": {
        "task": "src.tasks.global_lists_tasks.sync_project_table",
        "schedule": crm_settings.PROJECT_SYNC_INTERVAL_SECONDS,
    },
    "reconcile-pending-crm-outbox": {
        "task": "src.tasks.crm_outbox_tasks.reconcile_pending_outbox",
        # Короче интервала синхронизации справочника: это сеть безопасности от потери данных, зависшую строку не стоит ждать три минуты.
        "schedule": 60,
    },
    "reconcile-blocked-crm-outbox": {
        "task": "src.tasks.crm_outbox_tasks.reconcile_blocked_outbox",
        # Реже: 'blocked' — штатное ожидание зависимости, а не сбой.
        "schedule": 300,
    },
    "cleanup-done-crm-outbox": {
        "task": "src.tasks.crm_outbox_tasks.cleanup_done_outbox",
        # Раз в сутки в 03:00 UTC — вне пиковых часов (crm_outbox иначе растёт бесконечно). Крон, а не интервал.
        "schedule": crontab(hour=3, minute=0),
    },
}
# Явный импорт вместо autodiscover_tasks(["src.tasks"]): autodiscover ищет подмодуль src.tasks.tasks, которого нет, и не
# регистрирует ни одной задачи. Импорт модуля регистрирует все @celery_app.task в нём.
import src.tasks.crm_outbox_tasks  # noqa: F401,E402
import src.tasks.global_lists_tasks  # noqa: F401,E402

# Очереди шардов crm_sync.shard_N не задаются через task_routes: шард — свойство аргумента (task.crm_shard), а не задачи.
# Продюсер передаёт очередь явно: process_outbox_row.apply_async(args=[outbox_id], queue=f"crm_sync.{shard}").
# Шарды — src/tasks/sharding.py::shard_names(), по одному celery-worker-shard-N (--pool=solo) на шард.
