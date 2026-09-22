"""Юнит-тесты src.services.tasks: создание/чтение/обновление/удаление задач
без HTTP-слоя.

Сервисный слой больше не принимает CRM-абстракцию как параметр и сам CRM
никогда не вызывает (см. src/tasks/crm_outbox_tasks.py::dispatch_outbox_row) —
он лишь вставляет строку CrmOutbox в той же транзакции, что и основное
изменение, и диспатчит её в Celery. Эти тесты поэтому проверяют не факт CRM-
вызова (это дело tests/test_crm_outbox.py — обработчиков _do_create_task и
т.п.), а то, что нужная строка CrmOutbox появилась с правильным payload, и что
dispatch_outbox_row была вызвана для неё (мок из tests/conftest.py::
mock_outbox_dispatch — реального Celery/Redis в тестах нет).
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


# ── create_task ──────────────────────────────────────────────────────────────

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


# ── list_tasks / get_task ────────────────────────────────────────────────────

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


# ── update_task ──────────────────────────────────────────────────────────────

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


async def test_update_task_not_synced_skips_outbox(mock_outbox_dispatch):
    async with async_session_maker() as session:
        user = await _make_user(session)
        created = await task_service.create_task(session, user, TaskCreate(title="Orig", description="d"))
        mock_outbox_dispatch.reset_mock()

        result = await task_service.update_task(session, user, created.id, TaskUpdate(title="Renamed"))

        # crm_task_id всё ещё None (create ушёл в фон, ничего его не завершило) —
        # синхронизировать нечего, outbox-строка для update не создаётся.
        rows = await _outbox_rows_for(session, "task", created.id)
        assert not any(r.operation == "update" for r in rows)
        mock_outbox_dispatch.assert_not_called()


# ── delete_task ──────────────────────────────────────────────────────────────

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
