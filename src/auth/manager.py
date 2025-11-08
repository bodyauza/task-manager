from typing import Optional, Dict, Union

from fastapi import Depends, Request, Response, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import (BaseUserManager, IntegerIDMixin, exceptions, models,
                           schemas)
from fastapi_users.password import PasswordHelper
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from src.config import settings
from .models import User
from .user_repository import get_user_db

password_hash = PasswordHash((
    BcryptHasher(rounds=14),
))

password_helper_bc = PasswordHelper(password_hash)


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    password_helper = password_helper_bc

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        print(f"User {user.id} has registered.")

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

        # Создаем пользователя в базе данных
        created_user = await self.user_db.create(user_dict)

        await self.on_after_register(created_user, request)

        return created_user

    async def on_after_login(
            self,
            user: User,
            request: Optional[Request] = None,
            response: Optional[Response] = None,
    ):
        print(f"User {user.id} logged in.")

    async def on_after_logout(
            self,
            user: User,
            request: Optional[Request] = None,
            response: Optional[Response] = None,
    ):
        print(f"User {user.id} logged out.")

    async def authenticate(
            self,
            credentials: Union[Dict[str, str], OAuth2PasswordRequestForm]
    ) -> models.UP:  # Возвращает модель пользователя
        """
        Аутентификация пользователя с защитой от timing-атак и автоматическим
        обновлением устаревших хешей паролей.

        Union[...] означает, что метод принимает либо словарь {'email':..., 'password':...},
        либо стандартную форму OAuth2PasswordRequestForm.
        """
        email = credentials.get("email") if isinstance(credentials, dict) else credentials.username
        password = credentials.get("password") if isinstance(credentials, dict) else credentials.password

        try:
            user = await self.get_by_email(email)
        except exceptions.UserNotExists:
            # Защита от timing-атак: хешируем пароль даже если пользователь не существует
            self.password_helper.hash(password)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )

        # Проверка пароля и получение нового хеша (если алгоритм устарел)
        verified, updated_password_hash = self.password_helper.verify_and_update(
            password, user.hashed_password
        )
        if not verified:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )

        # Если алгоритм хеширования устарел, обновляем хеш в БД
        if updated_password_hash is not None:
            await self.user_db.update(user, {"hashed_password": updated_password_hash})

        return user

# Асинхронная функция для получения экземпляра UserManager с зависимостью от базы данных пользователей
async def get_user_manager(user_db=Depends(get_user_db)):
    yield UserManager(user_db)
