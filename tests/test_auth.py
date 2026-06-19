import pytest
from httpx import AsyncClient

VALID_EMAIL = "user@example.com"
VALID_PASSWORD = "Password1"


async def _register(client: AsyncClient, email=VALID_EMAIL, password=VALID_PASSWORD):
    return await client.post("/auth/register", json={
        "email": email,
        "password": password,
        "username": email.split("@")[0],
    })


async def _login(client: AsyncClient, email=VALID_EMAIL, password=VALID_PASSWORD):
    return await client.post("/auth/login", data={
        "username": email,
        "password": password,
    })


# ── Registration ────────────────────────────────────────────────────────────

async def test_register_success(client: AsyncClient):
    r = await _register(client)
    assert r.status_code == 201
    assert r.json()["email"] == VALID_EMAIL


async def test_register_duplicate_email(client: AsyncClient):
    await _register(client)
    r = await _register(client)
    assert r.status_code == 400


async def test_register_invalid_email(client: AsyncClient):
    r = await _register(client, email="not-an-email")
    assert r.status_code == 422


async def test_register_weak_password(client: AsyncClient):
    r = await _register(client, password="weak")
    assert r.status_code == 422


# ── Login ────────────────────────────────────────────────────────────────────

async def test_login_success(client: AsyncClient):
    await _register(client)
    r = await _login(client)
    assert r.status_code == 200
    assert "access_token" in r.cookies


async def test_login_wrong_password(client: AsyncClient):
    await _register(client)
    r = await _login(client, password="WrongPass1")
    assert r.status_code == 400


async def test_login_nonexistent_user(client: AsyncClient):
    r = await _login(client, email="nobody@example.com")
    assert r.status_code == 400


# ── Logout ───────────────────────────────────────────────────────────────────

async def test_logout(client: AsyncClient):
    await _register(client)
    await _login(client)
    r = await client.post("/auth/logout")
    assert r.status_code == 200


async def test_logout_unauthenticated(client: AsyncClient):
    r = await client.post("/auth/logout")
    assert r.status_code == 401


# ── Refresh token ────────────────────────────────────────────────────────────

async def test_refresh_token_success(client: AsyncClient):
    await _register(client)
    await _login(client)
    r = await client.post("/auth/access-token")
    assert r.status_code == 200
    assert "access_token" in r.cookies


async def test_refresh_token_missing(client: AsyncClient):
    r = await client.post("/auth/access-token")
    assert r.status_code == 401
