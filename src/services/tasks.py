"""Бизнес-логика задач: БД + CRM-синхронизация + WS-уведомления.

Вынесена из routers/tasks.py (SRP): роутер остаётся тонким HTTP-адаптером
(парсинг запроса → вызов функции отсюда → форма ответа), а оркестрация
транзакции, компенсирующих CRM-операций и broadcast_task_event живёт в
одном месте, не завязанном на Request/Response FastAPI.
"""

import logging
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
from src.tasks.crm_outbox_tasks import dispatch_outbox_row
from src.tasks.sharding import ensure_task_shard

logger = logging.getLogger(__name__)


async def _resolve_project(
    project_crm_id: Optional[str], db: AsyncSession,
) -> Optional[Project]:
    """CRM-ID опции списка "Проект" (то, что прислал клиент) → строка Project.

    None → «не передано»/«не выбрано» (create) или «не трогать» (update).
    "" (пустая строка) → явная очистка поля — возвращается None без похода в БД
    (пустая строка не может совпасть ни с одним настоящим crm_id).

    Возвращает саму строку (id + label), не только id: вызывающему коду нужен
    и id (для project_id FK), и label (для TaskResponse.project — в create/update
    task.project_ref ещё не подгружен без db.refresh(), см. _attach_project_option_id).

    Читает ТОЛЬКО локальную таблицу project — никакого живого запроса к CRM
    внутри HTTP-запроса. Актуальность таблицы — на совести Celery Beat
    (src/tasks/global_lists_tasks.py::sync_project_table, раз в
    crm_settings.PROJECT_SYNC_INTERVAL_SECONDS) или ручного admin-триггера
    (POST /admin/crm-options/refresh), не этого запроса.
    """
    if not project_crm_id:  # покрывает и None, и ""
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
    """Заполняет response.project/project_option_id из уже загруженной связи
    task.project_ref (lazy="selectin" — без дополнительного запроса).

    Вызывается для get_task/list_tasks/search_tasks (перечитывание уже
    сохранённой задачи) — НЕ для create_task/update_task, где эти поля
    выставляются напрямую из входных данных запроса (project_ref к этому
    моменту в текущей транзакции ещё не подгружен без db.refresh(), см.
    вызывающий код).
    """
    if task.project_ref is not None:
        response.project = task.project_ref.label
        response.project_option_id = task.project_ref.crm_id
    else:
        response.project = None
        response.project_option_id = None


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
    db: AsyncSession,
    user: User,
    task: TaskCreate,
    specification: Optional[UploadFile] = None,
    other_files: Optional[list[UploadFile]] = None,
) -> TaskResponse:
    # Захватываются здесь, а не читаются из user.* в конце функции — простая
    # предосторожность на случай будущего rollback где-то по ходу функции
    # (rollback() экспирует ВСЕ объекты сессии, включая user; синхронное
    # чтение user.email/user.id после этого упало бы MissingGreenlet — лениво
    # подгрузить expired-атрибут можно только через await). Простые
    # Python-переменные этой проблемы не имеют — тот же приём, что и для
    # db_task/db_subtask ниже по функции.
    user_id, user_email = user.id, user.email

    # Резолвинг project — первой строкой, до файлов/CRM/БД: дешёвый локальный SELECT
    # (не поход в CRM, см. _resolve_project), должен блокировать создание задачи
    # целиком (422), если CRM-ID не найден в локальной таблице project.
    project_row = await _resolve_project(task.project, db)

    # Валидация файлов — до CRM/БД: единственный невалидный файл
    # должен блокировать создание задачи целиком (422), ничего не должно быть тронуто.
    spec_validated, other_validated = await attachments.validate_files_for_create(
        specification, other_files
    )

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

    # CRM-создание теперь идёт через durable outbox (см. ниже), а не синхронно
    # ДО INSERT, как раньше — устраняет прежнюю необходимость в компенсирующем
    # удалении из CRM при гонке IntegrityError: CRM ещё не тронута к моменту
    # flush(), поэтому дубликат под гонкой просто ничего не оставляет в CRM.
    db_task = Task(
        **task.model_dump(exclude={"project"}), project_id=(project_row.id if project_row else None),
        owner_id=user.id, crm_task_id=None,
    )
    db.add(db_task)
    try:
        # flush() (не commit()): нужен db_task.id для путей файлов и для ключа
        # присвоения шарда ниже, но файлы ещё не должны быть записаны на диск, если
        # сама вставка задачи упадёт (дубликат title под гонкой — UNIQUE(title,
        # owner_id) проверяется PostgreSQL уже здесь, на INSERT).
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Task with title '{task.title}' already exists")

    # Sticky-присвоение шарда — РОВНО ОДИН РАЗ, на первом outbox-событии задачи —
    # все последующие события этой задачи и её подзадач читают уже сохранённое
    # значение, формула больше не пересчитывается.
    shard = ensure_task_shard(db_task)

    # Сущность гарантированно существует (в рамках открытой транзакции) — сохранение
    # файлов на диск теперь best-effort: сбой одного файла не откатывает задачу.
    spec_path, other_paths, file_upload_errors = await attachments.save_files_for_create(
        db_task.id, attachments.TASK_ATTACHMENTS, spec_validated, other_validated,
    )
    if spec_path is not None:
        db_task.specification_path = spec_path
    if other_paths:
        db_task.other_file_paths = other_paths

    # Durable outbox — ВСТАВЛЯЕТСЯ В ТОЙ ЖЕ ТРАНЗАКЦИИ, что и создание задачи
    # (db.add ниже, до db.commit()): если процесс упадёт в любой момент ПОСЛЕ
    # этого commit — в том числе до того, как код дойдёт до dispatch_outbox_row
    # ниже, — строка всё равно останется в PostgreSQL со status='pending', и её
    # найдёт и повторно обработает reconcile_pending_outbox (Celery Beat,
    # src/tasks/crm_outbox_tasks.py). CRM-вызов выполняется ИСКЛЮЧИТЕЛЬНО в
    # Celery-воркере (см. dispatch_outbox_row) — веб-процесс сам его никогда не
    # делает, именно это убирает риск потери запроса и его латентность из
    # ответа пользователю — см. основную документацию, «Векторы развития
    # проекта», п. 14.
    outbox_create = CrmOutbox(
        aggregate_type="task", aggregate_id=db_task.id, operation="create", shard=shard,
        payload={
            "title": task.title, "description": task.description,
            "completed": False, "project": task.project,
        },
    )
    db.add(outbox_create)
    # 'pending' в той же транзакции, что и строка outbox: пользователь видит бейдж
    # «В очереди», пока воркер не выставит 'synced' (или 'failed' при исчерпании попыток).
    db_task.sync_status = "pending"
    await db.flush()  # нужен outbox_create.id для depends_on_event_id строки ниже

    outbox_files: Optional[CrmOutbox] = None
    if spec_path is not None or other_paths:
        outbox_files = CrmOutbox(
            aggregate_type="task", aggregate_id=db_task.id, operation="sync_files", shard=shard,
            # Зависит от 'create' ТОГО ЖЕ агрегата (внутриагрегатная зависимость —
            # один из двух видов depends_on_event_id, см. докстринг CrmOutbox в
            # src/task_logic/models.py) — crm_task_id пока не известен (create
            # ещё не выполнялся), поэтому в
            # payload "crm_task_id": None; исполнитель (src/tasks/crm_outbox_tasks.py::
            # _do_sync_files_task) прочитает актуальное значение из уже обновлённой
            # записи Task, когда до него дойдёт очередь (после того как зависимость done).
            depends_on_event_id=outbox_create.id,
            payload={
                "crm_task_id": None,
                "specification_path": spec_path,
                "other_file_paths": other_paths or None,
            },
        )
        db.add(outbox_files)

    await db.commit()
    # db.refresh() здесь не нужен: async_session_maker сконфигурирован с
    # expire_on_commit=False (src/database.py) — атрибуты db_task не инвалидируются
    # после commit(), а id уже заполнен через INSERT ... RETURNING id, который
    # SQLAlchemy 2.0 + asyncpg используют автоматически при flush.

    # CRM-вызов веб-процесс больше не делает вообще — только вставленные выше
    # outbox-строки + немедленный диспатч в Celery (см. dispatch_outbox_row):
    # сам HTTP-ответ не ждёт CRM ни при каком исходе (успех/сбой/недоступность).
    # Если apply_async ниже не успеет выполниться (падение процесса) — строка
    # всё равно останется 'pending' и будет подхвачена reconcile_pending_outbox.
    dispatch_outbox_row(outbox_create)
    if outbox_files is not None:
        dispatch_outbox_row(outbox_files)

    result = TaskResponse.model_validate(db_task)
    result.file_upload_errors = file_upload_errors or None
    # Из входных данных, не через _attach_project_option_id: project_ref для
    # db_task в этой же транзакции без db.refresh() ещё не подгружен.
    result.project = project_row.label if project_row else None
    result.project_option_id = project_row.crm_id if project_row else None

    # actor_id (не exclude_user_id — событие теперь персистируется и должно
    # прийти актору тоже, чтобы «Вы: Создана задача: ...» появилось и после
    # перезагрузки страницы; на клиенте selfOrOther-логика решает по actor_id,
    # свой это рендер или чужой, см. task-board.js::buildEventMessage).
    await broadcast_task_event("task_created", task.title, sender_email=user_email, actor_id=user_id)
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
            # ORDER BY обязателен для стабильной пагинации: без него порядок строк не
            # определён, а UPDATE в PostgreSQL физически перемещает версию строки —
            # обновлённая задача «переезжала» бы между страницами (повтор/пропуск).
            .order_by(Task.id)
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
        _attach_project_option_id(r, task)
        results.append(r)
    return results, total


