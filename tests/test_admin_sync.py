"""Тесты admin-only обзора статуса CRM-синхронизации
(src/routers/admin.py::admin_tasks_sync_status/admin_subtasks_sync_status/
admin_crm_sync_page, src/services/admin_sync.py) — единственное место в
проекте, где статус CRM-синхронизации вообще виден (обычные TaskResponse/
SubtaskResponse его не отдают, см. tests/test_tasks.py/test_subtasks.py).
"""

import datetime
import json

from httpx import AsyncClient
from sqlalchemy import select

from src.database import async_session_maker
from src.task_logic.models import CrmOutbox, Subtask, Task
from tests.conftest import login_as_admin, register_and_login

ADMIN_EMAIL = "admin_sync@example.com"
USER_EMAIL  = "user_sync@example.com"
OTHER_EMAIL = "other_sync@example.com"
PASSWORD    = "Password1!"


async def _register_login(client: AsyncClient, mock_smtp: dict, email: str) -> None:
    await register_and_login(client, mock_smtp, email, PASSWORD)


async def _login_as_admin(client: AsyncClient, mock_smtp: dict) -> None:
    await login_as_admin(client, mock_smtp, ADMIN_EMAIL, PASSWORD)


async def _create_task(client: AsyncClient, title: str) -> dict:
    r = await client.post("/create-task/", data={"data": json.dumps({"title": title, "description": "d"})})
    assert r.status_code == 201
    return r.json()


async def _set_task_crm_id(task_id: int, crm_task_id: int) -> None:
    async with async_session_maker() as session:
        task = await session.get(Task, task_id)
        task.crm_task_id = crm_task_id
        task.sync_status = "synced"
        await session.commit()


async def _add_outbox_row(
    aggregate_type: str, aggregate_id: int, operation: str, status: str,
    attempts: int, updated_at: datetime.datetime,
) -> None:
    async with async_session_maker() as session:
        session.add(CrmOutbox(
            aggregate_type=aggregate_type, aggregate_id=aggregate_id, operation=operation,
            status=status, attempts=attempts, payload={}, updated_at=updated_at,
        ))
        await session.commit()


# ── JSON: /admin/crm-sync-status/tasks ──────────────────────────────────────

async def test_admin_tasks_sync_status_unauthenticated(client: AsyncClient):
    r = await client.get("/admin/crm-sync-status/tasks")
    assert r.status_code == 401


async def test_admin_tasks_sync_status_as_regular_user_forbidden(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, USER_EMAIL)
    r = await client.get("/admin/crm-sync-status/tasks")
    assert r.status_code == 403


async def test_admin_tasks_sync_status_shows_tasks_from_all_owners(client: AsyncClient, mock_smtp: dict):
    # Две задачи, у двух РАЗНЫХ владельцев — админ должен увидеть обе одним списком,
    # без owner-фильтрации (см. src/services/admin_sync.py).
    await _register_login(client, mock_smtp, USER_EMAIL)
    task1 = await _create_task(client, "Task Owner1")
    await client.post("/auth/logout")

    await _register_login(client, mock_smtp, OTHER_EMAIL)
    task2 = await _create_task(client, "Task Owner2")
    await client.post("/auth/logout")

    await _login_as_admin(client, mock_smtp)
    r = await client.get("/admin/crm-sync-status/tasks")
    assert r.status_code == 200
    body = r.json()
    ids_and_owners = {row["id"]: row["owner_email"] for row in body}
    assert ids_and_owners[task1["id"]] == USER_EMAIL
    assert ids_and_owners[task2["id"]] == OTHER_EMAIL


