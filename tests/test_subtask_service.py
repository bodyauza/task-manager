"""Юнит-тесты src.services.subtasks без HTTP-слоя: сервис вставляет CrmOutbox и диспатчит её в Celery (mock_outbox_dispatch), CRM не вызывает."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.auth.user_models import User
from src.database import async_session_maker
from src.services import subtasks as subtask_service
from src.task_logic.models import CrmOutbox, Subtask, Task
from src.task_logic.subtask_schemas import SubtaskCreate, SubtaskUpdate
from tests.conftest import make_user


async def _outbox_rows_for(session, aggregate_type: str, aggregate_id: int) -> list[CrmOutbox]:
    return (
        await session.execute(
            select(CrmOutbox)
            .where(CrmOutbox.aggregate_type == aggregate_type, CrmOutbox.aggregate_id == aggregate_id)
            .order_by(CrmOutbox.id)
        )
    ).scalars().all()


_make_user = make_user


async def _make_task(session, user: User, title: str = "Parent", crm_task_id: int | None = None) -> Task:
    task = Task(title=title, description="d", owner_id=user.id, crm_task_id=crm_task_id)
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def test_create_subtask_enqueues_outbox_when_parent_task_synced(mock_outbox_dispatch):
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await _make_task(session, user, crm_task_id=10)

        result = await subtask_service.create_subtask(
            session, user, SubtaskCreate(task_id=task.id, title="Sub", description="d"),
        )

        assert result.title == "Sub"
        # crm_subtask_id/crm_synced не в ответе (см. SubtaskResponse) — CRM-создание
        # отправлено в фон, статус проверяется через outbox-строку ниже.
        rows = await _outbox_rows_for(session, "subtask", result.id)
        assert len(rows) == 1
        assert rows[0].operation == "create"
        assert rows[0].status == "pending"
        assert rows[0].payload == {
            "title": "Sub", "description": "d", "completed": False,
            "creator_email": "alice@example.com",
        }
        # Родитель уже синхронизирован — зависимости нет, можно обрабатывать сразу.
        assert rows[0].depends_on_event_id is None
        mock_outbox_dispatch.assert_called_once()


async def test_create_subtask_without_parent_outbox_row_has_no_dependency(mock_outbox_dispatch):
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await _make_task(session, user, crm_task_id=None)  # родитель ещё не синхронизирован

        result = await subtask_service.create_subtask(
            session, user, SubtaskCreate(task_id=task.id, title="Sub", description="d"),
        )

        rows = await _outbox_rows_for(session, "subtask", result.id)
        assert len(rows) == 1
        assert rows[0].status == "pending"
        # Родитель создан напрямую в БД (без create-строки), зависеть не от чего. Зависимость от pending-create родителя —
        # test_crm_outbox.py::test_create_subtask_create_row_depends_on_parent_pending_create.
        assert rows[0].depends_on_event_id is None


async def test_create_subtask_parent_task_not_found_raises_404():
    async with async_session_maker() as session:
        user = await _make_user(session)

        with pytest.raises(HTTPException) as exc_info:
            await subtask_service.create_subtask(
                session, user, SubtaskCreate(task_id=9999, title="Sub", description="d"),
            )
        assert exc_info.value.status_code == 404


async def test_list_subtasks_pagination():
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await _make_task(session, user)
        for i in range(3):
            await subtask_service.create_subtask(session, user, SubtaskCreate(task_id=task.id, title=f"Sub {i}"))

        results, total = await subtask_service.list_subtasks(session, task.id, skip=0, limit=2)

        assert total == 3
        assert len(results) == 2


async def test_get_subtask_not_found_raises_404():
    async with async_session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await subtask_service.get_subtask(session, 9999)
        assert exc_info.value.status_code == 404


async def test_update_subtask_not_found_raises_404():
    async with async_session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await subtask_service.update_subtask(session, None, 9999, SubtaskUpdate(title="x"))
        assert exc_info.value.status_code == 404


async def test_update_subtask_enqueues_outbox_when_previously_synced(mock_outbox_dispatch):
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await _make_task(session, user, crm_task_id=10)
        created = await subtask_service.create_subtask(session, user, SubtaskCreate(task_id=task.id, title="Orig"))
        db_subtask = await session.get(Subtask, created.id)
        db_subtask.crm_subtask_id = 55  # симулирует то, что Celery уже выполнил 'create'
        await session.commit()
        mock_outbox_dispatch.reset_mock()

        result = await subtask_service.update_subtask(session, user, created.id, SubtaskUpdate(title="Renamed"))

        assert result.title == "Renamed"
        # crm_synced не в ответе (см. SubtaskResponse) — результат проверяется
        # через outbox-строку ниже, не через ответ.
        rows = await _outbox_rows_for(session, "subtask", created.id)
        update_rows = [r for r in rows if r.operation == "update"]
        assert len(update_rows) == 1
        assert update_rows[0].payload == {
            "crm_subtask_id": 55, "title": "Renamed", "description": None, "completed": None,
        }
        mock_outbox_dispatch.assert_called_once()


async def test_update_subtask_before_create_enqueues_dependent_update(mock_outbox_dispatch):
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await _make_task(session, user, crm_task_id=None)
        created = await subtask_service.create_subtask(session, user, SubtaskCreate(task_id=task.id, title="Orig"))
        mock_outbox_dispatch.reset_mock()

        await subtask_service.update_subtask(session, user, created.id, SubtaskUpdate(title="Renamed"))

        rows = await _outbox_rows_for(session, "subtask", created.id)
        create_row = next(r for r in rows if r.operation == "create")
        update_row = next(r for r in rows if r.operation == "update")
        assert update_row.depends_on_event_id == create_row.id
        assert update_row.payload["crm_subtask_id"] is None
        assert update_row.payload["title"] == "Renamed"
        mock_outbox_dispatch.assert_called_once()


async def test_delete_subtask_removes_row_and_enqueues_outbox(mock_outbox_dispatch):
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await _make_task(session, user, crm_task_id=10)
        created = await subtask_service.create_subtask(session, user, SubtaskCreate(task_id=task.id, title="ToDelete"))
        db_subtask = await session.get(Subtask, created.id)
        db_subtask.crm_subtask_id = 55
        await session.commit()
        mock_outbox_dispatch.reset_mock()

        snapshot = await subtask_service.delete_subtask(session, user, created.id)

        assert snapshot.title == "ToDelete"
        # crm_synced не в ответе (см. SubtaskResponse) — результат проверяется
        # через outbox-строку ниже.
        remaining = (
            await session.execute(select(Subtask).where(Subtask.id == created.id))
        ).scalar_one_or_none()
        assert remaining is None

        rows = await _outbox_rows_for(session, "subtask", created.id)
        delete_rows = [r for r in rows if r.operation == "delete"]
        assert len(delete_rows) == 1
        assert delete_rows[0].payload == {"crm_subtask_id": 55}
        mock_outbox_dispatch.assert_called_once()


async def test_delete_subtask_not_found_raises_404():
    async with async_session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await subtask_service.delete_subtask(session, None, 9999)
        assert exc_info.value.status_code == 404
