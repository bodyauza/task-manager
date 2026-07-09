import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user, get_access_strategy
from src.auth.manager import UserManager, get_user_manager
from src.auth.models import User
from src.task_logic.models import Task
from src.database import get_async_session
from src.task_logic.task_schemas import TaskCreate, TaskResponse, TaskUpdate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Working with tasks"])

# Словарь активных WS-соединений: user_id → (websocket, email).
# Ключ — user_id: один пользователь может иметь только одно активное соединение.
# Новое соединение вытесняет предыдущее (закрывает его кодом 1000).
# При разрыве соединения запись удаляется в блоке except WebSocketDisconnect.
active_connections: dict[int, tuple[WebSocket, str]] = {}


async def _send_safe(uid: int, connection: WebSocket, payload: str) -> bool:
    """Отправляет сообщение и возвращает False, если соединение мертво."""
    try:
        await connection.send_text(payload)
        return True
    except Exception:
        return False


async def publish_message(sender_user_id: int, message: str, sender: WebSocket):
    """Рассылает чат-сообщение всем клиентам, кроме отправителя."""
    sender_email = active_connections[sender_user_id][1]
    payload = json.dumps({"type": "chat", "sender": sender_email, "text": message})
    dead: list[int] = []
    for uid, (connection, _) in list(active_connections.items()):
        if connection is sender:
            continue
        if not await _send_safe(uid, connection, payload):
            dead.append(uid)
    for uid in dead:
        active_connections.pop(uid, None)


async def broadcast_task_event(
    event_type: str,
    title: str,
    exclude_user_id: int | None = None,
    sender_email: str = "",
):
    """Рассылает событие задачи всем клиентам, кроме инициатора изменения."""
    payload = json.dumps({"type": event_type, "title": title, "sender": sender_email})
    dead: list[int] = []
    for uid, (connection, _) in list(active_connections.items()):
        if uid == exclude_user_id:
            continue
        if not await _send_safe(uid, connection, payload):
            dead.append(uid)
    for uid in dead:
        active_connections.pop(uid, None)


@router.websocket("/ws/tasks/{client_id}")
async def websocket_endpoint(
    client_id: int,
    websocket: WebSocket,
    user_manager: UserManager = Depends(get_user_manager),
):
    # Браузеры не поддерживают заголовок Authorization при WS-хэндшейке.
    # Аутентификация выполняется через куку access_token, установленную при логине.
    token = websocket.cookies.get("access_token")
    if token is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user = await get_access_strategy().read_token(token, user_manager)
    if user is None or not user.is_active:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    old_entry = active_connections.get(user.id)
    if old_entry is not None and old_entry[0] is not websocket:
        try:
            await old_entry[0].close(code=status.WS_1000_NORMAL_CLOSURE)
        except RuntimeError:
            pass

    active_connections[user.id] = (websocket, user.email)
    try:
        while True:
            message = await websocket.receive_text()
            await publish_message(user.id, message, websocket)
    except WebSocketDisconnect:
        entry = active_connections.get(user.id)
        if entry is not None and entry[0] is websocket:
            del active_connections[user.id]


@router.post("/create-task/", response_model=TaskResponse, status_code=201)
async def create_task(
    task: TaskCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    # Пробуем создать задачу в CRM до INSERT в БД (best-effort).
    # crm_task_id=None означает, что синхронизация не выполнена.
    from src.crm.task_service import TaskManager as CRMTaskManager

    crm_task_id: Optional[int] = None
    try:
        tm = CRMTaskManager()
        crm_result = await tm.create_task(
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
        raise HTTPException(status_code=409, detail=f"Task with title '{task.title}' already exists")
    await db.refresh(db_task)
    await broadcast_task_event("task_created", db_task.title, exclude_user_id=user.id, sender_email=user.email)
    result = TaskResponse.model_validate(db_task)
    result.crm_synced = crm_task_id is not None
    return result


@router.get("/tasks/", response_model=List[TaskResponse])
async def read_tasks(
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(5, ge=1, le=100),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    tasks = (await db.execute(select(Task).offset(skip).limit(limit))).scalars().all()
    total = (await db.execute(select(func.count()).select_from(Task))).scalar_one()
    response.headers["X-Total-Count"] = str(total)
    return tasks


@router.get("/tasks/search", response_model=List[TaskResponse])
async def search_tasks_by_title(
    response: Response,
    title: str, # ← Query, обязательный: нет default → FastAPI требует его в URL
    skip: int = Query(0, ge=0), # ← Query, опциональный: default=0, валидация ≥ 0
    limit: int = Query(5, ge=1, le=100), # ← Query, опциональный: default=5, валидация 1–100
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    if not title.strip():
        raise HTTPException(status_code=400, detail="Title query parameter must not be empty")

    # Экранируем спецсимволы SQL LIKE (\, %, _) перед подстановкой в паттерн.
    # Без экранирования поиск по строке «100%» найдёт все записи, а не только содержащие «100%».
    escaped = title.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    tasks = (await db.execute(
        select(Task).where(Task.title.ilike(pattern, escape="\\")).offset(skip).limit(limit)
    )).scalars().all()
    total = (await db.execute(
        select(func.count()).select_from(Task).where(Task.title.ilike(pattern, escape="\\"))
    )).scalar_one()
    response.headers["X-Total-Count"] = str(total)
    return tasks


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    task_update: TaskUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
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
    await db.refresh(db_task)

    crm_synced: Optional[bool] = None
    if crm_task_id is not None:
        from src.crm.task_service import TaskManager as CRMTaskManager
        try:
            tm = CRMTaskManager()
            await tm.update_task(
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

    await broadcast_task_event("task_updated", db_task.title, exclude_user_id=user.id, sender_email=user.email)
    result = TaskResponse.model_validate(db_task)
    result.crm_synced = crm_synced
    return result


@router.delete("/delete-task/{task_id}", response_model=TaskResponse)
async def delete_task(
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    snapshot = TaskResponse.model_validate(task)
    # crm_task_id читается до delete/commit — после expire объект недоступен
    crm_task_id = task.crm_task_id

    await db.delete(task)
    await db.commit()

    if crm_task_id is not None:
        from src.crm.task_service import TaskManager as CRMTaskManager
        try:
            tm = CRMTaskManager()
            await tm.delete_task(crm_task_id)
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

    await broadcast_task_event("task_deleted", snapshot.title, exclude_user_id=user.id, sender_email=user.email)
    return snapshot
