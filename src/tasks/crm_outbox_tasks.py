"""Durable-синхронизация с CRM через outbox (схема — докстринг CrmOutbox в src/task_logic/models.py).

Продюсеры (services/tasks.py, subtasks.py, attachments.py) вставляют строку crm_outbox в одной
транзакции с изменением и после commit вызывают dispatch_outbox_row(). Веб-процесс CRM не вызывает.

- process_outbox_row(outbox_id) — выполняет одну операцию.
- reconcile_pending_outbox() — Beat, раз в минуту: ставит в очередь шарда pending-строки старше
  грейс-периода, у которых прошла пауза после неудачи (_retry_delay_seconds).
- reconcile_blocked_outbox() — Beat, раз в 5 минут: blocked → pending, когда зависимость стала done.

depends_on_event_id бывает межагрегатным (create подзадачи ждёт create задачи) и внутриагрегатным
(update/sync_files ждут create того же агрегата). В таких строках CRM-id в payload равен None,
обработчик читает его из БД. sync_files читает пути файлов из БД «по состоянию», а не из payload.

Порядок событий одной сущности: _has_older_unfinished не даёт событию начать попытку, пока есть
более старое незавершённое; sync_status пересчитывается в _refresh_sync_status.
Гонка «create в процессе — агрегат удалён»: _do_create_* берут FOR NO KEY UPDATE после CRM-вызова
(_lock_entity), при отсутствии агрегата _compensate_orphan удаляет созданную запись в CRM.
Идемпотентность create — find_task/find_subtask по Local ID. Шардирование, Redlock и rate limit —
src/tasks/sharding.py, crm_shard_lock.py, crm_rate_limit.py.

Celery-задачи синхронные: src.celery_app.run_celery_task() оборачивает async-тело в asyncio.run().
Тесты вызывают _process_outbox_row_async/_reconcile_*_async напрямую (await).
"""

import asyncio
import datetime
import logging
from typing import Any, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.celery_app import celery_app, run_celery_task
from src.crm.client import CRMRecordNotFoundError
from src.crm.crm_config import crm_settings
from src.crm.subtask_service import SubtaskManager
from src.crm.task_service import TaskManager
from src.database import async_session_maker
from src.task_logic.models import CrmOutbox, Subtask, Task
from src.tasks.crm_rate_limit import acquire_slot
from src.tasks.crm_shard_lock import shard_lock
from src.utils.file_utils import UPLOAD_ROOT, parse_other_paths

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
_PENDING_GRACE_SECONDS = 45
# Пауза перед повтором: min(60·2^(attempts-1), 900) с. Реальная пауза ограничена тиком reconcile (60 с).
_RETRY_BASE_DELAY_SECONDS = 60
_RETRY_MAX_DELAY_SECONDS = 900
# Строка с attempts == 0, которая не двигается (лимит CRM, более старое событие, зависимость), иначе
# ставилась бы в очередь на каждом тике. dispatched_at хранит момент последней постановки;
# ниже — минимальный интервал между ними.
_STALLED_REDISPATCH_COOLDOWN_SECONDS = 300
# Предел выборки за тик — не читать весь backlog в память.
_RECONCILE_BATCH_LIMIT = 500
LAST_ERROR_MAX_LEN = 1000
_DEFAULT_SHARD = "shard_0"  # фолбэк для строки с NULL shard


def _retry_delay_seconds(attempts: int) -> int:
    """Сколько секунд строка с данным числом попыток должна отлежаться перед повторной постановкой."""
    if attempts <= 0:
        return 0
    return min(_RETRY_BASE_DELAY_SECONDS * 2 ** (attempts - 1), _RETRY_MAX_DELAY_SECONDS)


def _format_error(exc: BaseException) -> str:
    """Тип и сообщение ошибки для CrmOutbox.last_error, обрезанные по длине."""
    return f"{type(exc).__name__}: {exc}"[:LAST_ERROR_MAX_LEN]


_SYNC_STATUS_MODELS = {"task": Task, "subtask": Subtask}


