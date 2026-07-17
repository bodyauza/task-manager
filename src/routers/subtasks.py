from typing import List

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user       # DI: возвращает текущего аутентифицированного User
from src.auth.models import User
from src.crm.subtask_service import SubtaskCRMSync, get_subtask_crm_sync
from src.database import get_async_session          # DI: выдаёт AsyncSession из пула
from src.services import subtasks as subtask_service
from src.task_logic.subtask_schemas import SubtaskCreate, SubtaskResponse, SubtaskUpdate

router = APIRouter(tags=["Working with subtasks"])  # тег группирует эндпоинты в Swagger UI


@router.post("/create-subtask/", response_model=SubtaskResponse, status_code=201)
async def create_subtask(
    subtask: SubtaskCreate,                         # тело запроса: task_id, title, description
    user: User = Depends(current_user),             # требует аутентификации; 401 если токен недействителен
    db: AsyncSession = Depends(get_async_session),  # сессия выдаётся на время запроса
    crm: SubtaskCRMSync = Depends(get_subtask_crm_sync),
):
    return await subtask_service.create_subtask(db, user, subtask, crm)


@router.get("/subtasks/", response_model=List[SubtaskResponse])
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


@router.get("/subtasks/{subtask_id}", response_model=SubtaskResponse)
async def get_subtask(
    subtask_id: int,                                # path-параметр: /subtasks/42
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await subtask_service.get_subtask(db, subtask_id)


@router.patch("/subtasks/{subtask_id}", response_model=SubtaskResponse)
async def update_subtask(
    subtask_id: int,
    subtask_update: SubtaskUpdate,                  # тело: только изменяемые поля (partial update)
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    crm: SubtaskCRMSync = Depends(get_subtask_crm_sync),
):
    return await subtask_service.update_subtask(db, user, subtask_id, subtask_update, crm)


@router.delete("/delete-subtask/{subtask_id}", response_model=SubtaskResponse)
async def delete_subtask(
    subtask_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    crm: SubtaskCRMSync = Depends(get_subtask_crm_sync),
):
    return await subtask_service.delete_subtask(db, user, subtask_id, crm)
