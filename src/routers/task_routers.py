import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user
from src.auth.user_models import User
from src.database import get_async_session
from src.openapi_responses import responses
from src.services import tasks as task_service
from src.task_logic.task_schemas import TaskCreate, TaskResponse, TaskUpdate

router = APIRouter(tags=["Working with tasks"])


@router.post(
    "/create-task/",
    response_model=TaskResponse,
    status_code=201,
    summary="Создать задачу",
    description=(
        "Создаёт задачу и (необязательно) её файлы одним запросом. Тело — `multipart/form-data`: "
        "поле `data` содержит **JSON-строку** задачи, файлы — поля `specification` (ТЗ) и `other_files` (до 10). "
        "Все файлы проверяются до записи в БД; сбой сохранения отдельного файла не отменяет создание — "
        "причины возвращаются в `file_upload_errors`."
    ),
    responses=responses(401, 409, c409="Задача с таким названием у этого владельца уже существует"),
)
async def create_task(
    # multipart/form-data: тело задачи — JSON-строка в form-поле "data" (FastAPI 0.115 не разворачивает
    # Pydantic-модель в form-поля рядом с File(...)). Задача и файлы создаются одним запросом.
    data: str = Form(
        ...,
        description='JSON-строка задачи: {"title": str (1–100), "description": str (≤ 2000), "project": str | null}',
        examples=['{"title": "Подготовить отчёт", "description": "Квартальный отчёт", "project": null}'],
    ),
    specification: Optional[UploadFile] = File(None),
    other_files: List[UploadFile] = File([]),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    try:
        task = TaskCreate.model_validate_json(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json()))
    return await task_service.create_task(db, user, task, specification, other_files)


@router.get(
    "/tasks/",
    response_model=List[TaskResponse],
    summary="Список задач",
    description="Общий список задач, по возрастанию `id`. Общее число — в заголовке `X-Total-Count`.",
    responses=responses(401),
)
async def read_tasks(
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(5, ge=1, le=100),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    results, total = await task_service.list_tasks(db, skip, limit)
    response.headers["X-Total-Count"] = str(total)
    return results


@router.get(
    "/tasks/search",
    response_model=List[TaskResponse],
    summary="Поиск задач по названию",
    description="Регистронезависимый поиск по части названия; `%`, `_` и `\\` ищутся как обычные символы. Общее число — в `X-Total-Count`.",
    responses=responses(400, 401, c400="Параметр `title` пустой"),
)
async def search_tasks_by_title(
    response: Response,
    title: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(5, ge=1, le=100),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    tasks, total = await task_service.search_tasks(db, title, skip, limit)
    response.headers["X-Total-Count"] = str(total)
    return tasks


@router.get(
    "/tasks/{task_id}",
    response_model=TaskResponse,
    summary="Получить задачу",
    responses=responses(401, 404),
)
async def get_task(
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await task_service.get_task(db, task_id)


@router.patch(
    "/tasks/{task_id}",
    response_model=TaskResponse,
    summary="Обновить задачу",
    description="Частичное обновление: меняются только переданные поля. `project: \"\"` очищает проект.",
    responses=responses(401, 404, 409),
)
async def update_task(
    task_id: int,
    task_update: TaskUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await task_service.update_task(db, user, task_id, task_update)


@router.delete(
    "/delete-task/{task_id}",
    response_model=TaskResponse,
    summary="Удалить задачу",
    description="Удаляет задачу вместе с подзадачами и файлами.",
    responses=responses(401, 404),
)
async def delete_task(
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await task_service.delete_task(db, user, task_id)
