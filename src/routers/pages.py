import os
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user
from src.auth.user_models import Role, User
from src.database import get_async_session
from src.task_logic.models import Subtask, Task

router = APIRouter(tags=["Pages"])

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


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
async def task_board(request: Request, user: User = Depends(current_user)):
    # current_page передаётся в _navbar.html для выделения активной ссылки меню.
    return templates.TemplateResponse(
        request, "task-board.html", {"user": user.id, "current_page": "tasks"}
    )


@router.get("/subtask-board/{task_id}", response_class=HTMLResponse)
async def subtask_board(
    request: Request,
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
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
        }
    )


@router.get("/task/{task_id}", response_class=HTMLResponse)
async def task_detail(
    request: Request,
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return templates.TemplateResponse(
        request, "task-detail.html", {
            "user": user.id,
            "task_id": task_id,
            "current_page": "tasks",
        }
    )


@router.get("/subtask/{subtask_id}", response_class=HTMLResponse)
async def subtask_detail(
    request: Request,
    subtask_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
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
        }
    )


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    role = await db.get(Role, user.role_id)

    # Jinja2 рендерит шаблон синхронно. Если передать ORM-объект напрямую,
    # обращение к «ленивым» атрибутам (например, user.role.name) внутри шаблона
    # вызовет MissingGreenlet: SQLAlchemy не может выполнить SELECT вне async-контекста.
    # Все нужные значения извлекаются здесь, пока сессия открыта, и передаются
    # в шаблон как обычные Python-значения.
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
            "role_name":     role.name if role else "—",
            "registered_at": (
                user.registered_at.strftime("%d.%m.%Y") if user.registered_at else "—"
            ),
            "is_active": user.is_active,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response
