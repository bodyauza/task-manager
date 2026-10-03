import os
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user, require_role
from src.auth.user_models import Role, User, user_role
from src.database import get_async_session
from src.task_logic.models import Project, Subtask, Task

router = APIRouter(tags=["Pages"])

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

_admin_only = require_role("admin")


async def _project_options(db: AsyncSession = Depends(get_async_session)) -> dict:
    """{crm_id: label} по активным строкам локальной таблицы project (наполняется Celery Beat)."""
    rows = (
        await db.execute(select(Project.crm_id, Project.label).where(Project.is_active.is_(True)))
    ).all()
    return dict(rows)


async def _is_admin(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_async_session),
) -> bool:
    """Нужно только чтобы решить, показывать ли ссылку на admin-страницу в навбаре; защитой эндпоинта служит require_role.
    Явный select(), а не user.roles — иначе MissingGreenlet в Jinja2.
    """
    role = (
        await db.execute(
            select(Role.id).join(user_role).where(
                user_role.c.person_id == user.id, Role.name == "admin",
            )
        )
    ).scalar_one_or_none()
    return role is not None


@router.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html")


@router.get("/confirm-email", response_class=HTMLResponse)
async def confirm_email_page(request: Request):
    return templates.TemplateResponse(request, "confirm-email.html")


@router.get("/complete-registration", response_class=HTMLResponse)
async def complete_registration_page(
    request: Request,
    reg_token: Optional[str] = Cookie(default=None),
):
    # Без reg_token на страницу попадать нельзя; полная валидация — при POST /auth/register/complete.
    if reg_token is None:
        return RedirectResponse(url="/register", status_code=302)
    return templates.TemplateResponse(request, "complete-registration.html")


@router.get("/task-board", response_class=HTMLResponse)
async def task_board(
    request: Request,
    user: User = Depends(current_user),
    project_options: dict = Depends(_project_options),
    is_admin: bool = Depends(_is_admin),
):
    return templates.TemplateResponse(
        request, "task-board.html",
        {"user": user.id, "current_page": "tasks", "project_options": project_options, "is_admin": is_admin},
    )


@router.get("/subtask-board/{task_id}", response_class=HTMLResponse)
async def subtask_board(
    request: Request,
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    is_admin: bool = Depends(_is_admin),
):
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return templates.TemplateResponse(
        request, "subtask-board.html", {
            "user": user.id,
            "task_id": task_id,
            "task_title": task.title,
            "current_page": "tasks",
            "is_admin": is_admin,
        }
    )


@router.get("/task/{task_id}", response_class=HTMLResponse)
async def task_detail(
    request: Request,
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    project_options: dict = Depends(_project_options),
    is_admin: bool = Depends(_is_admin),
):
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return templates.TemplateResponse(
        request, "task-detail.html", {
            "user": user.id,
            "task_id": task_id,
            "current_page": "tasks",
            "project_options": project_options,
            "is_admin": is_admin,
        }
    )


@router.get("/subtask/{subtask_id}", response_class=HTMLResponse)
async def subtask_detail(
    request: Request,
    subtask_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    is_admin: bool = Depends(_is_admin),
):
    subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id))
    ).scalar_one_or_none()
    if subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")
    return templates.TemplateResponse(
        request, "subtask-detail.html", {
            "user": user.id,
            "subtask_id": subtask_id,
            "task_id": subtask.task_id,
            "current_page": "tasks",
            "is_admin": is_admin,
        }
    )


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    # Явный select().join(), а не user.roles: ленивая связь в async роняет MissingGreenlet.
    roles = (await db.execute(
        select(Role).join(user_role).where(user_role.c.person_id == user.id)
    )).scalars().all()
    is_admin = any(role.name == "admin" for role in roles)

    # Jinja2 рендерит синхронно: значения извлекаем здесь, пока сессия открыта (иначе MissingGreenlet на ленивых атрибутах).
    response = templates.TemplateResponse(
        request,
        "profile.html",
        {
            "current_page":  "profile",
            "firstname":     user.firstname,
            "lastname":      user.lastname,
            "patronymic":    user.patronymic or "",
            "email":         user.email,
            "username":      user.username,
            "role_names":    [role.name for role in roles] if roles else ["—"],
            "registered_at": (
                user.registered_at.strftime("%d.%m.%Y") if user.registered_at else "—"
            ),
            "is_active": user.is_active,
            "is_admin": is_admin,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response
