import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user       # DI: возвращает текущего аутентифицированного User
from src.auth.user_models import User
from src.database import get_async_session          # DI: выдаёт AsyncSession из пула
from src.openapi_responses import responses
from src.services import subtasks as subtask_service
from src.task_logic.subtask_schemas import SubtaskCreate, SubtaskResponse, SubtaskUpdate

router = APIRouter(tags=["Working with subtasks"])  # тег группирует эндпоинты в Swagger UI


@router.post(
    "/create-subtask/",
    response_model=SubtaskResponse,
    status_code=201,
    summary="Создать подзадачу",
    description=(
        "Создаёт подзадачу и (необязательно) её файлы одним запросом. Тело — `multipart/form-data`: "
        "поле `data` содержит **JSON-строку**, файлы — `specification` и `other_files`. "
        "Семантика та же, что у `POST /create-task/` (включая `file_upload_errors`)."
    ),
    responses=responses(401, 404, 409,
                        c404="Родительская задача не найдена",
                        c409="Подзадача с таким названием в этой задаче уже существует"),
)
async def create_subtask(
    # multipart/form-data — см. пояснение в routers/task_routers.py::create_task.
    # task_id остаётся внутри JSON-строки "data" (SubtaskCreate не меняется).
    data: str = Form(
        ...,
        description='JSON-строка подзадачи: {"task_id": int, "title": str (1–100), "description": str (≤ 2000)}',
        examples=['{"task_id": 1, "title": "Собрать данные", "description": ""}'],
    ),
    specification: Optional[UploadFile] = File(None),
    other_files: List[UploadFile] = File([]),
    user: User = Depends(current_user),             # требует аутентификации; 401 если токен недействителен
    db: AsyncSession = Depends(get_async_session),  # сессия выдаётся на время запроса
):
    try:
        subtask = SubtaskCreate.model_validate_json(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json()))
    return await subtask_service.create_subtask(db, user, subtask, specification, other_files)


@router.get(
    "/subtasks/",
    response_model=List[SubtaskResponse],
    summary="Список подзадач задачи",
    description="Подзадачи одной задачи (`task_id` обязателен), по возрастанию `id`. Общее число — в `X-Total-Count`.",
    responses=responses(401),
)
async def read_subtasks(
    response: Response,                             # объект HTTP-ответа: для записи заголовков
    task_id: int,                                   # query-параметр: ?task_id=5
    skip: int = Query(0, ge=0),                     # смещение (offset) для пагинации; >= 0
    limit: int = Query(20, ge=1, le=100),           # размер страницы; от 1 до 100
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    subtasks, total = await subtask_service.list_subtasks(db, task_id, skip, limit)
    response.headers["X-Total-Count"] = str(total)  # фронтенд читает заголовок для пагинации
    return subtasks


@router.get(
    "/subtasks/{subtask_id}",
    response_model=SubtaskResponse,
    summary="Получить подзадачу",
    responses=responses(401, 404),
)
async def get_subtask(
    subtask_id: int,                                # path-параметр: /subtasks/42
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await subtask_service.get_subtask(db, subtask_id)


@router.patch(
    "/subtasks/{subtask_id}",
    response_model=SubtaskResponse,
    summary="Обновить подзадачу",
    description="Частичное обновление: меняются только переданные поля.",
    responses=responses(401, 404, 409),
)
async def update_subtask(
    subtask_id: int,
    subtask_update: SubtaskUpdate,                  # тело: только изменяемые поля (partial update)
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await subtask_service.update_subtask(db, user, subtask_id, subtask_update)


@router.delete(
    "/delete-subtask/{subtask_id}",
    response_model=SubtaskResponse,
    summary="Удалить подзадачу",
    responses=responses(401, 404),
)
async def delete_subtask(
    subtask_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await subtask_service.delete_subtask(db, user, subtask_id)
