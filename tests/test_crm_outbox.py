"""Тесты durable outbox (CrmOutbox): строка коммитится в одной транзакции с изменением, поэтому CRM-запрос переживает падение процесса.

Покрытие:
- продюсер: только специфичное для outbox — sticky-шардирование (id % N) и зависимость create подзадачи от create родителя
  (остальное — в test_task_service.py/test_subtask_service.py; dispatch замокан через mock_outbox_dispatch);
- консьюмер (_process_outbox_row_async): повторяет операцию по payload, не читая исходную строку Task;
- reconcile: находит только 'pending' старше грейс-периода.

_process_outbox_row_async/_reconcile_pending_outbox_async вызываются напрямую (await), а не через Celery .delay().
"""

import asyncio
import datetime
import time
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src.database import async_session_maker
from src.services import subtasks as subtask_service
from src.services import tasks as task_service
from src.task_logic.models import CrmOutbox, Subtask, Task
from src.task_logic.subtask_schemas import SubtaskCreate
from src.task_logic.task_schemas import TaskCreate
from tests.conftest import make_user as _make_user


class _NoopLock:
    """Заглушка async context manager для shard_lock: реальный Redis в тестах не поднимается."""

    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc_info):
        return None


@pytest.fixture(autouse=True)
def _no_redis_rate_limit_and_lock():
    with patch("src.tasks.crm_outbox_tasks.acquire_slot", AsyncMock(return_value=True)), \
         patch("src.tasks.crm_outbox_tasks.shard_lock", lambda shard: _NoopLock()):
        yield


async def _outbox_rows_for(session, aggregate_type: str, aggregate_id: int) -> list[CrmOutbox]:
    return (
        await session.execute(
            select(CrmOutbox)
            .where(CrmOutbox.aggregate_type == aggregate_type, CrmOutbox.aggregate_id == aggregate_id)
            .order_by(CrmOutbox.id)
        )
    ).scalars().all()


async def _outbox_rows(session, task_id: int) -> list[CrmOutbox]:
    return await _outbox_rows_for(session, "task", task_id)


# Продюсер: payload и диспатч проверены в test_task_service.py/test_subtask_service.py; здесь — шардирование id % N и
# depends_on_event_id между create подзадачи и create родителя.

async def test_create_task_inserts_pending_create_row_with_shard():
    async with async_session_maker() as session:
        user = await _make_user(session)
        result = await task_service.create_task(session, user, TaskCreate(title="No files", description="d"))

        rows = await _outbox_rows(session, result.id)
        assert len(rows) == 1
        assert rows[0].operation == "create"
        assert rows[0].status == "pending"
        assert rows[0].shard is not None  # sticky-присвоение через id % N


async def test_create_subtask_inserts_create_row_when_parent_synced():
    """Родитель уже синхронизирован — create подзадачи не зависит ни от чего
    (depends_on_event_id=None)."""
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await task_service.create_task(session, user, TaskCreate(title="Parent", description="d"))
        db_task = await session.get(Task, task.id)
        db_task.crm_task_id = 100  # симулирует то, что Celery уже выполнил 'create'
        await session.commit()

        result = await subtask_service.create_subtask(
            session, user, SubtaskCreate(task_id=task.id, title="Sub", description="d"),
        )

        rows = await _outbox_rows_for(session, "subtask", result.id)
        assert len(rows) == 1
        assert rows[0].operation == "create"
        assert rows[0].status == "pending"
        assert rows[0].depends_on_event_id is None
        assert rows[0].shard is not None


async def test_create_subtask_create_row_depends_on_parent_pending_create():
    """Родитель ещё не синхронизирован (его create — pending): create подзадачи получает depends_on_event_id на строку родителя."""
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = await task_service.create_task(session, user, TaskCreate(title="Parent2", description="d"))
        # crm_task_id не в ответе (см. TaskResponse) — родитель ещё не
        # синхронизирован, что и подтверждает pending-строка 'create' ниже.
        parent_rows = await _outbox_rows(session, task.id)
        assert len(parent_rows) == 1 and parent_rows[0].status == "pending"

        result = await subtask_service.create_subtask(
            session, user, SubtaskCreate(task_id=task.id, title="Sub2", description="d"),
        )

        rows = await _outbox_rows_for(session, "subtask", result.id)
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].depends_on_event_id == parent_rows[0].id


async def test_dispatch_outbox_row_swallows_apply_async_failure(caplog):
    """apply_async может бросить исключение (брокер недоступен): dispatch_outbox_row обязана перехватить его, иначе уже
    состоявшееся изменение превратилось бы в ложный 500. mock_outbox_dispatch подменяет функцию целиком,
    поэтому тестируется настоящая dispatch_outbox_row.
    """
    from src.tasks.crm_outbox_tasks import dispatch_outbox_row

    row = CrmOutbox(
        id=1, aggregate_type="task", aggregate_id=1, operation="create", shard="shard_0",
        payload={"title": "X", "description": "d", "completed": False, "project": None},
    )
    with patch(
        "src.tasks.crm_outbox_tasks.process_outbox_row.apply_async",
        side_effect=Exception("Celery broker unreachable"),
    ):
        await dispatch_outbox_row(row)  # не должно бросить исключение

    # Сбой не проглочен молча: он залогирован, и строка остаётся pending для reconcile.
    assert "немедленный диспатч не удался" in caplog.text
    assert "Celery broker unreachable" in caplog.text


async def test_dispatch_outbox_row_does_not_block_event_loop():
    """apply_async — синхронный сетевой вызов; без asyncio.to_thread внутри dispatch_outbox_row он блокирует event loop.
    apply_async имитирует задержку time.sleep() (asyncio.sleep блокировку не воспроизвёл бы).

    Разница — в общем времени gather(): при неблокирующем loop apply_async и тики идут параллельно (≈ 0.5 с), иначе
    последовательно (≈ 0.9 с). Порог 0.8 с рассчитан с запасом на нагрузку при параллельном прогоне.
    """
    from src.tasks.crm_outbox_tasks import dispatch_outbox_row

    row = CrmOutbox(
        id=1, aggregate_type="task", aggregate_id=1, operation="create", shard="shard_0",
        payload={"title": "X", "description": "d", "completed": False, "project": None},
    )

    async def _ticker():
        for _ in range(40):
            await asyncio.sleep(0.01)

    def _blocking_apply_async(*args, **kwargs):
        time.sleep(0.5)

    with patch(
        "src.tasks.crm_outbox_tasks.process_outbox_row.apply_async",
        side_effect=_blocking_apply_async,
    ):
        start = time.perf_counter()
        await asyncio.gather(dispatch_outbox_row(row), _ticker())
        elapsed = time.perf_counter() - start

    assert elapsed < 0.8, f"gather() занял {elapsed:.3f} с — apply_async, похоже, блокирует event loop"


