"""Бизнес-логика задач: БД + CRM-синхронизация + WS-уведомления.

Вынесена из routers/tasks.py (SRP): роутер остаётся тонким HTTP-адаптером
(парсинг запроса → вызов функции отсюда → форма ответа), а оркестрация
транзакции, компенсирующих CRM-операций и broadcast_task_event живёт в
одном месте, не завязанном на Request/Response FastAPI.
"""

import asyncio
import logging
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
from src.crm.subtask_service import SubtaskCRMSync
from src.crm.task_service import TaskCRMSync
from src.realtime import broadcast_task_event
from src.services import attachments
from src.task_logic.models import Subtask, Task
from src.task_logic.task_schemas import TaskCreate, TaskResponse, TaskUpdate

logger = logging.getLogger(__name__)


def _subtask_count_subquery():
    """Подзапрос COUNT(*) подзадач, сгруппированных по task_id.

    OUTER JOIN с этим подзапросом даёт subtask_count=0 для задач без подзадач.
    Вынесен в хелпер, чтобы не дублировать в list_tasks и get_task.
    """
    return (
        select(Subtask.task_id, func.count(Subtask.id).label("cnt"))
        .group_by(Subtask.task_id)
        .subquery("sub_counts")
    )


async def create_task(
    db: AsyncSession, user: User, task: TaskCreate, crm: TaskCRMSync,
) -> TaskResponse:
    # Проверяем уникальность title+owner ДО вызова CRM, чтобы не создавать
    # дубликаты в CRM при повторном запросе с тем же названием.
    existing = (
        await db.execute(
            select(Task).where(Task.title == task.title, Task.owner_id == user.id)
        )
    ).scalar_one_or_none()
    # scalar_one_or_none() = .scalars() (берёт первую колонку каждой строки — здесь
    # она единственная, т.к. select(Task) возвращает объект Task целиком одной колонкой)
    # + .one_or_none() (отдельно проверяет количество строк: 0 — вернёт None вместо
    # исключения, в отличие от scalar_one(), которому 0 строк — уже ошибка; больше
    # одной — поднимет MultipleResultsFound). Больше одной строки здесь невозможно
    # даже под гонкой, т.к. UNIQUE(title, owner_id) не даст двум строкам с одинаковой
    # парой существовать одновременно.
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Задача с названием '{task.title}' уже существует",
        )

    # Пробуем создать задачу в CRM до INSERT в БД (best-effort).
    # crm_task_id=None означает, что синхронизация не выполнена.
    crm_task_id: Optional[int] = None
    try:
        crm_result = await crm.create_task(
            title=task.title,
            description=task.description,
            completed=False,
        )
        crm_task_id = crm_result.get("id")
        logger.info("CRM: task '%s' created, crm_id=%s", task.title, crm_task_id)
    except Exception as exc:
        logger.error("CRM create_task failed for '%s': %s", task.title, exc)

    db_task = Task(**task.model_dump(), owner_id=user.id, crm_task_id=crm_task_id)
    db.add(db_task)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Компенсирующая транзакция: CRM-запись создана, но commit упал →
        # удаляем запись из CRM, чтобы не оставить сироту.
        if crm_task_id is not None:
            try:
                await crm.delete_task(crm_task_id)
            except Exception as crm_exc:
                logger.error("CRM compensating delete failed for crm_id=%s: %s", crm_task_id, crm_exc)
        raise HTTPException(status_code=409, detail=f"Task with title '{task.title}' already exists")
    # db.refresh() здесь не нужен: async_session_maker сконфигурирован с
    # expire_on_commit=False (src/database.py) — атрибуты db_task не инвалидируются
    # после commit(), а id уже заполнен через INSERT ... RETURNING id, который
    # SQLAlchemy 2.0 + asyncpg используют автоматически при flush.
    await broadcast_task_event("task_created", db_task.title, exclude_user_id=user.id, sender_email=user.email)
    result = TaskResponse.model_validate(db_task)
    result.crm_synced = crm_task_id is not None
    return result


async def list_tasks(db: AsyncSession, skip: int, limit: int) -> tuple[List[TaskResponse], int]:
    count_sq = _subtask_count_subquery()
    # func.count().over(): оконная функция — считает общее число строк, прошедших
    # WHERE (здесь — все задачи, фильтра нет), и прикрепляет его к каждой возвращённой
    # строке. В отличие от отдельного SELECT COUNT(*), не требует второго round-trip
    # к БД — единственное ограничение: если OFFSET увёл за пределы результата (страница
    # пустая), окно не вернёт ни одной строки вместе с данными — см. фолбэк ниже.
    rows = (
        await db.execute(
            select(
                Task,
                func.coalesce(count_sq.c.cnt, 0),
                func.count().over().label("total"),
            )
            .outerjoin(count_sq, Task.id == count_sq.c.task_id)
            .offset(skip)
            .limit(limit)
        )
    ).all()
    if rows:
        total = rows[0][2]
    else:
        # Пустая страница (skip >= фактического количества строк) — count() over()
        # ничего не вернул вместе с ней; единственный способ узнать total в этом
        # редком случае — отдельный запрос (тот самый round-trip, которого мы
        # избегаем в общем случае).
        total = (await db.execute(select(func.count()).select_from(Task))).scalar_one()
    results = []
    for task, cnt, _total in rows:
        r = TaskResponse.model_validate(task)
        r.subtask_count = cnt
        results.append(r)
    return results, total


