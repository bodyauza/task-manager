from httpx import AsyncClient

from tests.conftest import register_user

EMAIL1   = "alice@example.com"
EMAIL2   = "bob@example.com"
PASSWORD = "Password1!"


async def _register_login(
    client: AsyncClient, mock_smtp: dict, email: str = EMAIL1, password: str = PASSWORD
) -> None:
    await register_user(client, mock_smtp, email, password)
    await client.post("/auth/login", data={"username": email, "password": password})


async def _create(client: AsyncClient, title: str = "My Task", description: str = "desc"):
    return await client.post("/create-task/", json={"title": title, "description": description})


# ── Create ───────────────────────────────────────────────────────────────────

async def test_create_task_success(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await _create(client)
    assert r.status_code == 201
    data = r.json()
    assert data["title"] == "My Task"
    assert data["completed"] is False


async def test_create_task_unauthenticated(client: AsyncClient):
    r = await _create(client)
    assert r.status_code == 401


async def test_create_task_duplicate_title(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    await _create(client, title="Dup")
    r = await _create(client, title="Dup")
    assert r.status_code == 409


async def test_create_task_empty_title(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.post("/create-task/", json={"title": "", "description": "d"})
    assert r.status_code == 422


# ── Read / pagination ────────────────────────────────────────────────────────

async def test_get_tasks_empty(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/tasks/")
    assert r.status_code == 200
    assert r.json() == []


async def test_get_tasks_unauthenticated(client: AsyncClient):
    r = await client.get("/tasks/")
    assert r.status_code == 401


async def test_get_tasks_pagination(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    for i in range(7):
        await _create(client, title=f"Task {i}")
    r = await client.get("/tasks/?skip=0&limit=5")
    assert r.status_code == 200
    assert len(r.json()) == 5
    assert int(r.headers["X-Total-Count"]) == 7


async def test_get_tasks_second_page(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    for i in range(7):
        await _create(client, title=f"Task {i}")
    r = await client.get("/tasks/?skip=5&limit=5")
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_get_tasks_invalid_limit(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/tasks/?limit=0")
    assert r.status_code == 422


async def test_get_tasks_invalid_skip(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/tasks/?skip=-1")
    assert r.status_code == 422


# ── Search ───────────────────────────────────────────────────────────────────

async def test_search_tasks_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    await _create(client, title="Python Tips")
    r = await client.get("/tasks/search?title=python")
    assert r.status_code == 200
    assert any("Python Tips" in t["title"] for t in r.json())


async def test_search_tasks_not_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/tasks/search?title=zzz_nonexistent_xyz")
    assert r.status_code == 200
    assert r.json() == []


async def test_search_tasks_unauthenticated(client: AsyncClient):
    r = await client.get("/tasks/search?title=anything")
    assert r.status_code == 401


# ── Update ───────────────────────────────────────────────────────────────────

async def test_update_task_success(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    created = (await _create(client, title="Old Title")).json()
    r = await client.patch(f"/tasks/{created['id']}", json={"title": "New Title", "completed": True})
    assert r.status_code == 200
    assert r.json()["title"] == "New Title"
    assert r.json()["completed"] is True


async def test_update_task_partial(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    created = (await _create(client, title="Keep Title")).json()
    r = await client.patch(f"/tasks/{created['id']}", json={"completed": True})
    assert r.status_code == 200
    assert r.json()["title"] == "Keep Title"
    assert r.json()["completed"] is True


async def test_update_task_duplicate_title(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    await _create(client, title="Task A")
    t2 = (await _create(client, title="Task B")).json()
    r = await client.patch(f"/tasks/{t2['id']}", json={"title": "Task A"})
    assert r.status_code == 409


async def test_update_task_not_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.patch("/tasks/99999", json={"title": "X"})
    assert r.status_code == 404


async def test_update_task_unauthenticated(client: AsyncClient):
    r = await client.patch("/tasks/1", json={"title": "X"})
    assert r.status_code == 401


# ── Delete ───────────────────────────────────────────────────────────────────

async def test_delete_task_success(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    created = (await _create(client, title="ToDelete")).json()
    r = await client.delete(f"/delete-task/{created['id']}")
    assert r.status_code == 200
    assert r.json()["title"] == "ToDelete"


async def test_delete_task_not_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.delete("/delete-task/99999")
    assert r.status_code == 404


async def test_delete_task_unauthenticated(client: AsyncClient):
    r = await client.delete("/delete-task/1")
    assert r.status_code == 401


# ── Collaborative access ─────────────────────────────────────────────────────

async def test_other_user_can_update_task(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, EMAIL1)
    created = (await _create(client, title="Shared Task")).json()
    await client.post("/auth/logout")
    await _register_login(client, mock_smtp, EMAIL2)
    r = await client.patch(f"/tasks/{created['id']}", json={"completed": True})
    assert r.status_code == 200


async def test_other_user_can_delete_task(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, EMAIL1)
    created = (await _create(client, title="Remove Me")).json()
    await client.post("/auth/logout")
    await _register_login(client, mock_smtp, EMAIL2)
    r = await client.delete(f"/delete-task/{created['id']}")
    assert r.status_code == 200


async def test_all_users_see_all_tasks(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, EMAIL1)
    await _create(client, title="Visible To All")
    await client.post("/auth/logout")
    await _register_login(client, mock_smtp, EMAIL2)
    r = await client.get("/tasks/")
    assert r.status_code == 200
    assert "Visible To All" in [t["title"] for t in r.json()]
