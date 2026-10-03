"""Сборка sqladmin-панели (/admin): Admin + AuthenticationBackend + views.

Панель — инструмент администратора, а не замена продуктовых роутов: запись через форму идёт мимо outbox,
WebSocket и файловой логики, поэтому CRM-id, sync_status и файловые поля исключены из форм.
Маршруты /admin/* (routers/admin.py) должны быть подключены в main.py ДО setup_admin().
"""

import os

from fastapi import FastAPI
from sqladmin import Admin

from src.admin.auth import AdminAuth
from src.admin.outbox_admin import CrmOutboxAdmin
from src.admin.project_admin import ProjectAdmin
from src.admin.registration_pending_admin import RegistrationPendingAdmin
from src.admin.role_admin import RoleAdmin
from src.admin.task_admin import SubtaskAdmin, TaskAdmin
from src.admin.user_admin import UserAdmin
from src.config import settings
from src.database import async_session_maker, engine

# Абсолютный путь (не от cwd): здесь переопределения sqladmin/layout.html и login.html.
_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def setup_admin(app: FastAPI) -> None:
    admin = Admin(
        app,
        engine,
        session_maker=async_session_maker,
        title="Task Manager — админ-панель",
        templates_dir=_TEMPLATES_DIR,
        # ADMIN_SESSION_SECRET — ключ подписи сессионной куки sqladmin, независимый от JWT основного логина.
        authentication_backend=AdminAuth(secret_key=settings.ADMIN_SESSION_SECRET),
    )
    admin.add_view(UserAdmin)
    admin.add_view(RoleAdmin)
    admin.add_view(RegistrationPendingAdmin)
    admin.add_view(TaskAdmin)
    admin.add_view(SubtaskAdmin)
    admin.add_view(ProjectAdmin)
    admin.add_view(CrmOutboxAdmin)