async def test_process_outbox_row_update_calls_task_manager_and_marks_done():
    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="update", status="pending",
            payload={"crm_task_id": 42, "title": "Retried", "description": None, "completed": None, "project": None},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    fake_task_mgr.update_task.assert_called_once_with(
        task_id=42, title="Retried", description=None, completed=None, project=None,
    )
    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "done"
        assert row.attempts == 1


async def test_sync_files_task_reads_current_state_from_db():
    """payload несёт только флаги sync_specification/sync_other_files; пути обработчик читает из БД в момент обработки."""
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(
            title="SyncState", description="d", owner_id=user.id, crm_task_id=42, crm_shard="shard_0",
            specification_path="tasks/1/specification/x.pdf", other_file_paths=["tasks/1/other/y.pdf"],
        )
        session.add(task)
        await session.commit()
        task_id = task.id

        row = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="sync_files", status="pending",
            payload={"crm_task_id": 42, "sync_specification": True, "sync_other_files": True},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    fake_task_mgr.update_task.assert_called_once()
    kwargs = fake_task_mgr.update_task.call_args.kwargs
    assert kwargs["task_id"] == 42
    assert str(kwargs["specification_abs_path"]).replace("\\", "/").endswith(
        "tasks/1/specification/x.pdf"
    )
    assert kwargs["clear_specification"] is False
    assert len(kwargs["other_file_abs_paths"]) == 1

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "done"


async def test_sync_files_task_ignores_insertion_order_uses_current_db_state():
    """У задачи две pending sync_files-строки, но Task.specification_path уже отражает последнее состояние:
    какую бы строку воркер ни обработал, в CRM уйдёт одно и то же актуальное состояние.
    """
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(
            title="OrderIndependent", description="d", owner_id=user.id, crm_task_id=42, crm_shard="shard_0",
            specification_path="tasks/1/specification/new.pdf",
        )
        session.add(task)
        await session.commit()
        task_id = task.id

        older = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="sync_files", status="pending",
            payload={"crm_task_id": 42, "sync_specification": True},
        )
        session.add(older)
        await session.commit()
        older_id = older.id

        newer = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="sync_files", status="pending",
            payload={"crm_task_id": 42, "sync_specification": True},
        )
        session.add(newer)
        await session.commit()

    fake_task_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(older_id)  # обрабатываем СТАРУЮ строку первой

    fake_task_mgr.update_task.assert_called_once()
    kwargs = fake_task_mgr.update_task.call_args.kwargs
    # Старая строка всё равно отправляет АКТУАЛЬНОЕ состояние ("new.pdf"), не то,
    # что могло бы быть снимком на момент её вставки.
    assert str(kwargs["specification_abs_path"]).replace("\\", "/").endswith("new.pdf")

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == older_id))).scalar_one()
        assert row.status == "done"


async def test_sync_files_task_closes_internal_create_dependent_gap():
    """Внутриагрегатная строка create+sync_files (crm_task_id=None в payload): путь читается из БД так же, как crm_task_id,
    поэтому замена файла до первой обработки create не теряется.
    """
    async with async_session_maker() as session:
        user = await _make_user(session)
        # crm_task_id уже проставлен, а specification_path отражает файл, заменённый после вставки sync_files-строки.
        task = Task(
            title="GapClosed", description="d", owner_id=user.id, crm_task_id=99, crm_shard="shard_0",
            specification_path="tasks/1/specification/replaced.pdf",
        )
        session.add(task)
        await session.commit()
        task_id = task.id

        row = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="sync_files", status="pending",
            payload={"crm_task_id": None, "sync_specification": True},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    kwargs = fake_task_mgr.update_task.call_args.kwargs
    assert kwargs["task_id"] == 99  # crm_task_id прочитан из актуальной Task, не из payload (там None)
    assert str(kwargs["specification_abs_path"]).replace("\\", "/").endswith("replaced.pdf")


async def test_update_task_with_null_crm_id_reads_it_from_entity():
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(title="T", description="d", owner_id=user.id, crm_task_id=77, crm_shard="shard_0")
        session.add(task)
        await session.commit()
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=task.id, operation="update", status="pending",
            payload={"crm_task_id": None, "title": "Renamed", "description": None,
                     "completed": None, "project": None},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    kwargs = fake_task_mgr.update_task.call_args.kwargs
    assert kwargs["task_id"] == 77
    assert kwargs["title"] == "Renamed"


