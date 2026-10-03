"""sync_status в ответах API — источник бейджа на task-board/subtask-board (common.js::syncStatusTag). Значение должно приходить
и из списков/поиска, отражать работу воркера (synced/failed), а внутренние детали синхронизации наружу не отдаются.
"""

import json

from httpx import AsyncClient
from sqlalchemy import select

from src.database import async_session_maker
from src.task_logic.models import Subtask, Task
from tests.conftest import register_user

EMAIL = "alice_sync@example.com"
PASSWORD = "Password1!"


async def _login(client: AsyncClient, mock_smtp: dict) -> None:
    await register_user(client, mock_smtp, EMAIL, PASSWORD)
    await client.post("/auth/login", data={"username": EMAIL, "password": PASSWORD})


async def _create_task(client: AsyncClient, title: str) -> int:
    r = await client.post("/create-task/", data={"data": json.dumps({"title": title, "description": "d"})})
    assert r.status_code == 201
    return r.json()["id"]


async def _create_subtask(client: AsyncClient, task_id: int, title: str) -> int:
    r = await client.post(
        "/create-subtask/", data={"data": json.dumps({"task_id": task_id, "title": title, "description": "d"})},
    )
    assert r.status_code == 201
    return r.json()["id"]


async def _set_sync_status(model, entity_id: int, status: str) -> None:
    """Симулирует то, что делает Celery-воркер после обработки outbox-строки."""
    async with async_session_maker() as session:
        entity = (await session.execute(select(model).where(model.id == entity_id))).scalar_one()
        entity.sync_status = status
        await session.commit()


async def test_task_list_and_search_expose_sync_status(client: AsyncClient, mock_smtp: dict):
    await _login(client, mock_smtp)
    task_id = await _create_task(client, "Badge task")

    listed = (await client.get("/tasks/")).json()
    assert [t["sync_status"] for t in listed] == ["pending"]

    found = (await client.get("/tasks/search", params={"title": "Badge"})).json()
    assert [t["sync_status"] for t in found] == ["pending"]

    detail = (await client.get(f"/tasks/{task_id}")).json()
    assert detail["sync_status"] == "pending"


async def test_task_sync_status_follows_worker_result(client: AsyncClient, mock_smtp: dict):
    await _login(client, mock_smtp)
    task_id = await _create_task(client, "Follows worker")

    await _set_sync_status(Task, task_id, "synced")
    assert (await client.get("/tasks/")).json()[0]["sync_status"] == "synced"

    await _set_sync_status(Task, task_id, "failed")
    assert (await client.get("/tasks/")).json()[0]["sync_status"] == "failed"


async def test_subtask_list_exposes_sync_status_and_follows_worker(client: AsyncClient, mock_smtp: dict):
    await _login(client, mock_smtp)
    task_id = await _create_task(client, "Parent")
    subtask_id = await _create_subtask(client, task_id, "Child")

    listed = (await client.get("/subtasks/", params={"task_id": task_id})).json()
    assert [s["sync_status"] for s in listed] == ["pending"]

    await _set_sync_status(Subtask, subtask_id, "synced")
    listed = (await client.get("/subtasks/", params={"task_id": task_id})).json()
    assert [s["sync_status"] for s in listed] == ["synced"]


async def test_internal_sync_details_are_not_exposed(client: AsyncClient, mock_smtp: dict):
    """Наружу уходит только sync_status: CRM-id, шард и детали outbox видны
    администратору (/admin/crm-sync), но не в обычных ответах."""
    await _login(client, mock_smtp)
    task_id = await _create_task(client, "Internal")
    subtask_id = await _create_subtask(client, task_id, "Internal child")

    task = (await client.get(f"/tasks/{task_id}")).json()
    subtask = (await client.get(f"/subtasks/{subtask_id}")).json()
    for payload in (task, subtask):
        for hidden in ("crm_task_id", "crm_subtask_id", "crm_shard", "crm_synced", "attempts", "last_error"):
            assert hidden not in payload, hidden