async def test_admin_tasks_sync_status_reflects_latest_outbox_row(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, USER_EMAIL)
    task = await _create_task(client, "Task With History")
    await _set_task_crm_id(task["id"], 42)
    await client.post("/auth/logout")

    # Две строки crm_outbox ПОВЕРХ уже существующей 'create'-строки (её
    # вставляет сам create_task) — реалистичная история retry. Даты — заведомо
    # позже "сейчас" (не абсолютные в прошлом), иначе они оказались бы старше
    # автоматической 'create'-строки (updated_at=now() на момент create_task
    # выше) и не стали бы последними по ORDER BY updated_at DESC.
    now = datetime.datetime.now(datetime.timezone.utc)
    old_ts = now + datetime.timedelta(minutes=1)
    new_ts = now + datetime.timedelta(minutes=2)
    await _add_outbox_row("task", task["id"], "update", "failed", 1, old_ts)
    await _add_outbox_row("task", task["id"], "update", "done", 2, new_ts)

    await _login_as_admin(client, mock_smtp)
    r = await client.get("/admin/crm-sync-status/tasks")
    assert r.status_code == 200
    row = next(t for t in r.json() if t["id"] == task["id"])
    assert row["crm_task_id"] == 42
    assert row["sync_status"] == "synced"
    # Именно последняя (по updated_at) строка, не первая созданная.
    assert row["last_outbox_status"] == "done"
    assert row["last_attempts"] == 2


async def test_admin_tasks_sync_status_no_outbox_history(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, USER_EMAIL)
    task = await _create_task(client, "Fresh Task")
    await client.post("/auth/logout")

    await _login_as_admin(client, mock_smtp)
    r = await client.get("/admin/crm-sync-status/tasks")
    assert r.status_code == 200
    row = next(t for t in r.json() if t["id"] == task["id"])
    # 'create'-строка от самого создания задачи есть всегда (см. services/tasks.py) —
    # last_outbox_status должен отражать её ('pending', Celery-воркера в тестах нет).
    assert row["last_operation"] == "create"
    assert row["last_outbox_status"] == "pending"
    assert row["crm_task_id"] is None
    # create_task сразу ставит 'pending' (outbox-строка создана, воркера в тестах нет) —
    # 'unsynced' остаётся только у записей, для которых синхронизация ещё не начиналась.
    assert row["sync_status"] == "pending"


# ── JSON: /admin/crm-sync-status/subtasks ───────────────────────────────────

async def test_admin_subtasks_sync_status_unauthenticated(client: AsyncClient):
    r = await client.get("/admin/crm-sync-status/subtasks")
    assert r.status_code == 401


async def test_admin_subtasks_sync_status_as_regular_user_forbidden(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, USER_EMAIL)
    r = await client.get("/admin/crm-sync-status/subtasks")
    assert r.status_code == 403


async def test_admin_subtasks_sync_status_shows_task_title(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, USER_EMAIL)
    task = await _create_task(client, "Parent For Subtask")
    sub_r = await client.post(
        "/create-subtask/",
        data={"data": json.dumps({"task_id": task["id"], "title": "Sub1", "description": "d"})},
    )
    assert sub_r.status_code == 201
    subtask = sub_r.json()
    await client.post("/auth/logout")

    await _login_as_admin(client, mock_smtp)
    r = await client.get("/admin/crm-sync-status/subtasks")
    assert r.status_code == 200
    row = next(s for s in r.json() if s["id"] == subtask["id"])
    assert row["task_id"] == task["id"]
    assert row["task_title"] == "Parent For Subtask"
    assert row["crm_subtask_id"] is None
    assert row["sync_status"] == "pending"   # см. services/subtasks.py::create_subtask


# ── HTML: /admin/crm-sync ────────────────────────────────────────────────────

async def test_admin_crm_sync_page_unauthenticated(client: AsyncClient):
    r = await client.get("/admin/crm-sync")
    assert r.status_code == 401


async def test_admin_crm_sync_page_as_regular_user_forbidden(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, USER_EMAIL)
    r = await client.get("/admin/crm-sync")
    assert r.status_code == 403


async def test_admin_crm_sync_page_as_admin(client: AsyncClient, mock_smtp: dict):
    await _login_as_admin(client, mock_smtp)
    r = await client.get("/admin/crm-sync")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "tasksSyncTable" in r.text
    assert "subtasksSyncTable" in r.text


# ── Навбар: ссылка видна только администратору ──────────────────────────────

async def test_navbar_admin_link_hidden_for_regular_user(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp, USER_EMAIL)
    r = await client.get("/task-board")
    assert r.status_code == 200
    assert "/admin/crm-sync" not in r.text


async def test_navbar_admin_link_visible_for_admin(client: AsyncClient, mock_smtp: dict):
    await _login_as_admin(client, mock_smtp)
    r = await client.get("/task-board")
    assert r.status_code == 200
    assert "/admin/crm-sync" in r.text
