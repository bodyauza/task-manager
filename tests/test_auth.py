import pytest
from httpx import AsyncClient

VALID_PASSWORD = "Password1!"


async def _login(client: AsyncClient, email: str, password: str = VALID_PASSWORD):
    return await client.post("/auth/login", data={"username": email, "password": password})


# ── Login ────────────────────────────────────────────────────────────────────

async def test_login_success(client: AsyncClient, register_and_login: dict):
    r = await _login(client, **register_and_login)
    assert r.status_code == 200
    assert "access_token" in r.cookies


async def test_login_wrong_password(client: AsyncClient, register_and_login: dict):
    r = await _login(client, email=register_and_login["email"], password="WrongPass1!")
    assert r.status_code == 400


async def test_login_nonexistent_user(client: AsyncClient):
    r = await _login(client, email="nobody@example.com")
    assert r.status_code == 400


# ── Logout ───────────────────────────────────────────────────────────────────

async def test_logout(client: AsyncClient, register_and_login: dict):
    await _login(client, **register_and_login)
    r = await client.post("/auth/logout")
    assert r.status_code == 200


async def test_logout_unauthenticated(client: AsyncClient):
    r = await client.post("/auth/logout")
    assert r.status_code == 401


# ── Refresh token ────────────────────────────────────────────────────────────

async def test_refresh_token_success(client: AsyncClient, register_and_login: dict):
    await _login(client, **register_and_login)
    r = await client.post("/auth/access-token")
    assert r.status_code == 200
    assert "access_token" in r.cookies


async def test_refresh_token_missing(client: AsyncClient):
    r = await client.post("/auth/access-token")
    assert r.status_code == 401
