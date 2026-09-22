"""Тесты src.tasks.crm_outbox_tasks._cleanup_done_outbox_async — Celery Beat-
задача (раз в сутки, 03:00 UTC, см. src/celery_app.py::beat_schedule), которая
удаляет старые 'done'-строки crm_outbox, чтобы таблица не росла бесконечно.

retention_days передаётся явно в каждом тесте (не через crm_settings/env) —
тот же приём, что и src/tasks/sharding.py::shard_names(count): тестируемость
без monkeypatch/reload процесса.
"""

import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Delete

from src.database import async_session_maker
from src.task_logic.models import CrmOutbox, Task
from src.tasks.crm_outbox_tasks import _cleanup_done_outbox_async
from tests.conftest import make_user as _make_user

_OLD = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=60)
_RECENT = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)


async def _make_task() -> int:
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(title="t", description="d", owner_id=user.id, crm_task_id=42, sync_status="synced")
        session.add(task)
        await session.commit()
        return task.id


async def _make_row(
    task_id: int, *, status: str, updated_at: datetime.datetime, operation: str = "update",
    depends_on_event_id: "int | None" = None,
) -> int:
    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation=operation, status=status,
            attempts=1, shard="shard_0", payload={"crm_task_id": 42},
            depends_on_event_id=depends_on_event_id,
        )
        session.add(row)
        await session.commit()
        # updated_at имеет server_default=func.now(): чтобы задать своё значение
        # "задним числом", он выставляется ОТДЕЛЬНЫМ UPDATE после INSERT — прямой
        # присвоение перед первым commit просто использовалось бы в INSERT (тоже
        # сработало бы), но явный UPDATE нагляднее показывает намерение "давно
        # обработанная строка", не полагаясь на порядок присвоения атрибутов.
        row.updated_at = updated_at
        await session.commit()
        return row.id


async def _exists(row_id: int) -> bool:
    async with async_session_maker() as session:
        return (await session.get(CrmOutbox, row_id)) is not None


async def test_deletes_old_done_row():
    task_id = await _make_task()
    row_id = await _make_row(task_id, status="done", updated_at=_OLD)

    deleted = await _cleanup_done_outbox_async(retention_days=30)

    assert deleted == 1
    assert not await _exists(row_id)


async def test_keeps_recent_done_row():
    task_id = await _make_task()
    row_id = await _make_row(task_id, status="done", updated_at=_RECENT)

    deleted = await _cleanup_done_outbox_async(retention_days=30)

    assert deleted == 0
    assert await _exists(row_id)


async def test_never_deletes_pending_failed_or_blocked_regardless_of_age():
    task_id = await _make_task()
    ids = [
        await _make_row(task_id, status=status, updated_at=_OLD)
        for status in ("pending", "failed", "blocked")
    ]

    deleted = await _cleanup_done_outbox_async(retention_days=30)

    assert deleted == 0
    for row_id in ids:
        assert await _exists(row_id)


async def test_keeps_done_row_that_other_row_depends_on():
    """Родитель (create, done, старый) ещё занят ссылкой потомка (update,
    done, старый, depends_on_event_id=parent.id) — первый прогон удаляет
    только потомка; второй, когда потомка уже нет, удаляет и родителя."""
    task_id = await _make_task()
    parent_id = await _make_row(task_id, status="done", operation="create", updated_at=_OLD)
    child_id = await _make_row(
        task_id, status="done", operation="update", updated_at=_OLD, depends_on_event_id=parent_id,
    )

    first = await _cleanup_done_outbox_async(retention_days=30)
    assert first == 1
    assert not await _exists(child_id)
    assert await _exists(parent_id)          # родитель ещё занят — не тронут

    second = await _cleanup_done_outbox_async(retention_days=30)
    assert second == 1
    assert not await _exists(parent_id)      # ссылка исчезла — теперь можно


async def test_dependency_protection_holds_even_with_retention_zero():
    """retention_days=0 (все done-строки формально 'старые') не отменяет
    защиту по depends_on_event_id — только порядок удаления."""
    task_id = await _make_task()
    parent_id = await _make_row(task_id, status="done", operation="create", updated_at=_RECENT)
    child_id = await _make_row(
        task_id, status="done", operation="update", updated_at=_RECENT, depends_on_event_id=parent_id,
    )

    deleted = await _cleanup_done_outbox_async(retention_days=0)

    assert deleted == 1
    assert not await _exists(child_id)
    assert await _exists(parent_id)


async def test_batches_across_multiple_passes():
    """С маленьким _CLEANUP_BATCH удаление старых строк идёт несколькими
    итерациями внутреннего while — итоговый результат не отличается от
    одного большого батча."""
    task_id = await _make_task()
    ids = [await _make_row(task_id, status="done", updated_at=_OLD) for _ in range(5)]

    with patch("src.tasks.crm_outbox_tasks._CLEANUP_BATCH", 2):
        deleted = await _cleanup_done_outbox_async(retention_days=30)

    assert deleted == 5
    for row_id in ids:
        assert not await _exists(row_id)


async def test_returns_zero_when_nothing_to_clean():
    assert await _cleanup_done_outbox_async(retention_days=30) == 0


async def test_survives_integrity_error_from_row_inserted_between_select_and_delete():
    """Гонка, которую NOT EXISTS в SELECT-кандидатах не может исключить: между
    моментом, когда строка попала в выборку на удаление, и моментом, когда
    реально выполняется DELETE, ДРУГАЯ транзакция успевает вставить новую
    строку с depends_on_event_id на эту же строку — DELETE должен упасть с
    IntegrityError (fk_crm_outbox_depends_on_event_id, RESTRICT), а не молча
    удалить занятую строку и не закоммитить наполовину гонку.

    Настоящей многопроцессной гонки здесь нет (один event loop, один тест) —
    она симулируется детерминированно: AsyncSession.execute патчится так,
    чтобы ПЕРЕД первым же DELETE-запросом (найденным по типу statement, не по
    тексту SQL) отдельная сессия успела вставить и закоммитить зависимую
    строку. С точки зрения PostgreSQL это неотличимо от настоящей гонки двух
    независимых транзакций — FK проверяется в момент выполнения DELETE,
    независимо от того, как физически появилась вставленная строка."""
    task_id = await _make_task()
    victim_id = await _make_row(task_id, status="done", operation="create", updated_at=_OLD)

    real_execute = AsyncSession.execute
    already_raced = False

    async def racy_execute(self, statement, *args, **kwargs):
        nonlocal already_raced
        if not already_raced and isinstance(statement, Delete):
            already_raced = True
            # "Чужая" транзакция: вставляет зависимую строку и коммитит РАНЬШЕ,
            # чем наш DELETE реально уйдёт в Postgres.
            async with async_session_maker() as racer:
                racer.add(CrmOutbox(
                    aggregate_type="task", aggregate_id=task_id, operation="update",
                    status="pending", attempts=0, shard="shard_0",
                    payload={"crm_task_id": 42}, depends_on_event_id=victim_id,
                ))
                await racer.commit()
        return await real_execute(self, statement, *args, **kwargs)

    with patch.object(AsyncSession, "execute", racy_execute):
        with pytest.raises(IntegrityError):
            await _cleanup_done_outbox_async(retention_days=30)

    # FK отработал: строка, ставшая чужой зависимостью в последний момент,
    # НЕ удалена, несмотря на то что SELECT изначально выбрал её как кандидата.
    assert await _exists(victim_id)
