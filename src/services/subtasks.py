"""Бизнес-логика подзадач: БД, CRM-синхронизация (outbox) и WS-уведомления."""

import logging
from typing import List, Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.user_models import User
from src.realtime import broadcast_task_event
from src.services import attachments
from src.task_logic.models import CrmOutbox, Subtask, Task
from src.task_logic.subtask_schemas import SubtaskCreate, SubtaskResponse, SubtaskUpdate
from src.crm.outbox_queries import ensure_create_event, pending_create_event_id
from src.tasks.crm_outbox_tasks import dispatch_outbox_row
from src.tasks.sharding import ensure_task_shard

logger = logging.getLogger(__name__)


async def create_subtask(
    db: AsyncSession,
    user: User,
    subtask: SubtaskCreate,
    specification: Optional[UploadFile] = None,
    other_files: Optional[list[UploadFile]] = None,
) -> SubtaskResponse:
    # Захватываем до возможного rollback: после него объекты сессии expired (MissingGreenlet).
    user_id, user_email = user.id, user.email

    # Валидация файлов — первой, до БД: невалидный файл блокирует создание (422).
    spec_validated, other_validated = await attachments.validate_files_for_create(
        specification, other_files
    )

    task = (await db.execute(select(Task).where(Task.id == subtask.task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # Проверка владельца намеренно не выполняется: shared board, подзадачи может создавать любой пользователь.

    task_title = task.title
    db_subtask = Subtask(
        title=subtask.title,
        description=subtask.description,
        completed=False,
        task_id=subtask.task_id,
        crm_subtask_id=None,
    )
    db.add(db_subtask)
    try:
        # flush(), а не commit(): нужен id подзадачи для путей файлов, но файлы не пишем, если вставка упадёт.
        await db.flush()
    except IntegrityError:
        await db.rollback()
        # IntegrityError — либо UNIQUE(title, task_id), либо FK: родительская задача удалена конкурентным
        # delete_task (он берёт FOR UPDATE, поэтому INSERT падает так, а не создаёт сироту).
        # Различаем перезапросом задачи; subtask.task_id читаем из входной схемы — после rollback ORM-атрибуты expired.
        task_exists = (
            await db.execute(select(Task.id).where(Task.id == subtask.task_id))
        ).scalar_one_or_none() is not None
        if not task_exists:
            raise HTTPException(status_code=404, detail="Task not found")
        raise HTTPException(
            status_code=409,
            detail=f"Subtask with title '{subtask.title}' already exists in this task",
        )

    # Шард всегда родительский; ensure_task_shard присваивает task.crm_shard лениво.
    shard = ensure_task_shard(task)

    # Сущность уже в транзакции: сохранение файлов best-effort, сбой файла не откатывает подзадачу.
    spec_path, other_paths, file_upload_errors = await attachments.save_files_for_create(
        db_subtask.id, attachments.SUBTASK_ATTACHMENTS, spec_validated, other_validated,
    )
    if spec_path is not None:
        db_subtask.specification_path = spec_path
    if other_paths:
        db_subtask.other_file_paths = other_paths

    # Если родитель ещё не в CRM, привязываем create подзадачи к его create-событию:
    # иначе parent_item_id не на что ссылаться.
    depends_on_id: Optional[int] = None
    if task.crm_task_id is None:
        depends_on_id = await pending_create_event_id(db, "task", task.id)

    outbox_create = CrmOutbox(
        aggregate_type="subtask", aggregate_id=db_subtask.id, operation="create", shard=shard,
        depends_on_event_id=depends_on_id,
        payload={
            "title": subtask.title, "description": subtask.description,
            "completed": False, "creator_email": user_email,
        },
    )
    db.add(outbox_create)
    db_subtask.sync_status = "pending"

    # Файлы уже на диске; flush()/commit() могут упасть — при откате подзадачи не будет, и файлы остались бы сиротами.
    try:
        await db.flush()

        outbox_files: Optional[CrmOutbox] = None
        if spec_path is not None or other_paths:
            outbox_files = CrmOutbox(
                aggregate_type="subtask", aggregate_id=db_subtask.id, operation="sync_files", shard=shard,
                depends_on_event_id=outbox_create.id,
                # sync_* — флаги затронутых слотов; воркер читает пути из БД.
                payload={
                    "crm_subtask_id": None,
                    "sync_specification": spec_path is not None,
                    "sync_other_files": bool(other_paths),
                },
            )
            db.add(outbox_files)

        # Захватываем до rollback: после него ORM-объекты expired (MissingGreenlet).
        await db.commit()
    except Exception:
        await attachments.delete_orphaned_files(spec_path, other_paths)
        raise
    # db.refresh() не нужен: expire_on_commit=False, id заполнен через INSERT ... RETURNING.

    # CRM вызывает только воркер. Если родитель не синхронизирован, create подзадачи уже ждёт его
    # create через depends_on_event_id.
    await dispatch_outbox_row(outbox_create)
    if outbox_files is not None:
        await dispatch_outbox_row(outbox_files)

    result = SubtaskResponse.model_validate(db_subtask)
    result.file_upload_errors = file_upload_errors or None

    # Используем входную схему и захваченные user_id/user_email, а не ORM-объекты (MissingGreenlet после rollback).
    await broadcast_task_event(
        "subtask_created", subtask.title,
        sender_email=user_email,
        task_title=task_title,
        task_id=subtask.task_id,
        actor_id=user_id,
        # exclude_user_id не передаётся: broadcast идёт всем, включая актора; своё/чужое различает фронт по actor_id.
    )
    return result


async def list_subtasks(
    db: AsyncSession, task_id: int, skip: int, limit: int,
) -> tuple[List[Subtask], int]:
    subtasks = (
        await db.execute(
            select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.id).offset(skip).limit(limit)
            # ORDER BY — стабильная пагинация.
        )
    ).scalars().all()
    total = (
        await db.execute(
            select(func.count()).select_from(Subtask).where(Subtask.task_id == task_id)
        )
    ).scalar_one()
    return subtasks, total


async def get_subtask(db: AsyncSession, subtask_id: int) -> Subtask:
    subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id))
    ).scalar_one_or_none()
    if subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")
    return subtask


