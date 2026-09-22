"""Тесты durable-retry outbox (src/task_logic/models.py::CrmOutbox) — важнейший
критерий: CRM-запрос, теряемый сегодня при падении процесса между db.commit()
и вызовом CRM, должен переживать это падение, потому что строка crm_outbox
коммитится В ТОЙ ЖЕ транзакции, что и основное изменение Task.

Покрытие:
  Продюсер (services/tasks.py/subtasks.py) — здесь только то, что специфично
    именно durable-outbox механике и не покрыто tests/test_task_service.py/
    test_subtask_service.py: sticky-шардирование (id % N) и зависимость
    create подзадачи от ещё не готового create родителя (depends_on_event_id).
    Синхронных попыток вызвать CRM в продюсерах больше нет — веб-процесс CRM
    вообще не вызывает, только вставляет строку и диспатчит её в Celery (см.
    src/tasks/crm_outbox_tasks.py::dispatch_outbox_row, замоканную здесь через
    автоиспользуемую tests/conftest.py::mock_outbox_dispatch).
  Консьюмер (crm_outbox_tasks._process_outbox_row_async) — повторяет операцию
    по сохранённому payload, не читая исходную (возможно, уже несуществующую
    после каскадного удаления) строку Task.
  reconcile — находит только 'pending' строки старше грейс-периода.

Вызывает _process_outbox_row_async/_reconcile_pending_outbox_async напрямую
(await), не через Celery .delay() — см. предупреждение в src/celery_app.py
про asyncio.run() внутри уже работающего event loop.
"""

import datetime
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
    """Async context manager заглушка для src.tasks.crm_shard_lock.shard_lock —
    тесты этого модуля не поднимают реальный Redis (тот же принцип, что и для
    CRM: мокается прямая зависимость, а не внешняя система)."""

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


# ── Продюсер: sticky-шардирование и зависимость подзадачи от родителя ───────────
#
# Payload/pending-статус вставляемых строк и факт диспатча уже подробно
# проверены в tests/test_task_service.py и tests/test_subtask_service.py (там
# же — mock_outbox_dispatch из conftest.py, автоиспользуемый и здесь). Здесь —
# только то, что специфично именно этому модулю: шардирование id % N и
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
    """Родитель ещё не синхронизирован (его собственное 'create' — pending) —
    create подзадачи получает depends_on_event_id, указывающий на строку
    родителя."""
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


# ── dispatch_outbox_row: устойчивость к недоступности Celery/Redis ──────────────

async def test_dispatch_outbox_row_swallows_apply_async_failure(caplog):
    """Ключевой тест: apply_async может бросить исключение (брокер Celery/
    Redis временно недоступен) — dispatch_outbox_row обязана перехватить его
    сама (см. её докстринг), а не дать ему улететь наверх. Строка к этому
    моменту уже закоммичена в PostgreSQL — необработанное исключение здесь
    превратило бы уже состоявшееся успешное создание/изменение в ложный HTTP
    500, будто ничего не сохранилось (mock_outbox_dispatch в conftest.py
    подменяет саму функцию целиком и поэтому не мог бы поймать регрессию
    внутри неё — нужен тест именно настоящей dispatch_outbox_row)."""
    from src.tasks.crm_outbox_tasks import dispatch_outbox_row

    row = CrmOutbox(
        id=1, aggregate_type="task", aggregate_id=1, operation="create", shard="shard_0",
        payload={"title": "X", "description": "d", "completed": False, "project": None},
    )
    with patch(
        "src.tasks.crm_outbox_tasks.process_outbox_row.apply_async",
        side_effect=Exception("Celery broker unreachable"),
    ):
        dispatch_outbox_row(row)  # не должно бросить исключение

    # Сбой не проглочен молча: он залогирован, и строка остаётся pending для reconcile.
    assert "немедленный диспатч не удался" in caplog.text
    assert "Celery broker unreachable" in caplog.text


# ── Консьюмер: _process_outbox_row_async ─────────────────────────────────────────

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


