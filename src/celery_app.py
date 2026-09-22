"""Celery-приложение проекта — единственный инстанс на весь деплой.

Три независимые причины, по которым в проект введена связка Celery + Redis:

1. Outbox-очередь для синхронизации задач/подзадач с CRM «Руководитель» —
   durable retry для CRM-вызовов, теряющихся при падении процесса между
   db.commit() и вызовом CRM (src/tasks/crm_outbox_tasks.py).
2. Периодическая синхронизация локальной таблицы project со списком «Проект»
   CRM (src/tasks/global_lists_tasks.py) — один запуск на весь деплой,
   независимо от числа UVICORN_WORKERS.
3. WebSocket — горизонтальное масштабирование через Redis Pub/Sub (см. также
   комментарий к UVICORN_WORKERS в src/Dockerfile).

Все три пункта используют один и тот же Redis-инстанс (settings.REDIS_URL,
src/config.py) и один и тот же celery_app — второй Celery()-инстанс не
заводится. Веб-процесс (FastAPI) импортирует отсюда только объекты задач
(например, sync_project_table.delay(...)) для постановки в очередь — сам
celery_app не открывает соединений при импорте (ленивое подключение к
Redis происходит только при первой реальной отправке/выполнении задачи),
поэтому никакого lifespan-хука в src/main.py для этого модуля не требуется.
"""

from typing import Awaitable, TypeVar

from celery import Celery
from celery.schedules import crontab

from src.config import settings
from src.crm.client import aclose_http_client
from src.crm.crm_config import crm_settings
from src.database import run_isolated
# Регистрирует ВСЕ ORM-модели (Task/Subtask/Project/CrmOutbox И User/Role) в
# Base.metadata/registry ДО того, как какая-либо Celery-задача выполнит первый
# запрос — тот же приём, что alembic/env.py уже использует для генерации
# миграций (import src.models). Без этой строки src.tasks.crm_outbox_tasks/
# global_lists_tasks ниже импортируют только src.task_logic.models (Task,
# Subtask, Project, CrmOutbox) — src.auth.user_models (User, Role) в процессе
# celery-worker никогда не импортируется НИ ОДНИМ модулем, который реально
# нужен воркеру. Task.owner = relationship("User", ...) — строковая ссылка,
# резолвится SQLAlchemy лениво, при первом configure_mappers() (запускается
# при первом же ORM-запросе любой Celery-задачи) — если к этому моменту класс
# User не зарегистрирован в том же Base.registry, resolution падает:
# sqlalchemy.exc.InvalidRequestError: "expression 'User' failed to locate a
# name ('User')" — воркер не может выполнить ни одну задачу, обнаружено живым
# прогоном docker compose up (юнит-тесты этого не ловят: pytest импортирует
# src.main, который импортирует и routers, и auth напрямую, поэтому оба
# модуля регистрируются независимо от этой строки).
import src.models  # noqa: F401,E402

