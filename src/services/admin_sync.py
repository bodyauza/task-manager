"""Admin-only обзор статуса CRM-синхронизации — единственное место в проекте,
где виден ДЕТАЛЬНЫЙ статус синхронизации: CRM-id, последняя операция/статус/
число попыток outbox-строки и email владельца, агрегированные ОДНОЙ строкой на
задачу/подзадачу (см. ROW_NUMBER()-подзапрос ниже). Сам верхнеуровневый
sync_status ("unsynced" | "pending" | "synced" | "failed") виден и обычному
пользователю — бейджем на task-board/subtask-board, см. TaskResponse.sync_status
(src/task_logic/task_schemas.py) — это НЕ единственное место, где статус виден
вообще, только где виден ЭТОТ уровень детализации. От сырого списка событий
в sqladmin (src/admin/outbox_admin.py::CrmOutboxAdmin — одна строка на КАЖДОЕ
событие, без email владельца) эта страница отличается тем, что отвечает на
другой вопрос: не «что случилось с конкретным событием X», а «какие задачи
сейчас не синхронизированы и чьи они» — единица агрегации здесь сущность,
а не событие.

Оба запроса — ОДИН SQL-запрос каждый (не N+1 на строку): последняя строка
crm_outbox на агрегат берётся через ROW_NUMBER() OVER (PARTITION BY
aggregate_id ORDER BY updated_at DESC) в подзапросе, LEFT JOIN на неё —
задачи без единой строки crm_outbox (никогда не пытались синхронизироваться)
просто получают NULL в last_*-полях, а не пропадают из выдачи.

Без owner-фильтрации — сама admin-only защита эндпоинта (require_role,
см. routers/pages.py) уже достаточна, тот же принцип, что и в
routers/users.py::read_users (тоже не фильтрует по owner). Задачи и так
общий "shared board" (см. services/tasks.py) — здесь то же самое, просто
дополнительно с CRM-статусом и email владельца.
"""

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.user_models import User
from src.task_logic.admin_schemas import SubtaskSyncStatusResponse, TaskSyncStatusResponse
from src.task_logic.models import CrmOutbox, Subtask, Task


async def list_task_sync_status(db: AsyncSession) -> list[TaskSyncStatusResponse]:
    latest_outbox = (
        select(
            CrmOutbox.aggregate_id,
            CrmOutbox.operation,
            CrmOutbox.status,
            CrmOutbox.attempts,
            CrmOutbox.updated_at,
            func.row_number()
            .over(partition_by=CrmOutbox.aggregate_id, order_by=CrmOutbox.updated_at.desc())
            .label("rn"),
        )
        .where(CrmOutbox.aggregate_type == "task")
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                Task.id, Task.title, Task.owner_id, User.email,
                Task.crm_task_id, Task.sync_status,
                latest_outbox.c.operation, latest_outbox.c.status,
                latest_outbox.c.attempts, latest_outbox.c.updated_at,
            )
            .join(User, User.id == Task.owner_id)
            .outerjoin(
                latest_outbox,
                and_(latest_outbox.c.aggregate_id == Task.id, latest_outbox.c.rn == 1),
            )
            .order_by(Task.id)
        )
    ).all()
    return [
        TaskSyncStatusResponse(
            id=r[0], title=r[1], owner_id=r[2], owner_email=r[3],
            crm_task_id=r[4], sync_status=r[5],
            last_operation=r[6], last_outbox_status=r[7], last_attempts=r[8], last_updated_at=r[9],
        )
        for r in rows
    ]


async def list_subtask_sync_status(db: AsyncSession) -> list[SubtaskSyncStatusResponse]:
    latest_outbox = (
        select(
            CrmOutbox.aggregate_id,
            CrmOutbox.operation,
            CrmOutbox.status,
            CrmOutbox.attempts,
            CrmOutbox.updated_at,
            func.row_number()
            .over(partition_by=CrmOutbox.aggregate_id, order_by=CrmOutbox.updated_at.desc())
            .label("rn"),
        )
        .where(CrmOutbox.aggregate_type == "subtask")
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                Subtask.id, Subtask.title, Subtask.task_id, Task.title,
                Subtask.crm_subtask_id, Subtask.sync_status,
                latest_outbox.c.operation, latest_outbox.c.status,
                latest_outbox.c.attempts, latest_outbox.c.updated_at,
            )
            .join(Task, Task.id == Subtask.task_id)
            .outerjoin(
                latest_outbox,
                and_(latest_outbox.c.aggregate_id == Subtask.id, latest_outbox.c.rn == 1),
            )
            .order_by(Subtask.id)
        )
    ).all()
    return [
        SubtaskSyncStatusResponse(
            id=r[0], title=r[1], task_id=r[2], task_title=r[3],
            crm_subtask_id=r[4], sync_status=r[5],
            last_operation=r[6], last_outbox_status=r[7], last_attempts=r[8], last_updated_at=r[9],
        )
        for r in rows
    ]
