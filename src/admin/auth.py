"""Аутентификация для sqladmin-панели (/admin) — доступ только роли admin.

Не переиспользует access_token-куку основного приложения
(src/auth/auth_config.py): sqladmin ведёт собственную HttpOnly-сессию через
starlette SessionMiddleware (подключается автоматически AuthenticationBackend.
__init__), подписанную тем же settings.access_secret, но независимую от JWT-кук
/auth/login. Кука подписанная и без серверного состояния, поэтому работает при
UVICORN_WORKERS > 1. Истечение access_token не обрывает сессию администратора
в /admin, и наоборот — sqladmin не трогает куки, которые читает common.js.
"""

from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from src.auth.manager import UserManager
from src.auth.user_models import User
from src.database import async_session_maker
from src.services.access import is_admin


class _LoginCredentials:
    """Минимальная замена OAuth2PasswordRequestForm: UserManager.authenticate()
    обращается только к .username/.password — sqladmin присылает обычную
    HTML-форму (username, password)."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = (form.get("username") or "").strip()
        password = form.get("password") or ""
        if not email or not password:
            return False

        async with async_session_maker() as session:
            user_db = SQLAlchemyUserDatabase(session, User)
            # crm_registrar=None: authenticate() его не вызывает (только
            # get_by_email/password_helper) — CRM при входе в /admin не нужен.
            manager = UserManager(user_db, crm_registrar=None)
            user = await manager.authenticate(_LoginCredentials(email, password))
            if user is None or not user.is_active or not await is_admin(session, user.id):
                return False
            user_id = user.id

        request.session["admin_user_id"] = user_id
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        user_id = request.session.get("admin_user_id")
        if user_id is None:
            return False

        async with async_session_maker() as session:
            user = await session.get(User, user_id)
            return bool(
                user is not None and user.is_active and await is_admin(session, user.id)
            )
