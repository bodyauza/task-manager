"""Управление пользователями (admin-only, src/routers/users.py).

Проверяется и состояние БД после операции — иначе тест прошёл бы при реализации, отвечающей 200 без изменений.
"""

import json

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.auth.user_models import User
from src.database import async_session_maker
from src.task_logic.models import CrmOutbox, Subtask, Task
from tests.conftest import login_as_admin, register_and_login

ADMIN_EMAIL = "admin@example.com"
USER_EMAIL  = "user@example.com"
PASSWORD    = "Password1!"


async def _get_user(email: str) -> User | None:
    async with async_session_maker() as session:
        return (await session.execute(
            select(User).options(selectinload(User.roles)).where(User.email == email)
        )).scalar_one_or_none()


async def _target_id(client: AsyncClient, email: str) -> int:
    users = (await client.get("/users/")).json()
    return next(u["id"] for u in users if u["email"] == email)


async def test_list_users_as_admin(client: AsyncClient, mock_smtp: dict):
    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)

    r = await client.get("/users/")
    assert r.status_code == 200
    emails = [u["email"] for u in r.json()]
    assert ADMIN_EMAIL in emails


async def test_list_users_as_regular_user_forbidden(client: AsyncClient, mock_smtp: dict):
    await register_and_login(client, mock_smtp, USER_EMAIL)
    r = await client.get("/users/")
    assert r.status_code == 403


async def test_list_users_unauthenticated(client: AsyncClient):
    r = await client.get("/users/")
    assert r.status_code == 401


async def test_list_users_does_not_expose_password_hash(client: AsyncClient, mock_smtp: dict):
    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)
    r = await client.get("/users/")
    assert "hashed_password" not in r.text
    assert "$argon2" not in r.text and "$2b$" not in r.text


async def test_delete_user_as_admin(client: AsyncClient, mock_smtp: dict):
    await register_and_login(client, mock_smtp, USER_EMAIL)
    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)
    target_id = await _target_id(client, USER_EMAIL)

    r = await client.delete(f"/users/{target_id}")
    assert r.status_code == 200
    assert r.json()["email"] == USER_EMAIL

    # Пользователь действительно удалён из БД, остальные не затронуты.
    assert await _get_user(USER_EMAIL) is None
    assert await _get_user(ADMIN_EMAIL) is not None
    assert USER_EMAIL not in [u["email"] for u in (await client.get("/users/")).json()]


async def test_delete_user_as_regular_user_forbidden(client: AsyncClient, mock_smtp: dict):
    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)
    admin_id = await _target_id(client, ADMIN_EMAIL)
    await client.post("/auth/logout")

    await register_and_login(client, mock_smtp, USER_EMAIL)

    users_r = await client.get("/users/")
    assert users_r.status_code == 403

    r = await client.delete(f"/users/{admin_id}")
    assert r.status_code == 403
    assert await _get_user(ADMIN_EMAIL) is not None   # запрещённое удаление ничего не удалило


async def test_delete_own_account_is_rejected(client: AsyncClient, mock_smtp: dict):
    """Запрет самоудаления: единственный admin не должен потерять доступ к
    управлению пользователями (routers/users.py::delete_user)."""
    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)
    own_id = await _target_id(client, ADMIN_EMAIL)

    r = await client.delete(f"/users/{own_id}")
    assert r.status_code == 400
    assert r.json()["detail"] == "Cannot delete your own account"
    assert await _get_user(ADMIN_EMAIL) is not None


