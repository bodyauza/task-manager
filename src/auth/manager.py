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

# bcrypt (rounds=14) здесь используется ТОЛЬКО для хеша 6-значного кода подтверждения
# регистрации (registration_endpoints.py: password_helper_bc) — НЕ для паролей
# пользователей. Пароли хеширует стандартный PasswordHelper() из fastapi-users
# (argon2id; verify_and_update при входе понимает и bcrypt-хеши): BaseUserManager.
# __init__ создаёт его сам, см. UserManager ниже.
#
# rounds=14: число итераций bcrypt. При 14 раундах хеширование занимает ~0.5 с —
# достаточно для защиты кода от перебора, приемлемо для пользователя.
# 12 — минимум для production; 16 — задержка ~2 с без существенного прироста стойкости.
password_hash = PasswordHash((
    BcryptHasher(rounds=14),
))

password_helper_bc = PasswordHelper(password_hash)


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    # password_helper намеренно не переопределяется: BaseUserManager.__init__ создаёт
    # PasswordHelper() (argon2id) как атрибут ЭКЗЕМПЛЯРА, и любой одноимённый
    # класс-атрибут им перекрывался бы — раньше здесь стояло
    # `password_helper = password_helper_bc` и создавало ложное впечатление, что
    # пароли хешируются bcrypt'ом (реально — argon2id). Админ-форма
    # (admin/user_admin.py) берёт тот же помощник из экземпляра менеджера.

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        # Только аудит.
        logger.info("User %d registered (email=%s)", user.id, user.email)

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
        # asyncio.to_thread: хеширование пароля (argon2id, десятки-сотни миллисекунд) —
        # синхронный CPU-bound вызов. fastapi-users вызывает password_helper.hash()
        # без await (не оборачивает сама), поэтому оставленный «как есть» синхронный вызов
        # блокировал бы единственный event loop процесса при каждой регистрации,
        # замораживая вообще все остальные запросы приложения в этот момент — тот же приём,
        # что уже применён для magic.from_buffer в src/utils/file_utils.py. Безопасно
        # оборачивать именно здесь: create()/authenticate() — единственные во всём проекте
        # места, где password_helper реально вызывается (BaseUserManager.forgot_password/
        # reset_password/oauth_callback/_update недостижимы — их роутеры не подключены
        # в src/main.py, а routers/users.py::update_user пароль не трогает).
        user_dict["hashed_password"] = await asyncio.to_thread(self.password_helper.hash, password)
        # Роли — many-to-many (user_role): нельзя положить role_id=1 в user_dict, как
        # раньше — нужен реальный объект Role, присвоенный relationship-полю "roles".
        # self.user_db.session — та же AsyncSession, что use_db.create() использует ниже
        # (внедрена через тот же Depends(get_async_session), что и весь остальной код
        # запроса) — объект Role, полученный из неё, session-bound и корректно
        # ассоциируется при последующем session.add()/commit() внутри create().
        # По имени "user", а не по id=1: единственный источник истины о том, что такое
        # "роль по умолчанию" — имя, как и everywhere else после перехода на require_role().
        default_role = (await self.user_db.session.execute(
            select(Role).where(Role.name == "user")
        )).scalar_one_or_none()
        if default_role is None:
            raise RuntimeError(
                "Роль 'user' не найдена — create_initial_roles() должна была создать "
                "её при старте приложения (см. src/main.py)."
            )
        user_dict["roles"] = [default_role]
        # username в Task Manager = часть email до '@'.
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
        """
        Аутентификация пользователя с защитой от timing-атак и автоматическим
        обновлением устаревших хешей паролей.
        """
        # Сигнатура сужена до OAuth2PasswordRequestForm — как у родителя
        # (BaseUserManager.authenticate), без нарушения LSP: override не может
        # принимать МЕНЬШЕ типов, чем родитель формально гарантирует вызывающему
        # коду, а не больше. Union[Dict[str, str], OAuth2PasswordRequestForm]
        # был кодом на неиспользуемый сценарий — единственный вызов authenticate()
        # во всём проекте (auth/endpoints.py::login) всегда передаёт
        # OAuth2PasswordRequestForm, как и встроенный логин-роутер fastapi-users.
        email = credentials.username
        password = credentials.password

        try:
            user = await self.get_by_email(email)
        except exceptions.UserNotExists:
            # Хешируем пароль даже при отсутствии пользователя: время ответа
            # сопоставимо с verify_and_update(), иначе по разнице в задержке
            # атакующий может определить, зарегистрирован ли данный email.
            # asyncio.to_thread — см. пояснение в create() выше.
            await asyncio.to_thread(self.password_helper.hash, password)
            return None

        # asyncio.to_thread — см. пояснение в create() выше: verify_and_update()
        # внутри тоже считает хеш (argon2id/bcrypt — CPU-bound), без выноса в поток
        # блокирует event loop на каждый login.
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
    # Генератор-dependency: FastAPI вызывает его только для тех запросов,
    # route handler которых объявляет Depends(get_user_manager) в параметрах.
    # Запросы к маршрутам без этой dependency функцию не затрагивают.
    # yield (а не return) оставляет точку для cleanup-кода после отправки ответа.
    # user_db — SQLAlchemyUserDatabase, внедрённый через Depends(get_user_db);
    # он уже содержит открытую сессию, привязанную к текущему запросу.
    # Новый экземпляр UserManager на каждый запрос гарантирует изоляцию состояния:
    # нет разделяемых атрибутов между параллельными обработчиками.
    yield UserManager(user_db)
