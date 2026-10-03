"""UserAdmin — создание, просмотр и точечная правка пользователей (`person`).

**Создание** идёт через UserManager.create() (insert_model): хеш пароля, роль `user` по умолчанию, username из email.
Выбранные в форме роли заменяют роль по умолчанию; пользователь сразу is_verified=True.

**Пароль** — виртуальное поле (в модели колонки нет): при создании обязателен, при правке пустое значение не меняет хеш.
В sqladmin 0.20.1 нет form_extra_fields, поэтому поле добавляется в scaffold_form и должно быть в form_create_rules
и form_edit_rules.

**Не редактируется:** email, firstname/lastname/patronymic после создания, hashed_password/is_superuser/registered_at,
коллекция tasks. can_delete = False: каскад снёс бы задачи в обход outbox и очистки файлов — только DELETE /users/{id}.

**Ограничение:** смена пароля не отзывает выданные токены — при компрометации дополнительно снимите is_active.
"""

import asyncio
import logging
from typing import Type

from fastapi_users import exceptions
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqladmin import ModelView
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.requests import Request
from wtforms import Form, PasswordField

from src.admin.formatters import TYPE_FORMATTERS
from src.auth.manager import UserManager
from src.auth.user_models import Role, User
from src.auth.user_schemas import (
    PASSWORD_ERROR,
    UserCreate,
    is_valid_email_format,
    is_valid_password_format,
)
from src.database import async_session_maker

logger = logging.getLogger(__name__)

# Те же границы, что у UserCreate.password: 5..72 символа.
_PASSWORD_MAX_LEN = 72
_PASSWORD_TOO_LONG = f"Пароль не должен быть длиннее {_PASSWORD_MAX_LEN} символов."

# Тот же PasswordHelper, что у UserManager (из экземпляра менеджера) — хеши при правке и регистрации идентичны.
_password_helper = UserManager(None).password_helper


def _validate_password(password: str) -> None:
    """Те же правила, что у регистрации. ValueError с чистым текстом: sqladmin выводит str(e) на форму, а текст
    pydantic.ValidationError включил бы сам пароль.
    """
    if len(password) > _PASSWORD_MAX_LEN:
        raise ValueError(_PASSWORD_TOO_LONG)
    if not is_valid_password_format(password):
        raise ValueError(PASSWORD_ERROR)


def _roles_formatter(model, _attr) -> str:
    """User.roles — m2m: имена ролей через запятую, "—" для пустого."""
    return ", ".join(role.name for role in model.roles) or "—"


class UserAdmin(ModelView, model=User):
    name = "Пользователь"
    name_plural = "Пользователи"
    icon = "fa-solid fa-user"

    can_create = True
    can_delete = False

    column_list = [
        User.id, User.email, User.username, User.firstname, User.lastname,
        User.roles, User.is_active, User.is_verified, User.registered_at,
    ]
    column_searchable_list = [User.email, User.username, User.firstname, User.lastname]
    column_sortable_list = [User.id, User.email, User.registered_at]
    column_labels = {
        User.email: "Email", User.firstname: "Имя", User.lastname: "Фамилия",
        User.patronymic: "Отчество", User.roles: "Роли", User.is_active: "Активен",
    }

    column_type_formatters = TYPE_FORMATTERS
    column_formatters = {User.roles: _roles_formatter}
    column_formatters_detail = column_formatters

    # hashed_password не показываем: сырая правка обходит password_helper, а хеш небезопасно просматривать.
    column_details_exclude_list = [User.hashed_password, User.tasks]

    # form_columns — allow-list всех полей любой из форм; что показать на создании/правке, решают form_create_rules/form_edit_rules.
    # "password" — виртуальное поле, только в правилах.
    form_columns = [
        User.email, User.firstname, User.lastname, User.patronymic,
        User.roles, User.is_active,
    ]
    form_create_rules = ["email", "firstname", "lastname", "patronymic", "password", "roles"]
    form_edit_rules = ["roles", "is_active", "password"]
    form_ajax_refs = {
        "roles": {"fields": ("name",), "order_by": [Role.id]},
    }

    async def scaffold_form(self, rules=None) -> Type[Form]:
        form_class = await super().scaffold_form(rules)
        # Подпись одинакова для создания и правки; обязательность проверяется в insert_model/on_model_change, на уровне wtforms
        # поле необязательное — иначе пустое значение при правке не прошло бы валидацию.
        form_class.password = PasswordField(
            "Пароль",
            description=(
                "При создании обязателен. При правке оставьте пустым, чтобы не менять пароль. "
                "Минимум 5 символов, заглавная буква, цифра и спецсимвол."
            ),
        )
        return form_class

    async def insert_model(self, request: Request, data: dict) -> User:
        """Создание через UserManager.create(), а не прямой записью в БД."""
        email = (data.get("email") or "").strip()
        password = data.get("password") or ""
        firstname = (data.get("firstname") or "").strip()
        lastname = (data.get("lastname") or "").strip()
        patronymic = (data.get("patronymic") or "").strip() or None

        if not is_valid_email_format(email):
            raise ValueError("Некорректный email.")
        if not firstname or not lastname:
            raise ValueError("Имя и фамилия обязательны.")
        if not password:
            raise ValueError("При создании пользователя пароль обязателен.")
        _validate_password(password)

        role_ids = [int(r) for r in (data.get("roles") or [])]

        user_create = UserCreate(
            email=email, password=password, firstname=firstname, lastname=lastname,
            patronymic=patronymic, is_active=True, is_verified=True,
        )
        async with async_session_maker() as session:
            manager = UserManager(SQLAlchemyUserDatabase(session, User))
            try:
                user = await manager.create(user_create, safe=False, request=request)
            except exceptions.UserAlreadyExists:
                raise ValueError("Пользователь с таким email уже существует.") from None

            if role_ids:
                # Выбранные роли заменяют роль по умолчанию; перечитываем с selectinload, иначе MissingGreenlet.
                user = (await session.execute(
                    select(User).options(selectinload(User.roles)).where(User.id == user.id)
                )).scalar_one()
                user.roles = list((
                    await session.execute(select(Role).where(Role.id.in_(role_ids)))
                ).scalars().all())
                await session.commit()
            return user

    async def on_model_change(self, data: dict, model: User, is_created: bool, request: Request) -> None:
        """Правка (создание сюда не попадает).

        password — виртуальное поле: sqladmin при пустом значении обращается к column.nullable у None, поэтому ключ всегда вырезается.
        Непустой пароль хешируется тем же PasswordHelper в потоке.
        """
        password = data.pop("password", None) or ""
        if not password:
            return
        _validate_password(password)
        data["hashed_password"] = await asyncio.to_thread(_password_helper.hash, password)
