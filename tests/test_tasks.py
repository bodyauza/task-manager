import pytest
from httpx import AsyncClient

EMAIL1 = "alice@example.com"
EMAIL2 = "bob@example.com"
PASSWORD = "Password1"


async def _register_login(client: AsyncClient, email=EMAIL1, password=PASSWORD):
    await client.post("/auth/register", json={
        "email": email,
        "password": password,
        "username": email.split("@")[0],
    })
    await client.post("/auth/login", data={"username": email, "password": password})


async def _create(client: AsyncClient, title="My Task", description="desc"):
    return await client.post("/create-task/", json={"title": title, "description": description})


# ── Create ───────────────────────────────────────────────────────────────────

async def test_create_task_success(client: AsyncClient):
    await _register_login(client)
    r = await _create(client)
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "My Task"
    assert data["completed"] is False


async def test_create_task_unauthenticated(client: AsyncClient):
    r = await _create(client)
    assert r.status_code == 401


async def test_create_task_duplicate_title(client: AsyncClient):
    await _register_login(client)
    await _create(client, title="Dup")
    r = await _create(client, title="Dup")
    assert r.status_code == 409


async def test_create_task_empty_title(client: AsyncClient):
    await _register_login(client)
    r = await client.post("/create-task/", json={"title": "", "description": "d"})
    assert r.status_code == 422


# ── Read / pagination ────────────────────────────────────────────────────────

async def test_get_tasks_empty(client: AsyncClient):
    await _register_login(client)
    r = await client.get("/tasks/")
    assert r.status_code == 200
    assert r.json() == []


async def test_get_tasks_unauthenticated(client: AsyncClient):
    r = await client.get("/tasks/")
    assert r.status_code == 401


async def test_get_tasks_pagination(client: AsyncClient):
    await _register_login(client)
    for i in range(7):
        await _create(client, title=f"Task {i}")
    r = await client.get("/tasks/?skip=0&limit=5")
    assert r.status_code == 200
    assert len(r.json()) == 5
    assert int(r.headers["X-Total-Count"]) == 7


async def test_get_tasks_second_page(client: AsyncClient):
    await _register_login(client)
    for i in range(7):
        await _create(client, title=f"Task {i}")
    r = await client.get("/tasks/?skip=5&limit=5")
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_get_tasks_invalid_limit(client: AsyncClient):
    await _register_login(client)
    r = await client.get("/tasks/?limit=0")
    assert r.status_code == 422


async def test_get_tasks_invalid_skip(client: AsyncClient):
    await _register_login(client)
    r = await client.get("/tasks/?skip=-1")
    assert r.status_code == 422


# ── Search ───────────────────────────────────────────────────────────────────

async def test_search_tasks_found(client: AsyncClient):
    await _register_login(client)
    await _create(client, title="Python Tips")
    r = await client.get("/tasks/search?title=python")
    assert r.status_code == 200
    results = r.json()
    assert len(results) >= 1
    assert any("Python Tips" in t["title"] for t in results)


async def test_search_tasks_not_found(client: AsyncClient):
    await _register_login(client)
    r = await client.get("/tasks/search?title=zzz_nonexistent_xyz")
    assert r.status_code == 200
    assert r.json() == []


async def test_search_tasks_unauthenticated(client: AsyncClient):
    r = await client.get("/tasks/search?title=anything")
    assert r.status_code == 401


# ── Update ───────────────────────────────────────────────────────────────────

async def test_update_task_success(client: AsyncClient):
    await _register_login(client)
    created = (await _create(client, title="Old Title")).json()
    r = await client.put(f"/update-task/{created['id']}", json={"title": "New Title", "completed": True})
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "New Title"
    assert data["completed"] is True


async def test_update_task_partial(client: AsyncClient):
    await _register_login(client)
    created = (await _create(client, title="Keep Title")).json()
    r = await client.put(f"/update-task/{created['id']}", json={"completed": True})
    assert r.status_code == 200
    assert r.json()["title"] == "Keep Title"
    assert r.json()["completed"] is True


async def test_update_task_duplicate_title(client: AsyncClient):
    await _register_login(client)
    await _create(client, title="Task A")
    t2 = (await _create(client, title="Task B")).json()
    r = await client.put(f"/update-task/{t2['id']}", json={"title": "Task A"})
    assert r.status_code == 409


async def test_update_task_not_found(client: AsyncClient):
    await _register_login(client)
    r = await client.put("/update-task/99999", json={"title": "X"})
    assert r.status_code == 404


async def test_update_task_unauthenticated(client: AsyncClient):
    r = await client.put("/update-task/1", json={"title": "X"})
    assert r.status_code == 401


# ── Delete ───────────────────────────────────────────────────────────────────

async def test_delete_task_success(client: AsyncClient):
    await _register_login(client)
    created = (await _create(client, title="ToDelete")).json()
    r = await client.delete(f"/delete-task/{created['id']}")
    assert r.status_code == 200
    assert r.json()["title"] == "ToDelete"


async def test_delete_task_not_found(client: AsyncClient):
    await _register_login(client)
    r = await client.delete("/delete-task/99999")
    assert r.status_code == 404


async def test_delete_task_unauthenticated(client: AsyncClient):
    r = await client.delete("/delete-task/1")
    assert r.status_code == 401


# ── Collaborative access ─────────────────────────────────────────────────────

async def test_other_user_can_update_task(client: AsyncClient):
    """Any authenticated user can update any task (collaborative mode)."""
    await _register_login(client, EMAIL1)
    created = (await _create(client, title="Shared Task")).json()

    await client.post("/auth/logout")
    await _register_login(client, EMAIL2)

    r = await client.put(f"/update-task/{created['id']}", json={"completed": True})
    assert r.status_code == 200


async def test_other_user_can_delete_task(client: AsyncClient):
    """Any authenticated user can delete any task (collaborative mode)."""
    await _register_login(client, EMAIL1)
    created = (await _create(client, title="Remove Me")).json()

    await client.post("/auth/logout")
    await _register_login(client, EMAIL2)

    r = await client.delete(f"/delete-task/{created['id']}")
    assert r.status_code == 200


async def test_all_users_see_all_tasks(client: AsyncClient):
    """Tasks created by user1 are visible to user2."""
    await _register_login(client, EMAIL1)
    await _create(client, title="Visible To All")

    await client.post("/auth/logout")
    await _register_login(client, EMAIL2)

    r = await client.get("/tasks/")
    assert r.status_code == 200
    titles = [t["title"] for t in r.json()]
    assert "Visible To All" in titles
