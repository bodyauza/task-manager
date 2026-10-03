"""Периодическая синхронизация таблицы project с CRM.

Запускается Celery Beat (src/celery_app.py) и вручную через POST /admin/crm-options/refresh, который ставит ту же задачу
в очередь. Один запуск на деплой независимо от числа UVICORN_WORKERS. Тело задачи выполняется через run_celery_task()
и использует async_session_maker() напрямую.
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
    """Upsert по crm_id: новые строки — INSERT, изменившиеся метки — UPDATE (переименование подхватывается для всех задач).
    Пропавшие из choices опции получают is_active=false, а не удаляются (на них ссылаются Task.project_id).
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
    # notin_(()) на пустом множестве — валидный SQL; пустой seen_ids бывает, только если CRM вернула пустой список, и тогда все строки деактивируются.
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