celery_app = Celery("task_manager", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

# Настройки надёжности доставки задач — дополняют, а не заменяют, durable
# outbox (crm_outbox, см. докстринг модуля выше): outbox защищает от потери
# самого ФАКТА постановки задачи (строка commit'ится в той же транзакции, что
# и основное изменение), эти три флага — от потери задачи ПОСЛЕ того, как она
# уже встала в очередь Celery.
celery_app.conf.update(
    # ack ПОСЛЕ выполнения задачи, а не сразу при получении её воркером —
    # если воркер упадёт посередине выполнения (OOM, kill -9, падение
    # контейнера), Celery не посчитает задачу подтверждённой и переставит её
    # в очередь для другого воркера. Без этого флага (дефолт Celery) задача
    # ack'ается ДО выполнения — при падении воркера она тихо пропадает,
    # несмотря на то, что формально осталась в очереди Redis.
    task_acks_late=True,
    # Дополняет task_acks_late: если Celery обнаруживает, что воркер, уже
    # взявший задачу в работу, пропал (SIGKILL, OOM-killer) — задача считается
    # потерянной и не перезапускается автоматически НА ДРУГОМ воркере молча.
    # Без этого combo (acks_late + reject_on_worker_lost) возможна тихая
    # потеря задачи именно в момент падения воркера — редкий, но реальный
    # класс отказа, не покрываемый reconcile_pending_outbox (та подхватывает
    # ТОЛЬКО строки crm_outbox, а не любые Celery-задачи вообще, например
    # sync_project_table).
    task_reject_on_worker_lost=True,
    # Без этого флага (дефолт Celery до недавних версий) воркер/beat падает
    # при старте, если Redis ещё не готов принимать соединения — реальный
    # сценарий в docker-compose, где celery-worker* поднимается практически
    # одновременно с контейнером redis (depends_on гарантирует только порядок
    # СТАРТА контейнеров, не готовность Redis принимать TCP-соединения).
    # С флагом Celery повторяет попытку подключения к брокеру вместо падения.
    broker_connection_retry_on_startup=True,
    # События для Flower (src/docker-compose.yml, сервис flower): без них
    # Flower видит воркеры по heartbeat'ам, но НЕ видит сами задачи — ни
    # список, ни статус/аргументы/результат/время выполнения. Воркеры шлют
    # события в Redis (celery.events) независимо от того, запущен ли Flower
    # (не нужен флаг -E в каждой команде воркера); task_send_sent_event
    # добавляет событие task-sent на стороне продюсера (веб-процесс/Beat), чтобы
    # в Flower была видна и стадия «поставлена в очередь, ещё не взята воркером».
    worker_send_task_events=True,
    task_send_sent_event=True,
)

_T = TypeVar("_T")


def run_celery_task(coro: Awaitable[_T]) -> _T:
    """Точка входа для тела КАЖДОЙ Celery-задачи этого проекта (см. вызовы в
    src/tasks/crm_outbox_tasks.py/global_lists_tasks.py) — вместо голого
    asyncio.run() или одного src.database.run_isolated().

    src.database.run_isolated() уже освобождает пул соединений SQLAlchemy
    после каждого изолированного запуска (см. её докстринг — иначе второй и
    последующие вызовы в одном и том же Celery-воркере падают RuntimeError
    из-за переиспользования asyncpg-соединения от закрытого event loop). Но
    ВСЕ задачи в src/tasks/*.py также делают CRM-вызовы через общий
    httpx.AsyncClient (src/crm/client.py::_get_shared_http_client) — тот же
    класс проблемы: httpx-клиент, созданный под одним event loop, ломается
    при попытке использовать его из следующего asyncio.run() (новый loop),
    но уже не при закрытии пула, а прямо при следующем HTTP-запросе —
    обнаружено живым прогоном docker compose up (второй/третий тик Celery
    Beat в одном и том же воркере), не юнит-тестами (там один процесс
    pytest = один event loop на тест, ни разу не два подряд asyncio.run() в
    одном процессе поверх одного и того же клиента).

    aclose_http_client() здесь безопасен: он лишь обнуляет module-level
    singleton (см. его же реализацию) — следующий CRM-вызов из ЛЮБОГО
    процесса (в том числе из веб-процесса, если он импортировал этот же
    модуль) просто лениво создаст новый клиент под актуальный event loop,
    как и при самом первом обращении.
    """
    async def _wrapper() -> _T:
        try:
            return await coro
        finally:
            await aclose_http_client()

    return run_isolated(_wrapper())

# ВАЖНО: task_always_eager здесь сознательно НЕ включается для тестового
# режима, хотя это распространённый паттерн для тестирования Celery. Причина:
# все задачи этого проекта (sync_project_table, process_outbox_row,
# reconcile_pending_outbox) — sync-обёртки над asyncio.run(...) (см.
# src/tasks/*.py). Если какой-нибудь тест вызовет .delay() из-под уже
# работающего event loop pytest-asyncio (например, из HTTP-хендлера
# POST /admin/crm-options/refresh, вызванного тестовым AsyncClient), eager-
# режим выполнил бы задачу СИНХРОННО, ПРЯМО ТАМ — то есть asyncio.run()
# оказался бы вызван изнутри уже запущенного loop и упал бы с RuntimeError
# ("asyncio.run() cannot be called from a running event loop"), а не с
# понятной ошибкой подключения к Redis. Тесты, которым нужно проверить, что
# .delay() был вызван, мокируют его напрямую (unittest.mock.patch на
# конкретную функцию задачи) — см. tests/test_pages_admin.py,
# tests/test_crm_outbox.py — а не полагаются на eager-выполнение.
celery_app.conf.beat_schedule = {
    "sync-project-table": {
        "task": "src.tasks.global_lists_tasks.sync_project_table",
        "schedule": crm_settings.PROJECT_SYNC_INTERVAL_SECONDS,
    },
    "reconcile-pending-crm-outbox": {
        "task": "src.tasks.crm_outbox_tasks.reconcile_pending_outbox",
        # Короче интервала синхронизации справочника — это сеть безопасности от
        # потери данных (см. §1.4/services/tasks.py), а не фоновая синхронизация
        # справочника; не должна ждать три минуты, чтобы заметить зависшую строку.
        "schedule": 60,
    },
    "reconcile-blocked-crm-outbox": {
        "task": "src.tasks.crm_outbox_tasks.reconcile_blocked_outbox",
        # Реже: 'blocked' — не сигнал сбоя (в отличие от зависшего 'pending'),
        # а штатное ожидание своей event-зависимости (depends_on_event_id) —
        # нет смысла проверять чаще, чем реалистичный срок жизни зависимости.
        "schedule": 300,
    },
    "cleanup-done-crm-outbox": {
        "task": "src.tasks.crm_outbox_tasks.cleanup_done_outbox",
        # Раз в сутки, в 03:00 UTC — вне пиковых часов; таблица crm_outbox иначе
        # растёт бесконечно (см. докстринг _cleanup_done_outbox_async). Крон, а
        # не интервал в секундах (как остальные задачи выше): фиксированное
        # время суток, а не «раз в N секунд от момента старта Beat».
        "schedule": crontab(hour=3, minute=0),
    },
}
# НЕ autodiscover_tasks(["src.tasks"]): с дефолтным related_name="tasks" оно
# ищет подмодуль src.tasks.tasks, которого нет — наши модули называются
# crm_outbox_tasks.py/global_lists_tasks.py, поэтому autodiscover молча не
# регистрировал НИ ОДНОЙ задачи (баннер воркера показывал пустой [tasks]),
# и любой .delay()/Beat-тик падал KeyError "Received unregistered task of
# type ..." — обнаружено живым прогоном `docker compose up` (юнит-тесты этого
# не ловят: они вызывают _process_outbox_row_async/_sync_project_table_async
# напрямую, минуя реестр задач Celery). Явный импорт — тот же приём, что и в
# src/models/__init__.py для регистрации ORM-моделей: сам факт импорта модуля
# выполняет тело файла и регистрирует все @celery_app.task в нём.
import src.tasks.crm_outbox_tasks  # noqa: F401,E402
import src.tasks.global_lists_tasks  # noqa: F401,E402

# ВАЖНО про шардированные очереди crm_sync.shard_0..shard_{M-1}: маршрут НЕ
# задаётся статическим
# task_routes, потому что шард — не свойство самой задачи process_outbox_row,
# а свойство конкретного аргумента (task.crm_shard конкретного агрегата),
# вычисляемое продюсером в рантайме. Продюсер (src/tasks/crm_outbox_tasks.py,
# src/services/tasks.py/subtasks.py) передаёт очередь явно:
# process_outbox_row.apply_async(args=[outbox_id], queue=f"crm_sync.{shard}").
# Список самих шардов — src/tasks/sharding.py::shard_names(), число шардов —
# crm_settings.OUTBOX_SHARD_COUNT; каждому шарду соответствует отдельный
# сервис celery-worker-shard-N в src/docker-compose.yml (--pool=solo -Q
# crm_sync.shard_N, concurrency=1 — гарантия строгого порядка внутри шарда).
