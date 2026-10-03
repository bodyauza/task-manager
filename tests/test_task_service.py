"""Юнит-тесты src.services.tasks без HTTP-слоя.

Сервис CRM не вызывает: он вставляет строку CrmOutbox в одной транзакции с изменением и диспатчит её в Celery. Тесты проверяют
содержимое строки и факт вызова dispatch_outbox_row (mock_outbox_dispatch); обработчики — в test_crm_outbox.py.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.auth.user_models import User
from src.database import async_session_maker
from src.services import tasks as task_service
from src.task_logic.models import CrmOutbox, Task
from src.task_logic.task_schemas import TaskCreate, TaskUpdate
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


async def test_create_task_success(mock_outbox_dispatch):
    async with async_session_maker() as session:
        user = await _make_user(session)

        result = await task_service.create_task(
            session, user, TaskCreate(title="My Task", description="desc"),
        )

        assert result.title == "My Task"
        # crm_task_id/crm_synced не в ответе (см. TaskResponse) — CRM-создание
        # отправлено в фон, статус проверяется через outbox-строку ниже.
        rows = await _outbox_rows_for(session, "task", result.id)
        assert len(rows) == 1
        assert rows[0].operation == "create"
        assert rows[0].status == "pending"
        assert rows[0].payload == {
            "title": "My Task", "description": "desc", "completed": False, "project": None,
            "creator_email": "alice@example.com",
        }
        mock_outbox_dispatch.assert_called_once()


async def test_create_task_duplicate_title_raises_409_before_second_outbox_row():
    async with async_session_maker() as session:
        user = await _make_user(session)
        first = await task_service.create_task(session, user, TaskCreate(title="Dup", description="d"))

        with pytest.raises(HTTPException) as exc_info:
            await task_service.create_task(session, user, TaskCreate(title="Dup", description="d"))

        assert exc_info.value.status_code == 409
        # Проверка дубля выполняется до вставки outbox-строки — второй запрос не
        # должен породить вторую строку 'create' для другой задачи с тем же title.
        rows = await _outbox_rows_for(session, "task", first.id)
        assert len(rows) == 1


async def test_list_tasks_pagination():
    async with async_session_maker() as session:
        user = await _make_user(session)
        for i in range(3):
            await task_service.create_task(session, user, TaskCreate(title=f"Task {i}", description="d"))

        results, total = await task_service.list_tasks(session, skip=0, limit=2)

        assert total == 3
        assert len(results) == 2


async def test_get_task_not_found_raises_404():
    async with async_session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await task_service.get_task(session, 9999)
        assert exc_info.value.status_code == 404


async def test_update_task_not_found_raises_404():
    async with async_session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await task_service.update_task(session, None, 9999, TaskUpdate(title="x"))
        assert exc_info.value.status_code == 404


async def test_update_task_enqueues_outbox_when_previously_synced(mock_outbox_dispatch):
    async with async_session_maker() as session:
        user = await _make_user(session)
        created = await task_service.create_task(session, user, TaskCreate(title="Orig", description="d"))
        # Симулирует то, что Celery уже успел выполнить 'create' и записать crm_task_id
        # (в реальности это делает _do_create_task — здесь незачем гонять целиком Celery).
        db_task = await session.get(Task, created.id)
        db_task.crm_task_id = 7
        await session.commit()
        mock_outbox_dispatch.reset_mock()

        result = await task_service.update_task(session, user, created.id, TaskUpdate(title="Renamed"))

        assert result.title == "Renamed"
        # crm_synced не в ответе (см. TaskResponse) — результат проверяется
        # через outbox-строку ниже.
        rows = await _outbox_rows_for(session, "task", created.id)
        update_rows = [r for r in rows if r.operation == "update"]
        assert len(update_rows) == 1
        # project=None: поле "project" не передавалось в TaskUpdate(title="Renamed") —
        # см. src/services/tasks.py::update_task, project_crm_id_for_crm остаётся None.
        assert update_rows[0].payload == {
            "crm_task_id": 7, "title": "Renamed", "description": None, "completed": None, "project": None,
        }
        mock_outbox_dispatch.assert_called_once()


async def test_update_task_before_create_enqueues_dependent_update(mock_outbox_dispatch):
    async with async_session_maker() as session:
        user = await _make_user(session)
        created = await task_service.create_task(session, user, TaskCreate(title="Orig", description="d"))
        mock_outbox_dispatch.reset_mock()

        await task_service.update_task(session, user, created.id, TaskUpdate(title="Renamed"))

        rows = await _outbox_rows_for(session, "task", created.id)
        create_row = next(r for r in rows if r.operation == "create")
        update_row = next(r for r in rows if r.operation == "update")
        assert update_row.depends_on_event_id == create_row.id
        assert update_row.payload["crm_task_id"] is None
        assert update_row.payload["title"] == "Renamed"
        mock_outbox_dispatch.assert_called_once()


async def test_update_task_without_crm_id_and_without_pending_create_skips_outbox(mock_outbox_dispatch):
    async with async_session_maker() as session:
        user = await _make_user(session)
        created = await task_service.create_task(session, user, TaskCreate(title="Orig", description="d"))
        for row in await _outbox_rows_for(session, "task", created.id):
            row.status = "done"
        await session.commit()
        mock_outbox_dispatch.reset_mock()

        await task_service.update_task(session, user, created.id, TaskUpdate(title="Renamed"))

        rows = await _outbox_rows_for(session, "task", created.id)
        assert not any(r.operation == "update" for r in rows)
        mock_outbox_dispatch.assert_not_called()


async def test_delete_task_removes_row_and_enqueues_outbox(mock_outbox_dispatch):
    async with async_session_maker() as session:
        user = await _make_user(session)
        created = await task_service.create_task(session, user, TaskCreate(title="ToDelete", description="d"))
        db_task = await session.get(Task, created.id)
        db_task.crm_task_id = 7
        await session.commit()
        mock_outbox_dispatch.reset_mock()

        snapshot = await task_service.delete_task(session, user, created.id)

        assert snapshot.title == "ToDelete"
        # crm_synced не в ответе (см. TaskResponse) — результат проверяется
        # через outbox-строку ниже.
        remaining = (
            await session.execute(select(Task).where(Task.id == created.id))
        ).scalar_one_or_none()
        assert remaining is None

        rows = await _outbox_rows_for(session, "task", created.id)
        delete_rows = [r for r in rows if r.operation == "delete"]
        assert len(delete_rows) == 1
        assert delete_rows[0].payload == {"crm_task_id": 7, "crm_subtask_ids": []}
        mock_outbox_dispatch.assert_called_once()


async def test_delete_task_not_found_raises_404():
    async with async_session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await task_service.delete_task(session, None, 9999)
        assert exc_info.value.status_code == 404
