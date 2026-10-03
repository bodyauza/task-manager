"""Аутентификация sqladmin-панели (/admin): доступ только роли admin.

sqladmin ведёт собственную HttpOnly-сессию (SessionMiddleware, секрет ADMIN_SESSION_SECRET), независимую
от JWT-кук /auth/login. Кука подписана и не хранит состояние на сервере, поэтому работает при UVICORN_WORKERS > 1.
"""

from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqladmin.authentication import AuthenticationBackend
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

from src.auth.manager import UserManager
from src.auth.user_models import User
from src.config import settings
from src.database import async_session_maker
from src.services.access import is_admin

# AuthenticationBackend.__init__ из sqladmin подключает SessionMiddleware без https_only и same_site, срок жизни — 14 дней.
# SameSite=lax здесь реальный CSRF-вектор: @action («Повторить») регистрируются как GET, и кука уходит
# при переходе по ссылке с чужого сайта.
ADMIN_SESSION_MAX_AGE_SECONDS = 4 * 60 * 60


class _LoginCredentials:
    """Минимальная замена OAuth2PasswordRequestForm: UserManager.authenticate() читает только .username/.password."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password


class AdminAuth(AuthenticationBackend):
    def __init__(self, secret_key: str) -> None:
        # super().__init__() не вызываем: он строит SessionMiddleware без https_only/max_age/same_site.
        self.middlewares = [
            Middleware(
                SessionMiddleware,
                secret_key=secret_key,
                https_only=settings.is_production,
                max_age=ADMIN_SESSION_MAX_AGE_SECONDS,
                same_site="strict",
            ),
        ]

    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = (form.get("username") or "").strip()
        password = form.get("password") or ""
        if not email or not password:
            return False

        async with async_session_maker() as session:
            user_db = SQLAlchemyUserDatabase(session, User)
            manager = UserManager(user_db)
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
