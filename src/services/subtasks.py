"""Бизнес-логика подзадач: БД + CRM-синхронизация + WS-уведомления.

Вынесена из routers/subtasks.py — тот же SRP-разбор, что и в services/tasks.py.
"""

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
    # Захватываются здесь, а не читаются из user.* в конце функции — простая
    # предосторожность на случай будущего rollback где-то по ходу функции (та
    # же причина, что и в services/tasks.py::create_task).
    user_id, user_email = user.id, user.email

    # Валидация файлов — первым делом, до CRM/БД: единственный невалидный файл
    # должен блокировать создание подзадачи целиком (422), ничего не должно быть тронуто.
    spec_validated, other_validated = await attachments.validate_files_for_create(
        specification, other_files
    )

    task = (await db.execute(select(Task).where(Task.id == subtask.task_id))).scalar_one_or_none()
    # scalar_one_or_none(): вернёт объект Task или None; SELECT FROM task WHERE id=?
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # Namespace-проверка владельца намеренно не выполняется — Shared board: любой
    # аутентифицированный пользователь может создавать подзадачи в любой задаче,
    # это не забытая доработка. См. tests/test_subtasks.py::
    # test_update_subtask_other_user_allowed, который фиксирует это поведение
    # как ожидаемое.

    # CRM-создание теперь идёт через durable outbox (см. ниже), а не синхронно
    # ДО INSERT — тот же принцип, что и в services/tasks.py::create_task,
    # устраняет прежнюю необходимость в компенсирующем удалении из CRM при
    # гонке IntegrityError.
    task_title = task.title                          # захватить до commit (объект будет expired)
    db_subtask = Subtask(
        title=subtask.title,
        description=subtask.description,
        completed=False,
        task_id=subtask.task_id,
        crm_subtask_id=None,
    )
    db.add(db_subtask)                              # добавить в сессию (состояние pending)
    try:
        # flush() (не commit()): нужен db_subtask.id для путей файлов ниже, но файлы ещё
        # не должны быть записаны на диск, если сама вставка подзадачи упадёт.
        await db.flush()                            # INSERT INTO subtask ...
    except IntegrityError:
        await db.rollback()                         # откатить транзакцию при нарушении ограничения БД
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

    # Шард всегда родительский — у Subtask своего crm_shard нет; ensure_task_shard
    # присваивает task.crm_shard лениво, если задача старше этой фичи.
    shard = ensure_task_shard(task)

    # Сущность гарантированно существует (в рамках открытой транзакции) — сохранение
    # файлов на диск теперь best-effort: сбой одного файла не откатывает подзадачу.
    spec_path, other_paths, file_upload_errors = await attachments.save_files_for_create(
        db_subtask.id, attachments.SUBTASK_ATTACHMENTS, spec_validated, other_validated,
    )
    if spec_path is not None:
        db_subtask.specification_path = spec_path
    if other_paths:
        db_subtask.other_file_paths = other_paths

    # Межагрегатная зависимость (один из двух видов depends_on_event_id в
    # проекте — см. докстринг CrmOutbox в src/task_logic/models.py): если
    # родительская задача сама ещё не синхронизирована
    # с CRM, а её собственное 'create'-событие ещё не done — привязываем create
    # подзадачи к нему, чтобы воркер не пытался создать подзадачу в CRM раньше
    # родителя (parent_item_id иначе не на что было бы ссылаться).
    depends_on_id: Optional[int] = None
    if task.crm_task_id is None:
        depends_on_id = (
            await db.execute(
                select(CrmOutbox.id).where(
                    CrmOutbox.aggregate_type == "task",
                    CrmOutbox.aggregate_id == task.id,
                    CrmOutbox.operation == "create",
                    CrmOutbox.status != "done",
                )
            )
        ).scalar_one_or_none()

    outbox_create = CrmOutbox(
        aggregate_type="subtask", aggregate_id=db_subtask.id, operation="create", shard=shard,
        depends_on_event_id=depends_on_id,
        payload={
            "title": subtask.title, "description": subtask.description,
            "completed": False, "creator_email": user_email,
        },
    )
    db.add(outbox_create)
    db_subtask.sync_status = "pending"   # см. services/tasks.py::create_task
    await db.flush()  # нужен outbox_create.id для depends_on_event_id строки sync_files ниже

    outbox_files: Optional[CrmOutbox] = None
    if spec_path is not None or other_paths:
        outbox_files = CrmOutbox(
            aggregate_type="subtask", aggregate_id=db_subtask.id, operation="sync_files", shard=shard,
            depends_on_event_id=outbox_create.id,
            payload={
                "crm_subtask_id": None,
                "specification_path": spec_path,
                "other_file_paths": other_paths or None,
            },
        )
        db.add(outbox_files)

    # Захватываются здесь, а не читаются из ORM-объектов ниже: после
    # db.rollback() в except-блоках эти объекты expired, а синхронное чтение
    # expired-атрибута под асинхронным SQLAlchemy бросает MissingGreenlet
    # (лениво подгрузить можно только через await) — простые Python-переменные
    # этой проблемы не имеют (см. тот же приём в services/tasks.py::create_task).
    await db.commit()
    # db.refresh() не нужен: async_session_maker сконфигурирован с expire_on_commit=False
    # (src/database.py) — атрибуты db_subtask не инвалидируются после commit(), а id уже
    # заполнен через INSERT ... RETURNING id, который SQLAlchemy 2.0 + asyncpg используют
    # автоматически при flush.

    # CRM-вызов веб-процесс не делает — только вставленные выше outbox-строки +
    # немедленный диспатч в Celery (см. dispatch_outbox_row в
    # services/tasks.py::create_task). Если родитель ещё не синхронизирован
    # (task.crm_task_id is None) — 'create' подзадачи всё равно уже поставлен в
    # outbox с depends_on_event_id на create родителя (см. выше); Celery
    # обработает его, когда родитель будет готов, без какого-либо дополнительного
    # диспатча отсюда.
    dispatch_outbox_row(outbox_create)
    if outbox_files is not None:
        dispatch_outbox_row(outbox_files)

    result = SubtaskResponse.model_validate(db_subtask)
    result.file_upload_errors = file_upload_errors or None

    # subtask.title (входная схема), не db_subtask.title; user_id/user_email
    # (захвачены в начале функции), не user.id/user.email — см. пояснения там
    # же и в services/tasks.py::create_task про MissingGreenlet после rollback.
    await broadcast_task_event(
        "subtask_created", subtask.title,
        sender_email=user_email,  # email актора → data.sender для других пользователей
        task_title=task_title,    # название родительской задачи → data.task_title в payload
        task_id=subtask.task_id,  # subtask-board.js сравнивает с своим taskId, чтобы решить,
        # относится ли событие к странице подзадач, которая сейчас открыта у клиента
        actor_id=user_id,         # попадает в payload через **extra → data.actor_id на фронте;
        # exclude_user_id не передаётся (None по умолчанию): broadcast идёт всем, включая актора.
        # Актор и остальные получают один payload; разделение форматов — на фронте:
        #   String(data.actor_id) === userId → "Subtask for task '...' created: '...'"
        #   иначе                            → "email: Создана подзадача «...» [...]"
    )
    return result