async def search_tasks(
    db: AsyncSession, title: str, skip: int, limit: int,
) -> tuple[List[TaskResponse], int]:
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
            .order_by(Task.id)   # см. пояснение в list_tasks(): стабильный порядок страниц
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
    # Построение TaskResponse явно (не возврат ORM-объектов, как раньше) — иначе
    # project/project_option_id остались бы None: у Task нет одноимённого атрибута
    # project (см. Task.project_ref), from_attributes подставил бы дефолт схемы.
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
    # FOR NO KEY UPDATE (key_share=True, не FOR UPDATE): блокирует строку задачи на
    # время обновления, но не конфликтует с FOR KEY SHARE, которую PostgreSQL берёт на
    # родителя при параллельном create_subtask() (subtasks.py) — обновление
    # title/description/completed не должно мешать созданию подзадач в этой же задаче.
    # Конфликтует с FOR UPDATE конкурентного delete_task() (ниже в этом файле) — тот
    # либо дождётся commit/rollback этой транзакции перед удалением, либо, если успел
    # первым, эта SELECT просто не найдёт строку (db_task is None → 404 ниже).
    #
    # Без этой блокировки окно между SELECT и commit() ниже давало гонку: конкурентный
    # delete_task() успевал удалить задачу между ними, и commit() падал не тихим
    # no-op'ом (0 затронутых строк без ошибки), а sqlalchemy.orm.exc.StaleDataError —
    # ORM при флаше UPDATE-а сверяет число реально задетых строк с числом
    # обновляемых объектов и бросает исключение при несовпадении. StaleDataError не
    # перехватывался веткой except IntegrityError ниже и улетал бы наверх голым 500
    # вместо ожидаемого 404 — проверено эмпирически на реальной БД.
    db_task = (
        await db.execute(select(Task).where(Task.id == task_id).with_for_update(key_share=True))
    ).scalar_one_or_none()
    if db_task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # model_dump(exclude_unset=True): Pydantic хранит множество model_fields_set —
    # имена полей, явно переданных клиентом в теле запроса (не полученных из default).
    # Тело {"completed": true} даёт update_data = {"completed": True} без "title".
    # Без exclude_unset PATCH вёл бы себя как PUT: все поля попали бы в словарь
    # (title=None), и setattr перезаписал бы title задачи в NULL.
    update_data = task_update.model_dump(exclude_unset=True)
    title_to_report = update_data.get("title") or db_task.title
    # crm_task_id читается до commit — после expire объект недоступен
    crm_task_id = db_task.crm_task_id

    # Резолвинг project — до setattr: невалидный CRM-ID должен дать 422, не
    # затрагивая db_task (см. _resolve_project). project_crm_id_for_crm сохраняется
    # отдельно от update_data["project_id"] — CRM получает исходный CRM-ID, БД —
    # локальный project.id (та же асимметрия, что и в create_task).
    project_crm_id_for_crm: Optional[str] = None
    project_row: Optional[Project] = None
    if "project" in update_data:
        project_crm_id_for_crm = update_data.pop("project")
        project_row = await _resolve_project(project_crm_id_for_crm, db)
        update_data["project_id"] = project_row.id if project_row else None

    for key, value in update_data.items():
        setattr(db_task, key, value)

    # Durable outbox — та же схема, что в create_task (см. её докстринг выше):
    # вставляется в ОДНОЙ транзакции с самим UPDATE, поэтому переживает падение
    # процесса в любой момент после commit, включая до dispatch_outbox_row ниже.
    outbox_row: Optional[CrmOutbox] = None
    if crm_task_id is not None:
        db_task.sync_status = "pending"   # вернётся в 'synced' после успешного update в воркере
        outbox_row = CrmOutbox(
            aggregate_type="task",
            aggregate_id=task_id,
            operation="update",
            shard=ensure_task_shard(db_task),
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
        # Защита на случай, если гонка с delete_task() всё же произойдёт несмотря на
        # блокировку выше (например, при будущих изменениях кода) — вместо
        # необработанного 500 клиент получает тот же 404, что и при обычном
        # "задача не найдена". С блокировкой FOR NO KEY UPDATE выше этот путь не
        # должен достигаться через delete_task() — см. комментарий у SELECT.
        await db.rollback()
        raise HTTPException(status_code=404, detail="Task not found")
    # db.refresh() не нужен — см. пояснение в create_task() выше (expire_on_commit=False).

    if outbox_row is not None:
        # CRM-вызов веб-процесс не делает — только диспатч в Celery (см.
        # dispatch_outbox_row в create_task). Результат клиенту не сообщается
        # (см. TaskResponse) — статус синхронизации виден только администратору.
        dispatch_outbox_row(outbox_row)
    else:
        # Задача не числится в CRM — синхронизировать нечего, outbox-строка не создавалась.
        logger.warning("Task id=%s has no crm_task_id — CRM update skipped", task_id)

    # task_id в payload: детальная страница задачи (task-detail.js) сравнивает его со своим
    # taskId и перечитывает задачу через loadTask(), если её отредактировал другой пользователь.
    # actor_id (не exclude_user_id — см. комментарий в create_task выше): task-detail.js
    # теперь сам различает своё/чужое редактирование по actor_id (см. его WS-обработчик).
    await broadcast_task_event(
        "task_updated", db_task.title,
        sender_email=user.email, task_id=task_id, actor_id=user.id,
    )
    result = TaskResponse.model_validate(db_task)
    if "project" in task_update.model_fields_set:
        # Поле передавалось в запросе — отражаем результат резолвинга явно
        # (project_ref для db_task в этой же транзакции не подгружен без db.refresh()).
        result.project = project_row.label if project_row else None
        result.project_option_id = project_row.crm_id if project_row else None
    else:
        # Поле не передавалось — читаем как есть, через уже загруженную связь
        # (title/description/completed могли поменяться, project — не трогали).
        _attach_project_option_id(result, db_task)
    return result


async def delete_task(
    db: AsyncSession,
    user: User,
    task_id: int,
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

    # Durable outbox — та же схема, что в create_task/update_task (см. их докстринги
    # выше): вставляется в ОДНОЙ транзакции с самим удалением задачи, поэтому строка
    # переживает падение процесса в любой момент после commit. payload несёт весь
    # снимок, нужный для CRM-очистки (в т.ч. подзадач — см. _do_delete_task в
    # crm_outbox_tasks.py, обрабатывает и task, и все crm_subtask_ids одной
    # операцией), — после commit локальной строки task уже нет (CASCADE),
    # обратиться к ней за crm_task_id/crm_subtask_ids снова нельзя.
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
    await db.commit()

    await attachments.cleanup(task_id, attachments.TASK_ATTACHMENTS)

    # Файлы подзадач хранятся отдельно (uploads/subtasks/{id}/),
    # cleanup выше их не затрагивает — удаляем явно.
    if subtask_ids:
        for sub_id in subtask_ids:
            await attachments.cleanup(sub_id, attachments.SUBTASK_ATTACHMENTS)

    # CRM-очистка (и задачи, и каскадно удалённых подзадач) — целиком в фоне,
    # через уже вставленную outbox-строку (см. её payload выше и обработчик
    # _do_delete_task); веб-процесс сам в CRM не обращается. Результат клиенту
    # не сообщается (см. TaskResponse) — статус синхронизации виден только администратору.
    if outbox_row is not None:
        dispatch_outbox_row(outbox_row)
    else:
        # Задача не числится в CRM и не было синхронизированных подзадач —
        # синхронизировать нечего, outbox-строка не создавалась (см. условие выше).
        logger.warning("Task id=%s has no crm_task_id — CRM delete skipped", task_id)

    # task_id в payload: детальная страница удалённой задачи (task-detail.js) сравнивает его
    # со своим taskId и делает автоматический редирект на /task-board, если совпало.
    # actor_id (не exclude_user_id — см. комментарий в create_task выше): страницы,
    # слушающие task_deleted (task-detail.js/subtask-board.js), сами различают
    # своё/чужое удаление по actor_id, где это нужно.
    await broadcast_task_event(
        "task_deleted", snapshot.title,
        sender_email=user.email, task_id=task_id, actor_id=user.id,
    )
    return snapshot
