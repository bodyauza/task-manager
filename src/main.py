from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, Depends, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_utils import database_exists, create_database
from sqlalchemy import select
from starlette.responses import HTMLResponse

from src.auth.user_schemas import UserRead, UserCreate
from src.database import Base, engine, get_async_session
from src.auth.auth_config import (fastapi_users, auth_backend, current_user,
                                  get_access_strategy)
from src.auth.models import User, Task

from fastapi import Response, status
from fastapi.responses import JSONResponse
from fastapi_users import models

from typing import List, Set

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from src.task_logic.task_schemas import TaskResponse, TaskCreate, TaskUpdate

templates = Jinja2Templates(directory="templates")

async def create_clients_db():
    print("До create_all:", Base.metadata.tables.keys())
    sync_url = engine.url.set(drivername="postgresql+psycopg2")
    sync_engine = create_engine(sync_url)

    if not database_exists(sync_engine.url):
        create_database(sync_engine.url)
    sync_engine.dispose()  # закрываем синхронное соединение
    try:
        async with engine.begin() as conn:
            # Синхронно создает все таблицы из моделей, наследующих Base
            await conn.run_sync(Base.metadata.create_all)
        print("Таблицы базы данных успешно созданы")
    except Exception as e:
        print(f"Ошибка при создании таблиц: {e}")


# Инициализация FastAPI с lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_clients_db()
    yield

app = FastAPI(
    title='Currency Conversion',
    lifespan=lifespan
)


app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth",
    tags=["Authentication"],
)


app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["Authentication"],
)


origins = [
    "http://localhost",
    "http://localhost:8080",
    "http://127.0.0.1:8000",
    # port for react
    "http://localhost:3000",
    "http://127.0.0.1:3000"
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS", "DELETE", "PATCH", "PUT"],
    allow_headers=["Content-Type", "Set-Cookie", "Access-Control-Allow-Headers", "Access-Control-Allow-Origin",
                   "Authorization"],
)


# Добавляем новые маршруты для работы с токенами
auth_router = APIRouter(prefix="/auth", tags=["Authentication"])


@auth_router.post("/access-token")
async def get_access_token(
        response: Response,
        user: models.UP = Depends(current_user)
):
    # Генерируем access токен
    access_token = await auth_backend.login(strategy=get_access_strategy(), user=user)
    await auth_backend.transport.get_login_response(access_token, response)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Access token successfully updated!"},
    )


@auth_router.post("/logout")
async def logout(
        response: Response,
        user: models.UP = Depends(current_user),
):
    # Удаляем куки с токеном
    await auth_backend.transport.get_logout_response(response)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Successfully logged out"},
    )


app.include_router(auth_router)

# Добавляем новые маршруты для авторизации
router = APIRouter(
    tags=["Authorization"]
)


"""
- `GET /api/v1/tasks/`: Получение списка задач (защищённая конечная точка).
- `POST /api/v1/tasks/`: Создание новой задачи (защищённая конечная точка).
- `GET /api/v1/tasks/{task_id}`: Получить определённую задачу (защищённая конечная точка).
- `PUT /api/v1/tasks/{task_id}`: Обновить определённую задачу (защищённая конечная точка).
- `DELETE /api/v1/tasks/{task_id}`: Удалить определённую задачу (защищённая конечная точка).

WebSocket — протокол связи поверх TCP-соединения (см. Модель OSI), предназначенный для обмена сообщениями между браузером и веб-сервером,
используя постоянное соединение:

  - Использует собственный протокол `ws://` или `wss://` поверх TCP-соединения.
  - Соединение остается открытым, позволяя серверу и клиенту обмениваться данными в реальном времени без повторных запросов.
  - Сервер может самостоятельно инициировать отправку данных клиенту (например, уведомления, чаты, онлайн-игры).
  - Данные передаются в виде кадров (frames) с минимальными накладными расходами.
"""

# Множество для хранения активных WebSocket подключений
active_connections: Set[WebSocket] = set()

# Функция для отправки сообщения всем подписанным пользователям
async def publish_message(client_id, message):
    for connection in active_connections:
        await connection.send_text(f"Client with {client_id} wrote {message}!")

