"""Тесты формы создания пользователя и правки пароля в sqladmin
(src/admin/user_admin.py::UserAdmin): создание идёт через UserManager.create(),
пароль хешируется тем же PasswordHelper, что и при регистрации, валидация та
же, что у API, пароль не попадает в ответы/логи.
"""

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from fastapi_users.password import PasswordHelper
from src.auth.user_models import User
from src.database import async_session_maker
from tests.conftest import ADMIN_PANEL_EMAIL, register_user

# verify_and_update понимает и argon2, и bcrypt — тест не привязан к алгоритму хеша.
password_helper = PasswordHelper()

ADMIN_EMAIL = ADMIN_PANEL_EMAIL
PASSWORD = "Password1!"
NEW_EMAIL = "new.person@example.com"
NEW_PASSWORD = "Secret1!x"


def _create_form(**overrides) -> dict:
    form = {
        "email": NEW_EMAIL, "firstname": "Иван", "lastname": "Петров",
        "patronymic": "Сергеевич", "password": NEW_PASSWORD,
    }
    form.update(overrides)
    return {k: v for k, v in form.items() if v is not None}


async def _get_user(email: str) -> User | None:
    async with async_session_maker() as session:
        return (await session.execute(
            select(User).options(selectinload(User.roles)).where(User.email == email)
        )).scalar_one_or_none()


async def _post_create(client: AsyncClient, **overrides):
    return await client.post("/admin/user/create", data=_create_form(**overrides), follow_redirects=False)


# ── Формы ───────────────────────────────────────────────────────────────────

async def test_create_form_has_expected_fields(admin_client: AsyncClient):
    r = await admin_client.get("/admin/user/create")
    assert r.status_code == 200
    for name in ("email", "firstname", "lastname", "patronymic", "password"):
        assert f'name="{name}"' in r.text, name
    for hidden in ("hashed_password", "is_superuser", "registered_at", "tasks"):
        assert f'name="{hidden}"' not in r.text, hidden
    assert 'type="password"' in r.text


async def test_edit_form_has_password_roles_and_is_active_but_no_email(admin_client: AsyncClient):
    r = await admin_client.get("/admin/user/edit/1")
    assert r.status_code == 200
    assert 'name="password"' in r.text and 'type="password"' in r.text
    assert 'name="is_active"' in r.text
    for absent in ("email", "firstname", "lastname", "hashed_password"):
        assert f'name="{absent}"' not in r.text, absent


async def test_delete_stays_disabled(admin_client: AsyncClient):
    assert (await admin_client.delete("/admin/user/delete?pks=1")).status_code == 403


# ── Создание ────────────────────────────────────────────────────────────────

async def test_create_user_hashes_password_and_sets_defaults(admin_client: AsyncClient):
    r = await _post_create(admin_client)
    assert r.status_code == 302

    user = await _get_user(NEW_EMAIL)
    assert user is not None
    assert user.username == "new.person"                     # часть email до '@', как при регистрации
    assert (user.firstname, user.lastname, user.patronymic) == ("Иван", "Петров", "Сергеевич")
    assert user.is_active and user.is_verified
    assert [role.name for role in user.roles] == ["user"]    # роль по умолчанию
    assert user.hashed_password != NEW_PASSWORD
    assert password_helper.verify_and_update(NEW_PASSWORD, user.hashed_password)[0]


async def test_created_user_can_log_in(admin_client: AsyncClient):
    assert (await _post_create(admin_client)).status_code == 302
    admin_client.cookies.clear()
    r = await admin_client.post("/auth/login", data={"username": NEW_EMAIL, "password": NEW_PASSWORD})
    assert r.status_code in (200, 204)
    assert "access_token" in r.cookies or "access_token" in admin_client.cookies


async def test_create_user_without_patronymic_stores_null(admin_client: AsyncClient):
    assert (await _post_create(admin_client, patronymic="")).status_code == 302
    assert (await _get_user(NEW_EMAIL)).patronymic is None


async def test_create_user_with_selected_roles_replaces_default(admin_client: AsyncClient):
    r = await admin_client.post(
        "/admin/user/create", data={**_create_form(), "roles": ["2"]}, follow_redirects=False,
    )
    assert r.status_code == 302
    assert [role.name for role in (await _get_user(NEW_EMAIL)).roles] == ["admin"]


@pytest.mark.parametrize("overrides, expected_text", [
    ({"password": None}, "обязателен"),                               # нет пароля
    ({"password": ""}, "обязателен"),
    ({"password": "weakpass"}, "Пароль должен содержать"),            # нет заглавной/цифры/спецсимвола
    ({"password": "Aa1!" + "x" * 69}, "72 символ"),                   # 73 символа
    ({"email": "not-an-email"}, "email"),
    ({"firstname": ""}, ""),                                          # обязательное поле формы
])
async def test_create_user_rejects_invalid_input(
    admin_client: AsyncClient, overrides: dict, expected_text: str,
):
    r = await _post_create(admin_client, **overrides)
    assert r.status_code == 400
    assert expected_text.lower() in r.text.lower()
    assert await _get_user(NEW_EMAIL) is None


