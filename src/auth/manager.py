import logging
from typing import Optional, Dict, Union

from fastapi import Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import (BaseUserManager, IntegerIDMixin, exceptions, models,
                           schemas)
from fastapi_users.password import PasswordHelper
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from src.crm.client import CRMUnavailableError
from src.crm.user_service import UserRegistrar, get_user_registrar

from .models import User
from .user_repository import get_user_db

logger = logging.getLogger(__name__)

# rounds=14: число итераций bcrypt. При 14 раундах хеширование одного пароля
# занимает ~0.5 с — достаточно для защиты от brute-force, приемлемо для пользователя.
# 12 — минимум для production; 16 — задержка ~2 с без существенного прироста стойкости.
password_hash = PasswordHash((
    BcryptHasher(rounds=14),
))

password_helper_bc = PasswordHelper(password_hash)


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    password_helper = password_helper_bc

    def __init__(self, user_db, crm_registrar: UserRegistrar):
        # crm_registrar внедряется через get_user_manager (Depends(get_user_registrar)) —
        # UserManager зависит от протокола UserRegistrar, а не от конкретного CRMClient (DIP).
        super().__init__(user_db)
        self.crm_registrar = crm_registrar

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        # CRM-регистрация выполнена в create() до этого вызова — здесь только аудит.
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
        user_dict["hashed_password"] = self.password_helper.hash(password)
        user_dict["role_id"] = 1
        # username в Task Manager = часть email до '@'.
        # Та же логика применяется для username-поля при регистрации в CRM.
        user_dict["username"] = user_create.email.split("@")[0]

        # Порядок: сначала CRM, затем PostgreSQL.
        # Если CRM вернёт ошибку — person не создаётся, транзакция чистая.
        # Обратный порядок создал бы риск: пользователь есть в БД, но отсутствует в CRM,
        # что заблокирует ему вход (login-эндпоинт проверяет наличие в CRM).
        # Если PostgreSQL упадёт после успешного CRM — в CRM останется «висячая» запись;
        # сценарий маловероятен и требует ручной очистки через CRM-интерфейс.
        from src.crm.config import crm_settings

        try:
            await self.crm_registrar.register_user(
                group_id=crm_settings.USER_GROUP_ID,
                firstname=user_dict.get("firstname", ""),
                lastname=user_dict.get("lastname", ""),
                username=user_create.email.split("@")[0],
                email=user_create.email,
                password=password,
                notify=False,
            )
            logger.info("CRM: user %s registered successfully", user_create.email)
        except Exception as exc:
            logger.error("CRM registration failed for %s: %s", user_create.email, exc)
            # UserManager — доменный слой; он не решает, каким HTTP-кодом ответить клиенту
            # (fastapi-users перехватывает только UserAlreadyExists/InvalidPasswordException,
            # раньше здесь бросался голый HTTPException в обход их контракта — LSP-нарушение
            # относительно BaseUserManager.create()). Перевод в HTTP-ответ — на границе,
            # см. except CRMUnavailableError в registration_endpoints.py.
            raise CRMUnavailableError(str(exc)) from exc

        # INSERT выполняется только после успешной регистрации в CRM
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
            credentials: Union[Dict[str, str], OAuth2PasswordRequestForm]
    ) -> Optional[models.UP]:
        """
        Аутентификация пользователя с защитой от timing-атак и автоматическим
        обновлением устаревших хешей паролей.
        """
        email = credentials.get("email") if isinstance(credentials, dict) else credentials.username
        password = credentials.get("password") if isinstance(credentials, dict) else credentials.password

        try:
            user = await self.get_by_email(email)
        except exceptions.UserNotExists:
            # Хешируем пароль даже при отсутствии пользователя: время ответа
            # сопоставимо с verify_and_update(), иначе по разнице в задержке
            # атакующий может определить, зарегистрирован ли данный email.
            self.password_helper.hash(password)
            return None

        verified, updated_password_hash = self.password_helper.verify_and_update(
            password, user.hashed_password
        )
        if not verified:
            return None

        if updated_password_hash is not None:
            await self.user_db.update(user, {"hashed_password": updated_password_hash})

        return user


async def get_user_manager(
    user_db=Depends(get_user_db),
    crm_registrar: UserRegistrar = Depends(get_user_registrar),
):
    # Генератор-dependency: FastAPI вызывает его только для тех запросов,
    # route handler которых объявляет Depends(get_user_manager) в параметрах.
    # Запросы к маршрутам без этой dependency функцию не затрагивают.
    # yield (а не return) оставляет точку для cleanup-кода после отправки ответа.
    # user_db — SQLAlchemyUserDatabase, внедрённый через Depends(get_user_db);
    # он уже содержит открытую сессию, привязанную к текущему запросу.
    # crm_registrar — UserRegistrar, внедрённый через Depends(get_user_registrar);
    # тесты подменяют его через app.dependency_overrides, не патчингом импорта.
    # Новый экземпляр UserManager на каждый запрос гарантирует изоляцию состояния:
    # нет разделяемых атрибутов между параллельными обработчиками.
    yield UserManager(user_db, crm_registrar)