async def list_subtasks(
    db: AsyncSession, task_id: int, skip: int, limit: int,
) -> tuple[List[Subtask], int]:
    subtasks = (
        await db.execute(
            select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.id).offset(skip).limit(limit)
            # SELECT * FROM subtask WHERE task_id=? ORDER BY id OFFSET ? LIMIT ?
            # ORDER BY — стабильная пагинация (см. пояснение в services/tasks.py::list_tasks)
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
) -> SubtaskResponse:
    # FOR UPDATE: блокирует строку подзадачи на время обновления. Без этой блокировки
    # конкурентный delete_task() для родительской задачи (services/tasks.py) мог бы
    # прочитать и каскадно удалить эту же подзадачу уже после того, как SELECT ниже
    # её прочитал, но до commit() этой функции — UPDATE применился бы к уже
    # несуществующей строке. Не тихий no-op: ORM при флаше сверяет число реально
    # задетых UPDATE строк с числом обновляемых объектов и в этом случае бросает
    # sqlalchemy.orm.exc.StaleDataError (проверено эмпирически) — без блокировки это
    # исключение улетело бы наверх необработанным (голый 500), а не 200 OK, как можно
    # было бы подумать. FOR UPDATE заставляет delete_task() дождаться commit/rollback
    # этой транзакции, прежде чем прочитать (и возможно удалить) эту же строку —
    # см. симметричную блокировку subtask_rows в services/tasks.py::delete_task и
    # FOR NO KEY UPDATE в services/tasks.py::update_task для того же класса гонки.
    db_subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id).with_for_update())
    ).scalar_one_or_none()
    if db_subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")

    task = await db.get(Task, db_subtask.task_id)   # SELECT FROM task WHERE id=?; гарантированно не None (FK)
    # Namespace-проверка владельца намеренно не выполняется — см. create_subtask() выше
    # про Shared board.

    task_title = task.title                          # захватить до commit (объект будет expired)
    update_data = subtask_update.model_dump(exclude_unset=True)
    # exclude_unset=True: только явно переданные поля; отсутствующие поля не попадут в dict
    crm_subtask_id = db_subtask.crm_subtask_id     # захватить до refresh, пока объект в сессии
    # захватить title до commit: после rollback ORM-объект expired и db_subtask.title
    # требует lazy-load, несовместимого с async-контекстом (MissingGreenlet)
    title_for_err = update_data["title"] if "title" in update_data else db_subtask.title

    for key, value in update_data.items():
        setattr(db_subtask, key, value)             # применяем изменения к ORM-объекту

    # Durable outbox — та же схема, что в services/tasks.py::update_task:
    # вставляется в ОДНОЙ транзакции с самим UPDATE.
    outbox_row: Optional[CrmOutbox] = None
    if crm_subtask_id is not None:
        db_subtask.sync_status = "pending"   # вернётся в 'synced' после успешного update в воркере
        outbox_row = CrmOutbox(
            aggregate_type="subtask", aggregate_id=subtask_id, operation="update",
            shard=ensure_task_shard(task),
            payload={
                "crm_subtask_id": crm_subtask_id,
                "title": update_data.get("title"),
                "description": update_data.get("description"),
                "completed": update_data.get("completed"),
            },
        )
        db.add(outbox_row)

    try:
        await db.commit()                           # UPDATE subtask SET ... WHERE id=?
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Subtask with title '{title_for_err}' already exists in this task",
        )
    # db.refresh() не нужен — см. пояснение в create_subtask() выше (expire_on_commit=False).
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

    if outbox_row is not None:                       # подзадача ранее была синхронизирована с CRM
        # CRM-вызов веб-процесс не делает — только диспатч в Celery. Результат
        # клиенту не сообщается (см. SubtaskResponse) — статус синхронизации
        # виден только администратору.
        dispatch_outbox_row(outbox_row)
    # else: подзадача изначально не была в CRM — синхронизировать нечего.

    result = SubtaskResponse.model_validate(db_subtask)
    return result


