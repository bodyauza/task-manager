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


async def _create_task(client: AsyncClient, title: str = "Parent Task") -> dict:
    r = await client.post("/create-task/", json={"title": title, "description": "desc"})
    return r.json()


async def _create_subtask(
    client: AsyncClient,
    task_id: int,
    title: str = "My Subtask",
    description: str = "subdesc",
):
    return await client.post(
        "/create-subtask/",
        json={"task_id": task_id, "title": title, "description": description},
    )


# ── Create ───────────────────────────────────────────────────────────────────

async def test_create_subtask_success(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    r = await _create_subtask(client, task["id"])
    assert r.status_code == 201
    data = r.json()
    assert data["title"] == "My Subtask"
    assert data["description"] == "subdesc"
    assert data["completed"] is False
    assert data["task_id"] == task["id"]
    assert data["crm_subtask_id"] == 55      # mock_crm.subtask_mgr.create_subtask → id=55
    assert data["crm_synced"] is True


async def test_create_subtask_unauthenticated(client: AsyncClient):
    r = await client.post(
        "/create-subtask/", json={"task_id": 1, "title": "X", "description": ""}
    )
    assert r.status_code == 401


async def test_create_subtask_task_not_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await _create_subtask(client, task_id=99999)
    assert r.status_code == 404


async def test_create_subtask_empty_title(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    r = await client.post(
        "/create-subtask/",
        json={"task_id": task["id"], "title": "", "description": ""},
    )
    assert r.status_code == 422


async def test_create_subtask_whitespace_title_normalized(client: AsyncClient, mock_smtp: dict):
    # field_validator нормализует "  foo   bar  " → "foo bar" до проверки min_length
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    r = await client.post(
        "/create-subtask/",
        json={"task_id": task["id"], "title": "  foo   bar  ", "description": ""},
    )
    assert r.status_code == 201
    assert r.json()["title"] == "foo bar"


async def test_create_subtask_duplicate_title_same_task(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    await _create_subtask(client, task["id"], title="Dup")
    r = await _create_subtask(client, task["id"], title="Dup")
    assert r.status_code == 409


async def test_create_subtask_same_title_different_tasks(client: AsyncClient, mock_smtp: dict):
    # UniqueConstraint(title, task_id): "Shared" в task1 и task2 — разные пары, оба допустимы
    await _register_login(client, mock_smtp)
    task1 = await _create_task(client, title="Task One")
    task2 = await _create_task(client, title="Task Two")
    r1 = await _create_subtask(client, task1["id"], title="Shared")
    r2 = await _create_subtask(client, task2["id"], title="Shared")
    assert r1.status_code == 201
    assert r2.status_code == 201


async def test_create_subtask_no_description(client: AsyncClient, mock_smtp: dict):
    # description не передан → server_default="": PostgreSQL подставит пустую строку
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    r = await client.post(
        "/create-subtask/",
        json={"task_id": task["id"], "title": "No Desc"},
    )
    assert r.status_code == 201
    assert r.json()["description"] == ""


async def test_create_subtask_crm_failure_does_not_block(
    client: AsyncClient, mock_smtp: dict, mock_crm: dict
):
    # best-effort: ошибка CRM не блокирует INSERT в локальную БД
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    mock_crm["subtask_mgr"].create_subtask.side_effect = Exception("CRM down")
    r = await _create_subtask(client, task["id"])
    assert r.status_code == 201
    assert r.json()["crm_synced"] is False
    assert r.json()["crm_subtask_id"] is None


async def test_create_subtask_no_crm_sync_when_task_lacks_crm_id(
    client: AsyncClient, mock_smtp: dict, mock_crm: dict
):
    # task.crm_task_id is None → роутер пропускает блок SubtaskManager вовсе
    await _register_login(client, mock_smtp)
    mock_crm["task_mgr"].create_task.return_value = {"id": None}
    task = (
        await client.post("/create-task/", json={"title": "No CRM", "description": ""})
    ).json()
    r = await _create_subtask(client, task["id"])
    assert r.status_code == 201
    assert r.json()["crm_synced"] is False
    assert r.json()["crm_subtask_id"] is None
    mock_crm["subtask_mgr"].create_subtask.assert_not_called()


# ── Read list ─────────────────────────────────────────────────────────────────

async def test_read_subtasks_empty(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    r = await client.get(f"/subtasks/?task_id={task['id']}")
    assert r.status_code == 200
    assert r.json() == []
    assert int(r.headers["X-Total-Count"]) == 0


async def test_read_subtasks_unauthenticated(client: AsyncClient):
    r = await client.get("/subtasks/?task_id=1")
    assert r.status_code == 401


async def test_read_subtasks_pagination(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    for i in range(7):
        await _create_subtask(client, task["id"], title=f"Sub {i}")
    r = await client.get(f"/subtasks/?task_id={task['id']}&skip=0&limit=5")
    assert r.status_code == 200
    assert len(r.json()) == 5
    assert int(r.headers["X-Total-Count"]) == 7


async def test_read_subtasks_second_page(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    for i in range(7):
        await _create_subtask(client, task["id"], title=f"Sub {i}")
    r = await client.get(f"/subtasks/?task_id={task['id']}&skip=5&limit=5")
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_read_subtasks_invalid_limit(client: AsyncClient, mock_smtp: dict):
    # limit ge=1: 0 нарушает ограничение Query → 422
    await _register_login(client, mock_smtp)
    r = await client.get("/subtasks/?task_id=1&limit=0")
    assert r.status_code == 422


async def test_read_subtasks_invalid_skip(client: AsyncClient, mock_smtp: dict):
    # skip ge=0: отрицательное значение → 422
    await _register_login(client, mock_smtp)
    r = await client.get("/subtasks/?task_id=1&skip=-1")
    assert r.status_code == 422


async def test_read_subtasks_nonexistent_task_returns_empty(client: AsyncClient, mock_smtp: dict):
    # task_id не существует: фильтр WHERE task_id=99999 вернёт 0 строк, не ошибку
    await _register_login(client, mock_smtp)
    r = await client.get("/subtasks/?task_id=99999")
    assert r.status_code == 200
    assert r.json() == []
    assert int(r.headers["X-Total-Count"]) == 0


# ── Read single ───────────────────────────────────────────────────────────────

async def test_get_subtask_success(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"])).json()
    r = await client.get(f"/subtasks/{subtask['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == subtask["id"]
    assert r.json()["title"] == "My Subtask"


async def test_get_subtask_not_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/subtasks/99999")
    assert r.status_code == 404


async def test_get_subtask_unauthenticated(client: AsyncClient):
    r = await client.get("/subtasks/1")
    assert r.status_code == 401


# ── Update ────────────────────────────────────────────────────────────────────

async def test_update_subtask_success(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"])).json()
    r = await client.patch(
        f"/subtasks/{subtask['id']}",
        json={"title": "New Title", "completed": True},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "New Title"
    assert data["completed"] is True
    assert data["crm_synced"] is True   # crm_subtask_id=55 → update_subtask вызван


async def test_update_subtask_partial(client: AsyncClient, mock_smtp: dict):
    # exclude_unset=True: title не передан → остаётся "Keep Me"
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"], title="Keep Me")).json()
    r = await client.patch(f"/subtasks/{subtask['id']}", json={"completed": True})
    assert r.status_code == 200
    assert r.json()["title"] == "Keep Me"
    assert r.json()["completed"] is True


async def test_update_subtask_not_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.patch("/subtasks/99999", json={"title": "X"})
    assert r.status_code == 404


async def test_update_subtask_unauthenticated(client: AsyncClient):
    r = await client.patch("/subtasks/1", json={"title": "X"})
    assert r.status_code == 401


async def test_update_subtask_duplicate_title(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    await _create_subtask(client, task["id"], title="Alpha")
    s2 = (await _create_subtask(client, task["id"], title="Beta")).json()
    r = await client.patch(f"/subtasks/{s2['id']}", json={"title": "Alpha"})
    assert r.status_code == 409


async def test_update_subtask_other_user_allowed(client: AsyncClient, mock_smtp: dict):
    # shared board: проверка owner_id закомментирована → любой авторизованный пользователь
    # может изменить подзадачу чужой задачи
    await _register_login(client, mock_smtp, EMAIL1)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"])).json()
    await client.post("/auth/logout")

    await _register_login(client, mock_smtp, EMAIL2)
    r = await client.patch(f"/subtasks/{subtask['id']}", json={"title": "Updated by Bob"})
    assert r.status_code == 200
    assert r.json()["title"] == "Updated by Bob"


async def test_update_subtask_crm_failure(client: AsyncClient, mock_smtp: dict, mock_crm: dict):
    # CRM недоступен при update → 200, локальная БД уже изменена, crm_synced=False
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"])).json()
    mock_crm["subtask_mgr"].update_subtask.side_effect = Exception("CRM unreachable")
    r = await client.patch(f"/subtasks/{subtask['id']}", json={"completed": True})
    assert r.status_code == 200
    assert r.json()["crm_synced"] is False
    assert r.json()["completed"] is True    # изменение в БД сохранено несмотря на ошибку CRM


async def test_update_subtask_crm_synced_false_when_no_crm_id(
    client: AsyncClient, mock_smtp: dict, mock_crm: dict
):
    # crm_subtask_id is None → else-ветка роутера → crm_synced=False без вызова CRM
    await _register_login(client, mock_smtp)
    mock_crm["task_mgr"].create_task.return_value = {"id": None}
    task = (
        await client.post("/create-task/", json={"title": "No CRM T", "description": ""})
    ).json()
    subtask = (await _create_subtask(client, task["id"])).json()
    assert subtask["crm_subtask_id"] is None

    r = await client.patch(f"/subtasks/{subtask['id']}", json={"title": "Updated"})
    assert r.status_code == 200
    assert r.json()["crm_synced"] is False
    mock_crm["subtask_mgr"].update_subtask.assert_not_called()


# ── Delete ────────────────────────────────────────────────────────────────────

async def test_delete_subtask_success(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"], title="ToDelete")).json()
    r = await client.delete(f"/delete-subtask/{subtask['id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "ToDelete"
    assert data["crm_synced"] is True   # crm_subtask_id=55 → delete_subtask вызван


async def test_delete_subtask_not_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.delete("/delete-subtask/99999")
    assert r.status_code == 404


async def test_delete_subtask_unauthenticated(client: AsyncClient):
    r = await client.delete("/delete-subtask/1")
    assert r.status_code == 401


async def test_delete_subtask_other_user_allowed(client: AsyncClient, mock_smtp: dict):
    # shared board: проверка owner_id закомментирована → любой авторизованный пользователь
    # может удалить подзадачу чужой задачи
    await _register_login(client, mock_smtp, EMAIL1)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"])).json()
    await client.post("/auth/logout")

    await _register_login(client, mock_smtp, EMAIL2)
    r = await client.delete(f"/delete-subtask/{subtask['id']}")
    assert r.status_code == 200


async def test_delete_subtask_actually_removed(client: AsyncClient, mock_smtp: dict):
    # после DELETE запись должна исчезнуть из БД
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"])).json()
    await client.delete(f"/delete-subtask/{subtask['id']}")
    r = await client.get(f"/subtasks/{subtask['id']}")
    assert r.status_code == 404


async def test_delete_subtask_crm_failure(client: AsyncClient, mock_smtp: dict, mock_crm: dict):
    # CRM недоступен при delete → 200, запись уже удалена из локальной БД, crm_synced=False
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"])).json()
    mock_crm["subtask_mgr"].delete_subtask.side_effect = Exception("CRM down")
    r = await client.delete(f"/delete-subtask/{subtask['id']}")
    assert r.status_code == 200
    assert r.json()["crm_synced"] is False
    check = await client.get(f"/subtasks/{subtask['id']}")
    assert check.status_code == 404        # в локальной БД удалено несмотря на ошибку CRM


async def test_delete_task_cascades_subtasks(client: AsyncClient, mock_smtp: dict):
    # ForeignKey(ondelete="CASCADE"): при удалении task PostgreSQL удалит все subtask автоматически
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"])).json()
    await client.delete(f"/delete-task/{task['id']}")
    r = await client.get(f"/subtasks/{subtask['id']}")
    assert r.status_code == 404