async def test_update_subtask_with_null_crm_id_reads_it_from_entity():
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(title="T", description="d", owner_id=user.id, crm_task_id=10, crm_shard="shard_0")
        session.add(task)
        await session.commit()
        subtask = Subtask(title="S", task_id=task.id, crm_subtask_id=55)
        session.add(subtask)
        await session.commit()
        row = CrmOutbox(
            aggregate_type="subtask", aggregate_id=subtask.id, operation="update", status="pending",
            payload={"crm_subtask_id": None, "title": "Renamed", "description": None, "completed": None},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.SubtaskManager", return_value=fake_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    kwargs = fake_mgr.update_subtask.call_args.kwargs
    assert kwargs["subtask_id"] == 55
    assert kwargs["title"] == "Renamed"


async def test_sync_files_task_entity_deleted_is_noop():
    """delete_task удаляет Task синхронно, до обработки строки: задача читается всегда, и её отсутствие — тихий 'done' без обращения к CRM."""
    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=999999, operation="sync_files", status="pending",
            payload={"crm_task_id": 42, "sync_specification": True},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    fake_task_mgr.update_task.assert_not_called()
    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "done"


async def test_sync_files_task_clears_specification_when_current_state_none():
    """sync_specification=True, но specification_path сейчас None (файл удалён): обработчик сам видит «пусто» и чистит поле в CRM."""
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(
            title="ClearedSpec", description="d", owner_id=user.id, crm_task_id=42, crm_shard="shard_0",
            specification_path=None,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

        row = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="sync_files", status="pending",
            payload={"crm_task_id": 42, "sync_specification": True},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    kwargs = fake_task_mgr.update_task.call_args.kwargs
    assert kwargs["specification_abs_path"] is None
    assert kwargs["clear_specification"] is True


async def test_sync_files_task_skips_untouched_slot():
    """sync_other_files нет в payload (событие только про ТЗ): поле «иных документов» в CRM не уходит, даже если в БД оно заполнено."""
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(
            title="UntouchedSlot", description="d", owner_id=user.id, crm_task_id=42, crm_shard="shard_0",
            specification_path="tasks/1/specification/x.pdf",
            other_file_paths=["tasks/1/other/a.pdf", "tasks/1/other/b.pdf"],
        )
        session.add(task)
        await session.commit()
        task_id = task.id

        row = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="sync_files", status="pending",
            payload={"crm_task_id": 42, "sync_specification": True},  # sync_other_files отсутствует
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    kwargs = fake_task_mgr.update_task.call_args.kwargs
    assert kwargs["other_file_abs_paths"] is None  # не тронуто, хотя в БД список непустой


async def test_sync_files_task_file_not_found_is_ordinary_failure():
    """Остаточный FileNotFoundError — обычный сбой: попытка потрачена, строка остаётся pending, last_error заполнен."""
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(
            title="GoneFile", description="d", owner_id=user.id, crm_task_id=42, crm_shard="shard_0",
            specification_path="tasks/1/specification/gone.pdf",
        )
        session.add(task)
        await session.commit()
        task_id = task.id

        row = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="sync_files", status="pending",
            payload={"crm_task_id": 42, "sync_specification": True},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    fake_task_mgr.update_task.side_effect = FileNotFoundError(
        "[Errno 2] No such file or directory: 'tasks/1/specification/gone.pdf'"
    )
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "pending"
        assert row.attempts == 1
        assert "FileNotFoundError" in row.last_error


async def test_sync_files_subtask_reads_current_state_from_db():
    """Симметрично test_sync_files_task_reads_current_state_from_db, для
    _do_sync_files_subtask."""
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(title="Parent", description="d", owner_id=user.id, crm_task_id=1, crm_shard="shard_0")
        session.add(task)
        await session.commit()
        subtask = Subtask(
            title="Sub", description="d", task_id=task.id, crm_subtask_id=77,
            specification_path="subtasks/1/specification/x.pdf", other_file_paths=["subtasks/1/other/y.pdf"],
        )
        session.add(subtask)
        await session.commit()
        subtask_id = subtask.id

        row = CrmOutbox(
            aggregate_type="subtask", aggregate_id=subtask_id, operation="sync_files", status="pending",
            payload={"crm_subtask_id": 77, "sync_specification": True, "sync_other_files": True},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_subtask_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.SubtaskManager", return_value=fake_subtask_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    fake_subtask_mgr.update_subtask.assert_called_once()
    kwargs = fake_subtask_mgr.update_subtask.call_args.kwargs
    assert kwargs["subtask_id"] == 77
    assert str(kwargs["specification_abs_path"]).replace("\\", "/").endswith(
        "subtasks/1/specification/x.pdf"
    )
    assert len(kwargs["other_file_abs_paths"]) == 1

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "done"


async def test_sync_files_subtask_entity_deleted_is_noop():
    """Симметрично test_sync_files_task_entity_deleted_is_noop."""
    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="subtask", aggregate_id=999999, operation="sync_files", status="pending",
            payload={"crm_subtask_id": 77, "sync_specification": True},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_subtask_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.SubtaskManager", return_value=fake_subtask_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    fake_subtask_mgr.update_subtask.assert_not_called()
    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "done"


async def test_process_outbox_row_delete_cascades_subtasks_then_task():
    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="delete", status="pending",
            payload={"crm_task_id": 42, "crm_subtask_ids": [100, 101]},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    fake_subtask_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr), \
         patch("src.tasks.crm_outbox_tasks.SubtaskManager", return_value=fake_subtask_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    assert fake_subtask_mgr.delete_subtask.call_count == 2
    fake_task_mgr.delete_task.assert_called_once_with(42)
    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "done"


async def test_process_outbox_row_already_done_is_noop():
    """Синхронная попытка успела пометить строку 'done' до reconcile — повторного обращения к CRM быть не должно."""
    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="update", status="done",
            payload={"crm_task_id": 42, "title": "X", "description": None, "completed": None, "project": None},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    fake_task_mgr.update_task.assert_not_called()


async def test_process_outbox_row_failure_increments_attempts_and_stays_pending():
    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="update", status="pending", attempts=0,
            payload={"crm_task_id": 42, "title": "X", "description": None, "completed": None, "project": None},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    fake_task_mgr.update_task.side_effect = Exception("still down")
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        from src.tasks.crm_outbox_tasks import _process_outbox_row_async
        await _process_outbox_row_async(outbox_id)

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "pending"
        assert row.attempts == 1


async def test_process_outbox_row_marks_failed_after_max_attempts():
    from src.tasks.crm_outbox_tasks import MAX_ATTEMPTS, _process_outbox_row_async

    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="update", status="pending", attempts=MAX_ATTEMPTS - 1,
            payload={"crm_task_id": 42, "title": "X", "description": None, "completed": None, "project": None},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    fake_task_mgr.update_task.side_effect = Exception("still down")
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        await _process_outbox_row_async(outbox_id)

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "failed"
        assert row.attempts == MAX_ATTEMPTS


async def test_process_outbox_row_create_task_finds_existing_record_instead_of_duplicating():
    """Retry 'create' после сбоя между созданием в CRM и записью crm_task_id не создаёт дубликат: find_task находит запись по Local ID."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(title="Found", description="d", owner_id=user.id, crm_task_id=None, crm_shard="shard_0")
        session.add(task)
        await session.commit()
        task_id = task.id

        row = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="create", shard="shard_0",
            payload={"title": "Found", "description": "d", "completed": False, "project": None},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    fake_task_mgr.find_task.return_value = {"id": "77"}  # уже существует в CRM
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        await _process_outbox_row_async(outbox_id)

    fake_task_mgr.create_task.assert_not_called()
    fake_task_mgr.find_task.assert_called_once_with(task_id)

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "done"
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert task.crm_task_id == 77
        assert task.sync_status == "synced"


async def test_create_task_finds_by_local_id_and_creates_with_local_id():
    """find_task ищет по точному Local ID (= row.aggregate_id), а create_task передаёт тот же local_id при создании записи."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(title="WithLocalId", description="d", owner_id=user.id, crm_task_id=None, crm_shard="shard_0")
        session.add(task)
        await session.commit()
        task_id = task.id

        row = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="create", shard="shard_0",
            payload={
                "title": "WithLocalId", "description": "d", "completed": False, "project": None,
                "creator_email": "alice@example.com",
            },
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    fake_task_mgr.find_task.return_value = None  # не найдено — реальный create_task
    fake_task_mgr.create_task.return_value = {"id": "78"}
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        await _process_outbox_row_async(outbox_id)

    fake_task_mgr.find_task.assert_called_once_with(task_id)
    fake_task_mgr.create_task.assert_called_once_with(
        local_id=task_id, title="WithLocalId", description="d",
        completed=False, project=None, creator_email="alice@example.com",
    )


async def test_create_task_rejects_adopting_crm_id_already_owned_by_another_task():
    """Явная проверка владельца — резервный барьер на случай, если find_task вернёт запись другой локальной задачи (например, ошибка
    конфигурации CRM_TASK_FIELD_LOCAL_ID): «усыновление» отклоняется явной ошибкой.
    """
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    async with async_session_maker() as session:
        user = await _make_user(session)
        owner_task = Task(
            title="Owner", description="d", owner_id=user.id, crm_task_id=77, crm_shard="shard_0",
        )
        adopter_task = Task(
            title="Adopter", description="d2", owner_id=user.id, crm_task_id=None, crm_shard="shard_0",
        )
        session.add_all([owner_task, adopter_task])
        await session.commit()
        adopter_id = adopter_task.id

        row = CrmOutbox(
            aggregate_type="task", aggregate_id=adopter_id, operation="create", shard="shard_0",
            payload={"title": "Adopter", "description": "d2", "completed": False, "project": None},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    fake_task_mgr.find_task.return_value = {"id": "77"}  # find_task "вернул" чужую запись
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        await _process_outbox_row_async(outbox_id)

    fake_task_mgr.create_task.assert_not_called()  # find_task уже что-то "нашёл" — insert не нужен
    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "pending"  # обычный сбой попытки, не done
        assert "усыновление" in row.last_error

        adopter = (await session.execute(select(Task).where(Task.id == adopter_id))).scalar_one()
        assert adopter.crm_task_id is None  # НЕ присвоен чужой crm_id

        owner = (await session.execute(select(Task).where(Task.title == "Owner"))).scalar_one()
        assert owner.crm_task_id == 77  # владелец не тронут


async def test_process_outbox_row_final_commit_integrity_error_marks_failed_with_diagnostics():
    """Unique-индекс срабатывает только на финальном commit (conflicting_owner ничего не видит) — смоделировано monkeypatch AsyncSession.commit.
    Ранее такой commit был вне try/except: транзакция откатывалась целиком и строка вечно оставалась pending.
    Теперь — терминальный 'failed' с понятным last_error.
    """
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(
            title="RaceVictim", description="d", owner_id=user.id, crm_task_id=None, crm_shard="shard_0",
        )
        session.add(task)
        await session.commit()
        task_id = task.id

        row = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="create", shard="shard_0",
            payload={"title": "RaceVictim", "description": "d", "completed": False, "project": None},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    fake_task_mgr.find_task.return_value = None  # conflicting_owner-проверка проходит без возражений
    fake_task_mgr.create_task.return_value = {"id": "500"}

    original_commit = AsyncSession.commit
    calls = {"n": 0}

    async def _commit_raises_once(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError(
                "INSERT", {},
                Exception('duplicate key value violates unique constraint "ix_task_crm_task_id_unique"'),
            )
        return await original_commit(self, *args, **kwargs)

    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr), \
         patch.object(AsyncSession, "commit", new=_commit_raises_once):
        await _process_outbox_row_async(outbox_id)

    # Первый commit упал (перехвачен новым except), второй (внутри него) реально закоммитил.
    assert calls["n"] == 2

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "failed"          # терминальное состояние, не тихий бесконечный retry
        assert row.attempts == 1
        assert "IntegrityError" in row.last_error
        assert "unique constraint" in row.last_error

        refreshed_task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert refreshed_task.sync_status == "failed"  # _refresh_sync_status пересчитан после rollback


async def test_process_outbox_row_create_subtask_reads_fresh_parent_crm_task_id():
    """_do_create_subtask использует актуальный task.crm_task_id из БД, а не payload (родитель при вставке ещё не был синхронизирован)."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(title="Parent", description="d", owner_id=user.id, crm_task_id=42, crm_shard="shard_0")
        session.add(task)
        await session.flush()
        subtask = Subtask(title="Sub", description="d", task_id=task.id, crm_subtask_id=None)
        session.add(subtask)
        await session.commit()
        subtask_id = subtask.id

        row = CrmOutbox(
            aggregate_type="subtask", aggregate_id=subtask_id, operation="create", shard="shard_0",
            payload={"title": "Sub", "description": "d", "completed": False},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_subtask_mgr = AsyncMock()
    fake_subtask_mgr.find_subtask.return_value = None
    fake_subtask_mgr.create_subtask.return_value = {"id": "88"}
    with patch("src.tasks.crm_outbox_tasks.SubtaskManager", return_value=fake_subtask_mgr):
        await _process_outbox_row_async(outbox_id)

    fake_subtask_mgr.find_subtask.assert_called_once_with(subtask_id)
    fake_subtask_mgr.create_subtask.assert_called_once_with(
        parent_item_id=42, local_id=subtask_id, title="Sub", description="d", completed=False, creator_email=None,
    )
    async with async_session_maker() as session:
        subtask = (await session.execute(select(Subtask).where(Subtask.id == subtask_id))).scalar_one()
        assert subtask.crm_subtask_id == 88


async def test_create_subtask_rejects_adopting_crm_id_already_owned_by_another_subtask():
    """Симметрично версии для задач: резервный барьер против «усыновления» чужого crm_id."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(title="Parent", description="d", owner_id=user.id, crm_task_id=42, crm_shard="shard_0")
        session.add(task)
        await session.flush()
        owner_sub = Subtask(title="OwnerSub", description="d", task_id=task.id, crm_subtask_id=77)
        adopter_sub = Subtask(title="AdopterSub", description="d2", task_id=task.id, crm_subtask_id=None)
        session.add_all([owner_sub, adopter_sub])
        await session.commit()
        adopter_id = adopter_sub.id

        row = CrmOutbox(
            aggregate_type="subtask", aggregate_id=adopter_id, operation="create", shard="shard_0",
            payload={"title": "AdopterSub", "description": "d2", "completed": False},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_subtask_mgr = AsyncMock()
    fake_subtask_mgr.find_subtask.return_value = {"id": "77"}
    with patch("src.tasks.crm_outbox_tasks.SubtaskManager", return_value=fake_subtask_mgr):
        await _process_outbox_row_async(outbox_id)

    fake_subtask_mgr.create_subtask.assert_not_called()
    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "pending"
        assert "усыновление" in row.last_error

        adopter = (await session.execute(select(Subtask).where(Subtask.id == adopter_id))).scalar_one()
        assert adopter.crm_subtask_id is None

        owner = (await session.execute(select(Subtask).where(Subtask.title == "OwnerSub"))).scalar_one()
        assert owner.crm_subtask_id == 77


async def test_process_outbox_row_delete_treats_already_absent_record_as_success():
    """CRMRecordNotFoundError — достигнутая цель: строка помечается 'done', а не проваливается вечно."""
    from src.crm.client import CRMRecordNotFoundError
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="delete", status="pending", shard="shard_0",
            payload={"crm_task_id": 42, "crm_subtask_ids": [100]},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake_task_mgr = AsyncMock()
    fake_task_mgr.delete_task.side_effect = CRMRecordNotFoundError("already gone")
    fake_subtask_mgr = AsyncMock()
    fake_subtask_mgr.delete_subtask.side_effect = CRMRecordNotFoundError("already gone")
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr), \
         patch("src.tasks.crm_outbox_tasks.SubtaskManager", return_value=fake_subtask_mgr):
        await _process_outbox_row_async(outbox_id)

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "done"
        assert row.attempts == 1  # не выросло дальше при повторных вызовах — цель достигнута с первого раза


async def test_process_outbox_row_waits_when_dependency_still_pending():
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    async with async_session_maker() as session:
        dep = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="create", status="pending", shard="shard_0",
            payload={"title": "X", "description": "d", "completed": False, "project": None},
        )
        session.add(dep)
        await session.commit()
        dep_id = dep.id

        dependent = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="sync_files", status="pending", shard="shard_0",
            depends_on_event_id=dep_id,
            payload={"crm_task_id": None, "sync_specification": True},
        )
        session.add(dependent)
        await session.commit()
        dependent_id = dependent.id

    fake_task_mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake_task_mgr):
        await _process_outbox_row_async(dependent_id)

    fake_task_mgr.update_task.assert_not_called()
    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == dependent_id))).scalar_one()
        assert row.status == "pending"  # не тронута — ждёт, попыток не потрачено
        assert row.attempts == 0


