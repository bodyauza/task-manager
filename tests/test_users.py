from httpx import AsyncClient
from sqlalchemy import select

from src.database import async_session_maker
from src.task_logic.models import Subtask, Task
from tests.conftest import promote_to_admin, register_user

ADMIN_EMAIL = "admin@example.com"
USER_EMAIL  = "user@example.com"
PASSWORD    = "Password1!"


async def _register_login(
    client: AsyncClient, mock_smtp: dict, email: str, password: str = PASSWORD
) -> None:
    await register_user(client, mock_smtp, email, password)
    await client.post("/auth/login", data={"username": email, "password": password})


# ── list users ───────────────────────────────────────────────────────────────

async def test_list_users_as_admin(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, ADMIN_EMAIL)
    await promote_to_admin(ADMIN_EMAIL)
    await client.post("/auth/logout")
    await client.post("/auth/login", data={"username": ADMIN_EMAIL, "password": PASSWORD})

    r = await client.get("/users/")
    assert r.status_code == 200
    emails = [u["email"] for u in r.json()]
    assert ADMIN_EMAIL in emails


async def test_list_users_as_regular_user_forbidden(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, USER_EMAIL)
    r = await client.get("/users/")
    assert r.status_code == 403


async def test_list_users_unauthenticated(client: AsyncClient):
    r = await client.get("/users/")
    assert r.status_code == 401


# ── delete user ──────────────────────────────────────────────────────────────

async def test_delete_user_as_admin(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, USER_EMAIL)

    await _register_login(client, mock_smtp, ADMIN_EMAIL)
    await promote_to_admin(ADMIN_EMAIL)
    await client.post("/auth/logout")
    await client.post("/auth/login", data={"username": ADMIN_EMAIL, "password": PASSWORD})

    users_r = await client.get("/users/")
    target = next(u for u in users_r.json() if u["email"] == USER_EMAIL)

    r = await client.delete(f"/users/{target['id']}")
    assert r.status_code == 200
    assert r.json()["email"] == USER_EMAIL


async def test_delete_user_as_regular_user_forbidden(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, ADMIN_EMAIL)
    await promote_to_admin(ADMIN_EMAIL)
    await client.post("/auth/logout")

    await _register_login(client, mock_smtp, USER_EMAIL)

    users_r = await client.get("/users/")
    assert users_r.status_code == 403

    r = await client.delete("/users/1")
    assert r.status_code == 403


async def test_delete_user_cascades_tasks_and_subtasks(client: AsyncClient, mock_smtp: dict):
    # Регрессия на ondelete="CASCADE" + passive_deletes=True (User.tasks):
    # удаление пользователя через ORM (session.delete) должно каскадно
    # удалить его задачи, а через них — и подзадачи.
    await _register_login(client, mock_smtp, USER_EMAIL)
    task_r = await client.post(
        "/create-task/", json={"title": "Owned task", "description": "desc"}
    )
    task_id = task_r.json()["id"]
    subtask_r = await client.post(
        "/create-subtask/",
        json={"task_id": task_id, "title": "Owned subtask", "description": "desc"},
    )
    subtask_id = subtask_r.json()["id"]

    await _register_login(client, mock_smtp, ADMIN_EMAIL)
    await promote_to_admin(ADMIN_EMAIL)
    await client.post("/auth/logout")
    await client.post("/auth/login", data={"username": ADMIN_EMAIL, "password": PASSWORD})

    users_r = await client.get("/users/")
    target = next(u for u in users_r.json() if u["email"] == USER_EMAIL)

    r = await client.delete(f"/users/{target['id']}")
    assert r.status_code == 200

    async with async_session_maker() as session:
        assert (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none() is None
        assert (
            await session.execute(select(Subtask).where(Subtask.id == subtask_id))
        ).scalar_one_or_none() is None


async def test_delete_user_not_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, ADMIN_EMAIL)
    await promote_to_admin(ADMIN_EMAIL)
    await client.post("/auth/logout")
    await client.post("/auth/login", data={"username": ADMIN_EMAIL, "password": PASSWORD})

    r = await client.delete("/users/99999")
    assert r.status_code == 404


# ── update user ──────────────────────────────────────────────────────────────

async def test_update_user_as_admin(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, USER_EMAIL)

    await _register_login(client, mock_smtp, ADMIN_EMAIL)
    await promote_to_admin(ADMIN_EMAIL)
    await client.post("/auth/logout")
    await client.post("/auth/login", data={"username": ADMIN_EMAIL, "password": PASSWORD})

    users_r = await client.get("/users/")
    target = next(u for u in users_r.json() if u["email"] == USER_EMAIL)

    r = await client.patch(f"/users/{target['id']}", json={"username": "renamed"})
    assert r.status_code == 200
    assert r.json()["username"] == "renamed"


async def test_update_user_as_regular_user_forbidden(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, USER_EMAIL)
    r = await client.patch("/users/1", json={"username": "hacker"})
    assert r.status_code == 403
