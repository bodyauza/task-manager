import json
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user, get_access_strategy
from src.auth.manager import UserManager, get_user_manager
from src.auth.models import Task, User
from src.database import get_async_session
from src.task_logic.task_schemas import TaskCreate, TaskResponse, TaskUpdate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Working with tasks"])

# Ключ — user.id из проверенного JWT-токена, не client_id из URL.
# Это исключает подмену слота: злоумышленник не может занять соединение
# другого пользователя, подставив чужой ID в URL.
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
    """
    WebSocket с JWT-аутентификацией до accept().
    client_id в URL не используется для идентификации — слот регистрируется
    по user.id из токена, что исключает захват чужого соединения.
    """
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
    db_task = Task(**task.model_dump(), owner_id=user.id)
    db.add(db_task)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Task with title '{task.title}' already exists")
    await db.refresh(db_task)
    await broadcast_task_event("task_created", db_task.title, exclude_user_id=user.id, sender_email=user.email)
    return db_task


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
    title: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(5, ge=1, le=100),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    if not title.strip():
        raise HTTPException(status_code=400, detail="Title query parameter must not be empty")

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


@router.put("/update-task/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    task_update: TaskUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    db_task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if db_task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    update_data = task_update.model_dump(exclude_unset=True)
    # Вычисляем до commit, пока db_task не expire после возможного rollback
    title_to_report = update_data.get("title") or db_task.title
    for key, value in update_data.items():
        setattr(db_task, key, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Task with title '{title_to_report}' already exists")
    await db.refresh(db_task)
    await broadcast_task_event("task_updated", db_task.title, exclude_user_id=user.id, sender_email=user.email)
    return db_task


@router.delete("/delete-task/{task_id}", response_model=TaskResponse)
async def delete_task(
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # snapshot — это простой Pydantic-объект, не знает о SQLAlchemy.
    # обращение к snapshot.title — это просто чтение поля Python-класса.
    snapshot = TaskResponse.model_validate(task)
    await db.delete(task)
    await db.commit()
    await broadcast_task_event("task_deleted", snapshot.title, exclude_user_id=user.id, sender_email=user.email)
    return snapshot
