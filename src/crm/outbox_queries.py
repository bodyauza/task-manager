"""Запросы к crm_outbox, общие для сервисов задач, подзадач и вложений."""

from typing import Optional, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.user_models import User
from src.task_logic.models import CrmOutbox, Project, Subtask, Task
from src.tasks.sharding import ensure_task_shard


async def pending_create_event_id(
    db: AsyncSession, aggregate_type: str, aggregate_id: int,
) -> Optional[int]:
    """id ещё не завершённого 'create'-события агрегата; None, если его нет или оно done.

    Зависимые события (depends_on_event_id) не обгоняют create, пока у сущности нет CRM-id.
    """
    return (
        await db.execute(
            select(CrmOutbox.id).where(
                CrmOutbox.aggregate_type == aggregate_type,
                CrmOutbox.aggregate_id == aggregate_id,
                CrmOutbox.operation == "create",
                CrmOutbox.status != "done",
            ).order_by(CrmOutbox.id).limit(1)
        )
    ).scalar_one_or_none()


async def ensure_create_event(
    db: AsyncSession, aggregate_type: str, entity: Union[Task, Subtask],
) -> tuple[int, list[CrmOutbox]]:
    """id незавершённого 'create'-события сущности без CRM-id; при его отсутствии ставит новое по ТЕКУЩЕМУ состоянию.

    Вызывается, когда у сущности нет CRM-id и нужно поставить зависимое событие (update, sync_files). Create-события
    может не быть у сущности, заведённой в обход сервисов или до появления интеграции с CRM: без него зависимое событие
    ждало бы несуществующий create, а правка молча не попала бы в CRM. Повторный create безопасен: _do_create_* сначала
    ищет запись по Local ID и не плодит дубликат.

    Для подзадачи, чей родитель тоже без CRM-id, create ставится и ему (create подзадачи зависит от create родителя).
    Возвращает (id события, новые строки); вызывающий код после commit обязан передать новые строки в dispatch_outbox_row.
    Строки добавляются в сессию и flush-атся (нужен id), commit — на вызывающем коде.
    """
    existing_id = await pending_create_event_id(db, aggregate_type, entity.id)
    if existing_id is not None:
        return existing_id, []

    new_rows: list[CrmOutbox] = []
    depends_on_id: Optional[int] = None
    if aggregate_type == "task":
        owner_email = (
            await db.execute(select(User.email).where(User.id == entity.owner_id))
        ).scalar_one_or_none()
        project_crm_id: Optional[str] = None
        if entity.project_id is not None:
            project_crm_id = (
                await db.execute(select(Project.crm_id).where(Project.id == entity.project_id))
            ).scalar_one_or_none()
        shard = ensure_task_shard(entity)
        payload = {
            "title": entity.title, "description": entity.description, "completed": entity.completed,
            "project": project_crm_id, "creator_email": owner_email,
        }
    else:
        parent = await db.get(Task, entity.task_id)
        shard = ensure_task_shard(parent)
        if parent.crm_task_id is None:
            depends_on_id, parent_rows = await ensure_create_event(db, "task", parent)
            new_rows.extend(parent_rows)
        # У подзадачи нет владельца: создатель в CRM останется пустым.
        payload = {
            "title": entity.title, "description": entity.description, "completed": entity.completed,
            "creator_email": None,
        }

    row = CrmOutbox(
        aggregate_type=aggregate_type, aggregate_id=entity.id, operation="create", shard=shard,
        depends_on_event_id=depends_on_id, payload=payload,
    )
    db.add(row)
    await db.flush()
    new_rows.append(row)
    return row.id, new_rows
