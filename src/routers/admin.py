"""Маршруты под /admin/* (кроме самой sqladmin-панели, src/admin/).

Вынесены из pages.py в отдельный роутер: sqladmin монтируется на /admin как
Mount(...) через setup_admin(app), а Starlette матчит маршруты в порядке
регистрации и НЕ проваливается дальше после совпадения префикса Mount —
пути под /admin/*, зарегистрированные после него (pages_router подключается
последним), получили бы 404 от sqladmin. admin_router подключается в main.py
ДО setup_admin(app), поэтому эти пути матчатся первыми.
"""

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
    """Ставит задачу немедленной синхронизации таблицы project с CRM в очередь
    Celery — не выполняет её сама в веб-процессе (см.
    docs/project_field_crm_implementation_guide.md, §3.13). 202 Accepted, не 204:
    работа принята к исполнению, но не гарантированно завершена к моменту
    ответа — Celery-воркер выполнит её асинхронно.

    Вызывается вручную администратором сразу после правки списка «Проект» в
    самой CRM — не дожидаясь следующего срабатывания Celery Beat
    (CRM_PROJECT_SYNC_INTERVAL_SECONDS, по умолчанию 180с).
    """
    sync_project_table.delay()
    return {"status": "queued"}


# ── Admin: статус CRM-синхронизации (единственное официальное место, где он
# вообще виден — см. TaskResponse/SubtaskResponse, где этих полей больше нет) ──

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
    """Все задачи ВСЕХ владельцев (без owner-фильтрации — сама admin-only
    защита уже достаточна, см. services/admin_sync.py) с текущим
    Task.sync_status и снимком последней попытки из crm_outbox."""
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
    """HTML-страница над двумя эндпоинтами выше — данные грузит клиентский
    JS (admin-crm-sync.js), тем же паттерном, что task-board.html/*.js."""
    return templates.TemplateResponse(
        request, "admin-crm-sync.html", {"current_page": "admin-crm-sync", "is_admin": True},
    )
