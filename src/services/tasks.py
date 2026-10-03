"""Бизнес-логика задач: БД, CRM-синхронизация (outbox) и WS-уведомления."""

import logging
from dataclasses import dataclass
from typing import List, Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from src.auth.user_models import User
from src.realtime import broadcast_task_event
from src.services import attachments
from src.task_logic.models import CrmOutbox, Project, Subtask, Task
from src.task_logic.task_schemas import TaskCreate, TaskResponse, TaskUpdate
from src.crm.outbox_queries import pending_create_event_id
from src.tasks.crm_outbox_tasks import dispatch_outbox_row
from src.tasks.sharding import ensure_task_shard

logger = logging.getLogger(__name__)


async def _resolve_project(
    project_crm_id: Optional[str], db: AsyncSession,
) -> Optional[Project]:
    """CRM-ID опции списка «Проект» → строка Project.

    None — «не передано» (create) или «не трогать» (update); "" — явная очистка, возвращает None без запроса к БД.
    Возвращает всю строку: нужны и id (project_id), и label (для ответа). Читает только локальную таблицу
    project — актуальность поддерживает Celery Beat (sync_project_table) или POST /admin/crm-options/refresh.
    """
    if not project_crm_id:
        return None
    project_row = (
        await db.execute(
            select(Project).where(Project.crm_id == project_crm_id, Project.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if project_row is None:
        raise HTTPException(422, f"Значение {project_crm_id!r} не найдено в списке «Проект» CRM")
    return project_row


def _attach_project_option_id(response: TaskResponse, task: Task) -> None:
    """Заполняет response.project/project_option_id из загруженной связи task.project_ref (lazy="selectin").

    Для get_task/list_tasks/search_tasks; create/update выставляют эти поля из входных данных.
    """
    if task.project_ref is not None:
        response.project = task.project_ref.label
        response.project_option_id = task.project_ref.crm_id
    else:
        response.project = None
        response.project_option_id = None


def _subtask_count_subquery():
    """Подзапрос COUNT(*) подзадач по task_id; OUTER JOIN даёт subtask_count=0 для задач без подзадач."""
    return (
        select(Subtask.task_id, func.count(Subtask.id).label("cnt"))
        .group_by(Subtask.task_id)
        .subquery("sub_counts")
    )


async def create_task(
    db: AsyncSession,
    user: User,
    task: TaskCreate,
    specification: Optional[UploadFile] = None,
    other_files: Optional[list[UploadFile]] = None,
) -> TaskResponse:
    # Захватываем до возможного rollback: после него объекты сессии expired (MissingGreenlet).
    user_id, user_email = user.id, user.email

    # Резолвинг project — первым, до файлов/БД: неизвестный CRM-ID даёт 422 (локальный SELECT, не поход в CRM).
    project_row = await _resolve_project(task.project, db)

    # Валидация файлов — до БД: невалидный файл блокирует создание (422).
    spec_validated, other_validated = await attachments.validate_files_for_create(
        specification, other_files
    )

    # Проверяем уникальность title+owner до создания записей.
    existing = (
        await db.execute(
            select(Task).where(Task.title == task.title, Task.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Задача с названием '{task.title}' уже существует",
        )

    db_task = Task(
        **task.model_dump(exclude={"project"}), project_id=(project_row.id if project_row else None),
        owner_id=user.id, crm_task_id=None,
    )
    db.add(db_task)
    try:
        # flush(), а не commit(): нужен id задачи для путей файлов и шарда, но файлы не пишем, если вставка упадёт.
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Task with title '{task.title}' already exists")

    # Шард присваивается один раз, на первом outbox-событии задачи, дальше только читается.
    shard = ensure_task_shard(db_task)

    # Сущность уже в транзакции: сохранение файлов best-effort, сбой файла не откатывает задачу.
    spec_path, other_paths, file_upload_errors = await attachments.save_files_for_create(
        db_task.id, attachments.TASK_ATTACHMENTS, spec_validated, other_validated,
    )
    if spec_path is not None:
        db_task.specification_path = spec_path
    if other_paths:
        db_task.other_file_paths = other_paths

    # Outbox вставляется в одной транзакции с созданием задачи: если процесс упадёт после commit,
    # строка останется pending и её подберёт reconcile_pending_outbox. CRM вызывает только воркер.
    outbox_create = CrmOutbox(
        aggregate_type="task", aggregate_id=db_task.id, operation="create", shard=shard,
        payload={
            "title": task.title, "description": task.description,
            "completed": False, "project": task.project,
            "creator_email": user_email,
        },
    )
    db.add(outbox_create)
    # 'pending' в одной транзакции с outbox: пользователь видит «В очереди», пока воркер не выставит итог.
    db_task.sync_status = "pending"

    # Файлы уже на диске; flush()/commit() могут упасть — при откате задачи не будет, и файлы остались бы сиротами.
    try:
        await db.flush()

        outbox_files: Optional[CrmOutbox] = None
        if spec_path is not None or other_paths:
            outbox_files = CrmOutbox(
                aggregate_type="task", aggregate_id=db_task.id, operation="sync_files", shard=shard,
                # Внутриагрегатная зависимость от 'create': crm_task_id пока неизвестен, воркер прочитает его из Task.
                # sync_* — флаги затронутых слотов; пути воркер читает из БД.
                depends_on_event_id=outbox_create.id,
                payload={
                    "crm_task_id": None,
                    "sync_specification": spec_path is not None,
                    "sync_other_files": bool(other_paths),
                },
            )
            db.add(outbox_files)

        await db.commit()
    except Exception:
        await attachments.delete_orphaned_files(spec_path, other_paths)
        raise
    # db.refresh() не нужен: expire_on_commit=False, id заполнен через INSERT ... RETURNING.

    # CRM-вызов делает только воркер; ответ не ждёт CRM. Если apply_async не выполнится, строку подберёт
    # reconcile_pending_outbox.
    await dispatch_outbox_row(outbox_create)
    if outbox_files is not None:
        await dispatch_outbox_row(outbox_files)

    result = TaskResponse.model_validate(db_task)
    result.file_upload_errors = file_upload_errors or None
    # Из входных данных: project_ref в этой транзакции ещё не подгружен.
    result.project = project_row.label if project_row else None
    result.project_option_id = project_row.crm_id if project_row else None

    # actor_id, а не exclude_user_id: событие персистируется и должно прийти актору тоже; фронт различает своё/чужое по actor_id.
    await broadcast_task_event("task_created", task.title, sender_email=user_email, actor_id=user_id)
    return result


async def list_tasks(db: AsyncSession, skip: int, limit: int) -> tuple[List[TaskResponse], int]:
    count_sq = _subtask_count_subquery()
    # func.count().over() даёт total в том же запросе без второго round-trip; для пустой страницы — фолбэк ниже.
    rows = (
        await db.execute(
            select(
                Task,
                func.coalesce(count_sq.c.cnt, 0),
                func.count().over().label("total"),
            )
            .outerjoin(count_sq, Task.id == count_sq.c.task_id)
            # ORDER BY обязателен для стабильной пагинации: UPDATE перемещает версию строки физически.
            .order_by(Task.id)
            .offset(skip)
            .limit(limit)
        )
    ).all()
    if rows:
        total = rows[0][2]
    else:
        # Пустая страница: оконная функция строк не вернула, total берём отдельным запросом.
        total = (await db.execute(select(func.count()).select_from(Task))).scalar_one()
    results = []
    for task, cnt, _total in rows:
        r = TaskResponse.model_validate(task)
        r.subtask_count = cnt
        _attach_project_option_id(r, task)
        results.append(r)
    return results, total


async def search_tasks(
    db: AsyncSession, title: str, skip: int, limit: int,
) -> tuple[List[TaskResponse], int]:
    if not title.strip():
        raise HTTPException(status_code=400, detail="Title query parameter must not be empty")

    # Экранируем спецсимволы LIKE (\, %, _): иначе поиск «100%» нашёл бы всё.
    escaped = title.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    # total в том же запросе (см. list_tasks); фолбэк для пустой страницы ниже.
    rows = (
        await db.execute(
            select(Task, func.count().over().label("total"))
            .where(Task.title.ilike(pattern, escape="\\"))
            .order_by(Task.id)
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
    # TaskResponse строим явно: у Task нет атрибута project (см. Task.project_ref).
    results = []
    for task in tasks:
        r = TaskResponse.model_validate(task)
        _attach_project_option_id(r, task)
        results.append(r)
    return results, total


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
    _attach_project_option_id(result, task)
    return result


async def update_task(
    db: AsyncSession,
    user: User,
    task_id: int,
    task_update: TaskUpdate,
) -> TaskResponse:
    # FOR NO KEY UPDATE (key_share=True): не конфликтует с FOR KEY SHARE при create_subtask, но конфликтует с
    # FOR UPDATE в delete_task. Без блокировки удаление между SELECT и commit давало StaleDataError (500) вместо 404.
    db_task = (
        await db.execute(select(Task).where(Task.id == task_id).with_for_update(key_share=True))
    ).scalar_one_or_none()
    if db_task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # exclude_unset: только явно переданные поля — иначе PATCH вёл бы себя как PUT и обнулил бы title.
    update_data = task_update.model_dump(exclude_unset=True)
    title_to_report = update_data.get("title") or db_task.title
    # crm_task_id читаем до commit — после expire объект недоступен.
    crm_task_id = db_task.crm_task_id

    # Резолвинг project — до setattr: неверный CRM-ID даёт 422 и не затрагивает db_task. В CRM уходит исходный
    # CRM-ID, в БД — локальный project.id.
    #
    # `or ""`: явный null нормализуем к "" (очистить поле), иначе _do_update_task пропустил бы очистку в CRM,
    # а project_id в БД уже обнулился бы. Применяется только если ключ "project" передан.
    project_crm_id_for_crm: Optional[str] = None
    project_row: Optional[Project] = None
    if "project" in update_data:
        project_crm_id_for_crm = update_data.pop("project") or ""
        project_row = await _resolve_project(project_crm_id_for_crm, db)
        update_data["project_id"] = project_row.id if project_row else None

    for key, value in update_data.items():
        setattr(db_task, key, value)

    # Outbox — в одной транзакции с UPDATE. Если CRM-id ещё нет, но create не завершён, строка зависит от
    # этого create (depends_on_event_id), и правка не теряется.
    outbox_row: Optional[CrmOutbox] = None
    depends_on_id: Optional[int] = None
    if crm_task_id is None:
        depends_on_id = await pending_create_event_id(db, "task", task_id)
    if crm_task_id is not None or depends_on_id is not None:
        db_task.sync_status = "pending"
        outbox_row = CrmOutbox(
            aggregate_type="task",
            aggregate_id=task_id,
            operation="update",
            shard=ensure_task_shard(db_task),
            depends_on_event_id=depends_on_id,
            payload={
                "crm_task_id": crm_task_id,
                "title": update_data.get("title"),
                "description": update_data.get("description"),
                "completed": update_data.get("completed"),
                "project": project_crm_id_for_crm,
            },
        )
        db.add(outbox_row)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Task with title '{title_to_report}' already exists")
    except StaleDataError:
        # Защита на случай гонки с delete_task (FOR NO KEY UPDATE выше её закрывает): 404 вместо 500.
        await db.rollback()
        raise HTTPException(status_code=404, detail="Task not found")

    if outbox_row is not None:
        # CRM вызывает только воркер; статус синхронизации виден только администратору.
        await dispatch_outbox_row(outbox_row)
    else:
        # Нет crm_task_id и незавершённого create — синхронизировать нечего.
        logger.warning("Task id=%s has no crm_task_id — CRM update skipped", task_id)

    # task_id — для task-detail.js (перечитать задачу при правке другим пользователем).
    # actor_id, а не exclude_user_id: событие персистируется, фронт различает своё/чужое по actor_id.
    await broadcast_task_event(
        "task_updated", db_task.title,
        sender_email=user.email, task_id=task_id, actor_id=user.id,
    )
    result = TaskResponse.model_validate(db_task)
    if "project" in task_update.model_fields_set:
        # Поле передавалось — отражаем результат резолвинга (project_ref без refresh не подгружен).
        result.project = project_row.label if project_row else None
        result.project_option_id = project_row.crm_id if project_row else None
    else:
        # Поле не передавалось — читаем через загруженную связь.
        _attach_project_option_id(result, db_task)
    return result


@dataclass
class TaskDeletion:
    """Данные для шагов после commit: после CASCADE строк task/subtask уже нет, поэтому id подзадач и outbox-строка
    запоминаются заранее (в _prepare_task_deletion).
    """

    task_id: int
    snapshot: TaskResponse
    subtask_ids: list[int]
    outbox_row: Optional[CrmOutbox]


async def delete_task(
    db: AsyncSession,
    user: User,
    task_id: int,
) -> TaskResponse:
    # FOR UPDATE (не NO KEY: строка будет удалена) берём до чтения subtask_rows: он конфликтует с FOR KEY SHARE,
    # который берёт INSERT подзадачи, поэтому конкурентный create_subtask ждёт commit/rollback. Иначе подзадача
    # успела бы вставиться, каскадно удалиться, а её файлы и CRM-запись остались бы сиротами. После разблокировки
    # create_subtask получает IntegrityError (обработано в subtasks.py).
    task = (
        await db.execute(select(Task).where(Task.id == task_id).with_for_update())
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    deletion = await _prepare_task_deletion(db, task)
    await db.commit()
    await finish_tasks_deletion([deletion], actor_email=user.email, actor_id=user.id)
    return deletion.snapshot


async def prepare_owner_tasks_deletion(db: AsyncSession, owner_id: int) -> list[TaskDeletion]:
    """Готовит удаление всех задач пользователя в текущей транзакции (без commit) — для DELETE /users/{id}.

    Для каждой задачи — та же подготовка, что в delete_task(); после commit нужно вызвать finish_tasks_deletion().
    FOR UPDATE по задачам владельца, ORDER BY id — одинаковый порядок блокировок у параллельных удалений.
    """
    tasks = (
        await db.execute(
            select(Task).where(Task.owner_id == owner_id).order_by(Task.id).with_for_update()
        )
    ).scalars().all()
    return [await _prepare_task_deletion(db, task) for task in tasks]


async def _prepare_task_deletion(db: AsyncSession, task: Task) -> TaskDeletion:
    """Шаги удаления задачи до commit: снимок, блокировка подзадач, outbox 'delete', db.delete(task).
    Строка task должна быть уже заблокирована вызывающим кодом (FOR UPDATE).
    """
    task_id = task.id
    snapshot = TaskResponse.model_validate(task)
    crm_task_id = task.crm_task_id

    # Снимок до CASCADE: id подзадач нужен для очистки файлов, crm_subtask_id — для удаления в CRM.
    #
    # FOR UPDATE по подзадачам защищает от конкурентного DELETE/PATCH /subtasks/{id}: иначе подзадача
    # удалилась бы в CRM дважды или потеряла бы чужое обновление. ORDER BY id — дешёвая защита порядка блокировок.
    subtask_rows = (
        await db.execute(
            select(Subtask.id, Subtask.crm_subtask_id)
            .where(Subtask.task_id == task_id)
            .order_by(Subtask.id)
            .with_for_update()
        )
    ).all()
    subtask_ids: list[int] = [row[0] for row in subtask_rows]
    crm_subtask_ids: list[int] = [row[1] for row in subtask_rows if row[1] is not None]

    # Outbox вставляется в той же транзакции, что и удаление. payload несёт весь снимок для CRM-очистки
    # (задача и все crm_subtask_ids): после commit строки task уже нет.
    outbox_row: Optional[CrmOutbox] = None
    if crm_task_id is not None or crm_subtask_ids:
        outbox_row = CrmOutbox(
            aggregate_type="task",
            aggregate_id=task_id,
            operation="delete",
            shard=ensure_task_shard(task),
            payload={"crm_task_id": crm_task_id, "crm_subtask_ids": crm_subtask_ids},
        )
        db.add(outbox_row)

    await db.delete(task)
    return TaskDeletion(task_id=task_id, snapshot=snapshot, subtask_ids=subtask_ids, outbox_row=outbox_row)


async def finish_tasks_deletion(
    deletions: list[TaskDeletion], actor_email: str, actor_id: int,
) -> None:
    """Шаги удаления после commit: файлы с диска, диспатч outbox-строк, событие task_deleted.
    До commit нельзя: при откате файлы были бы уже удалены.
    """
    for deletion in deletions:
        task_id = deletion.task_id
        await attachments.cleanup(task_id, attachments.TASK_ATTACHMENTS)

        # Файлы подзадач лежат отдельно (uploads/subtasks/{id}/) — удаляем явно.
        for sub_id in deletion.subtask_ids:
            await attachments.cleanup(sub_id, attachments.SUBTASK_ATTACHMENTS)

        # CRM-очистка целиком в фоне через outbox-строку (_do_delete_task); статус виден только администратору.
        if deletion.outbox_row is not None:
            await dispatch_outbox_row(deletion.outbox_row)
        else:
            # Нет ни crm_task_id, ни синхронизированных подзадач — синхронизировать нечего.
            logger.warning("Task id=%s has no crm_task_id — CRM delete skipped", task_id)

        # task_id — для task-detail.js (редирект на /task-board, если удалена открытая задача).
        # actor_id, а не exclude_user_id: страницы сами различают своё/чужое удаление.
        await broadcast_task_event(
            "task_deleted", deletion.snapshot.title,
            sender_email=actor_email, task_id=task_id, actor_id=actor_id,
        )
