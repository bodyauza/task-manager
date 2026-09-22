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
    """{crm_id: label} по активным строкам project — прямой запрос к локальной
    таблице, без похода в CRM (см. docs/project_field_crm_implementation_guide.md,
    §1.2/§3.11/§3.13) — таблица наполняется отдельно, Celery Beat
    (src/tasks/global_lists_tasks.py::sync_project_table).
    """
    rows = (
        await db.execute(select(Project.crm_id, Project.label).where(Project.is_active.is_(True)))
    ).all()
    return dict(rows)


async def _is_admin(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_async_session),
) -> bool:
    """Только для решения «показывать ли ссылку на admin-страницу в навбаре»
    (_navbar.html) — НЕ заменяет require_role("admin") как защиту самого
    admin-эндпоинта (тот использует _admin_only отдельно). Явный select(),
    а не user.roles: связь не lazy="selectin" — синхронное обращение к ней в
    Jinja2 упало бы MissingGreenlet (тот же приём, что и в require_role() и
    profile_page ниже).
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
    # Server-side guard: без reg_token пользователь не должен попасть на эту страницу.
    # Полная валидация (подпись + срок) происходит при POST /auth/register/complete.
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
    # current_page передаётся в _navbar.html для выделения активной ссылки меню.
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
    # Явный select().join() вместо user.roles: пользователь теперь может иметь
    # несколько ролей (many-to-many, user_role) — тот же приём, что в require_role()
    # (src/auth/auth_config.py), и та же причина, по которой это не user.roles
    # напрямую — обращение к незагруженной lazy-relationship в async-коде роняет
    # запрос MissingGreenlet.
    roles = (await db.execute(
        select(Role).join(user_role).where(user_role.c.person_id == user.id)
    )).scalars().all()
    is_admin = any(role.name == "admin" for role in roles)  # уже загружено выше — без доп. запроса

    # Jinja2 рендерит шаблон синхронно. Если передать ORM-объект напрямую,
    # обращение к «ленивым» атрибутам внутри шаблона вызовет MissingGreenlet:
    # SQLAlchemy не может выполнить SELECT вне async-контекста. Все нужные значения
    # извлекаются здесь, пока сессия открыта, и передаются в шаблон как обычные
    # Python-значения.
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