# Для получения обновлений статуса задачи в режиме реального времени
# используйте WebSocket-подключения к `"ws://localhost:8000/ws/tasks/{client_id}`.
# Пример клиентской стороны для подписки на обновление статуса задачи:
# const socket = new WebSocket("ws://localhost:8000/ws/tasks/{client_id}")
@app.websocket("/ws/tasks/{client_id}")
async def websocket_endpoint(client_id: int, websocket: WebSocket):
    # Принимаем WebSocket соединение
    await websocket.accept()
    # Добавляем соединение в множество активных
    active_connections.add(websocket)
    try:
        # Бесконечный цикл для приема сообщений от клиента
        while True:
            # Ожидаем текстовое сообщение от клиента
            message = await websocket.receive_text()
            # Рассылаем сообщение всем подключенным клиентам
            await publish_message(client_id, message)
    except WebSocketDisconnect:
        # При отключении клиента удаляем его из активных соединений
        active_connections.remove(websocket)

"""
Тест подключения к WebSocket:

INFO:     127.0.0.1:2310 - "GET /websocket-test HTTP/1.1" 200 OK
INFO:     ('127.0.0.1', 3897) - "WebSocket /ws/tasks/1" [accepted]
INFO:     connection open
"""
@router.get("/websocket-test", response_class=HTMLResponse)
async def protected_user_route(request: Request):
    return templates.TemplateResponse(
        "websocket_conn.html",
        {
            "request": request
        }
    )

# Создание новой задачи
@router.post("/tasks/", response_model=TaskResponse)
async def create_task(task: TaskCreate, user: User = Depends(current_user), db: AsyncSession = Depends(get_async_session)):
    # Создаем объект задачи, добавляя ID владельца
    db_task = Task(**task.model_dump(), owner_id=user.id)
    # Добавляем задачу в сессию
    db.add(db_task)
    # Сохраняем изменения в базе данных
    await db.commit()
    # Обновляем объект из базы (получаем сгенерированный ID и т.д.)
    await db.refresh(db_task)
    # Рассылаем уведомление всем активным WebSocket клиентам
    for connection in active_connections:
        await connection.send_text(f"New task created: {db_task.title}")
    return db_task

# Получение списка задач с пагинацией
@router.get("/tasks/", response_model=List[TaskResponse])
async def read_tasks(skip: int = 0, limit: int = 10, db: AsyncSession = Depends(get_async_session)):
    # Запрос задач с пропуском и ограничением количества
    stmt = select(Task).offset(skip).limit(limit)
    result = await db.execute(stmt)
    tasks = result.scalars().all()
    return tasks

# Получение конкретной задачи по ID
@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def read_task(task_id: int, db: AsyncSession = Depends(get_async_session)):
    # Поиск задачи по ID
    stmt = select(Task).where(Task.id == task_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

# Обновление задачи
@router.put("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(task_id: int, task_update: TaskUpdate, db: AsyncSession = Depends(get_async_session)):
    # Поиск задачи для обновления
    stmt = select(Task).where(Task.id == task_id)
    result = await db.execute(stmt)
    db_task = result.scalar_one_or_none()
    if db_task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # Обновление полей задачи из переданных данных
    update_data = task_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_task, key, value)
    # Сохранение изменений
    await db.commit()
    # Обновление объекта из базы
    await db.refresh(db_task)
    # Уведомление клиентов об обновлении
    for connection in active_connections:
        await connection.send_text(f"Task {db_task.id} updated")
    return db_task

# Удаление задачи
@router.delete("/tasks/{task_id}", response_model=TaskResponse)
async def delete_task(task_id: int, db: AsyncSession = Depends(get_async_session)):
    # Поиск задачи для удаления
    stmt = select(Task).where(Task.id == task_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # Удаление задачи из базы данных
    await db.delete(task)
    await db.commit()
    # Уведомление клиентов об удалении
    for connection in active_connections:
        await connection.send_text(f"Task {task.id} deleted")
    return task


app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, ws='auto')
