from typing import List

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user
from src.auth.models import User
from src.crm.subtask_service import SubtaskCRMSync, get_subtask_crm_sync
from src.crm.task_service import TaskCRMSync, get_task_crm_sync
from src.database import get_async_session
from src.services import tasks as task_service
from src.task_logic.task_schemas import TaskCreate, TaskResponse, TaskUpdate

router = APIRouter(tags=["Working with tasks"])


@router.post("/create-task/", response_model=TaskResponse, status_code=201)
async def create_task(
    task: TaskCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    crm: TaskCRMSync = Depends(get_task_crm_sync),
):
    return await task_service.create_task(db, user, task, crm)


@router.get("/tasks/", response_model=List[TaskResponse])
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


@router.get("/tasks/search", response_model=List[TaskResponse])
async def search_tasks_by_title(
    response: Response,
    title: str,  # ← Query, обязательный: нет default → FastAPI требует его в URL
    skip: int = Query(0, ge=0),  # ← Query, опциональный: default=0, валидация ≥ 0
    limit: int = Query(5, ge=1, le=100),  # ← Query, опциональный: default=5, валидация 1–100
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    tasks, total = await task_service.search_tasks(db, title, skip, limit)
    response.headers["X-Total-Count"] = str(total)
    return tasks


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await task_service.get_task(db, task_id)


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    task_update: TaskUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    crm: TaskCRMSync = Depends(get_task_crm_sync),
):
    return await task_service.update_task(db, user, task_id, task_update, crm)


@router.delete("/delete-task/{task_id}", response_model=TaskResponse)
async def delete_task(
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    crm: TaskCRMSync = Depends(get_task_crm_sync),
    subtask_crm: SubtaskCRMSync = Depends(get_subtask_crm_sync),
):
    return await task_service.delete_task(db, user, task_id, crm, subtask_crm)