async def test_delete_user_cascades_tasks_and_subtasks(client: AsyncClient, mock_smtp: dict):
    # ondelete="CASCADE" + passive_deletes=True (User.tasks): удаление пользователя через ORM каскадно удаляет его задачи и подзадачи.
    await register_and_login(client, mock_smtp, USER_EMAIL)
    task_r = await client.post(
        "/create-task/", data={"data": json.dumps({"title": "Owned task", "description": "desc"})}
    )
    assert task_r.status_code == 201
    task_id = task_r.json()["id"]
    subtask_r = await client.post(
        "/create-subtask/",
        data={"data": json.dumps({"task_id": task_id, "title": "Owned subtask", "description": "desc"})},
    )
    assert subtask_r.status_code == 201
    subtask_id = subtask_r.json()["id"]

    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)
    target_id = await _target_id(client, USER_EMAIL)

    r = await client.delete(f"/users/{target_id}")
    assert r.status_code == 200

    async with async_session_maker() as session:
        assert (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none() is None
        assert (
            await session.execute(select(Subtask).where(Subtask.id == subtask_id))
        ).scalar_one_or_none() is None


async def test_delete_user_cleans_up_files_and_crm(
    client: AsyncClient, mock_smtp: dict, mock_magic, upload_root, mock_outbox_dispatch,
):
    # DELETE /users/{id} удаляет каждую задачу как DELETE /delete-task/{id}: чистятся каталоги uploads и ставится outbox-строка 'delete'
    # (раньше полагались только на ON DELETE CASCADE).
    await register_and_login(client, mock_smtp, USER_EMAIL)
    task_id = (await client.post(
        "/create-task/", data={"data": json.dumps({"title": "Task with files", "description": "d"})}
    )).json()["id"]
    subtask_id = (await client.post(
        "/create-subtask/",
        data={"data": json.dumps({"task_id": task_id, "title": "Subtask with files", "description": "d"})},
    )).json()["id"]
    pdf = ("tz.pdf", b"%PDF-1.4 fake pdf content for tests", "application/pdf")
    r = await client.post(f"/tasks/{task_id}/specification", files={"file": pdf})
    assert r.status_code == 200
    r = await client.post(f"/subtasks/{subtask_id}/files", files=[("files", pdf)])
    assert r.status_code == 200

    task_dir = upload_root / "tasks" / str(task_id)
    subtask_dir = upload_root / "subtasks" / str(subtask_id)
    assert task_dir.exists() and subtask_dir.exists()

    # Симулируем уже выполненный Celery 'create' (реального воркера в тестах нет).
    async with async_session_maker() as session:
        (await session.get(Task, task_id)).crm_task_id = 7
        (await session.get(Subtask, subtask_id)).crm_subtask_id = 8
        await session.commit()

    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)
    target_id = await _target_id(client, USER_EMAIL)
    mock_outbox_dispatch.reset_mock()

    r = await client.delete(f"/users/{target_id}")
    assert r.status_code == 200

    assert not task_dir.exists()
    assert not subtask_dir.exists()

    async with async_session_maker() as session:
        delete_rows = (await session.execute(
            select(CrmOutbox).where(
                CrmOutbox.aggregate_type == "task",
                CrmOutbox.aggregate_id == task_id,
                CrmOutbox.operation == "delete",
            )
        )).scalars().all()
    assert len(delete_rows) == 1
    assert delete_rows[0].payload == {"crm_task_id": 7, "crm_subtask_ids": [8]}
    assert [call.args[0].id for call in mock_outbox_dispatch.call_args_list] == [delete_rows[0].id]


async def test_delete_user_not_found(client: AsyncClient, mock_smtp: dict):
    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)

    r = await client.delete("/users/99999")
    assert r.status_code == 404


async def test_update_user_as_admin(client: AsyncClient, mock_smtp: dict):
    await register_and_login(client, mock_smtp, USER_EMAIL)
    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)
    target_id = await _target_id(client, USER_EMAIL)

    r = await client.patch(f"/users/{target_id}", json={"username": "renamed"})
    assert r.status_code == 200
    assert r.json()["username"] == "renamed"
    assert (await _get_user(USER_EMAIL)).username == "renamed"     # изменение сохранено в БД


async def test_update_user_is_partial(client: AsyncClient, mock_smtp: dict):
    """PATCH меняет только переданные поля."""
    await register_and_login(client, mock_smtp, USER_EMAIL)
    before = await _get_user(USER_EMAIL)
    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)

    r = await client.patch(f"/users/{before.id}", json={"is_active": False})
    assert r.status_code == 200

    after = await _get_user(USER_EMAIL)
    assert after.is_active is False
    assert (after.username, after.firstname, after.lastname) == (
        before.username, before.firstname, before.lastname,
    )
    assert [role.name for role in after.roles] == ["user"]


async def test_update_user_role_ids_replaces_roles(client: AsyncClient, mock_smtp: dict):
    """role_ids заменяет набор ролей целиком (не добавляет к существующим)."""
    await register_and_login(client, mock_smtp, USER_EMAIL)
    target = await _get_user(USER_EMAIL)
    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)

    r = await client.patch(f"/users/{target.id}", json={"role_ids": [2]})   # 2 = admin
    assert r.status_code == 200
    assert r.json()["role_ids"] == [2]
    assert [role.name for role in (await _get_user(USER_EMAIL)).roles] == ["admin"]


async def test_update_user_role_ids_invalid_returns_400_and_keeps_roles(client: AsyncClient, mock_smtp: dict):
    await register_and_login(client, mock_smtp, USER_EMAIL)
    target = await _get_user(USER_EMAIL)
    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)

    r = await client.patch(f"/users/{target.id}", json={"role_ids": [1, 999]})
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid role_ids"
    # Ни одна роль не изменилась (в том числе валидная из того же запроса).
    assert [role.name for role in (await _get_user(USER_EMAIL)).roles] == ["user"]


async def test_update_user_not_found(client: AsyncClient, mock_smtp: dict):
    await login_as_admin(client, mock_smtp, ADMIN_EMAIL)
    r = await client.patch("/users/99999", json={"username": "x"})
    assert r.status_code == 404


async def test_update_user_as_regular_user_forbidden(client: AsyncClient, mock_smtp: dict):
    await register_and_login(client, mock_smtp, USER_EMAIL)
    before = await _get_user(USER_EMAIL)

    r = await client.patch(f"/users/{before.id}", json={"username": "hacker", "role_ids": [2]})
    assert r.status_code == 403

    # Обычный пользователь не смог ни переименоваться, ни назначить себе admin.
    after = await _get_user(USER_EMAIL)
    assert after.username == before.username
    assert [role.name for role in after.roles] == ["user"]
