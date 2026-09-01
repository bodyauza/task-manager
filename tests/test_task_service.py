"""Юнит-тесты src.services.tasks: создание/чтение/обновление/удаление задач
без HTTP-слоя.

Сервисный слой принимает db-сессию, пользователя и CRM-абстракцию как обычные
параметры — тесты вызывают его напрямую, подставляя фейковую реализацию
TaskCRMSync/SubtaskCRMSync (см. DIP в src/crm/task_service.py), а не мокая
импорт конкретного класса. По духу — то же самое, что делает
tests/test_realtime.py для FakeBroadcaster.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.auth.user_models import User
from src.database import async_session_maker
from src.services import tasks as task_service
from src.task_logic.models import Task
from src.task_logic.task_schemas import TaskCreate, TaskUpdate


class FakeTaskCRMSync:
    """Реализация протокола TaskCRMSync для тестов — без сети, с журналом вызовов."""

    def __init__(self, create_id: int | None = 99, fail_create: bool = False):
        self.create_id = create_id
        self.fail_create = fail_create
        self.created: list[dict] = []
        self.updated: list[dict] = []
        self.deleted: list[int] = []

    async def create_task(self, title, description, completed=False):
        self.created.append({"title": title, "description": description, "completed": completed})
        if self.fail_create:
            raise Exception("CRM unreachable")
        return {"id": self.create_id}

    async def update_task(self, task_id, **kwargs):
        self.updated.append({"task_id": task_id, **kwargs})
        return {}

    async def delete_task(self, task_id):
        self.deleted.append(task_id)
        return {}


class FakeSubtaskCRMSync:
    """Реализация протокола SubtaskCRMSync — используется только в delete_task
    (каскадное удаление подзадач из CRM), содержимое не важно для тестов задач."""

    async def create_subtask(self, **kwargs):
        return {"id": None}

    async def update_subtask(self, **kwargs):
        return {}

    async def delete_subtask(self, subtask_id):
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


# ── create_task ──────────────────────────────────────────────────────────────

async def test_create_task_success():
    async with async_session_maker() as session:
        user = await _make_user(session)
        crm = FakeTaskCRMSync(create_id=42)

        result = await task_service.create_task(
            session, user, TaskCreate(title="My Task", description="desc"), crm,
        )

        assert result.title == "My Task"
        assert result.crm_task_id == 42
        assert result.crm_synced is True
        assert crm.created == [{"title": "My Task", "description": "desc", "completed": False}]


async def test_create_task_duplicate_title_raises_409_before_calling_crm_again():
    async with async_session_maker() as session:
        user = await _make_user(session)
        crm = FakeTaskCRMSync()
        await task_service.create_task(session, user, TaskCreate(title="Dup", description="d"), crm)

        with pytest.raises(HTTPException) as exc_info:
            await task_service.create_task(session, user, TaskCreate(title="Dup", description="d"), crm)

        assert exc_info.value.status_code == 409
        # Проверка дубля выполняется до вызова CRM — второй запрос не должен дойти до create_task.
        assert len(crm.created) == 1


async def test_create_task_crm_failure_is_best_effort():
    async with async_session_maker() as session:
        user = await _make_user(session)
        crm = FakeTaskCRMSync(fail_create=True)

        result = await task_service.create_task(
            session, user, TaskCreate(title="No CRM", description="d"), crm,
        )

        assert result.crm_task_id is None
        assert result.crm_synced is False


# ── list_tasks / get_task ────────────────────────────────────────────────────

async def test_list_tasks_pagination():
    async with async_session_maker() as session:
        user = await _make_user(session)
        crm = FakeTaskCRMSync()
        for i in range(3):
            await task_service.create_task(
                session, user, TaskCreate(title=f"Task {i}", description="d"), crm,
            )

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
        crm = FakeTaskCRMSync()
        with pytest.raises(HTTPException) as exc_info:
            await task_service.update_task(session, None, 9999, TaskUpdate(title="x"), crm)
        assert exc_info.value.status_code == 404


async def test_update_task_syncs_crm_when_previously_synced():
    async with async_session_maker() as session:
        user = await _make_user(session)
        crm = FakeTaskCRMSync(create_id=7)
        created = await task_service.create_task(
            session, user, TaskCreate(title="Orig", description="d"), crm,
        )

        result = await task_service.update_task(
            session, user, created.id, TaskUpdate(title="Renamed"), crm,
        )

        assert result.title == "Renamed"
        assert result.crm_synced is True
        assert crm.updated == [{"task_id": 7, "title": "Renamed", "description": None, "completed": None}]


# ── delete_task ──────────────────────────────────────────────────────────────

async def test_delete_task_removes_row_and_calls_crm():
    async with async_session_maker() as session:
        user = await _make_user(session)
        crm = FakeTaskCRMSync(create_id=7)
        created = await task_service.create_task(
            session, user, TaskCreate(title="ToDelete", description="d"), crm,
        )

        snapshot = await task_service.delete_task(session, user, created.id, crm, FakeSubtaskCRMSync())

        assert snapshot.title == "ToDelete"
        assert snapshot.crm_synced is True
        assert crm.deleted == [7]

        remaining = (
            await session.execute(select(Task).where(Task.id == created.id))
        ).scalar_one_or_none()
        assert remaining is None


async def test_delete_task_not_found_raises_404():
    async with async_session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await task_service.delete_task(session, None, 9999, FakeTaskCRMSync(), FakeSubtaskCRMSync())
        assert exc_info.value.status_code == 404
