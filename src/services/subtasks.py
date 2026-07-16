"""Бизнес-логика подзадач: БД + CRM-синхронизация + WS-уведомления.

Вынесена из routers/subtasks.py — тот же SRP-разбор, что и в services/tasks.py.
"""

import logging
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
from src.crm.subtask_service import SubtaskCRMSync
from src.realtime import broadcast_task_event
from src.services import attachments
from src.task_logic.models import Subtask, Task
from src.task_logic.subtask_schemas import SubtaskCreate, SubtaskResponse, SubtaskUpdate

logger = logging.getLogger(__name__)


async def create_subtask(
    db: AsyncSession, user: User, subtask: SubtaskCreate, crm: SubtaskCRMSync,
) -> SubtaskResponse:
    task = (await db.execute(select(Task).where(Task.id == subtask.task_id))).scalar_one_or_none()
    # scalar_one_or_none(): вернёт объект Task или None; SELECT FROM task WHERE id=?
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # if task.owner_id != user.id:
    #     raise HTTPException(status_code=403, detail="Forbidden")

    crm_subtask_id: Optional[int] = None
    # CRM-синхронизация возможна только если родительская задача зарегистрирована в CRM
    if task.crm_task_id is not None:
        try:
            crm_result = await crm.create_subtask(
                parent_item_id=task.crm_task_id,    # CRM-ID задачи-родителя
                title=subtask.title,
                description=subtask.description,
                completed=False,                    # новые подзадачи всегда начинают как невыполненные
            )
            crm_subtask_id = crm_result.get("id")  # None если CRM вернул нестандартный ответ
            logger.info("CRM: subtask '%s' created, crm_id=%s", subtask.title, crm_subtask_id)
        except Exception as exc:
            logger.error("CRM create_subtask failed for '%s': %s", subtask.title, exc)
            # best-effort: ошибка CRM не блокирует создание подзадачи в локальной БД

    task_title = task.title                          # захватить до commit (объект будет expired)
    db_subtask = Subtask(
        title=subtask.title,
        description=subtask.description,
        completed=False,
        task_id=subtask.task_id,
        crm_subtask_id=crm_subtask_id,             # None если CRM недоступен или не синхронизирован
    )
    db.add(db_subtask)                              # добавить в сессию (состояние pending)
    try:
        await db.commit()                           # INSERT INTO subtask ...; фиксирует транзакцию
    except IntegrityError:
        await db.rollback()                         # откатить транзакцию при нарушении ограничения БД
        # Компенсирующая транзакция: CRM-запись создана, но commit упал →
        # удаляем запись из CRM, чтобы не оставить сироту.
        if crm_subtask_id is not None:
            try:
                await crm.delete_subtask(crm_subtask_id)
            except Exception as crm_exc:
                logger.error("CRM compensating delete failed for crm_id=%s: %s", crm_subtask_id, crm_exc)
        # IntegrityError здесь может означать две разные вещи, и клиенту нужно ответить по-разному:
        #   1. UNIQUE(title, task_id) — подзадача с таким названием уже есть в этой задаче (обычный случай).
        #   2. ForeignKeyViolation — родительская задача удалена конкурентным delete_task ровно между
        #      нашим SELECT task (строка выше) и этим commit. delete_task берёт FOR UPDATE на task
        #      (см. services/tasks.py::delete_task), поэтому наш INSERT либо целиком проходит до удаления
        #      задачи, либо блокируется и после её удаления падает именно так — а не создаёт
        #      "подзадачу-сироту", которую потом пришлось бы искать руками.
        # Различаем причины перезапросом задачи. subtask.task_id — поле входного Pydantic-объекта
        # (SubtaskCreate), а не атрибут expired ORM-объекта task — читать его после rollback безопасно,
        # в отличие от task.id, которое после rollback вызвало бы MissingGreenlet (lazy-load в async).
        task_exists = (
            await db.execute(select(Task.id).where(Task.id == subtask.task_id))
        ).scalar_one_or_none() is not None
        if not task_exists:
            raise HTTPException(status_code=404, detail="Task not found")
        raise HTTPException(
            status_code=409,
            detail=f"Subtask with title '{subtask.title}' already exists in this task",
        )
    await db.refresh(db_subtask)                    # перечитать id и server_default из БД после commit
    await broadcast_task_event(
        "subtask_created", db_subtask.title,
        sender_email=user.email,  # email актора → data.sender для других пользователей
        task_title=task_title,    # название родительской задачи → data.task_title в payload
        task_id=subtask.task_id,  # subtask-board.js сравнивает с своим taskId, чтобы решить,
        # относится ли событие к странице подзадач, которая сейчас открыта у клиента
        actor_id=user.id,         # попадает в payload через **extra → data.actor_id на фронте;
        # exclude_user_id не передаётся (None по умолчанию): broadcast идёт всем, включая актора.
        # Актор и остальные получают один payload; разделение форматов — на фронте:
        #   String(data.actor_id) === userId → "Subtask for task '...' created: '...'"
        #   иначе                            → "email: Создана подзадача «...» [...]"
    )
    result = SubtaskResponse.model_validate(db_subtask)     # ORM-объект → Pydantic-схема
    result.crm_synced = crm_subtask_id is not None  # True если CRM вернул id, False если нет
    return result


