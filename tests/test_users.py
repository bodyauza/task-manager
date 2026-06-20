import pytest
from httpx import AsyncClient

from tests.conftest import promote_to_admin

ADMIN_EMAIL = "admin@example.com"
USER_EMAIL = "user@example.com"
PASSWORD = "Password1"


async def _register_login(client: AsyncClient, email: str, password: str = PASSWORD):
    await client.post("/auth/register", json={
        "email": email,
        "password": password,
        "username": email.split("@")[0],
    })
    await client.post("/auth/login", data={"username": email, "password": password})


# ── list users ───────────────────────────────────────────────────────────────

async def test_list_users_as_admin(client: AsyncClient):
    await _register_login(client, ADMIN_EMAIL)
    await promote_to_admin(ADMIN_EMAIL)
    # re-login to pick up the updated role via fresh token
    await client.post("/auth/logout")
    await client.post("/auth/login", data={"username": ADMIN_EMAIL, "password": PASSWORD})

    r = await client.get("/users/")
    assert r.status_code == 200
    emails = [u["email"] for u in r.json()]
    assert ADMIN_EMAIL in emails


async def test_list_users_as_regular_user_forbidden(client: AsyncClient):
    await _register_login(client, USER_EMAIL)
    r = await client.get("/users/")
    assert r.status_code == 403


async def test_list_users_unauthenticated(client: AsyncClient):
    r = await client.get("/users/")
    assert r.status_code == 401


# ── delete user ──────────────────────────────────────────────────────────────

async def test_delete_user_as_admin(client: AsyncClient):
    await _register_login(client, USER_EMAIL)

    await _register_login(client, ADMIN_EMAIL)
    await promote_to_admin(ADMIN_EMAIL)
    await client.post("/auth/logout")
    await client.post("/auth/login", data={"username": ADMIN_EMAIL, "password": PASSWORD})

    users_r = await client.get("/users/")
    target = next(u for u in users_r.json() if u["email"] == USER_EMAIL)

    r = await client.delete(f"/users/{target['id']}")
    assert r.status_code == 200
    assert r.json()["email"] == USER_EMAIL


async def test_delete_user_as_regular_user_forbidden(client: AsyncClient):
    await _register_login(client, ADMIN_EMAIL)
    await promote_to_admin(ADMIN_EMAIL)
    await client.post("/auth/logout")

    await _register_login(client, USER_EMAIL)

    users_r = await client.get("/users/")
    assert users_r.status_code == 403

    r = await client.delete(f"/users/1")
    assert r.status_code == 403


async def test_delete_user_not_found(client: AsyncClient):
    await _register_login(client, ADMIN_EMAIL)
    await promote_to_admin(ADMIN_EMAIL)
    await client.post("/auth/logout")
    await client.post("/auth/login", data={"username": ADMIN_EMAIL, "password": PASSWORD})

    r = await client.delete("/users/99999")
    assert r.status_code == 404


# ── update user ──────────────────────────────────────────────────────────────

async def test_update_user_as_admin(client: AsyncClient):
    await _register_login(client, USER_EMAIL)

    await _register_login(client, ADMIN_EMAIL)
    await promote_to_admin(ADMIN_EMAIL)
    await client.post("/auth/logout")
    await client.post("/auth/login", data={"username": ADMIN_EMAIL, "password": PASSWORD})

    users_r = await client.get("/users/")
    target = next(u for u in users_r.json() if u["email"] == USER_EMAIL)

    r = await client.patch(f"/users/{target['id']}", json={"username": "renamed"})
    assert r.status_code == 200
    assert r.json()["username"] == "renamed"


async def test_update_user_as_regular_user_forbidden(client: AsyncClient):
    await _register_login(client, USER_EMAIL)
    r = await client.patch("/users/1", json={"username": "hacker"})
    assert r.status_code == 403