async def search_tasks(
    db: AsyncSession, title: str, skip: int, limit: int,
) -> tuple[List[Task], int]:
    if not title.strip():
        raise HTTPException(status_code=400, detail="Title query parameter must not be empty")

    # Экранируем спецсимволы SQL LIKE (\, %, _) перед подстановкой в паттерн.
    # Без экранирования поиск по строке «100%» найдёт все записи, а не только содержащие «100%».
    escaped = title.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    # func.count().over() — см. пояснение в list_tasks() выше: total в том же запросе,
    # без отдельного round-trip, кроме редкого случая пустой страницы (фолбэк ниже).
    rows = (
        await db.execute(
            select(Task, func.count().over().label("total"))
            .where(Task.title.ilike(pattern, escape="\\"))
            .offset(skip)
            .limit(limit)
        )
    ).all()
    if rows:
        total = rows[0][1]
        tasks = [row[0] for row in rows]
    else:
        total = (await db.execute(
            select(func.count()).select_from(Task).where(Task.title.ilike(pattern, escape="\\"))
        )).scalar_one()
        tasks = []
    return tasks, total


async def get_task(db: AsyncSession, task_id: int) -> TaskResponse:
    count_sq = _subtask_count_subquery()
    row = (
        await db.execute(
            select(Task, func.coalesce(count_sq.c.cnt, 0))
            .outerjoin(count_sq, Task.id == count_sq.c.task_id)
            .where(Task.id == task_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task, cnt = row
    result = TaskResponse.model_validate(task)
    result.subtask_count = cnt
    return result


async def update_task(
    db: AsyncSession,
    user: User,
    task_id: int,
    task_update: TaskUpdate,
    crm: TaskCRMSync,
) -> TaskResponse:
    db_task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if db_task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # model_dump(exclude_unset=True): Pydantic хранит множество __fields_set__ —
    # имена полей, явно переданных клиентом в теле запроса (не полученных из default).
    # Тело {"completed": true} даёт update_data = {"completed": True} без "title".
    # Без exclude_unset PATCH вёл бы себя как PUT: все поля попали бы в словарь
    # (title=None), и setattr перезаписал бы title задачи в NULL.
    update_data = task_update.model_dump(exclude_unset=True)
    title_to_report = update_data.get("title") or db_task.title
    # crm_task_id читается до commit — после expire объект недоступен
    crm_task_id = db_task.crm_task_id

    for key, value in update_data.items():
        setattr(db_task, key, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Task with title '{title_to_report}' already exists")
    # db.refresh() не нужен — см. пояснение в create_task() выше (expire_on_commit=False).

    crm_synced: Optional[bool] = None
    if crm_task_id is not None:
        try:
            await crm.update_task(
                task_id=crm_task_id,
                title=update_data.get("title"),
                description=update_data.get("description"),
                completed=update_data.get("completed"),
            )
            crm_synced = True
            logger.info("CRM: task id=%s updated (crm_id=%s)", task_id, crm_task_id)
        except Exception as exc:
            crm_synced = False
            logger.error("CRM update_task failed for task id=%s: %s", task_id, exc)
    else:
        # Задача не числится в CRM — синхронизация невозможна.
        # False (не None): фронтенд показывает уведомление при crm_synced === false.
        crm_synced = False
        logger.warning("Task id=%s has no crm_task_id — CRM update skipped", task_id)

    # task_id в payload: детальная страница задачи (task-detail.js) сравнивает его со своим
    # taskId и перечитывает задачу через loadTask(), если её отредактировал другой пользователь.
    await broadcast_task_event(
        "task_updated", db_task.title,
        exclude_user_id=user.id, sender_email=user.email, task_id=task_id,
    )
    result = TaskResponse.model_validate(db_task)
    result.crm_synced = crm_synced
    return result


async def delete_task(
    db: AsyncSession,
    user: User,
    task_id: int,
    crm: TaskCRMSync,
    subtask_crm: SubtaskCRMSync,
) -> TaskResponse:
    # FOR UPDATE (не FOR NO KEY UPDATE — эта строка будет удалена, а не просто изменена)
    # берётся до чтения subtask_rows: между этим SELECT и db.delete(task)+commit ниже
    # PostgreSQL требует FOR KEY SHARE на эту же строку для любого INSERT в subtask с FK
    # на неё (create_subtask в subtasks.py) — FOR UPDATE конфликтует с FOR KEY SHARE, поэтому
    # конкурентная попытка создать подзадачу для удаляемой задачи блокируется до commit/rollback
    # этой транзакции, а не проходит "в узкое окно" между SELECT subtask_rows и самим удалением.
    # Без этой блокировки такая подзадача успешно вставилась бы, затем была бы каскадно удалена
    # ON DELETE CASCADE вместе со строкой task — но её файлы на диске и запись в CRM (если она
    # успела туда синхронизироваться) остались бы сиротами, так как не попали бы в snapshot
    # subtask_ids/crm_subtask_ids ниже (он читается ДО того, как гонка успела бы что-то вставить).
    # Конкурентный create_subtask после разблокировки получит IntegrityError (FK violation) —
    # обработка этого случая добавлена в subtasks.py::create_subtask.
    task = (
        await db.execute(select(Task).where(Task.id == task_id).with_for_update())
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    snapshot = TaskResponse.model_validate(task)
    crm_task_id = task.crm_task_id

    # Один запрос до CASCADE-удаления: id нужен для очистки файлов на диске,
    # crm_subtask_id — для удаления подзадач в CRM. После commit оба недоступны.
    #
    # FOR UPDATE: блокирует все строки подзадач этой задачи перед снятием snapshot.
    # Симметрично блокировкам в update_subtask()/delete_subtask() (services/subtasks.py):
    # без неё эта функция могла бы прочитать crm_subtask_id подзадачи, которую
    # конкурентно удаляет/обновляет прямой запрос DELETE /subtasks/{id} или
    # PATCH /subtasks/{id}, и либо вызвать crm.delete_subtask() повторно для уже
    # удалённого id, либо каскадно удалить подзадачу, которую параллельно
    # редактирует другой пользователь, потеряв его обновление. FOR UPDATE заставляет
    # эту транзакцию дождаться commit/rollback конкурентной операции над той же
    # строкой; если та удалила подзадачу первой — эта SELECT просто не вернёт её
    # (Postgres не включает в результат FOR UPDATE строку, которая была удалена и
    # закоммичена транзакцией, державшей на неё блокировку).
    subtask_rows = (
        await db.execute(
            select(Subtask.id, Subtask.crm_subtask_id)
            .where(Subtask.task_id == task_id)
            .with_for_update()
        )
    ).all()
    subtask_ids: list[int] = [row[0] for row in subtask_rows]
    crm_subtask_ids: list[int] = [row[1] for row in subtask_rows if row[1] is not None]

    await db.delete(task)
    await db.commit()

    await attachments.cleanup(task_id, attachments.TASK_ATTACHMENTS)

    # Файлы подзадач хранятся отдельно (uploads/subtasks/{id}/),
    # cleanup выше их не затрагивает — удаляем явно.
    if subtask_ids:
        for sub_id in subtask_ids:
            await attachments.cleanup(sub_id, attachments.SUBTASK_ATTACHMENTS)

    # Удаляем подзадачи из CRM: CASCADE удалил их в локальной БД,
    # но CRM не знает об этом — подзадачи (entity_id=30) остались бы orphan-записями.
    # asyncio.gather: параллельные запросы к CRM вместо последовательных (N×RTT → 1×RTT).
    if crm_subtask_ids:
        results = await asyncio.gather(
            *[subtask_crm.delete_subtask(cid) for cid in crm_subtask_ids],
            return_exceptions=True,
        )
        for cid, result in zip(crm_subtask_ids, results):
            if isinstance(result, Exception):
                logger.error("CRM: cascade delete subtask crm_id=%s failed: %s", cid, result)
            else:
                logger.info("CRM: subtask crm_id=%s deleted (cascade from task %s)", cid, task_id)

    if crm_task_id is not None:
        try:
            await crm.delete_task(crm_task_id)
            snapshot.crm_synced = True
            logger.info("CRM: task id=%s deleted (crm_id=%s)", task_id, crm_task_id)
        except Exception as exc:
            snapshot.crm_synced = False
            logger.error(
                "CRM delete_task failed for task id=%s (crm_id=%s): %s",
                task_id, crm_task_id, exc,
            )
    else:
        # Задача не числится в CRM — синхронизация невозможна.
        # False (не None): фронтенд показывает уведомление при crm_synced === false.
        snapshot.crm_synced = False
        logger.warning("Task id=%s has no crm_task_id — CRM delete skipped", task_id)

    # task_id в payload: детальная страница удалённой задачи (task-detail.js) сравнивает его
    # со своим taskId и делает автоматический редирект на /task-board, если совпало.
    await broadcast_task_event(
        "task_deleted", snapshot.title,
        exclude_user_id=user.id, sender_email=user.email, task_id=task_id,
    )
    return snapshot
