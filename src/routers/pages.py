import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.auth.auth_config import current_user
from src.auth.models import User

router = APIRouter(tags=["Pages"])

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html")


@router.get("/task-board", response_class=HTMLResponse)
async def task_board(request: Request, user: User = Depends(current_user)):
    return templates.TemplateResponse(request, "task-board.html", {"user": user.id})
