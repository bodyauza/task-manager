"""Сборка sqladmin-панели (/admin): Admin(...) + AuthenticationBackend + views.

Панель — дополнительный инструмент для admin (просмотр/точечная правка данных
напрямую в БД), а НЕ замена продуктовых роутов: запись через форму sqladmin
идёт в обход CRM-синхронизации (outbox), WebSocket-событий и файловой логики,
поэтому CRM-id, sync_status и файловые поля из форм исключены (см. task_admin.py,
user_admin.py).

setup_admin() монтирует sqladmin как Mount на /admin — все остальные маршруты
под /admin/* (admin_router в routers/admin.py) обязаны быть подключены в
main.py ДО вызова setup_admin(), иначе Mount перехватит их и ответит своим 404.
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

# Абсолютный путь (не от cwd процесса — он зависит от способа запуска):
# здесь лежат только переопределения sqladmin/layout.html и login.html;
# Jinja2 ChoiceLoader сначала ищет шаблон тут, потом — в пакете sqladmin.
_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def setup_admin(app: FastAPI) -> None:
    admin = Admin(
        app,
        engine,
        session_maker=async_session_maker,
        title="Task Manager — админ-панель",
        templates_dir=_TEMPLATES_DIR,
        # access_secret — ключ подписи сессионной куки sqladmin (Starlette
        # SessionMiddleware, см. auth.py): независимая кука от JWT основного логина.
        authentication_backend=AdminAuth(secret_key=settings.access_secret),
    )
    admin.add_view(UserAdmin)
    admin.add_view(RoleAdmin)
    admin.add_view(RegistrationPendingAdmin)
    admin.add_view(TaskAdmin)
    admin.add_view(SubtaskAdmin)
    admin.add_view(ProjectAdmin)
    admin.add_view(CrmOutboxAdmin)
