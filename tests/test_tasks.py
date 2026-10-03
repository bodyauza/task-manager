import asyncio
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.database import async_session_maker
from src.task_logic.models import Subtask

from tests.conftest import register_and_login

EMAIL1   = "alice@example.com"
EMAIL2   = "bob@example.com"
PASSWORD = "Password1!"


async def _register_login(
    client: AsyncClient, mock_smtp: dict, email: str = EMAIL1, password: str = PASSWORD
) -> None:
    await register_and_login(client, mock_smtp, email, password)


async def _create(client: AsyncClient, title: str = "My Task", description: str = "desc"):
    # POST /create-task/ теперь multipart/form-data: тело задачи — JSON-строка в
    # form-поле "data" (см. routers/task_routers.py); файлы в этих тестах не нужны.
    return await client.post(
        "/create-task/", data={"data": json.dumps({"title": title, "description": description})}
    )


async def test_create_task_success(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await _create(client)
    assert r.status_code == 201
    data = r.json()
    assert data["title"] == "My Task"
    assert data["completed"] is False
    # crm_task_id/crm_synced не входят в ответ; sync_status — единственная отдаваемая деталь: сразу после создания 'pending'.
    assert "crm_task_id" not in data
    assert "crm_synced" not in data
    assert data["sync_status"] == "pending"


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
    r = await client.post("/create-task/", data={"data": json.dumps({"title": "", "description": "d"})})
    assert r.status_code == 422


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


async def test_get_tasks_pages_are_ordered_by_id_and_disjoint(client: AsyncClient, mock_smtp: dict):
    """Страницы не пересекаются и идут по возрастанию id, даже если строку обновили (без ORDER BY страницы «плывут»)."""
    await _register_login(client, mock_smtp)
    ids = [(await _create(client, title=f"Task {i}")).json()["id"] for i in range(7)]
    assert (await client.patch(f"/tasks/{ids[0]}", json={"completed": True})).status_code == 200

    page1 = (await client.get("/tasks/?skip=0&limit=5")).json()
    page2 = (await client.get("/tasks/?skip=5&limit=5")).json()

    assert [t["id"] for t in page1] == sorted(ids)[:5]
    assert [t["id"] for t in page2] == sorted(ids)[5:]


async def test_get_tasks_invalid_limit(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/tasks/?limit=0")
    assert r.status_code == 422


async def test_get_tasks_invalid_skip(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/tasks/?skip=-1")
    assert r.status_code == 422


async def test_search_tasks_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    await _create(client, title="Python Tips")
    await _create(client, title="Unrelated")
    r = await client.get("/tasks/search?title=python")
    assert r.status_code == 200
    assert [t["title"] for t in r.json()] == ["Python Tips"]     # регистронезависимо; чужое не попало


async def test_search_tasks_pages_are_ordered_by_id_and_disjoint(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    ids = [(await _create(client, title=f"Common {i}")).json()["id"] for i in range(7)]
    await _create(client, title="Other")
    assert (await client.patch(f"/tasks/{ids[0]}", json={"completed": True})).status_code == 200

    page1 = (await client.get("/tasks/search?title=common&skip=0&limit=5")).json()
    page2 = (await client.get("/tasks/search?title=common&skip=5&limit=5")).json()

    assert [t["id"] for t in page1] == sorted(ids)[:5]
    assert [t["id"] for t in page2] == sorted(ids)[5:]


async def test_search_tasks_not_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/tasks/search?title=zzz_nonexistent_xyz")
    assert r.status_code == 200
    assert r.json() == []


async def test_search_tasks_unauthenticated(client: AsyncClient):
    r = await client.get("/tasks/search?title=anything")
    assert r.status_code == 401


async def test_update_task_success(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    created = (await _create(client, title="Old Title")).json()
    r = await client.patch(f"/tasks/{created['id']}", json={"title": "New Title", "completed": True})
    assert r.status_code == 200
    assert r.json()["title"] == "New Title"
    assert r.json()["completed"] is True
    # Изменение сохранено, а не только отражено в ответе.
    stored = (await client.get(f"/tasks/{created['id']}")).json()
    assert (stored["title"], stored["completed"]) == ("New Title", True)


async def test_update_task_partial(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    created = (await _create(client, title="Keep Title")).json()
    r = await client.patch(f"/tasks/{created['id']}", json={"completed": True})
    assert r.status_code == 200
    assert r.json()["title"] == "Keep Title"
    assert r.json()["completed"] is True
    stored = (await client.get(f"/tasks/{created['id']}")).json()
    assert (stored["title"], stored["completed"]) == ("Keep Title", True)


async def test_update_task_duplicate_title(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    await _create(client, title="Task A")
    t2 = (await _create(client, title="Task B")).json()
    r = await client.patch(f"/tasks/{t2['id']}", json={"title": "Task A"})
    assert r.status_code == 409
    assert (await client.get(f"/tasks/{t2['id']}")).json()["title"] == "Task B"   # не изменилось


async def test_update_task_not_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.patch("/tasks/99999", json={"title": "X"})
    assert r.status_code == 404


@pytest.mark.parametrize("payload", [{"title": None}, {"description": None}, {"completed": None}])
async def test_update_task_explicit_null_for_not_null_field_returns_422_not_409(
    client: AsyncClient, mock_smtp: dict, payload: dict,
):
    """Явный `null` для title/description/completed (NOT NULL) даёт 422 на уровне схемы, а не 409 из IntegrityError.
    Название совпадает с существующей задачей специально — чтобы доказать, что срабатывает валидация null, а не проверка дубля.
    """
    await _register_login(client, mock_smtp)
    await _create(client, title="PoC task")
    other = (await _create(client, title="Other task")).json()

    r = await client.patch(f"/tasks/{other['id']}", json=payload)

    assert r.status_code == 422
    stored = (await client.get(f"/tasks/{other['id']}")).json()
    assert stored["title"] == "Other task"  # не изменилось


async def test_update_task_missing_key_still_leaves_field_unchanged(client: AsyncClient, mock_smtp: dict):
    """Отсутствие ключа (в отличие от явного null выше) — штатный частичный
    update, exclude_unset=True — не должно задеваться новой валидацией."""
    await _register_login(client, mock_smtp)
    created = (await _create(client, title="Keep Title")).json()
    r = await client.patch(f"/tasks/{created['id']}", json={"completed": True})
    assert r.status_code == 200
    assert r.json()["title"] == "Keep Title"


async def test_update_task_unauthenticated(client: AsyncClient):
    r = await client.patch("/tasks/1", json={"title": "X"})
    assert r.status_code == 401


async def test_delete_task_success(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    created = (await _create(client, title="ToDelete")).json()
    r = await client.delete(f"/delete-task/{created['id']}")
    assert r.status_code == 200
    assert r.json()["title"] == "ToDelete"
    # Задача действительно удалена: не находится ни по id, ни в списке.
    assert (await client.get(f"/tasks/{created['id']}")).status_code == 404
    assert (await client.get("/tasks/")).json() == []


async def test_delete_task_not_found(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.delete("/delete-task/99999")
    assert r.status_code == 404


async def test_delete_task_unauthenticated(client: AsyncClient):
    r = await client.delete("/delete-task/1")
    assert r.status_code == 401


async def test_other_user_can_update_task(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, EMAIL1)
    created = (await _create(client, title="Shared Task")).json()
    await client.post("/auth/logout")
    await _register_login(client, mock_smtp, EMAIL2)
    r = await client.patch(f"/tasks/{created['id']}", json={"completed": True})
    assert r.status_code == 200
    assert (await client.get(f"/tasks/{created['id']}")).json()["completed"] is True


async def test_other_user_can_delete_task(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, EMAIL1)
    created = (await _create(client, title="Remove Me")).json()
    await client.post("/auth/logout")
    await _register_login(client, mock_smtp, EMAIL2)
    r = await client.delete(f"/delete-task/{created['id']}")
    assert r.status_code == 200
    assert (await client.get(f"/tasks/{created['id']}")).status_code == 404


async def test_all_users_see_all_tasks(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, EMAIL1)
    await _create(client, title="Visible To All")
    await client.post("/auth/logout")
    await _register_login(client, mock_smtp, EMAIL2)
    r = await client.get("/tasks/")
    assert r.status_code == 200
    assert "Visible To All" in [t["title"] for t in r.json()]


async def test_concurrent_delete_and_create_subtask_no_crash(client: AsyncClient, mock_smtp: dict):
    """FOR UPDATE в delete_task и FK-violation в create_subtask: параллельные delete_task и create_subtask для одной задачи не дают ни 500,
    ни подзадачи-сироты, ни ложного «already exists» — только 201 либо осмысленный 404/409.
    """
    await _register_login(client, mock_smtp)
    task = (await _create(client, title="RaceParent")).json()
    tid = task["id"]

    delete_resp, create_resp = await asyncio.gather(
        client.delete(f"/delete-task/{tid}"),
        client.post(
            "/create-subtask/",
            data={"data": json.dumps({"task_id": tid, "title": "RaceSub", "description": ""})},
        ),
    )

    assert delete_resp.status_code == 200
    assert create_resp.status_code in (201, 404, 409)
    # Что бы ни победило в гонке, подзадачи-сироты (без родительской задачи) быть не может:
    # либо она каскадно удалена вместе с задачей, либо не была создана.
    async with async_session_maker() as session:
        orphans = (await session.execute(select(Subtask).where(Subtask.task_id == tid))).scalars().all()
    assert orphans == []
    if create_resp.status_code == 404:
        assert create_resp.json()["detail"] == "Task not found"
    if create_resp.status_code == 409:
        assert "already exists" in create_resp.json()["detail"]