async def test_process_outbox_row_becomes_blocked_when_dependency_failed():
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    async with async_session_maker() as session:
        dep = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="create", status="failed", shard="shard_0",
            payload={"title": "X", "description": "d", "completed": False, "project": None},
        )
        session.add(dep)
        await session.commit()
        dep_id = dep.id

        dependent = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="sync_files", status="pending", shard="shard_0",
            depends_on_event_id=dep_id,
            payload={"crm_task_id": None, "sync_specification": True},
        )
        session.add(dependent)
        await session.commit()
        dependent_id = dependent.id

    await _process_outbox_row_async(dependent_id)

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == dependent_id))).scalar_one()
        assert row.status == "blocked"


async def test_reconcile_blocked_outbox_unblocks_when_dependency_done():
    from src.tasks.crm_outbox_tasks import _reconcile_blocked_outbox_async

    async with async_session_maker() as session:
        dep = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="create", status="done", shard="shard_0",
            payload={"title": "X", "description": "d", "completed": False, "project": None},
        )
        session.add(dep)
        await session.commit()
        dep_id = dep.id

        blocked = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="sync_files", status="blocked", shard="shard_0",
            depends_on_event_id=dep_id,
            payload={"crm_task_id": None, "sync_specification": True},
        )
        still_blocked = CrmOutbox(
            aggregate_type="task", aggregate_id=2, operation="sync_files", status="blocked", shard="shard_0",
            depends_on_event_id=None,
            payload={"crm_task_id": None, "sync_specification": True},
        )
        session.add_all([blocked, still_blocked])
        await session.commit()
        blocked_id, still_blocked_id = blocked.id, still_blocked.id

    unblocked = await _reconcile_blocked_outbox_async()

    assert blocked_id in unblocked
    assert still_blocked_id not in unblocked
    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == blocked_id))).scalar_one()
        assert row.status == "pending"


