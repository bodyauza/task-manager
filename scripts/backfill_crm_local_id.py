"""Разовая миграция данных: дозаписывает CRM-поле «Local ID» в записи, уже синхронизированные с CRM (crm_task_id/crm_subtask_id известен),
но созданные до появления этого поля.

find_task/find_subtask ищут запись только по Local ID (идемпотентность retry 'create'), поэтому у старых записей его нужно дописать.
Скрипт закрывает только записи с известным локально CRM-id (запись идемпотентна). Запись, чей 'create' ещё pending/failed в момент
миграции (в CRM вставлена, локально id нет), он не видит: найти её без Local ID нечем — это остаточный риск.

По умолчанию — только отчёт, без CRM-вызовов; запись — с явным --apply. Используются БД и CRM, которые резолвит текущий API_MODE.

Использование:
    .venv/Scripts/python.exe scripts/backfill_crm_local_id.py            # отчёт (dry-run)
    .venv/Scripts/python.exe scripts/backfill_crm_local_id.py --apply    # запись в CRM
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

# Регистрирует все ORM-модели в Base.registry (как scripts/cleanup_orphaned_uploads.py и src/celery_app.py).
import src.models  # noqa: F401,E402
from src.crm.subtask_service import SubtaskManager  # noqa: E402
from src.crm.task_service import TaskManager  # noqa: E402
from src.database import async_session_maker  # noqa: E402
from src.task_logic.models import Subtask, Task  # noqa: E402


async def _synced_rows(session, model) -> list[tuple[int, int]]:
    """Возвращает [(local_id, crm_id), ...] для строк с известным crm_id."""
    crm_id_column = model.crm_task_id if model is Task else model.crm_subtask_id
    rows = (
        await session.execute(select(model.id, crm_id_column).where(crm_id_column.is_not(None)))
    ).all()
    return [(row[0], row[1]) for row in rows]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="реально записать Local ID в CRM (по умолчанию — только отчёт)")
    args = parser.parse_args()

    async with async_session_maker() as session:
        task_rows = await _synced_rows(session, Task)
        subtask_rows = await _synced_rows(session, Subtask)

    print(f"Задач с известным crm_task_id: {len(task_rows)}")
    print(f"Подзадач с известным crm_subtask_id: {len(subtask_rows)}")

    if not args.apply:
        print("\nЭто отчёт (dry-run). Для реальной записи Local ID в CRM — запустить с --apply.")
        return

    task_mgr = TaskManager()
    subtask_mgr = SubtaskManager()
    errors = 0

    for local_id, crm_id in task_rows:
        try:
            await task_mgr.backfill_local_id(crm_id, local_id)
        except Exception as exc:
            errors += 1
            print(f"[ОШИБКА] task local_id={local_id} crm_task_id={crm_id}: {exc}")

    for local_id, crm_id in subtask_rows:
        try:
            await subtask_mgr.backfill_local_id(crm_id, local_id)
        except Exception as exc:
            errors += 1
            print(f"[ОШИБКА] subtask local_id={local_id} crm_subtask_id={crm_id}: {exc}")

    total = len(task_rows) + len(subtask_rows)
    print(f"\nОбработано {total} запис(ей), ошибок: {errors}.")


if __name__ == "__main__":
    asyncio.run(main())