async def delete_subtask(
    db: AsyncSession, user: User, subtask_id: int,
) -> SubtaskResponse:
    # FOR UPDATE: та же защита, что и в update_subtask() выше. Без неё конкурентный
    # delete_task() для родительской задачи мог бы прочитать crm_subtask_id этой же
    # подзадачи в свой snapshot (subtask_rows) ДО того, как эта функция её удалит,
    # и после каскадного удаления повторно вызвать crm.delete_subtask() для того же
    # id — CRM получила бы два запроса на удаление одной записи. FOR UPDATE
    # сериализует обе операции: только одна из них увидит существующую строку и
    # реально удалит её в CRM, вторая получит 404 (строка уже не входит в результат
    # блокирующего SELECT после commit конкурентной транзакции).
    subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id).with_for_update())
    ).scalar_one_or_none()
    if subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")

    task = await db.get(Task, subtask.task_id)      # SELECT FROM task WHERE id=?
    # Namespace-проверка владельца намеренно не выполняется — см. create_subtask() выше
    # про Shared board.

    task_title = task.title                          # захватить до commit (объект будет expired)
    parent_task_id = subtask.task_id                 # захватить до commit — для payload task_id
    snapshot = SubtaskResponse.model_validate(subtask)
    # snapshot создаётся ДО удаления: после db.delete+commit объект в состоянии detached,
    # атрибуты недоступны; snapshot хранит данные для возврата в ответе
    crm_subtask_id = subtask.crm_subtask_id        # захватить до удаления

    # Durable outbox — та же схема, что в services/tasks.py::delete_task:
    # вставляется В ТОЙ ЖЕ транзакции, что и само удаление, шард берём ДО
    # удаления (subtask ещё жив, task — тем более: она не трогается здесь).
    outbox_row: Optional[CrmOutbox] = None
    if crm_subtask_id is not None:
        outbox_row = CrmOutbox(
            aggregate_type="subtask", aggregate_id=subtask_id, operation="delete",
            shard=ensure_task_shard(task),
            payload={"crm_subtask_id": crm_subtask_id},
        )
        db.add(outbox_row)

    await db.delete(subtask)                        # DELETE FROM subtask WHERE id=?
    await db.commit()                               # фиксируем; после этого запись в БД не существует

    # Удаляем файлы подзадачи с диска после commit.
    await attachments.cleanup(subtask_id, attachments.SUBTASK_ATTACHMENTS)
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

    if outbox_row is not None:
        # CRM-вызов веб-процесс не делает — только диспатч в Celery. Результат
        # клиенту не сообщается (см. SubtaskResponse) — статус синхронизации
        # виден только администратору.
        dispatch_outbox_row(outbox_row)
    # else: не было в CRM — синхронизировать нечего.

    return snapshot                                 # возвращаем данные удалённой подзадачи
