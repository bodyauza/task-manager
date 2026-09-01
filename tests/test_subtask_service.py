"""Юнит-тесты src.services.subtasks: создание/чтение/обновление/удаление
подзадач без HTTP-слоя (см. test_task_service.py — тот же подход)."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.auth.user_models import User
from src.database import async_session_maker
from src.services import subtasks as subtask_service
from src.task_logic.models import Subtask, Task
from src.task_logic.subtask_schemas import SubtaskCreate, SubtaskUpdate


class FakeSubtaskCRMSync:
    """Реализация протокола SubtaskCRMSync для тестов — без сети, с журналом вызовов."""

    def __init__(self, create_id: int | None = 55):
        self.create_id = create_id
        self.created: list[dict] = []
        self.updated: list[dict] = []
        self.deleted: list[int] = []

    async def create_subtask(self, parent_item_id, title, description, completed=False):
        self.created.append({
            "parent_item_id": parent_item_id, "title": title,
            "description": description, "completed": completed,
        })
        return {"id": self.create_id}

    async def update_subtask(self, subtask_id, **kwargs):
        self.updated.append({"subtask_id": subtask_id, **kwargs})
        return {}

    async def delete_subtask(self, subtask_id):
        self.deleted.append(subtask_id)
        return {}


async def _make_user(session, email: str = "alice@example.com") -> User:
    user = User(
        email=email, username=email.split("@")[0], firstname="A", lastname="B",
        hashed_password="x", role_id=1, is_active=True, is_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_task(session, user: User, title: str = "Parent", crm_task_id: int | None = None) -> Task:
    task = Task(title=title, description="d", owner_id=user.id, crm_task_id=crm_task_id)
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


# ── create_subtask ───────────────────────────────────────────────────────────

async def test_create_subtask_syncs_crm_when_parent_task_synced():
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await _make_task(session, user, crm_task_id=10)
        crm = FakeSubtaskCRMSync(create_id=55)

        result = await subtask_service.create_subtask(
            session, user, SubtaskCreate(task_id=task.id, title="Sub", description="d"), crm,
        )

        assert result.title == "Sub"
        assert result.crm_subtask_id == 55
        assert result.crm_synced is True
        assert crm.created == [{
            "parent_item_id": 10, "title": "Sub", "description": "d", "completed": False,
        }]


async def test_create_subtask_skips_crm_when_parent_task_not_synced():
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await _make_task(session, user, crm_task_id=None)
        crm = FakeSubtaskCRMSync()

        result = await subtask_service.create_subtask(
            session, user, SubtaskCreate(task_id=task.id, title="Sub", description="d"), crm,
        )

        assert result.crm_subtask_id is None
        assert result.crm_synced is False
        assert crm.created == []


async def test_create_subtask_parent_task_not_found_raises_404():
    async with async_session_maker() as session:
        user = await _make_user(session)
        crm = FakeSubtaskCRMSync()

        with pytest.raises(HTTPException) as exc_info:
            await subtask_service.create_subtask(
                session, user, SubtaskCreate(task_id=9999, title="Sub", description="d"), crm,
            )
        assert exc_info.value.status_code == 404


# ── list_subtasks / get_subtask ──────────────────────────────────────────────

async def test_list_subtasks_pagination():
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await _make_task(session, user)
        crm = FakeSubtaskCRMSync()
        for i in range(3):
            await subtask_service.create_subtask(
                session, user, SubtaskCreate(task_id=task.id, title=f"Sub {i}"), crm,
            )

        results, total = await subtask_service.list_subtasks(session, task.id, skip=0, limit=2)

        assert total == 3
        assert len(results) == 2


async def test_get_subtask_not_found_raises_404():
    async with async_session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await subtask_service.get_subtask(session, 9999)
        assert exc_info.value.status_code == 404


# ── update_subtask ───────────────────────────────────────────────────────────

async def test_update_subtask_not_found_raises_404():
    async with async_session_maker() as session:
        crm = FakeSubtaskCRMSync()
        with pytest.raises(HTTPException) as exc_info:
            await subtask_service.update_subtask(session, None, 9999, SubtaskUpdate(title="x"), crm)
        assert exc_info.value.status_code == 404


async def test_update_subtask_syncs_crm_when_previously_synced():
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await _make_task(session, user, crm_task_id=10)
        crm = FakeSubtaskCRMSync(create_id=55)
        created = await subtask_service.create_subtask(
            session, user, SubtaskCreate(task_id=task.id, title="Orig"), crm,
        )

        result = await subtask_service.update_subtask(
            session, user, created.id, SubtaskUpdate(title="Renamed"), crm,
        )

        assert result.title == "Renamed"
        assert result.crm_synced is True
        assert crm.updated == [
            {"subtask_id": 55, "title": "Renamed", "description": None, "completed": None},
        ]


# ── delete_subtask ───────────────────────────────────────────────────────────

async def test_delete_subtask_removes_row_and_calls_crm():
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await _make_task(session, user, crm_task_id=10)
        crm = FakeSubtaskCRMSync(create_id=55)
        created = await subtask_service.create_subtask(
            session, user, SubtaskCreate(task_id=task.id, title="ToDelete"), crm,
        )

        snapshot = await subtask_service.delete_subtask(session, user, created.id, crm)

        assert snapshot.title == "ToDelete"
        assert snapshot.crm_synced is True
        assert crm.deleted == [55]

        remaining = (
            await session.execute(select(Subtask).where(Subtask.id == created.id))
        ).scalar_one_or_none()
        assert remaining is None


async def test_delete_subtask_not_found_raises_404():
    async with async_session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await subtask_service.delete_subtask(session, None, 9999, FakeSubtaskCRMSync())
        assert exc_info.value.status_code == 404