async def test_create_user_rejects_duplicate_email(admin_client: AsyncClient):
    r = await _post_create(admin_client, email=ADMIN_EMAIL)
    assert r.status_code == 400
    assert "уже существует" in r.text


async def test_create_error_does_not_leak_password(admin_client: AsyncClient, caplog):
    """Ни в HTML ответа, ни в логи (sqladmin делает logger.exception(e)) пароль
    не попадает — в отличие от текста pydantic.ValidationError (input_value)."""
    secret = "leakcheck"           # заведомо слабый: не пройдёт проверку формата
    with caplog.at_level(logging.DEBUG):
        r = await _post_create(admin_client, password=secret)
    assert r.status_code == 400
    assert secret not in r.text
    assert secret not in caplog.text


async def test_create_requires_admin_login(client: AsyncClient):
    r = await client.post("/admin/user/create", data=_create_form(), follow_redirects=False)
    assert r.status_code in (302, 401, 403)
    assert await _get_user(NEW_EMAIL) is None


# ── Правка пароля ───────────────────────────────────────────────────────────

async def _make_target(admin_client: AsyncClient) -> User:
    assert (await _post_create(admin_client)).status_code == 302
    return await _get_user(NEW_EMAIL)


async def _edit(client: AsyncClient, user_id: int, **fields):
    data = {"is_active": "y", **fields}
    return await client.post(f"/admin/user/edit/{user_id}", data=data, follow_redirects=False)


async def test_edit_without_password_keeps_hash(admin_client: AsyncClient):
    target = await _make_target(admin_client)
    old_hash = target.hashed_password

    r = await _edit(admin_client, target.id, password="")
    assert r.status_code == 302

    assert (await _get_user(NEW_EMAIL)).hashed_password == old_hash


async def test_edit_without_password_field_at_all_keeps_hash(admin_client: AsyncClient):
    target = await _make_target(admin_client)
    old_hash = target.hashed_password

    assert (await _edit(admin_client, target.id)).status_code == 302
    assert (await _get_user(NEW_EMAIL)).hashed_password == old_hash


async def test_edit_with_password_sets_new_hash_and_allows_login(admin_client: AsyncClient):
    target = await _make_target(admin_client)
    old_hash = target.hashed_password
    fresh_password = "Fresh2@pass"

    r = await _edit(admin_client, target.id, password=fresh_password)
    assert r.status_code == 302

    updated = await _get_user(NEW_EMAIL)
    assert updated.hashed_password not in (old_hash, fresh_password)
    assert password_helper.verify_and_update(fresh_password, updated.hashed_password)[0]
    assert not password_helper.verify_and_update(NEW_PASSWORD, updated.hashed_password)[0]

    admin_client.cookies.clear()
    assert (await admin_client.post(
        "/auth/login", data={"username": NEW_EMAIL, "password": fresh_password},
    )).status_code in (200, 204)
    admin_client.cookies.clear()
    assert (await admin_client.post(
        "/auth/login", data={"username": NEW_EMAIL, "password": NEW_PASSWORD},
    )).status_code == 400


@pytest.mark.parametrize("bad_password, expected_text", [
    ("weakpass", "Пароль должен содержать"),
    ("Aa1!" + "x" * 69, "72 символ"),
])
async def test_edit_rejects_invalid_password_and_keeps_hash(
    admin_client: AsyncClient, bad_password: str, expected_text: str,
):
    target = await _make_target(admin_client)
    old_hash = target.hashed_password

    r = await _edit(admin_client, target.id, password=bad_password)
    assert r.status_code == 400
    assert expected_text in r.text
    assert bad_password not in r.text
    assert (await _get_user(NEW_EMAIL)).hashed_password == old_hash


async def test_edit_password_together_with_is_active_and_roles(admin_client: AsyncClient):
    target = await _make_target(admin_client)

    r = await admin_client.post(
        f"/admin/user/edit/{target.id}",
        data={"password": "Fresh2@pass", "roles": ["2"]},   # is_active не передан = снят
        follow_redirects=False,
    )
    assert r.status_code == 302

    updated = await _get_user(NEW_EMAIL)
    assert updated.is_active is False
    assert [role.name for role in updated.roles] == ["admin"]
    assert password_helper.verify_and_update("Fresh2@pass", updated.hashed_password)[0]


async def test_edit_form_never_echoes_password_or_hash(admin_client: AsyncClient):
    target = await _make_target(admin_client)
    r = await admin_client.get(f"/admin/user/edit/{target.id}")
    assert r.status_code == 200
    assert NEW_PASSWORD not in r.text
    assert target.hashed_password not in r.text
