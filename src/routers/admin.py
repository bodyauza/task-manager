"""Маршруты /admin/* вне sqladmin-панели. Подключается в main.py ДО setup_admin(): Mount на /admin
иначе перехватил бы эти пути и ответил 404.
"""

import asyncio
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import require_role
from src.auth.user_models import User
from src.database import get_async_session
from src.openapi_responses import responses
from src.services import admin_sync
from src.task_logic.admin_schemas import SubtaskSyncStatusResponse, TaskSyncStatusResponse
from src.tasks.global_lists_tasks import sync_project_table

admin_router = APIRouter(tags=["Admin"])

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

_admin_only = require_role("admin")


@admin_router.post(
    "/admin/crm-options/refresh",
    status_code=202,
    summary="Обновить справочник «Проект» из CRM",
    description="Ставит задачу синхронизации в очередь Celery; `202` — принято, но не обязательно завершено.",
    responses=responses(401, 403),
)
async def refresh_crm_options(admin: User = Depends(_admin_only)) -> dict:
    """Ставит в очередь Celery немедленную синхронизацию таблицы project с CRM (202 Accepted — работа принята, но не завершена).

    Вызывается администратором после правки списка «Проект» в CRM, не дожидаясь Beat. .delay() синхронный,
    поэтому вынесен в asyncio.to_thread. Исключение не перехватывается: durable-строки за этим путём нет,
    сбой должен быть виден администратору как 500.
    """
    await asyncio.to_thread(sync_project_table.delay)
    return {"status": "queued"}


# Admin: статус CRM-синхронизации

@admin_router.get(
    "/admin/crm-sync-status/tasks",
    response_model=list[TaskSyncStatusResponse],
    summary="Статус CRM-синхронизации задач",
    responses=responses(401, 403),
)
async def admin_tasks_sync_status(
    admin: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_async_session),
) -> list[TaskSyncStatusResponse]:
    """Все задачи всех владельцев с Task.sync_status и снимком последней попытки из crm_outbox."""
    return await admin_sync.list_task_sync_status(db)


@admin_router.get(
    "/admin/crm-sync-status/subtasks",
    response_model=list[SubtaskSyncStatusResponse],
    summary="Статус CRM-синхронизации подзадач",
    responses=responses(401, 403),
)
async def admin_subtasks_sync_status(
    admin: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_async_session),
) -> list[SubtaskSyncStatusResponse]:
    return await admin_sync.list_subtask_sync_status(db)


@admin_router.get("/admin/crm-sync", response_class=HTMLResponse)
async def admin_crm_sync_page(request: Request, admin: User = Depends(_admin_only)):
    """HTML-страница над эндпоинтами выше; данные грузит admin-crm-sync.js."""
    return templates.TemplateResponse(
        request, "admin-crm-sync.html", {"current_page": "admin-crm-sync", "is_admin": True},
    )