async def set_aggregate_sync_status(
    db: AsyncSession, row: CrmOutbox, status: str, *, only_from: Optional[tuple[str, ...]] = None,
) -> None:
    """Задаёт Task/Subtask.sync_status агрегата; ничего не делает, если агрегата нет.

    only_from — менять, только если текущее значение среди перечисленных. Используется админ-действием
    «Повторить»; воркер статус пересчитывает через _refresh_sync_status.
    """
    model = _SYNC_STATUS_MODELS.get(row.aggregate_type)
    if model is None:
        return
    entity = await db.get(model, row.aggregate_id)
    if entity is None:
        return
    if only_from is not None and entity.sync_status not in only_from:
        return
    entity.sync_status = status


async def _other_unfinished_events_exist(db: AsyncSession, row: CrmOutbox) -> bool:
    """True, если у сущности есть другая строка (id != row.id) в статусе pending/blocked."""
    other = (
        await db.execute(
            select(CrmOutbox.id)
            .where(
                CrmOutbox.aggregate_type == row.aggregate_type,
                CrmOutbox.aggregate_id == row.aggregate_id,
                CrmOutbox.id != row.id,
                CrmOutbox.status.in_(("pending", "blocked")),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return other is not None


async def _has_older_unfinished(db: AsyncSession, row: CrmOutbox) -> bool:
    """Есть ли у той же сущности более старое (id < row.id) незавершённое событие (pending/blocked).

    Событие не начинает попытку, пока порядок не восстановлен: строка с паузой перед повтором иначе
    пропустила бы вперёд более новое событие, и устаревшие данные перезаписали бы свежие в CRM.
    """
    older = (
        await db.execute(
            select(CrmOutbox.id)
            .where(
                CrmOutbox.aggregate_type == row.aggregate_type,
                CrmOutbox.aggregate_id == row.aggregate_id,
                CrmOutbox.id < row.id,
                CrmOutbox.status.in_(("pending", "blocked")),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return older is not None


async def _refresh_sync_status(db: AsyncSession, row: CrmOutbox, *, failed: bool) -> None:
    """Пересчитывает sync_status агрегата при терминальном исходе строки (done/failed).

    failed=True — 'failed'. Благодаря _has_older_unfinished более новое событие ещё не выполнялось,
    поэтому затирать нечего. failed=False — 'pending', если остались незавершённые события, иначе 'synced'.
    Агрегата может не быть (удалён) — тогда функция ничего не делает.
    """
    model = _SYNC_STATUS_MODELS.get(row.aggregate_type)
    if model is None:
        return
    entity = await db.get(model, row.aggregate_id)
    if entity is None:
        return
    if failed:
        entity.sync_status = "failed"
        return
    entity.sync_status = "pending" if await _other_unfinished_events_exist(db, row) else "synced"


async def has_newer_done_sibling(db: AsyncSession, row: CrmOutbox) -> bool:
    """True, если у сущности есть строка с большим id в статусе 'done'.

    Нужна админ-действию «Повторить»: не повторять failed-строку, которую перекрыло более новое успешное
    событие, иначе устаревшие данные перезапишут свежие.
    """
    newer = (
        await db.execute(
            select(CrmOutbox.id)
            .where(
                CrmOutbox.aggregate_type == row.aggregate_type,
                CrmOutbox.aggregate_id == row.aggregate_id,
                CrmOutbox.id > row.id,
                CrmOutbox.status == "done",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return newer is not None


async def _lock_entity(db: AsyncSession, model, entity_id: int):
    """SELECT ... FOR NO KEY UPDATE строки агрегата; None, если её уже нет.

    Удаление на веб-стороне берёт FOR UPDATE на ту же строку, поэтому запись crm_*_id и удаление
    сериализуются. populate_existing — чтобы не получить устаревший объект из identity map.
    """
    return (
        await db.execute(
            select(model)
            .where(model.id == entity_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _compensate_orphan(db: AsyncSession, row: CrmOutbox, aggregate_type: str, crm_id: int) -> None:
    """Агрегат удалён, пока воркер создавал его в CRM: созданная запись никем не учтена — удаляем сразу.
    При сбое CRM ставим обычную строку 'delete' (коммитится вместе с исходной).
    """
    try:
        if aggregate_type == "task":
            await TaskManager().delete_task(crm_id)
        else:
            await SubtaskManager().delete_subtask(crm_id)
        logger.warning(
            "crm_outbox id=%s: %s id=%s удалён локально во время create — запись crm_id=%s в CRM удалена (компенсация)",
            row.id, aggregate_type, row.aggregate_id, crm_id,
        )
    except CRMRecordNotFoundError:
        logger.info("crm_outbox id=%s: сирота crm_id=%s уже отсутствует в CRM", row.id, crm_id)
    except Exception as exc:
        payload = (
            {"crm_task_id": crm_id, "crm_subtask_ids": []}
            if aggregate_type == "task" else {"crm_subtask_id": crm_id}
        )
        db.add(CrmOutbox(
            aggregate_type=aggregate_type, aggregate_id=row.aggregate_id, operation="delete",
            shard=row.shard, payload=payload, last_error=_format_error(exc),
        ))
        logger.warning(
            "crm_outbox id=%s: компенсирующее удаление crm_id=%s не удалось (%s) — поставлена строка 'delete'",
            row.id, crm_id, exc,
        )


async def dispatch_outbox_row(row: CrmOutbox) -> None:
    """Ставит закоммиченную outbox-строку в очередь её шарда.

    Это оптимизация задержки, а не механизм надёжности: при сбое строку подхватит
    reconcile_pending_outbox. Исключение apply_async перехватывается — строка уже в БД, а 500 ввёл бы
    пользователя в заблуждение. apply_async синхронный и блокирующий, поэтому вызывается через
    asyncio.to_thread, чтобы недоступный Redis не замораживал event loop.
    """
    try:
        await asyncio.to_thread(
            process_outbox_row.apply_async,
            args=[row.id], queue=f"crm_sync.{row.shard or _DEFAULT_SHARD}",
        )
    except Exception as exc:
        logger.warning(
            "crm_outbox id=%s: немедленный диспатч не удался (%s) — строка остаётся "
            "pending, подхватит reconcile_pending_outbox", row.id, exc,
        )


async def _do_create_task(db: AsyncSession, row: CrmOutbox) -> None:
    """Идемпотентный create: сначала ищем запись по Local ID (find_task), чтобы повтор после сбоя между
    созданием в CRM и записью crm_task_id не создал дубликат. sync_status не трогаем.
    """
    payload = row.payload
    mgr = TaskManager()
    found = await mgr.find_task(row.aggregate_id)
    created_here = found is None
    if found is not None:
        crm_id = found.get("id")
    else:
        result = await mgr.create_task(
            local_id=row.aggregate_id,
            title=payload["title"], description=payload["description"],
            completed=payload.get("completed", False), project=payload.get("project"),
            creator_email=payload.get("creator_email"),
        )
        crm_id = result.get("id")
    crm_id = int(crm_id) if crm_id is not None else None

    # Блокировка после CRM-вызова: либо мы успеваем записать crm_task_id и delete_task увидит его,
    # либо удаление опередило нас и запись в CRM — сирота.
    task = await _lock_entity(db, Task, row.aggregate_id)
    if task is None:
        if crm_id is not None and created_here:
            await _compensate_orphan(db, row, "task", crm_id)
        elif crm_id is not None:
            # Запись найдена по Local ID, а не создана этой попыткой — вслепую не удаляем.
            logger.warning(
                "crm_outbox id=%s: task id=%s удалён локально, найденная в CRM запись crm_id=%s НЕ удалена "
                "(создана не этой попыткой) — проверить вручную", row.id, row.aggregate_id, crm_id,
            )
        return
    if crm_id is None:
        raise Exception("CRM create_task retry: no valid id in response")
    # Проверка владельца до присваивания (не полагаемся на IntegrityError, чтобы не оставить транзакцию
    # aborted). Структурно конфликт невозможен — защита от ошибок конфигурации CRM.
    conflicting_owner = (
        await db.execute(
            select(Task.id).where(Task.crm_task_id == crm_id, Task.id != row.aggregate_id)
        )
    ).scalar_one_or_none()
    if conflicting_owner is not None:
        raise Exception(
            f"find_task: crm_id={crm_id} уже принадлежит task id={conflicting_owner} — "
            f"усыновление отклонено, проверить вручную"
        )
    task.crm_task_id = crm_id


async def _do_sync_files_task(db: AsyncSession, row: CrmOutbox) -> None:
    """«По состоянию»: payload несёт только флаги слотов (sync_specification/sync_other_files), пути файлов
    читаются из Task в момент обработки — файл мог быть заменён, пока строка стояла в очереди.
    Если задачи уже нет — выходим без обращения к CRM.
    """
    task = await db.get(Task, row.aggregate_id)
    if task is None:
        return

    payload = row.payload
    crm_task_id = payload.get("crm_task_id")
    if crm_task_id is None:
        # Для зависимой строки create уже done — берём актуальный crm_task_id из Task.
        crm_task_id = task.crm_task_id
        if crm_task_id is None:
            raise Exception("sync_files retry: task still has no crm_task_id")

    sync_spec = payload.get("sync_specification", False)
    await TaskManager().update_task(
        task_id=crm_task_id,
        specification_abs_path=(UPLOAD_ROOT / task.specification_path) if (sync_spec and task.specification_path) else None,
        clear_specification=bool(sync_spec and not task.specification_path),
        other_file_abs_paths=(
            [UPLOAD_ROOT / p for p in parse_other_paths(task.other_file_paths)]
            if payload.get("sync_other_files", False) else None
        ),
    )


async def _do_update_task(db: AsyncSession, row: CrmOutbox) -> None:
    payload = row.payload
    crm_task_id = payload.get("crm_task_id")
    if crm_task_id is None:
        task = await db.get(Task, row.aggregate_id)
        if task is None:
            return
        crm_task_id = task.crm_task_id
        if crm_task_id is None:
            raise Exception("update retry: task still has no crm_task_id")
    await TaskManager().update_task(
        task_id=crm_task_id,
        title=payload.get("title"),
        description=payload.get("description"),
        completed=payload.get("completed"),
        project=payload.get("project"),
    )


async def _do_delete_task(db: AsyncSession, row: CrmOutbox) -> None:
    """Идемпотентное удаление: CRMRecordNotFoundError по отдельному id означает «уже удалено», а не сбой."""
    payload = row.payload
    crm_task_id = payload.get("crm_task_id")
    crm_subtask_ids = payload.get("crm_subtask_ids") or []
    for cid in crm_subtask_ids:
        try:
            await SubtaskManager().delete_subtask(cid)
        except CRMRecordNotFoundError:
            logger.info("crm_outbox id=%s: subtask crm_id=%s уже отсутствует в CRM — цель достигнута", row.id, cid)
    if crm_task_id is not None:
        try:
            await TaskManager().delete_task(crm_task_id)
        except CRMRecordNotFoundError:
            logger.info("crm_outbox id=%s: task crm_id=%s уже отсутствует в CRM — цель достигнута", row.id, crm_task_id)


async def _do_create_subtask(db: AsyncSession, row: CrmOutbox) -> None:
    """Симметрично _do_create_task. Родитель уже синхронизирован (зависимость на его create);
    parent_item_id читаем из актуальной записи Task, а не из payload.
    """
    payload = row.payload
    subtask = await db.get(Subtask, row.aggregate_id)
    if subtask is None:
        # Подзадача удалена локально раньше retry — найти возможную сироту в CRM нечем. Не бросаем:
        # своя строка 'delete' корректно завершится через CRMRecordNotFoundError.
        logger.warning(
            "crm_outbox id=%s: subtask id=%s больше не существует локально — create retry пропущен",
            row.id, row.aggregate_id,
        )
        return
    task = await db.get(Task, subtask.task_id)
    if task is None or task.crm_task_id is None:
        raise Exception("create_subtask retry: parent task not yet synced with CRM")

    mgr = SubtaskManager()
    found = await mgr.find_subtask(row.aggregate_id)
    created_here = found is None
    if found is not None:
        crm_id = found.get("id")
    else:
        result = await mgr.create_subtask(
            parent_item_id=task.crm_task_id, local_id=row.aggregate_id, title=payload["title"],
            description=payload["description"], completed=payload.get("completed", False),
            creator_email=payload.get("creator_email"),
        )
        crm_id = result.get("id")
    crm_id = int(crm_id) if crm_id is not None else None

    subtask = await _lock_entity(db, Subtask, row.aggregate_id)
    if subtask is None:
        if crm_id is not None and created_here:
            await _compensate_orphan(db, row, "subtask", crm_id)
        elif crm_id is not None:
            logger.warning(
                "crm_outbox id=%s: subtask id=%s удалён локально, найденная в CRM запись crm_id=%s НЕ удалена "
                "(создана не этой попыткой) — проверить вручную", row.id, row.aggregate_id, crm_id,
            )
        return
    if crm_id is None:
        raise Exception("CRM create_subtask retry: no valid id in response")
    # Проверка владельца до присваивания — см. _do_create_task.
    conflicting_owner = (
        await db.execute(
            select(Subtask.id).where(Subtask.crm_subtask_id == crm_id, Subtask.id != row.aggregate_id)
        )
    ).scalar_one_or_none()
    if conflicting_owner is not None:
        raise Exception(
            f"find_subtask: crm_id={crm_id} уже принадлежит subtask id={conflicting_owner} — "
            f"усыновление отклонено, проверить вручную"
        )
    subtask.crm_subtask_id = crm_id


async def _do_sync_files_subtask(db: AsyncSession, row: CrmOutbox) -> None:
    """Симметрично _do_sync_files_task."""
    subtask = await db.get(Subtask, row.aggregate_id)
    if subtask is None:
        return

    payload = row.payload
    crm_subtask_id = payload.get("crm_subtask_id")
    if crm_subtask_id is None:
        crm_subtask_id = subtask.crm_subtask_id
        if crm_subtask_id is None:
            raise Exception("sync_files retry: subtask still has no crm_subtask_id")

    sync_spec = payload.get("sync_specification", False)
    await SubtaskManager().update_subtask(
        subtask_id=crm_subtask_id,
        specification_abs_path=(UPLOAD_ROOT / subtask.specification_path) if (sync_spec and subtask.specification_path) else None,
        clear_specification=bool(sync_spec and not subtask.specification_path),
        other_file_abs_paths=(
            [UPLOAD_ROOT / p for p in parse_other_paths(subtask.other_file_paths)]
            if payload.get("sync_other_files", False) else None
        ),
    )


async def _do_update_subtask(db: AsyncSession, row: CrmOutbox) -> None:
    payload = row.payload
    crm_subtask_id = payload.get("crm_subtask_id")
    if crm_subtask_id is None:
        subtask = await db.get(Subtask, row.aggregate_id)
        if subtask is None:
            return
        crm_subtask_id = subtask.crm_subtask_id
        if crm_subtask_id is None:
            raise Exception("update retry: subtask still has no crm_subtask_id")
    await SubtaskManager().update_subtask(
        subtask_id=crm_subtask_id,
        title=payload.get("title"),
        description=payload.get("description"),
        completed=payload.get("completed"),
    )


async def _do_delete_subtask(db: AsyncSession, row: CrmOutbox) -> None:
    """Удаление одной подзадачи (DELETE /subtasks/{id}); та же идемпотентность, что в _do_delete_task."""
    crm_subtask_id = row.payload.get("crm_subtask_id")
    if crm_subtask_id is None:
        return
    try:
        await SubtaskManager().delete_subtask(crm_subtask_id)
    except CRMRecordNotFoundError:
        logger.info("crm_outbox id=%s: subtask crm_id=%s уже отсутствует в CRM — цель достигнута", row.id, crm_subtask_id)


_HANDLERS_BY_AGGREGATE: dict[str, dict[str, Any]] = {
    "task": {
        "create": _do_create_task,
        "sync_files": _do_sync_files_task,
        "update": _do_update_task,
        "delete": _do_delete_task,
    },
    "subtask": {
        "create": _do_create_subtask,
        "sync_files": _do_sync_files_subtask,
        "update": _do_update_subtask,
        "delete": _do_delete_subtask,
    },
}


async def _dependency_status(db: AsyncSession, depends_on_event_id: int) -> Optional[str]:
    return (
        await db.execute(select(CrmOutbox.status).where(CrmOutbox.id == depends_on_event_id))
    ).scalar_one_or_none()


async def _process_outbox_row_async(outbox_id: int) -> None:
    async with async_session_maker() as db:
        row = (
            await db.execute(select(CrmOutbox).where(CrmOutbox.id == outbox_id))
        ).scalar_one_or_none()
        if row is None or row.status == "done":
            # Уже обработана синхронной попыткой.
            return

        if await _has_older_unfinished(db, row):
            # Есть более старое незавершённое событие сущности: попытка не тратится, строку найдёт следующий тик.
            logger.info(
                "crm_outbox id=%s: у сущности есть более старое незавершённое событие — ждём",
                outbox_id,
            )
            return

        if row.depends_on_event_id is not None:
            dep_status = await _dependency_status(db, row.depends_on_event_id)
            if dep_status != "done":
                if dep_status == "failed":
                    row.status = "blocked"
                    await db.commit()
                    logger.warning(
                        "crm_outbox id=%s: событие-зависимость id=%s провалилось — переведена в blocked",
                        outbox_id, row.depends_on_event_id,
                    )
                # Зависимость ещё не done — строку найдёт следующий тик reconcile.
                return

        handlers = _HANDLERS_BY_AGGREGATE.get(row.aggregate_type)
        handler = handlers.get(row.operation) if handlers else None
        if handler is None:
            logger.error("crm_outbox id=%s: неизвестная пара (%r, %r)", outbox_id, row.aggregate_type, row.operation)
            row.status = "failed"
            await db.commit()
            return

        if not await acquire_slot():
            # Лимит запросов к CRM исчерпан — попытка не тратится, строку переставит следующий тик.
            logger.info("crm_outbox id=%s: отложена — лимит запросов к CRM исчерпан", outbox_id)
            return

        row.attempts += 1
        try:
            async with shard_lock(row.shard or _DEFAULT_SHARD):
                await handler(db, row)
            row.status = "done"
            row.last_error = None
            logger.info(
                "crm_outbox id=%s (%s/%s) выполнена, попытка %d",
                outbox_id, row.aggregate_type, row.operation, row.attempts,
            )
        except Exception as exc:
            row.last_error = _format_error(exc)
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "failed"
                logger.error(
                    "crm_outbox id=%s (%s/%s): попытки исчерпаны (%d) — требуется ручное вмешательство: %s",
                    outbox_id, row.aggregate_type, row.operation, row.attempts, exc,
                )
            else:
                logger.warning(
                    "crm_outbox id=%s (%s/%s): попытка %d не удалась, остаётся pending: %s",
                    outbox_id, row.aggregate_type, row.operation, row.attempts, exc,
                )

        if row.status in ("done", "failed"):
            # Единая точка пересчёта sync_status для любой операции.
            await _refresh_sync_status(db, row, failed=(row.status == "failed"))
        try:
            await db.commit()
        except IntegrityError as exc:
            # Частичный уникальный индекс по crm_*_id может сработать здесь: проверка владельца выше не
            # транзакционная, и конкурентный commit другого шарда мог занять тот же id. Это неретраебельный сбой:
            # повтор упадёт так же, поэтому помечаем строку failed. Остальные сбои commit (сеть, пул, таймаут)
            # преходящи — для них строка остаётся pending.
            #
            # db.refresh(row): после rollback() объект expired, а синхронное чтение атрибута в async-сессии
            # бросило бы MissingGreenlet.
            await db.rollback()
            await db.refresh(row)
            row.attempts += 1
            row.status = "failed"
            row.last_error = _format_error(exc)
            logger.error(
                "crm_outbox id=%s (%s/%s): commit упал с IntegrityError — вероятно, "
                "конкурентная гонка на уникальном индексе crm_task_id/crm_subtask_id "
                "помечена failed вместо тихого бесконечного retry: %s",
                outbox_id, row.aggregate_type, row.operation, exc,
            )
            await _refresh_sync_status(db, row, failed=True)
            await db.commit()


@celery_app.task(name="src.tasks.crm_outbox_tasks.process_outbox_row")
def process_outbox_row(outbox_id: int) -> None:
    run_celery_task(_process_outbox_row_async(outbox_id))


async def _reconcile_pending_outbox_async(dispatch=None) -> list[int]:
    """dispatch(outbox_id) ставит строку в очередь повторно; тесты подставляют свою функцию (например,
    list.append), чтобы проверить выбор строк без Celery. Без dispatch очередь выбирается по row.shard.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    threshold = now - datetime.timedelta(seconds=_PENDING_GRACE_SECONDS)
    async with async_session_maker() as db:
        candidates = (
            await db.execute(
                select(
                    CrmOutbox.id, CrmOutbox.shard, CrmOutbox.attempts,
                    CrmOutbox.updated_at, CrmOutbox.dispatched_at,
                )
                .where(CrmOutbox.status == "pending", CrmOutbox.created_at < threshold)
                .order_by(CrmOutbox.created_at)
                .limit(_RECONCILE_BATCH_LIMIT)
            )
        ).all()

        # attempts > 0: берём, когда с последнего UPDATE прошло _retry_delay_seconds(attempts).
        # attempts == 0: ограничено _STALLED_REDISPATCH_COOLDOWN_SECONDS с момента dispatched_at.
        rows = [
            (outbox_id, shard) for outbox_id, shard, attempts, updated_at, dispatched_at in candidates
            if (
                attempts > 0
                and (now - updated_at).total_seconds() >= _retry_delay_seconds(attempts)
            )
            or (
                attempts <= 0
                and (
                    dispatched_at is None
                    or (now - dispatched_at).total_seconds() >= _STALLED_REDISPATCH_COOLDOWN_SECONDS
                )
            )
        ]

        ids = [r[0] for r in rows]
        if ids:
            await db.execute(
                update(CrmOutbox).where(CrmOutbox.id.in_(ids)).values(dispatched_at=now)
            )
            await db.commit()

    for outbox_id, shard in rows:
        if dispatch is not None:
            dispatch(outbox_id)
        else:
            process_outbox_row.apply_async(args=[outbox_id], queue=f"crm_sync.{shard or _DEFAULT_SHARD}")
    if ids:
        logger.info("reconcile_pending_outbox: поставлено в очередь повторно %d строк", len(ids))
    return ids


@celery_app.task(name="src.tasks.crm_outbox_tasks.reconcile_pending_outbox")
def reconcile_pending_outbox() -> None:
    run_celery_task(_reconcile_pending_outbox_async())


async def _reconcile_blocked_outbox_async() -> list[int]:
    """Переводит 'blocked' в 'pending', если зависимость стала 'done'; в очередь строку поставит
    следующий тик reconcile_pending_outbox.
    """
    unblocked: list[int] = []
    async with async_session_maker() as db:
        blocked_rows = (
            await db.execute(select(CrmOutbox).where(CrmOutbox.status == "blocked"))
        ).scalars().all()
        for row in blocked_rows:
            if row.depends_on_event_id is None:
                continue
            dep_status = await _dependency_status(db, row.depends_on_event_id)
            if dep_status == "done":
                row.status = "pending"
                unblocked.append(row.id)
        if unblocked:
            await db.commit()
    if unblocked:
        logger.info("reconcile_blocked_outbox: разблокировано %d строк", len(unblocked))
    return unblocked


@celery_app.task(name="src.tasks.crm_outbox_tasks.reconcile_blocked_outbox")
def reconcile_blocked_outbox() -> None:
    run_celery_task(_reconcile_blocked_outbox_async())


_CLEANUP_BATCH = 1000  # строк за транзакцию — без долгой блокировки таблицы


async def _cleanup_done_outbox_async(retention_days: Optional[int] = None) -> int:
    """Удаляет старые 'done'-строки crm_outbox; failed/blocked/pending не трогает.

    retention_days=None читает актуальный crm_settings.OUTBOX_RETENTION_DAYS. Строки, на которые ещё
    ссылается depends_on_event_id (self-FK, RESTRICT), пропускаются: цепочка create → update удаляется
    с конца за несколько прогонов. Работает батчами по _CLEANUP_BATCH, каждая в своей транзакции;
    возвращает число удалённых строк.
    """
    days = retention_days if retention_days is not None else crm_settings.OUTBOX_RETENTION_DAYS
    threshold = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    dependent = aliased(CrmOutbox)
    total_deleted = 0
    while True:
        async with async_session_maker() as db:
            ids = (
                await db.execute(
                    select(CrmOutbox.id)
                    .where(
                        CrmOutbox.status == "done",
                        CrmOutbox.updated_at < threshold,
                        ~(
                            select(dependent.id)
                            .where(dependent.depends_on_event_id == CrmOutbox.id)
                            .exists()
                        ),
                    )
                    .limit(_CLEANUP_BATCH)
                )
            ).scalars().all()
            if not ids:
                break
            await db.execute(delete(CrmOutbox).where(CrmOutbox.id.in_(ids)))
            await db.commit()
        total_deleted += len(ids)
        if len(ids) < _CLEANUP_BATCH:
            break
    if total_deleted:
        logger.info("cleanup_done_outbox: удалено %d строк старше %d дн.", total_deleted, days)
    return total_deleted


@celery_app.task(name="src.tasks.crm_outbox_tasks.cleanup_done_outbox")
def cleanup_done_outbox() -> int:
    return run_celery_task(_cleanup_done_outbox_async())
