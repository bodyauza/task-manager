import json

from httpx import AsyncClient
from sqlalchemy import select

from src.database import async_session_maker
from src.task_logic.models import CrmOutbox, Task

from tests.conftest import register_and_login

EMAIL1   = "alice@example.com"
EMAIL2   = "bob@example.com"
PASSWORD = "Password1!"


async def _register_login(
    client: AsyncClient, mock_smtp: dict, email: str = EMAIL1, password: str = PASSWORD
) -> None:
    await register_and_login(client, mock_smtp, email, password)


async def _create_task(client: AsyncClient, title: str = "Parent Task") -> dict:
    # POST /create-task/ теперь multipart/form-data — см. tests/test_tasks.py::_create.
    r = await client.post(
        "/create-task/", data={"data": json.dumps({"title": title, "description": "desc"})}
    )
    return r.json()


async def _create_subtask(
    client: AsyncClient,
    task_id: int,
    title: str = "My Subtask",
    description: str = "subdesc",
):
    return await client.post(
        "/create-subtask/",
        data={"data": json.dumps({"task_id": task_id, "title": title, "description": description})},
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
    # crm_subtask_id/crm_synced НЕ являются полями ответа (см. SubtaskResponse);
    # sync_status — бейдж на subtask-board, сразу после создания 'pending'.
    assert "crm_subtask_id" not in data
    assert "crm_synced" not in data
    assert data["sync_status"] == "pending"


async def test_create_subtask_unauthenticated(client: AsyncClient):
    r = await client.post(
        "/create-subtask/",
        data={"data": json.dumps({"task_id": 1, "title": "X", "description": ""})},
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
        data={"data": json.dumps({"task_id": task["id"], "title": "", "description": ""})},
    )
    assert r.status_code == 422


async def test_create_subtask_whitespace_title_normalized(client: AsyncClient, mock_smtp: dict):
    # field_validator нормализует "  foo   bar  " → "foo bar" до проверки min_length
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    r = await client.post(
        "/create-subtask/",
        data={"data": json.dumps({"task_id": task["id"], "title": "  foo   bar  ", "description": ""})},
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
        data={"data": json.dumps({"task_id": task["id"], "title": "No Desc"})},
    )
    assert r.status_code == 201
    assert r.json()["description"] == ""


async def test_create_subtask_while_parent_not_synced_depends_on_parent_create(client: AsyncClient, mock_smtp: dict):
    # Родитель ещё не синхронизирован с CRM (crm_task_id = None: синхронизация целиком
    # в Celery-воркере, которого в тестах нет) — создание подзадачи всё равно проходит,
    # а её 'create' уходит в outbox с зависимостью от ещё не готового create родителя.
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    r = await _create_subtask(client, task["id"])
    assert r.status_code == 201

    async with async_session_maker() as session:
        assert (await session.get(Task, task["id"])).crm_task_id is None      # родитель действительно не в CRM
        parent_create = (await session.execute(
            select(CrmOutbox).where(
                CrmOutbox.aggregate_type == "task", CrmOutbox.aggregate_id == task["id"],
                CrmOutbox.operation == "create",
            )
        )).scalar_one()
        sub_create = (await session.execute(
            select(CrmOutbox).where(
                CrmOutbox.aggregate_type == "subtask", CrmOutbox.aggregate_id == r.json()["id"],
                CrmOutbox.operation == "create",
            )
        )).scalar_one()
    assert sub_create.depends_on_event_id == parent_create.id


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


async def test_read_subtasks_pages_are_ordered_by_id_and_disjoint(client: AsyncClient, mock_smtp: dict):
    """См. test_tasks.py::test_get_tasks_pages_are_ordered_by_id_and_disjoint —
    без ORDER BY обновлённая строка «уезжает» в конец и страницы плывут."""
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    other = await _create_task(client, title="Other parent")
    ids = [(await _create_subtask(client, task["id"], title=f"Sub {i}")).json()["id"] for i in range(7)]
    await _create_subtask(client, other["id"], title="Foreign")     # чужая подзадача в выдачу не попадает
    assert (await client.patch(f"/subtasks/{ids[0]}", json={"completed": True})).status_code == 200

    page1 = (await client.get(f"/subtasks/?task_id={task['id']}&skip=0&limit=5")).json()
    page2 = (await client.get(f"/subtasks/?task_id={task['id']}&skip=5&limit=5")).json()

    assert [s["id"] for s in page1] == sorted(ids)[:5]
    assert [s["id"] for s in page2] == sorted(ids)[5:]


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
    # crm_subtask_id/crm_synced НЕ являются полями ответа (см. SubtaskResponse).
    assert "crm_subtask_id" not in data
    assert "crm_synced" not in data


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


# ── Delete ────────────────────────────────────────────────────────────────────

async def test_delete_subtask_success(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"], title="ToDelete")).json()
    r = await client.delete(f"/delete-subtask/{subtask['id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "ToDelete"
    # crm_subtask_id/crm_synced НЕ являются полями ответа (см. SubtaskResponse).
    assert "crm_subtask_id" not in data
    assert "crm_synced" not in data


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
    assert (await client.get(f"/subtasks/{subtask['id']}")).status_code == 404


async def test_delete_subtask_actually_removed(client: AsyncClient, mock_smtp: dict):
    # после DELETE запись должна исчезнуть из БД
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"])).json()
    await client.delete(f"/delete-subtask/{subtask['id']}")
    r = await client.get(f"/subtasks/{subtask['id']}")
    assert r.status_code == 404


async def test_delete_task_cascades_subtasks(client: AsyncClient, mock_smtp: dict):
    # ForeignKey(ondelete="CASCADE"): при удалении task PostgreSQL удалит все subtask автоматически
    await _register_login(client, mock_smtp)
    task = await _create_task(client)
    subtask = (await _create_subtask(client, task["id"])).json()
    await client.delete(f"/delete-task/{task['id']}")
    r = await client.get(f"/subtasks/{subtask['id']}")
    assert r.status_code == 404