async def test_reconcile_dispatches_only_pending_rows_older_than_grace_period():
    from src.tasks.crm_outbox_tasks import _reconcile_pending_outbox_async

    old_pending_id = None
    async with async_session_maker() as session:
        old_pending = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="update", status="pending",
            payload={"crm_task_id": 1, "title": None, "description": None, "completed": None, "project": None},
        )
        fresh_pending = CrmOutbox(
            aggregate_type="task", aggregate_id=2, operation="update", status="pending",
            payload={"crm_task_id": 2, "title": None, "description": None, "completed": None, "project": None},
        )
        already_done = CrmOutbox(
            aggregate_type="task", aggregate_id=3, operation="update", status="done",
            payload={"crm_task_id": 3, "title": None, "description": None, "completed": None, "project": None},
        )
        session.add_all([old_pending, fresh_pending, already_done])
        await session.commit()
        old_pending_id = old_pending.id
        fresh_pending_id = fresh_pending.id

        # Симулируем "старую" строку — искусственно отодвигаем created_at в
        # прошлое (в реальности это происходит естественно, за счёт времени).
        old_pending.created_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
        session.add(old_pending)
        await session.commit()

    dispatched: list[int] = []
    result = await _reconcile_pending_outbox_async(dispatch=dispatched.append)

    assert old_pending_id in result
    assert fresh_pending_id not in result  # моложе грейс-периода — не трогаем
    assert dispatched == result


def test_retry_delay_seconds_is_exponential_with_cap():
    from src.tasks.crm_outbox_tasks import _retry_delay_seconds

    assert _retry_delay_seconds(0) == 0
    assert [_retry_delay_seconds(n) for n in (1, 2, 3, 4)] == [60, 120, 240, 480]
    assert _retry_delay_seconds(5) == 900   # 960 → потолок 900
    assert _retry_delay_seconds(20) == 900


async def _add_pending_row(session, aggregate_id: int, attempts: int, updated_ago_s: int) -> int:
    now = datetime.datetime.now(datetime.timezone.utc)
    row = CrmOutbox(
        aggregate_type="task", aggregate_id=aggregate_id, operation="update", status="pending",
        attempts=attempts, payload={"crm_task_id": aggregate_id},
        created_at=now - datetime.timedelta(hours=1),
        updated_at=now - datetime.timedelta(seconds=updated_ago_s),
    )
    session.add(row)
    await session.commit()
    return row.id


