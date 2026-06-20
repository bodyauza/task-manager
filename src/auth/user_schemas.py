import re
from typing import Optional

from pydantic import ConfigDict, field_validator
from fastapi_users import schemas

# Модели Pydantic для автоматической валидации получаемых данных (DTO).

# Добавлена проверка email и password общепринятыми регулярными
# выражениями (помимо стандартной валидации EmailStr/email-validator у
# schemas.BaseUserCreate, которая проверяет только формальную структуру адреса,
# но не сложность пароля - её там вообще нет).

# EMAIL_REGEX - классический паттерн "локальная часть @ домен.зона":
# буквы/цифры/._+- до "@", затем доменные метки через точку, последняя
# зона из 2+ букв/цифр.
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

# PASSWORD_REGEX - минимум 8 символов, как минимум одна строчная буква,
# одна заглавная буква и одна цифра. Это общепринятый минимальный набор
# требований к "сложному" паролю (OWASP baseline).
PASSWORD_REGEX = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$")


# Проверки вынесены в самостоятельные функции (а не оставлены
# только как pydantic field_validator внутри UserCreate), чтобы их можно было
# повторно использовать при ЛОГИНЕ (POST /auth/login). Эндпоинт логина
# принимает OAuth2PasswordRequestForm, а не UserCreate, поэтому
# field_validator-ы UserCreate там не сработали бы автоматически - раньше
# валидация формата email/пароля проверялась только при регистрации, и можно
# было залогиниться (или хотя бы попытаться) с заведомо некорректным по
# формату email/паролем.
def is_valid_email_format(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email))


def is_valid_password_format(password: str) -> bool:
    return bool(PASSWORD_REGEX.match(password))


class UserRead(schemas.BaseUser[int]):
    id: int
    email: str
    username: str
    role_id: int
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = False

    model_config = ConfigDict(from_attributes=True)


class UserCreate(schemas.BaseUserCreate):
    username: str
    email: str
    password: str
    is_active: Optional[bool] = True
    is_superuser: Optional[bool] = False
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
            raise ValueError(
                "Password must be at least 8 characters long and contain "
                "at least one lowercase letter, one uppercase letter, and one digit"
            )
        return value