async def list_subtasks(
    db: AsyncSession, task_id: int, skip: int, limit: int,
) -> tuple[List[Subtask], int]:
    subtasks = (
        await db.execute(
            select(Subtask).where(Subtask.task_id == task_id).offset(skip).limit(limit)
            # SELECT * FROM subtask WHERE task_id=? OFFSET ? LIMIT ?
        )
    ).scalars().all()                               # scalars(): первая колонка как ORM-объекты; all(): список
    total = (
        await db.execute(
            select(func.count()).select_from(Subtask).where(Subtask.task_id == task_id)
            # SELECT COUNT(*) FROM subtask WHERE task_id=?
        )
    ).scalar_one()                                  # scalar_one(): единственное скалярное значение
    return subtasks, total


async def get_subtask(db: AsyncSession, subtask_id: int) -> Subtask:
    subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id))
    ).scalar_one_or_none()                          # SELECT * FROM subtask WHERE id=?
    if subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")
    return subtask                                  # FastAPI сериализует через response_model


async def update_subtask(
    db: AsyncSession,
    user: User,
    subtask_id: int,
    subtask_update: SubtaskUpdate,
    crm: SubtaskCRMSync,
) -> SubtaskResponse:
    db_subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id))
    ).scalar_one_or_none()
    if db_subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")

    task = await db.get(Task, db_subtask.task_id)   # SELECT FROM task WHERE id=?; гарантированно не None (FK)
    # if task.owner_id != user.id:
    #     raise HTTPException(status_code=403, detail="Forbidden")
    # 403 Forbidden: пользователь не владеет родительской задачей → не может менять её подзадачи

    task_title = task.title                          # захватить до commit (объект будет expired)
    update_data = subtask_update.model_dump(exclude_unset=True)
    # exclude_unset=True: только явно переданные поля; отсутствующие поля не попадут в dict
    crm_subtask_id = db_subtask.crm_subtask_id     # захватить до refresh, пока объект в сессии
    # захватить title до commit: после rollback ORM-объект expired и db_subtask.title
    # требует lazy-load, несовместимого с async-контекстом (MissingGreenlet)
    title_for_err = update_data["title"] if "title" in update_data else db_subtask.title

    for key, value in update_data.items():
        setattr(db_subtask, key, value)             # применяем изменения к ORM-объекту
    try:
        await db.commit()                           # UPDATE subtask SET ... WHERE id=?
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Subtask with title '{title_for_err}' already exists in this task",
        )
    await db.refresh(db_subtask)                    # перечитать актуальные данные из БД
    await broadcast_task_event(
        "subtask_updated", db_subtask.title,
        sender_email=user.email,  # email актора → data.sender для других пользователей
        task_title=task_title,    # название родительской задачи → data.task_title в payload
        task_id=db_subtask.task_id,  # subtask-board.js сравнивает с своим taskId
        subtask_id=subtask_id,    # детальная страница подзадачи (subtask-detail.js) сравнивает
        # с своим subtaskId и перечитывает подзадачу через loadSubtask()
        actor_id=user.id,         # попадает в payload через **extra → data.actor_id на фронте;
        # exclude_user_id не передаётся (None по умолчанию): broadcast идёт всем, включая актора.
        # Актор и остальные получают один payload; разделение форматов — на фронте:
        #   String(data.actor_id) === userId → "Subtask for task '...' updated: '...'"
        #   иначе                            → "email: Обновлена подзадача «...» [...]"
    )

    crm_synced: Optional[bool] = None
    if crm_subtask_id is not None:                  # подзадача ранее была синхронизирована с CRM
        try:
            await crm.update_subtask(
                subtask_id=crm_subtask_id,          # CRM-ID подзадачи
                title=update_data.get("title"),     # None если поле не передано → CRM не изменит
                description=update_data.get("description"),
                completed=update_data.get("completed"),
            )
            crm_synced = True
            logger.info("CRM: subtask id=%s updated (crm_id=%s)", subtask_id, crm_subtask_id)
        except Exception as exc:
            crm_synced = False                      # обновление в CRM не удалось; локально уже сохранено
            logger.error("CRM update_subtask failed for id=%s: %s", subtask_id, exc)
    else:
        crm_synced = False                          # подзадача изначально не была в CRM

    result = SubtaskResponse.model_validate(db_subtask)
    result.crm_synced = crm_synced
    return result