async def test_reconcile_waits_exponential_pause_after_failed_attempts():
    from src.tasks.crm_outbox_tasks import _reconcile_pending_outbox_async

    async with async_session_maker() as session:
        never_tried = await _add_pending_row(session, 1, attempts=0, updated_ago_s=1)
        too_soon = await _add_pending_row(session, 2, attempts=2, updated_ago_s=100)    # нужно ≥120
        waited = await _add_pending_row(session, 3, attempts=2, updated_ago_s=130)
        first_retry_soon = await _add_pending_row(session, 4, attempts=1, updated_ago_s=30)   # нужно ≥60
        first_retry_ok = await _add_pending_row(session, 5, attempts=1, updated_ago_s=61)

    dispatched: list[int] = []
    result = await _reconcile_pending_outbox_async(dispatch=dispatched.append)

    assert set(result) == {never_tried, waited, first_retry_ok}
    assert too_soon not in result and first_retry_soon not in result
    assert dispatched == result


async def _add_stalled_row(session, aggregate_id: int, dispatched_ago_s) -> int:
    """attempts=0 (ни разу не дошла до реальной попытки CRM-вызова — застряла
    на _has_older_unfinished/acquire_slot/зависимости) с заданным dispatched_at."""
    now = datetime.datetime.now(datetime.timezone.utc)
    dispatched_at = None if dispatched_ago_s is None else now - datetime.timedelta(seconds=dispatched_ago_s)
    row = CrmOutbox(
        aggregate_type="task", aggregate_id=aggregate_id, operation="update", status="pending",
        attempts=0, payload={"crm_task_id": aggregate_id},
        created_at=now - datetime.timedelta(hours=1),
        dispatched_at=dispatched_at,
    )
    session.add(row)
    await session.commit()
    return row.id


async def test_reconcile_does_not_redispatch_stalled_row_before_cooldown():
    """Строка attempts=0, недавно поставленная в очередь тем же reconcile (dispatched_at свежий), но всё ещё pending, реально застряла:
    без dispatched_at она переставлялась бы на каждом тике.
    """
    from src.tasks.crm_outbox_tasks import _STALLED_REDISPATCH_COOLDOWN_SECONDS, _reconcile_pending_outbox_async

    async with async_session_maker() as session:
        never_dispatched = await _add_stalled_row(session, 1, dispatched_ago_s=None)
        just_dispatched = await _add_stalled_row(session, 2, dispatched_ago_s=5)
        cooldown_elapsed = await _add_stalled_row(
            session, 3, dispatched_ago_s=_STALLED_REDISPATCH_COOLDOWN_SECONDS + 1,
        )

    dispatched: list[int] = []
    result = await _reconcile_pending_outbox_async(dispatch=dispatched.append)

    assert set(result) == {never_dispatched, cooldown_elapsed}
    assert just_dispatched not in result
    assert dispatched == result


async def test_reconcile_stamps_dispatched_at_on_rows_it_queues():
    from src.tasks.crm_outbox_tasks import _reconcile_pending_outbox_async

    async with async_session_maker() as session:
        row_id = await _add_stalled_row(session, 1, dispatched_ago_s=None)

    before = datetime.datetime.now(datetime.timezone.utc)
    result = await _reconcile_pending_outbox_async(dispatch=lambda _id: None)
    assert row_id in result

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == row_id))).scalar_one()
        assert row.dispatched_at is not None
        assert row.dispatched_at >= before - datetime.timedelta(seconds=5)


async def test_process_outbox_row_stores_last_error_and_clears_it_on_success():
    from src.tasks.crm_outbox_tasks import LAST_ERROR_MAX_LEN, _process_outbox_row_async

    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="update", status="pending",
            payload={"crm_task_id": 42, "title": "X", "description": None, "completed": None, "project": None},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    failing = AsyncMock()
    failing.update_task.side_effect = RuntimeError("boom " + "x" * 5000)
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=failing):
        await _process_outbox_row_async(outbox_id)

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "pending"
        assert row.last_error.startswith("RuntimeError: boom")
        assert len(row.last_error) == LAST_ERROR_MAX_LEN

    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=AsyncMock()):
        await _process_outbox_row_async(outbox_id)

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "done"
        assert row.last_error is None


async def _make_task_with_create_row(title: str = "Racy") -> tuple[int, int]:
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(title=title, description="d", owner_id=user.id, crm_shard="shard_0")
        session.add(task)
        await session.commit()
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=task.id, operation="create", shard="shard_0",
            payload={"title": title, "description": "d", "completed": False, "project": None},
        )
        session.add(row)
        await session.commit()
        return task.id, row.id


async def _delete_task_locally(task_id: int) -> None:
    async with async_session_maker() as session:
        await session.delete(await session.get(Task, task_id))
        await session.commit()


async def test_create_task_compensates_orphan_when_task_deleted_meanwhile():
    """Задача удалена до записи crm_task_id: созданная воркером запись в CRM удаляется сразу; create — done, лишних строк нет."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    task_id, outbox_id = await _make_task_with_create_row()
    await _delete_task_locally(task_id)

    fake = AsyncMock()
    fake.find_task.return_value = None
    fake.create_task.return_value = {"id": "555"}
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake):
        await _process_outbox_row_async(outbox_id)

    fake.delete_task.assert_awaited_once_with(555)
    async with async_session_maker() as session:
        rows = (await session.execute(select(CrmOutbox))).scalars().all()
        assert [(r.operation, r.status) for r in rows] == [("create", "done")]


async def test_create_task_compensation_failure_queues_delete_row():
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    task_id, outbox_id = await _make_task_with_create_row()
    await _delete_task_locally(task_id)

    fake = AsyncMock()
    fake.find_task.return_value = None
    fake.create_task.return_value = {"id": "556"}
    fake.delete_task.side_effect = RuntimeError("CRM down")
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake):
        await _process_outbox_row_async(outbox_id)

    async with async_session_maker() as session:
        rows = (await session.execute(select(CrmOutbox).order_by(CrmOutbox.id))).scalars().all()
        assert [(r.operation, r.status) for r in rows] == [("create", "done"), ("delete", "pending")]
        delete_row = rows[1]
        assert delete_row.aggregate_id == task_id
        assert delete_row.shard == "shard_0"
        assert delete_row.payload == {"crm_task_id": 556, "crm_subtask_ids": []}
        assert "CRM down" in delete_row.last_error


async def test_create_task_does_not_delete_record_it_only_found():
    """find_task нашёл уже существующую запись (не создана этой попыткой) и
    задачи уже нет — удалять её вслепую нельзя."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    task_id, outbox_id = await _make_task_with_create_row()
    await _delete_task_locally(task_id)

    fake = AsyncMock()
    fake.find_task.return_value = {"id": "77"}
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake):
        await _process_outbox_row_async(outbox_id)

    fake.create_task.assert_not_called()
    fake.delete_task.assert_not_called()
    async with async_session_maker() as session:
        rows = (await session.execute(select(CrmOutbox))).scalars().all()
        assert [(r.operation, r.status) for r in rows] == [("create", "done")]


