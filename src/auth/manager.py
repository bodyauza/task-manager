import asyncio
import logging
from typing import Optional

from fastapi import Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import (BaseUserManager, IntegerIDMixin, exceptions, models,
                           schemas)
from fastapi_users.password import PasswordHelper
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from sqlalchemy import select

from .user_models import Role, User
from .user_repository import get_user_db

logger = logging.getLogger(__name__)

# bcrypt (rounds=6) используется только для хеша 6-значного кода подтверждения (registration_endpoints.py),
# а не для паролей: их хеширует стандартный PasswordHelper fastapi-users (argon2id, bcrypt-хеши тоже понимает).
#
# rounds=6 достаточно: онлайн-перебор ограничен тремя попытками и IP-лимитом, а офлайн-перебор 10^6 кодов
# дольше TTL кода (15 минут).
password_hash = PasswordHash((
    BcryptHasher(rounds=6),
))

password_helper_bc = PasswordHelper(password_hash)


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    # password_helper не переопределяется: BaseUserManager.__init__ создаёт его как атрибут экземпляра (argon2id),
    # а одноимённый класс-атрибут вводил бы в заблуждение. Админ-форма берёт помощник из экземпляра менеджера.

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        # Только аудит: email в лог не пишем (ПДн), запись находится по user.id.
        logger.info("User %d registered", user.id)

    async def create(
            self,
            user_create: schemas.UC,
            safe: bool = False,
            request: Optional[Request] = None,
    ) -> models.UP:
        await self.validate_password(user_create.password, user_create)

        existing_user = await self.user_db.get_by_email(user_create.email)
        if existing_user is not None:
            raise exceptions.UserAlreadyExists()

        user_dict = (
            user_create.create_update_dict()
            if safe
            else user_create.create_update_dict_superuser()
        )
        password = user_dict.pop("password")
        # Хеширование пароля (argon2id) синхронное и CPU-bound — выносим в asyncio.to_thread, чтобы не блокировать
        # event loop. password_helper реально вызывается только в create() и authenticate().
        user_dict["hashed_password"] = await asyncio.to_thread(self.password_helper.hash, password)
        # Роли many-to-many: нужен объект Role, присвоенный relationship "roles" (role_id в user_dict не положить).
        # Сессия та же, что у user_db.create(). Ищем по имени "user", а не по id.
        default_role = (await self.user_db.session.execute(
            select(Role).where(Role.name == "user")
        )).scalar_one_or_none()
        if default_role is None:
            raise RuntimeError(
                "Роль 'user' не найдена — create_initial_roles() должна была создать "
                "её при старте приложения (см. src/main.py)."
            )
        user_dict["roles"] = [default_role]
        user_dict["username"] = user_create.email.split("@")[0]

        created_user = await self.user_db.create(user_dict)
        await self.on_after_register(created_user, request)
        return created_user

    async def on_after_login(
            self,
            user: User,
            request: Optional[Request] = None,
            response: Optional[Response] = None,
    ):
        logger.info("User %d logged in.", user.id)

    async def on_after_logout(
            self,
            user: User,
            request: Optional[Request] = None,
            response: Optional[Response] = None,
    ):
        logger.info("User %d logged out.", user.id)

    async def authenticate(
            self,
            credentials: OAuth2PasswordRequestForm,
    ) -> Optional[models.UP]:
        """Аутентификация с защитой от timing-атак и обновлением устаревших хешей."""
        # Сигнатура сужена до OAuth2PasswordRequestForm: единственный вызов (auth/endpoints.py::login) всегда передаёт её.
        email = credentials.username
        password = credentials.password

        try:
            user = await self.get_by_email(email)
        except exceptions.UserNotExists:
            # Хешируем пароль и при отсутствии пользователя: по разнице во времени ответа иначе можно определить, зарегистрирован ли email.
            await asyncio.to_thread(self.password_helper.hash, password)
            return None

        # verify_and_update тоже считает хеш (CPU-bound) — выносим в поток, как в create().
        verified, updated_password_hash = await asyncio.to_thread(
            self.password_helper.verify_and_update, password, user.hashed_password
        )
        if not verified:
            return None

        if updated_password_hash is not None:
            await self.user_db.update(user, {"hashed_password": updated_password_hash})

        return user


async def get_user_manager(
    user_db=Depends(get_user_db),
):
    # Генератор-dependency: новый UserManager на каждый запрос, без разделяемого состояния.
    yield UserManager(user_db)
