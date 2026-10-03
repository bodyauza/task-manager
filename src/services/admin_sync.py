"""Admin-only обзор CRM-синхронизации: по одной строке на задачу/подзадачу с CRM-id, последней outbox-операцией,
статусом, числом попыток и email владельца (в отличие от sqladmin, где одна строка на каждое событие).

Каждый запрос — один SQL: последняя строка crm_outbox на агрегат берётся через ROW_NUMBER() в подзапросе,
LEFT JOIN оставляет в выдаче задачи без outbox-строк. Без owner-фильтра: доступ ограничен ролью admin.
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
