"""Периодическая синхронизация локальной таблицы project с CRM.

Запускается ТОЛЬКО Celery Beat (src/celery_app.py::beat_schedule) — не веб-
процессом и не по запросу пользователя напрямую (кроме ручного admin-триггера
через POST /admin/crm-options/refresh, src/routers/pages.py, который просто
ставит эту же задачу в очередь раньше расписания). Один запуск на весь деплой
независимо от числа UVICORN_WORKERS-процессов — обоснование см.
docs/project_field_crm_implementation_guide.md, §1.4.

Celery-задачи по умолчанию синхронные — src.celery_app.run_celery_task()
оборачивает async-тело в свой asyncio.run(), освобождает пул соединений
SQLAlchemy и закрывает общий httpx-клиент CRM сразу после (см. её докстринг —
без этого второй и последующие вызовы в одном и том же Celery-воркере падают
RuntimeError из-за переиспользования asyncpg-соединения/httpx-клиента от
закрытого event loop). Само тело использует async_session_maker() напрямую
(без FastAPI Depends/запроса), тот же приём, что уже применяет
create_initial_roles() в src/main.py вне HTTP-запроса.
"""

import logging
from typing import Dict

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.celery_app import celery_app, run_celery_task
from src.crm.crm_config import crm_settings
from src.crm.global_lists_service import GlobalListsManager
from src.database import async_session_maker
from src.task_logic.models import Project

logger = logging.getLogger(__name__)


async def _upsert_project_rows(db: AsyncSession, choices: Dict[str, str]) -> None:
    """Upsert по crm_id: новые строки — INSERT, изменившиеся метки — UPDATE
    (переименование в CRM подхватывается здесь автоматически для ВСЕХ задач,
    ссылающихся на этот project_id, — не нужно ждать, пока кто-то отредактирует
    задачу заново). Опции, пропавшие из choices, переводятся в is_active=false,
    а не удаляются — на них могут ссылаться существующие Task.project_id
    (удаление физически сломало бы FK).
    """
    seen_ids = set(choices.keys())
    if choices:
        stmt = pg_insert(Project).values([
            {"crm_id": crm_id, "label": label, "is_active": True, "synced_at": func.now()}
            for crm_id, label in choices.items()
        ])
        stmt = stmt.on_conflict_do_update(
            index_elements=["crm_id"],
            set_={"label": stmt.excluded.label, "is_active": True, "synced_at": stmt.excluded.synced_at},
        )
        await db.execute(stmt)
    # notin_(()) на пустом множестве — валидный SQL (WHERE false), но seen_ids
    # пустым бывает только если CRM вернула пустой список опций; в этом случае
    # предложение ниже корректно деактивирует все текущие строки.
    await db.execute(
        update(Project).where(Project.crm_id.notin_(seen_ids)).values(is_active=False)
    )
    await db.commit()
    logger.info("CRM: project table synced, %d active option(s)", len(choices))


async def _sync_project_table_async() -> None:
    choices = await GlobalListsManager().get_choices(crm_settings.LIST_PROJECT)
    async with async_session_maker() as db:
        await _upsert_project_rows(db, choices)


@celery_app.task(name="src.tasks.global_lists_tasks.sync_project_table")
def sync_project_table() -> None:
    run_celery_task(_sync_project_table_async())