async def delete_subtask(
    db: AsyncSession, user: User, subtask_id: int, crm: SubtaskCRMSync,
) -> SubtaskResponse:
    subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id))
    ).scalar_one_or_none()
    if subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")

    task = await db.get(Task, subtask.task_id)      # SELECT FROM task WHERE id=?
    # if task.owner_id != user.id:
    #     raise HTTPException(status_code=403, detail="Forbidden")

    task_title = task.title                          # захватить до commit (объект будет expired)
    parent_task_id = subtask.task_id                 # захватить до commit — для payload task_id
    snapshot = SubtaskResponse.model_validate(subtask)
    # snapshot создаётся ДО удаления: после db.delete+commit объект в состоянии detached,
    # атрибуты недоступны; snapshot хранит данные для возврата в ответе
    crm_subtask_id = subtask.crm_subtask_id        # захватить до удаления

    await db.delete(subtask)                        # DELETE FROM subtask WHERE id=?
    await db.commit()                               # фиксируем; после этого запись в БД не существует

    # Удаляем файлы подзадачи с диска после commit.
    attachments.cleanup(subtask_id, attachments.SUBTASK_ATTACHMENTS)
    await broadcast_task_event(
        "subtask_deleted", snapshot.title,
        sender_email=user.email,  # email актора → data.sender для других пользователей
        task_title=task_title,    # название родительской задачи → data.task_title в payload
        task_id=parent_task_id,   # subtask-board.js сравнивает с своим taskId, чтобы обновить список
        actor_id=user.id,         # попадает в payload через **extra → data.actor_id на фронте;
        subtask_id=subtask_id,    # детальная страница подзадачи сравнивает с своим subtaskId
        # и делает автоматический редирект на /subtask-board/{task_id}, если совпало.
        # exclude_user_id не передаётся (None по умолчанию): broadcast идёт всем, включая актора.
        # Актор и остальные получают один payload; разделение форматов — на фронте:
        #   String(data.actor_id) === userId → "Subtask for task '...' deleted: '...'"
        #   иначе                            → "email: Удалена подзадача «...» [...]"
    )

    if crm_subtask_id is not None:
        try:
            await crm.delete_subtask(crm_subtask_id)
            snapshot.crm_synced = True
            logger.info("CRM: subtask id=%s deleted (crm_id=%s)", subtask_id, crm_subtask_id)
        except Exception as exc:
            snapshot.crm_synced = False             # удаление из CRM не удалось; в локальной БД уже удалено
            logger.error("CRM delete_subtask failed for id=%s: %s", subtask_id, exc)
    else:
        snapshot.crm_synced = False                 # не было в CRM — удалять нечего

    return snapshot                                 # возвращаем данные удалённой подзадачи