async def test_create_subtask_deleted_before_start_skips_crm_call():
    """Подзадачи уже нет к моменту начала обработки — ранний выход без вызова
    CRM (create не начинался вовсе, компенсация не нужна)."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(title="P", description="d", owner_id=user.id, crm_task_id=42, crm_shard="shard_0")
        session.add(task)
        await session.flush()
        subtask = Subtask(title="S", description="d", task_id=task.id)
        session.add(subtask)
        await session.commit()
        subtask_id = subtask.id
        row = CrmOutbox(
            aggregate_type="subtask", aggregate_id=subtask_id, operation="create", shard="shard_0",
            payload={"title": "S", "description": "d", "completed": False},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    async with async_session_maker() as session:
        await session.delete(await session.get(Subtask, subtask_id))
        await session.commit()

    fake = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.SubtaskManager", return_value=fake):
        await _process_outbox_row_async(outbox_id)
    fake.create_subtask.assert_not_called()
    fake.delete_subtask.assert_not_called()


async def test_create_subtask_waits_for_web_delete_lock_then_compensates():
    """Гонка для подзадачи: веб-сторона держит FOR UPDATE на её строке, воркер
    уже вызвал CRM и ждёт замок; после commit удаления компенсирует сироту."""
    import asyncio

    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(title="P2", description="d", owner_id=user.id, crm_task_id=42, crm_shard="shard_0")
        session.add(task)
        await session.flush()
        subtask = Subtask(title="S2", description="d", task_id=task.id)
        session.add(subtask)
        await session.commit()
        subtask_id = subtask.id
        row = CrmOutbox(
            aggregate_type="subtask", aggregate_id=subtask_id, operation="create", shard="shard_0",
            payload={"title": "S2", "description": "d", "completed": False},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    fake = AsyncMock()
    fake.find_subtask.return_value = None
    fake.create_subtask.return_value = {"id": "902"}
    with patch("src.tasks.crm_outbox_tasks.SubtaskManager", return_value=fake):
        async with async_session_maker() as web:
            await web.execute(select(Subtask).where(Subtask.id == subtask_id).with_for_update())
            worker = asyncio.create_task(_process_outbox_row_async(outbox_id))
            await asyncio.sleep(1.0)
            assert not worker.done()
            fake.create_subtask.assert_awaited_once()
            await web.delete(await web.get(Subtask, subtask_id))
            await web.commit()
        await asyncio.wait_for(worker, timeout=10)

    fake.delete_subtask.assert_awaited_once_with(902)


async def test_create_task_waits_for_web_delete_lock_then_compensates():
    """Гонка целиком: веб-сторона держит FOR UPDATE (как delete_task), воркер ждёт замок и после commit удаления компенсирует сироту."""
    import asyncio

    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    task_id, outbox_id = await _make_task_with_create_row()

    fake = AsyncMock()
    fake.find_task.return_value = None
    fake.create_task.return_value = {"id": "777"}
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake):
        async with async_session_maker() as web:
            await web.execute(select(Task).where(Task.id == task_id).with_for_update())
            worker = asyncio.create_task(_process_outbox_row_async(outbox_id))
            await asyncio.sleep(1.0)
            assert not worker.done()   # воркер ждёт замок строки
            fake.create_task.assert_awaited_once()
            await web.delete(await web.get(Task, task_id))
            await web.commit()
        await asyncio.wait_for(worker, timeout=10)

    fake.delete_task.assert_awaited_once_with(777)


async def test_create_task_worker_lock_makes_web_delete_see_crm_id():
    """Обратный порядок: воркер успевает первым — веб-удаление ждёт его commit и видит записанный crm_task_id."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    task_id, outbox_id = await _make_task_with_create_row()

    fake = AsyncMock()
    fake.find_task.return_value = None
    fake.create_task.return_value = {"id": "778"}
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=fake):
        await _process_outbox_row_async(outbox_id)

    async with async_session_maker() as session:
        task = (
            await session.execute(select(Task).where(Task.id == task_id).with_for_update())
        ).scalar_one()
        assert task.crm_task_id == 778
        assert task.sync_status == "synced"
    fake.delete_task.assert_not_called()


async def _task_and_update_row(attempts: int, sync_status: str = "synced") -> tuple[int, int]:
    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(
            title="T", description="d", owner_id=user.id, crm_task_id=42,
            crm_shard="shard_0", sync_status=sync_status,
        )
        session.add(task)
        await session.commit()
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=task.id, operation="update", status="pending",
            attempts=attempts, shard="shard_0",
            payload={"crm_task_id": 42, "title": "X", "description": None, "completed": None, "project": None},
        )
        session.add(row)
        await session.commit()
        return task.id, row.id


async def test_exhausted_attempts_mark_task_sync_status_failed():
    from src.tasks.crm_outbox_tasks import MAX_ATTEMPTS, _process_outbox_row_async

    task_id, outbox_id = await _task_and_update_row(attempts=MAX_ATTEMPTS - 1)
    failing = AsyncMock()
    failing.update_task.side_effect = Exception("CRM down")
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=failing):
        await _process_outbox_row_async(outbox_id)

    async with async_session_maker() as session:
        assert (await session.get(CrmOutbox, outbox_id)).status == "failed"
        assert (await session.get(Task, task_id)).sync_status == "failed"