async def test_process_outbox_row_sync_files_reads_paths_from_payload():
    async with async_session_maker() as session:
        row = CrmOutbox(
            aggregate_type="task", aggregate_id=1, operation="sync_files", status="pending",
            payload={
                "crm_task_id": 42,
                "specification_path": "tasks/1/specification/x.pdf",
                "other_file_paths": ["tasks/1/other/y.pdf"],
            },
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
    assert len(kwargs["other_file_abs_paths"]) == 1

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
    """Гонка: синхронная попытка в исходном запросе успела завершиться (пометив
    строку 'done') ДО того, как reconcile успел её подхватить — повторного
    обращения к CRM быть не должно."""
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


# ── Консьюмер: create (идемпотентность через find_task/find_subtask) ────────────

async def test_process_outbox_row_create_task_finds_existing_record_instead_of_duplicating():
    """Retry 'create' после сбоя между «CRM создала запись» и «мы записали
    crm_task_id» не должен создать дубликат — find_task находит уже
    существующую запись первой."""
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
    fake_task_mgr.find_task.assert_called_once_with("Found", "d")

    async with async_session_maker() as session:
        row = (await session.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))).scalar_one()
        assert row.status == "done"
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert task.crm_task_id == 77
        assert task.sync_status == "synced"


async def test_process_outbox_row_create_subtask_reads_fresh_parent_crm_task_id():
    """_do_create_subtask должен использовать актуальный task.crm_task_id из
    БД, а не то, что было в payload на момент вставки (его там и не было —
    родитель тогда ещё не был синхронизирован)."""
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

    fake_subtask_mgr.create_subtask.assert_called_once_with(
        parent_item_id=42, title="Sub", description="d", completed=False,
    )
    async with async_session_maker() as session:
        subtask = (await session.execute(select(Subtask).where(Subtask.id == subtask_id))).scalar_one()
        assert subtask.crm_subtask_id == 88


# ── Консьюмер: идемпотентность delete (фикс известного ограничения) ─────────────

async def test_process_outbox_row_delete_treats_already_absent_record_as_success():
    """Фикс: раньше CRMRecordNotFoundError не отличался от реального сбоя —
    повтор удаления уже отсутствующей в CRM записи 'проваливался' бы вечно.
    Теперь это трактуется как достигнутая цель — строка помечается 'done'."""
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


# ── Консьюмер: depends_on_event_id (зависимости) ─────────────────────────────────

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
            payload={"crm_task_id": None, "specification_path": "x.pdf", "other_file_paths": None},
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
            payload={"crm_task_id": None, "specification_path": "x.pdf", "other_file_paths": None},
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
            payload={"crm_task_id": None, "specification_path": "x.pdf", "other_file_paths": None},
        )
        still_blocked = CrmOutbox(
            aggregate_type="task", aggregate_id=2, operation="sync_files", status="blocked", shard="shard_0",
            depends_on_event_id=None,
            payload={"crm_task_id": None, "specification_path": "y.pdf", "other_file_paths": None},
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


# ── Консьюмер: reconcile — что именно попадает в выборку ────────────────────────

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


# ── reconcile: экспоненциальная пауза перед повтором ────────────────────────────

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


# ── last_error ─────────────────────────────────────────────────────────────────

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


# ── create: гонка «агрегат удалён, пока воркер создавал запись в CRM» ───────────

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
    """Задача удалена локально ДО записи crm_task_id: запись, только что
    созданная воркером в CRM, удаляется сразу; строка create — done, лишних
    outbox-строк нет."""
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
    """find_task нашёл запись эвристикой (не создана этой попыткой) и задачи уже
    нет — удалять её вслепую нельзя (могла принадлежать другой задаче)."""
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
    """Гонка целиком: веб-сторона держит FOR UPDATE на строке задачи (как
    services/tasks.py::delete_task) — воркер, уже вызвавший CRM, ждёт замок и
    после commit удаления компенсирует сироту, а не пишет crm_task_id в
    несуществующую строку."""
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
    """Обратный порядок: воркер успевает первым — веб-удаление (FOR UPDATE)
    ждёт его commit и видит уже записанный crm_task_id (достаточно для
    постановки 'delete'-события)."""
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


# ── sync_status: failed при исчерпании попыток, восстановление при успехе ───────

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
