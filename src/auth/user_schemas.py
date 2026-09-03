import re
from typing import Optional

from pydantic import ConfigDict, Field, field_validator
from fastapi_users import schemas

# Базовая проверка синтаксиса: допускает user@domain.tld.
# Полноценная верификация email — только через отправку письма с кодом подтверждения.
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

# Lookahead (?=...) проверяет наличие каждого класса символов независимо от позиции,
# поэтому порядок символов в пароле не важен.
PASSWORD_REGEX = re.compile(
    r"^(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()\-_=+\[\]{};:'\",.<>/?]).{5,}$"
)

PASSWORD_ERROR = (
    "Пароль должен содержать: цифры, символы верхнего регистра "
    "и специальные символы. Минимальная длина пароля — 5 символов."
)


def is_valid_email_format(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email))


def is_valid_password_format(password: str) -> bool:
    return bool(PASSWORD_REGEX.match(password))


class UserRead(schemas.BaseUser[int]):
    id: int
    email: str
    username: str
    firstname: str
    lastname: str
    # None если пользователь не указал отчество при регистрации или оно не хранится в БД.
    patronymic: Optional[str] = None
    # Источник — User.role_ids (property в auth/user_models.py, читает user.roles).
    # from_attributes=True вызывает getattr(user, "role_ids") как обычный атрибут —
    # роли пользователя many-to-many (user_role), поэтому список, а не одно значение.
    role_ids: list[int]
    is_active: bool = True
    # exclude=True: поле скрыто из JSON-ответов API, но сохраняется в БД.
    # fastapi-users требует is_superuser в модели; права в проекте задаются через roles/require_role().
    is_superuser: bool = Field(default=False, exclude=True)
    is_verified: bool = False

    model_config = ConfigDict(from_attributes=True)


class UserCreate(schemas.BaseUserCreate):
    # username не передаётся клиентом: вычисляется в UserManager.create()
    # как email.split("@")[0] и записывается в БД.
    username: Optional[str] = None
    firstname: str
    lastname: str
    # None если отчество не передано — Pydantic не подставляет пустую строку.
    patronymic: Optional[str] = None
    email: str
    # max_length=72: bcrypt учитывает только первые 72 байта пароля и молча
    # обрезает остальное (проверено эмпирически на bcrypt==4.1.2 — два разных
    # пароля с общим 72-байтовым префиксом проходят проверку по хешу друг
    # друга). Без верхней границы пользователь мог бы рассчитывать на энтропию
    # длинного пароля, которой bcrypt на самом деле не учитывает.
    password: str = Field(..., min_length=5, max_length=72)
    is_active: Optional[bool] = True
    is_verified: Optional[bool] = False

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, value: str) -> str:
        if not is_valid_email_format(value):
            raise ValueError("Invalid email format")
        return value

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if not is_valid_password_format(value):
            raise ValueError(PASSWORD_ERROR)
        return value
