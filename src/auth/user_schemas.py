import re
from typing import Optional

from pydantic import ConfigDict, Field, field_validator
from fastapi_users import schemas

# Базовая проверка синтаксиса email; полная верификация — через письмо с кодом.
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

# Lookahead (?=...) проверяет наличие каждого класса символов независимо от позиции.
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
    patronymic: Optional[str] = None
    # Источник — User.role_ids (читает user.roles).
    role_ids: list[int]
    is_active: bool = True
    # exclude=True: поле скрыто из JSON; fastapi-users требует is_superuser в модели.
    is_superuser: bool = Field(default=False, exclude=True)
    is_verified: bool = False

    model_config = ConfigDict(from_attributes=True)


class UserCreate(schemas.BaseUserCreate):
    # username не передаётся клиентом: вычисляется в UserManager.create().
    username: Optional[str] = None
    firstname: str
    lastname: str
    patronymic: Optional[str] = None
    email: str
    # max_length=72 — общий лимит для регистрации, API и админ-формы (продублирован в HTML/JS): защита от чрезмерно длинного ввода
    # и от bcrypt-хешей, которые учитывают только первые 72 байта.
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