async def test_newer_event_of_same_task_is_not_overtaken_by_older_pending_sibling():
    """Запрет на обгон (_has_older_unfinished): более новое событие не начинает попытку, пока старое не завершилось; CRM не вызывается."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    task_id, older_id = await _task_and_update_row(attempts=0, sync_status="pending")
    async with async_session_maker() as session:
        newer = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="update", status="pending",
            attempts=0, shard="shard_0",
            payload={"crm_task_id": 42, "title": "Y", "description": None, "completed": None, "project": None},
        )
        session.add(newer)
        await session.commit()
        newer_id = newer.id
        assert newer_id > older_id  # иначе тест не проверяет то, что задуман

    mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=mgr):
        await _process_outbox_row_async(newer_id)   # older_id всё ещё pending

    mgr.update_task.assert_not_awaited()
    async with async_session_maker() as session:
        row = await session.get(CrmOutbox, newer_id)
        assert row.status == "pending"
        assert row.attempts == 0  # попытка не потрачена — строка просто отложена


async def test_newer_event_waits_while_older_sibling_is_blocked():
    """Тот же запрет — 'blocked' считается незавершённым наравне с 'pending'
    (строка ждёт свою event-зависимость, а не провалилась окончательно)."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    task_id, older_id = await _task_and_update_row(attempts=0, sync_status="pending")
    async with async_session_maker() as session:
        older = await session.get(CrmOutbox, older_id)
        older.status = "blocked"
        newer = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="update", status="pending",
            attempts=0, shard="shard_0",
            payload={"crm_task_id": 42, "title": "Y", "description": None, "completed": None, "project": None},
        )
        session.add(newer)
        await session.commit()
        newer_id = newer.id

    mgr = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=mgr):
        await _process_outbox_row_async(newer_id)

    mgr.update_task.assert_not_awaited()
    async with async_session_maker() as session:
        assert (await session.get(CrmOutbox, newer_id)).status == "pending"


async def test_after_older_event_fails_newer_event_can_proceed_and_sync():
    """Сквозной сценарий (живой баг): более старое событие исчерпывает попытки — sync_status 'failed'; после этого более новое не заблокировано,
    выполняется и пересчитывает статус в 'synced'.
    """
    from src.tasks.crm_outbox_tasks import MAX_ATTEMPTS, _process_outbox_row_async

    task_id, older_id = await _task_and_update_row(attempts=MAX_ATTEMPTS - 1, sync_status="pending")
    async with async_session_maker() as session:
        newer = CrmOutbox(
            aggregate_type="task", aggregate_id=task_id, operation="update", status="pending",
            attempts=0, shard="shard_0",
            payload={"crm_task_id": 42, "title": "Y", "description": None, "completed": None, "project": None},
        )
        session.add(newer)
        await session.commit()
        newer_id = newer.id

    failing = AsyncMock()
    failing.update_task.side_effect = Exception("CRM down")
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=failing):
        await _process_outbox_row_async(older_id)   # 5-я попытка — исчерпаны

    async with async_session_maker() as session:
        assert (await session.get(CrmOutbox, older_id)).status == "failed"
        assert (await session.get(Task, task_id)).sync_status == "failed"

    succeeding = AsyncMock()
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=succeeding):
        await _process_outbox_row_async(newer_id)   # older уже терминален — теперь можно

    succeeding.update_task.assert_awaited_once()
    async with async_session_maker() as session:
        assert (await session.get(CrmOutbox, newer_id)).status == "done"
        assert (await session.get(Task, task_id)).sync_status == "synced"


async def test_non_final_failure_keeps_task_sync_status():
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    task_id, outbox_id = await _task_and_update_row(attempts=0)
    failing = AsyncMock()
    failing.update_task.side_effect = Exception("CRM down")
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=failing):
        await _process_outbox_row_async(outbox_id)

    async with async_session_maker() as session:
        assert (await session.get(Task, task_id)).sync_status == "synced"


async def test_exhausted_delete_does_not_touch_missing_aggregate():
    """delete: агрегата уже нет — исчерпание попыток не должно падать."""
    from src.tasks.crm_outbox_tasks import MAX_ATTEMPTS, _process_outbox_row_async

    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=999, operation="delete", status="pending",
            attempts=MAX_ATTEMPTS - 1, payload={"crm_task_id": 5, "crm_subtask_ids": []},
        )
        session.add(row)
        await session.commit()
        outbox_id = row.id

    failing = AsyncMock()
    failing.delete_task.side_effect = Exception("CRM down")
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=failing):
        await _process_outbox_row_async(outbox_id)

    async with async_session_maker() as session:
        assert (await session.get(CrmOutbox, outbox_id)).status == "failed"


async def test_successful_retry_restores_failed_sync_status():
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    task_id, outbox_id = await _task_and_update_row(attempts=0, sync_status="pending")
    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=AsyncMock()):
        await _process_outbox_row_async(outbox_id)

    async with async_session_maker() as session:
        assert (await session.get(CrmOutbox, outbox_id)).status == "done"
        assert (await session.get(Task, task_id)).sync_status == "synced"


async def test_concurrent_pending_events_do_not_mark_synced_prematurely():
    """Два параллельных update, оба pending: если #1 завершился первым, sync_status не должен стать 'synced', пока #2 не выполнено."""
    from src.tasks.crm_outbox_tasks import _process_outbox_row_async

    async with async_session_maker() as session:
        user = await _make_user(session)
        task = Task(
            title="T", description="d", owner_id=user.id, crm_task_id=42,
            crm_shard="shard_0", sync_status="pending",
        )
        session.add(task)
        await session.commit()
        payload = {"crm_task_id": 42, "title": "X", "description": None, "completed": None, "project": None}
        row1 = CrmOutbox(
            aggregate_type="task", aggregate_id=task.id, operation="update", status="pending",
            attempts=0, shard="shard_0", payload=payload,
        )
        row2 = CrmOutbox(
            aggregate_type="task", aggregate_id=task.id, operation="update", status="pending",
            attempts=0, shard="shard_0", payload=payload,
        )
        session.add_all([row1, row2])
        await session.commit()
        task_id, row1_id, row2_id = task.id, row1.id, row2.id

    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=AsyncMock()):
        await _process_outbox_row_async(row1_id)

    async with async_session_maker() as session:
        assert (await session.get(CrmOutbox, row1_id)).status == "done"
        # row2 всё ещё pending — sync_status НЕ должен стать synced раньше времени.
        assert (await session.get(Task, task_id)).sync_status == "pending"

    with patch("src.tasks.crm_outbox_tasks.TaskManager", return_value=AsyncMock()):
        await _process_outbox_row_async(row2_id)

    async with async_session_maker() as session:
        assert (await session.get(CrmOutbox, row2_id)).status == "done"
        # Оба события выполнены — теперь можно.
        assert (await session.get(Task, task_id)).sync_status == "synced"
