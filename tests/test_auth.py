import time

import jwt
from httpx import AsyncClient

from src.config import settings

VALID_PASSWORD = "Password1!"


async def _login(client: AsyncClient, email: str, password: str = VALID_PASSWORD):
    return await client.post("/auth/login", data={"username": email, "password": password})


def _expired_access_token() -> str:
    # Токен с корректной подписью/claims (aud — как ожидает fastapi-users JWTStrategy),
    # но с exp в прошлом — имитирует пользователя, бездействовавшего дольше 30 минут
    # (settings.access_exp) и затем нажавшего «Выйти».
    return jwt.encode(
        {"sub": "1", "aud": ["fastapi-users:auth"], "exp": int(time.time()) - 60},
        settings.access_secret,
        algorithm=settings.algorithm,
    )


def _assert_clears_both_cookies(set_cookie_headers: list) -> None:
    assert any("access_token=" in h and "Max-Age=0" in h for h in set_cookie_headers)
    assert any("refresh_token=" in h and "Max-Age=0" in h for h in set_cookie_headers)


# ── Login ────────────────────────────────────────────────────────────────────

async def test_login_success(client: AsyncClient, registered_user: dict):
    r = await _login(client, **registered_user)
    assert r.status_code == 200
    assert "access_token" in r.cookies


async def test_login_wrong_password(client: AsyncClient, registered_user: dict):
    r = await _login(client, email=registered_user["email"], password="WrongPass1!")
    assert r.status_code == 400


async def test_login_nonexistent_user(client: AsyncClient):
    r = await _login(client, email="nobody@example.com")
    assert r.status_code == 400


# ── Logout ───────────────────────────────────────────────────────────────────

async def test_logout(client: AsyncClient, registered_user: dict):
    await _login(client, **registered_user)
    r = await client.post("/auth/logout")
    assert r.status_code == 200


async def test_logout_without_session_succeeds(client: AsyncClient):
    # Логаут идемпотентен: без единой cookie — это тоже "уже не залогинен", а не
    # ошибка. До фикса (Depends(current_user) в /auth/logout) здесь ожидался 401 —
    # это и было симптомом бага: логаут не должен требовать валидную сессию для
    # собственного выполнения.
    r = await client.post("/auth/logout")
    assert r.status_code == 200


async def test_logout_with_expired_access_token_still_clears_cookies(client: AsyncClient):
    # Регрессия: до фикса Depends(current_user) в /auth/logout возвращал 401 на
    # просроченный access_token ДО тела функции — куки (в частности, refresh_token,
    # живущий 7 дней) не очищались вовсе. Пользователь, бездействовавший дольше
    # settings.access_exp (30 минут) и нажавший «Выйти», не выходил из системы
    # по-настоящему: refresh_token оставался рабочим. Проверено эмпирически на
    # реальном приложении перед фиксом (302 без единого Set-Cookie в ответе).
    client.cookies.set("access_token", _expired_access_token())
    client.cookies.set("refresh_token", "irrelevant-for-this-check")
    r = await client.post("/auth/logout")
    assert r.status_code == 200
    _assert_clears_both_cookies(r.headers.get_list("set-cookie"))


async def test_do_logout_with_expired_access_token_still_clears_cookies(client: AsyncClient):
    # Тот же регрессионный сценарий, что и test_logout_with_expired_access_token_*,
    # но для форм-based /auth/do-logout (см. do_logout() в src/auth/endpoints.py).
    client.cookies.set("access_token", _expired_access_token())
    client.cookies.set("refresh_token", "irrelevant-for-this-check")
    r = await client.post("/auth/do-logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    _assert_clears_both_cookies(r.headers.get_list("set-cookie"))


async def test_do_logout_without_session_succeeds(client: AsyncClient):
    r = await client.post("/auth/do-logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


# ── Refresh token ────────────────────────────────────────────────────────────

async def test_refresh_token_success(client: AsyncClient, registered_user: dict):
    await _login(client, **registered_user)
    r = await client.post("/auth/access-token")
    assert r.status_code == 200
    assert "access_token" in r.cookies


async def test_refresh_token_missing(client: AsyncClient):
    r = await client.post("/auth/access-token")
    assert r.status_code == 401