async def update_subtask(
    db: AsyncSession,
    user: User,
    subtask_id: int,
    subtask_update: SubtaskUpdate,
) -> SubtaskResponse:
    # FOR UPDATE: без блокировки конкурентный delete_task мог бы каскадно удалить подзадачу между SELECT и commit,
    # и UPDATE упал бы с StaleDataError (500). Теперь delete_task дожидается этой транзакции.
    db_subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id).with_for_update())
    ).scalar_one_or_none()
    if db_subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")

    task = await db.get(Task, db_subtask.task_id)
    # Проверка владельца не выполняется — shared board (см. create_subtask).

    task_title = task.title
    update_data = subtask_update.model_dump(exclude_unset=True)
    # exclude_unset: только явно переданные поля (PATCH, а не PUT).
    crm_subtask_id = db_subtask.crm_subtask_id
    # title захватываем до commit: после rollback ORM-объект expired (MissingGreenlet).
    title_for_err = update_data["title"] if "title" in update_data else db_subtask.title

    for key, value in update_data.items():
        setattr(db_subtask, key, value)

    # Outbox вставляется в той же транзакции, что и UPDATE (как в services/tasks.py::update_task): без CRM-id строка
    # зависит от create-события, а при его отсутствии ensure_create_event ставит create. Построение строк внутри try:
    # autoflush их запросов выполняет UPDATE подзадачи, и нарушение уникальности title должно стать 409.
    outbox_rows: list[CrmOutbox] = []
    try:
        depends_on_id: Optional[int] = None
        if crm_subtask_id is None:
            depends_on_id, outbox_rows = await ensure_create_event(db, "subtask", db_subtask)
        db_subtask.sync_status = "pending"
        update_row = CrmOutbox(
            aggregate_type="subtask", aggregate_id=subtask_id, operation="update",
            shard=ensure_task_shard(task),
            depends_on_event_id=depends_on_id,
            payload={
                "crm_subtask_id": crm_subtask_id,
                "title": update_data.get("title"),
                "description": update_data.get("description"),
                "completed": update_data.get("completed"),
            },
        )
        db.add(update_row)
        outbox_rows.append(update_row)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Subtask with title '{title_for_err}' already exists in this task",
        )
    await broadcast_task_event(
        "subtask_updated", db_subtask.title,
        sender_email=user.email,
        task_title=task_title,
        task_id=db_subtask.task_id,
        subtask_id=subtask_id,
        actor_id=user.id,
        # exclude_user_id не передаётся: broadcast идёт всем, включая актора.
    )

    for row in outbox_rows:
        await dispatch_outbox_row(row)

    result = SubtaskResponse.model_validate(db_subtask)
    return result


async def delete_subtask(
    db: AsyncSession, user: User, subtask_id: int,
) -> SubtaskResponse:
    # FOR UPDATE: иначе параллельный delete_task мог бы прочитать crm_subtask_id этой подзадачи и удалить её в CRM второй раз.
    subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id).with_for_update())
    ).scalar_one_or_none()
    if subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")

    task = await db.get(Task, subtask.task_id)
    # Проверка владельца не выполняется — shared board.

    task_title = task.title
    parent_task_id = subtask.task_id
    snapshot = SubtaskResponse.model_validate(subtask)
    # Снимок создаётся до удаления: после commit объект detached.
    crm_subtask_id = subtask.crm_subtask_id

    # Outbox вставляется в той же транзакции, что и удаление; шард берём до удаления.
    outbox_row: Optional[CrmOutbox] = None
    if crm_subtask_id is not None:
        outbox_row = CrmOutbox(
            aggregate_type="subtask", aggregate_id=subtask_id, operation="delete",
            shard=ensure_task_shard(task),
            payload={"crm_subtask_id": crm_subtask_id},
        )
        db.add(outbox_row)

    await db.delete(subtask)
    await db.commit()

    # Файлы подзадачи удаляем с диска после commit.
    await attachments.cleanup(subtask_id, attachments.SUBTASK_ATTACHMENTS)
    await broadcast_task_event(
        "subtask_deleted", snapshot.title,
        sender_email=user.email,
        task_title=task_title,
        task_id=parent_task_id,
        actor_id=user.id,
        subtask_id=subtask_id,
        # exclude_user_id не передаётся: broadcast идёт всем, включая актора.
    )

    if outbox_row is not None:
        await dispatch_outbox_row(outbox_row)

    return snapshot
