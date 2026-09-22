"""Durable retry для CRM-вызовов, теряющихся при падении процесса между
db.commit() и вызовом CRM — src/task_logic/models.py::CrmOutbox, докстринг там
же объясняет схему целиком. Продюсеры — src/services/tasks.py,
src/services/subtasks.py (create_*/update_*/delete_*) и
src/services/attachments.py (upload/delete-функции) — вставляют строку в той
же транзакции, что и основное изменение, и сразу после db.commit() вызывают
dispatch_outbox_row(row) (см. ниже), чтобы Celery-воркер подхватил её
практически немедленно. Сам CRM-вызов веб-процесс больше не выполняет НИКОГДА —
только Celery-воркер, здесь же:

- process_outbox_row(outbox_id) — выполняет одну конкретную операцию
  (вызывается и через dispatch_outbox_row сразу после commit, и повторно через
  reconcile_pending_outbox ниже).
- reconcile_pending_outbox() — Celery Beat, раз в минуту (см. celery_app.py):
  находит строки status='pending' старше грейс-периода (_PENDING_GRACE_SECONDS),
  у которых уже прошла экспоненциальная пауза после прошлой неудачной попытки
  (_retry_delay_seconds, см. ниже), и ставит process_outbox_row в очередь для каждой, в очередь её шарда
  (row.shard — снимок Task.crm_shard на момент вставки строки, не lookup в
  реальном времени, см. докстринг CrmOutbox). Грейс-период нужен, чтобы не
  диспетчеризовать строку повторно, пока её ещё обрабатывает (или вот-вот
  начнёт обрабатывать) задача, поставленная dispatch_outbox_row сразу после
  вставки, — иначе два параллельных process_outbox_row для одной строки могли
  бы отправить в CRM один и тот же запрос дважды.
- reconcile_blocked_outbox() — Celery Beat, раз в 5 минут: переводит
  status='blocked' обратно в 'pending', если событие-зависимость
  (depends_on_event_id) с тех пор стало 'done' — следующий тик
  reconcile_pending_outbox подхватит разблокированную строку как обычную
  pending (её created_at уже старше грейс-периода к этому моменту).

Два вида зависимости depends_on_event_id: межагрегатная (create подзадачи
зависит от create родительской задачи) и внутриагрегатная (sync_files
зависит от create того же агрегата —
на момент вставки sync_files, если create ещё не выполнялся синхронно,
CRM-ID агрегата не известен, payload несёт crm_task_id=None; обработчик в
этом случае читает уже актуальное значение из БД, а не из своего payload —
единственное намеренное исключение из общего принципа «payload самодостаточен»,
подробно прокомментировано в _do_sync_files_task/_do_sync_files_subtask).

Гонка «create в процессе — пользователь удалил агрегат» закрыта на стороне
воркера: _do_create_* берут FOR NO KEY UPDATE на строку агрегата ПОСЛЕ
CRM-вызова (_lock_entity); веб-сторона (services/tasks.py::delete_task,
subtasks.py::delete_subtask) берёт FOR UPDATE на ту же строку и конфликтует с
ним. Если к моменту записи crm_*_id строки уже нет, только что созданная
воркером запись в CRM — сирота: _compensate_orphan удаляет её сразу, а при
сбое CRM ставит в crm_outbox обычную строку 'delete'.

Идемпотентность retry 'create' — src/crm/task_service.py::TaskManager.find_task/
src/crm/subtask_service.py::SubtaskManager.find_subtask (эвристика по
title+description, не гарантия — см. их докстринги).

Шардирование (id % N, sticky)/Redlock/token-bucket — src/tasks/sharding.py,
src/tasks/crm_shard_lock.py, src/tasks/crm_rate_limit.py. Redlock здесь —
дополнительная страховка, не механизм порядка: порядок обеспечивается
топологией деплоя (один celery-worker-shard-N процесс на шард, --pool=solo,
concurrency=1 — см. src/docker-compose.yml).

Celery-задачи синхронные — src.celery_app.run_celery_task() оборачивает
async-тело в свой asyncio.run(), освобождает пул соединений SQLAlchemy и
закрывает общий httpx-клиент CRM сразу после (см. её докстринг — без этого
второй и последующие вызовы в одном и том же Celery-воркере падают
RuntimeError из-за переиспользования asyncpg-соединения/httpx-клиента от
закрытого event loop; обнаружено живым прогоном docker compose up, не
юнит-тестами). В тестах НЕ вызывать process_outbox_row/reconcile_*/.delay()
напрямую как функцию из-под уже работающего event loop pytest-asyncio —
asyncio.run() внутри уже запущенного loop бросает RuntimeError. Тесты вызывают
_process_outbox_row_async(...)/_reconcile_*_async() напрямую (await), минуя
синхронную Celery-обёртку, — см. tests/test_crm_outbox.py. Redis (Redlock,
token-bucket) в тестах не поднимается — acquire_slot/shard_lock патчатся
автоматической фикстурой в самом тестовом файле.
"""

import datetime
import logging
from typing import Any, Optional

from sqlalchemy import delete, select
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
from src.utils.file_utils import UPLOAD_ROOT

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
_PENDING_GRACE_SECONDS = 45
# Пауза перед повтором неудачной строки: min(60·2^(attempts-1), 900) с
# (60 → 120 → 240 → 480 с, потолок 15 мин). Отдельный механизм задержки
# (countdown/ETA) не нужен: updated_at обновляется на каждом UPDATE строки
# (onupdate=func.now()), в т.ч. при учёте очередной попытки. Реальная пауза
# ограничена частотой тика reconcile (60 с) — минимум до минуты.
_RETRY_BASE_DELAY_SECONDS = 60
_RETRY_MAX_DELAY_SECONDS = 900
LAST_ERROR_MAX_LEN = 1000
_DEFAULT_SHARD = "shard_0"  # фолбэк для строки с NULL shard — не должно происходить
                            # у корректно вставленных строк, но не должно и валить
                            # reconcile-цикл целиком из-за одной аномальной строки


def _retry_delay_seconds(attempts: int) -> int:
    """Сколько секунд строка с данным числом уже сделанных попыток должна
    отлежаться в pending перед повторной постановкой в очередь. 0 попыток —
    без паузы (только грейс-период created_at)."""
    if attempts <= 0:
        return 0
    return min(_RETRY_BASE_DELAY_SECONDS * 2 ** (attempts - 1), _RETRY_MAX_DELAY_SECONDS)


def _format_error(exc: BaseException) -> str:
    """Текст для CrmOutbox.last_error: тип + сообщение, обрезано — ответ CRM
    может содержать фрагменты данных задачи, а сообщение — быть произвольно
    длинным."""
    return f"{type(exc).__name__}: {exc}"[:LAST_ERROR_MAX_LEN]


_SYNC_STATUS_MODELS = {"task": Task, "subtask": Subtask}


async def set_aggregate_sync_status(
    db: AsyncSession, row: CrmOutbox, status: str, *, only_from: Optional[tuple[str, ...]] = None,
) -> None:
    """Task.sync_status / Subtask.sync_status агрегата этой outbox-строки
    ('unsynced' | 'pending' | 'synced' | 'failed'). Агрегата уже может не
    быть (удалён) — тогда ничего не делает. only_from — менять только если
    текущее значение среди перечисленных (не затирать, например, 'failed'
    успешным статусом чужой операции). Общая точка для воркера (исчерпание
    попыток / восстановление после успешного повтора) и admin-действия
    «Повторить» (src/admin/outbox_admin.py)."""
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
    """True, если у той же сущности (aggregate_type, aggregate_id) есть ДРУГАЯ
    строка crm_outbox (id != row.id) со статусом 'pending' или 'blocked'.

    Защита от преждевременного 'synced': два параллельных события одной
    сущности (например, update #10 и update #11, оба pending) — если #10
    завершится первым, set_aggregate_sync_status(..., only_from=("failed",
    "pending")) сам по себе безусловно поставил бы 'synced', хотя #11 ещё не
    выполнено. Эта проверка вызывается ПЕРЕД таким вызовом и не даёт пометить
    сущность синхронизированной, пока остаются незавершённые события. 'done'
    здесь не считается 'unfinished' — своё же событие row тоже не в счёт
    (исключено через id != row.id)."""
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


async def _lock_entity(db: AsyncSession, model, entity_id: int):
    """SELECT ... FOR NO KEY UPDATE строки агрегата (Task/Subtask); None, если
    её уже нет (удалена конкурентно). Веб-сторона удаления берёт FOR UPDATE на
    ту же строку — они конфликтуют, поэтому запись crm_*_id и удаление
    сериализуются. populate_existing: объект мог быть загружен в сессию ДО
    CRM-вызова — без него вернулось бы устаревшее состояние из identity map."""
    return (
        await db.execute(
            select(model)
            .where(model.id == entity_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _compensate_orphan(db: AsyncSession, row: CrmOutbox, aggregate_type: str, crm_id: int) -> None:
    """Агрегат удалён локально, пока воркер создавал его в CRM: запись только
    что создана нами и никем не учтена (web-стороне её crm_id на момент
    удаления был неизвестен, delete-событие не ставилось) — удаляем сразу. При
    сбое CRM ставим обычную строку 'delete' в crm_outbox (коммитится вместе с
    исходной строкой), дальше её ведёт штатный retry/reconcile."""
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


def dispatch_outbox_row(row: CrmOutbox) -> None:
    """Ставит только что вставленную (и закоммиченную) outbox-строку в очередь
    её шарда сразу же — вызывается продюсерами (services/tasks.py,
    services/subtasks.py, services/attachments.py) сразу после db.commit(),
    ЗАМЕНЯЯ прежнюю синхронную best-effort попытку вызвать CRM прямо внутри
    HTTP-запроса. CRM-вызов теперь выполняется исключительно в Celery-воркере
    (process_outbox_row), а не в веб-процессе — это и убирает CRM-латентность
    из ответа пользователю, и не требует отдельной синхронной попытки: в
    типовом случае воркер простаивает и подхватывает задачу практически
    мгновенно.

    Не более чем оптимизация задержки — не механизм надёжности: если сам
    apply_async не успеет выполниться (падение процесса между commit и этим
    вызовом) или Celery/Redis временно недоступны, строка остаётся 'pending' и
    её всё равно найдёт и повторно поставит в очередь reconcile_pending_outbox
    (Celery Beat) на следующем тике — см. докстринг модуля.

    Именно поэтому исключение из apply_async (например, брокер Redis временно
    недоступен) здесь ПЕРЕХВАТЫВАЕТСЯ, а не улетает наверх: строка к этому
    моменту уже закоммичена в PostgreSQL — реальная потеря данных невозможна,
    а необработанное исключение здесь превратило бы уже состоявшееся успешное
    изменение (задача/подзадача создана и сохранена) в ложный HTTP 500 для
    пользователя, будто ничего не сохранилось.
    """
    try:
        process_outbox_row.apply_async(args=[row.id], queue=f"crm_sync.{row.shard or _DEFAULT_SHARD}")
    except Exception as exc:
        logger.warning(
            "crm_outbox id=%s: немедленный диспатч не удался (%s) — строка остаётся "
            "pending, подхватит reconcile_pending_outbox", row.id, exc,
        )


# ── Обработчики: Task ────────────────────────────────────────────────────────────

async def _do_create_task(db: AsyncSession, row: CrmOutbox) -> None:
    """Идемпотентный retry: сначала ищем уже созданную запись (find_task,
    эвристика title+description — см. её докстринг), чтобы повторная попытка
    после сбоя между "CRM создала запись" и "мы записали crm_task_id" не
    создала дубликат. Task.crm_task_id/sync_status обновляются здесь же —
    в отличие от update/delete/sync_files, 'create' не может унаследовать
    crm_task_id из своего payload (его ещё не существовало на момент вставки).
    """
    payload = row.payload
    mgr = TaskManager()
    found = await mgr.find_task(payload["title"], payload["description"])
    created_here = found is None
    if found is not None:
        crm_id = found.get("id")
    else:
        result = await mgr.create_task(
            title=payload["title"], description=payload["description"],
            completed=payload.get("completed", False), project=payload.get("project"),
        )
        crm_id = result.get("id")
    crm_id = int(crm_id) if crm_id is not None else None

    # Блокировка ПОСЛЕ CRM-вызова (см. докстринг модуля): либо мы успеваем
    # записать crm_task_id, и конкурентный delete_task дождётся commit'а и
    # увидит его, либо удаление опередило нас — тогда запись в CRM сирота.
    task = await _lock_entity(db, Task, row.aggregate_id)
    if task is None:
        if crm_id is not None and created_here:
            await _compensate_orphan(db, row, "task", crm_id)
        elif crm_id is not None:
            # Запись найдена эвристикой find_task (title+description), а не
            # создана нами в этой попытке — может принадлежать другой задаче;
            # удалять её вслепую нельзя.
            logger.warning(
                "crm_outbox id=%s: task id=%s удалён локально, найденная в CRM запись crm_id=%s НЕ удалена "
                "(создана не этой попыткой) — проверить вручную", row.id, row.aggregate_id, crm_id,
            )
        return
    task.crm_task_id = crm_id
    task.sync_status = "synced" if crm_id is not None else "failed"
    if crm_id is None:
        raise Exception("CRM create_task retry: no valid id in response")


async def _do_sync_files_task(db: AsyncSession, row: CrmOutbox) -> None:
    payload = row.payload
    crm_task_id = payload.get("crm_task_id")
    if crm_task_id is None:
        # 'create' на момент вставки этой строки ещё не выполнялся синхронно
        # (весь create теперь тоже идёт через outbox) — depends_on_event_id
        # гарантирует, что к этому моменту создание уже done, поэтому читаем
        # актуальный crm_task_id из уже обновлённой записи Task, а не из
        # своего (заведомо пустого на момент вставки) payload.
        task = await db.get(Task, row.aggregate_id)
        crm_task_id = task.crm_task_id if task is not None else None
        if crm_task_id is None:
            raise Exception("sync_files retry: task still has no crm_task_id")

    spec_path = payload.get("specification_path")
    # "other_file_paths" отсутствует в payload → None (не трогать это поле в CRM);
    # ключ есть, но [] → явная очистка; ключ есть и непустой список → полная замена.
    # Раньше `payload.get(...) or []` схлопывало отсутствие ключа и [] в одно и то
    # же значение — годилось только для create-флоу (там нечего было чистить), но
    # ломало бы attachments.py::delete_other_file (там [] означает «удалить все
    # файлы», а не «не трогать»).
    other_paths = payload.get("other_file_paths")
    await TaskManager().update_task(
        task_id=crm_task_id,
        specification_abs_path=(UPLOAD_ROOT / spec_path) if spec_path else None,
        clear_specification=payload.get("clear_specification", False),
        other_file_abs_paths=(
            [UPLOAD_ROOT / p for p in other_paths] if other_paths is not None else None
        ),
    )


async def _do_update_task(db: AsyncSession, row: CrmOutbox) -> None:
    payload = row.payload
    await TaskManager().update_task(
        task_id=payload["crm_task_id"],
        title=payload.get("title"),
        description=payload.get("description"),
        completed=payload.get("completed"),
        project=payload.get("project"),
    )


async def _do_delete_task(db: AsyncSession, row: CrmOutbox) -> None:
    """Известное ограничение — исправлено: CRMRecordNotFoundError (запись уже
    отсутствует в CRM, см. client.py) перехватывается ОТДЕЛЬНО для каждого
    удаляемого id — «уже удалено» здесь означает, что цель достигнута, а не
    сбой. Раньше (голый Exception без различения причины) повтор удаления
    после частичного успеха (часть подзадач удалена, остальные — нет) мог
    списывать уже достигнутую цель в очередную неудачную попытку и без конца
    наращивать attempts, даже когда реального сбоя больше нет — см. историю
    этого файла до фикса. Настоящие сбои (сеть, таймаут, невалидный ответ)
    остаются реальным сбоем — CRMRecordNotFoundError их не перехватывает.
    """
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


# ── Обработчики: Subtask ─────────────────────────────────────────────────────────

async def _do_create_subtask(db: AsyncSession, row: CrmOutbox) -> None:
    """Симметрично _do_create_task. Родитель Task должен быть уже
    синхронизирован (depends_on_event_id на create-событие Task гарантирует
    это при межагрегатной зависимости — см. докстринг модуля) — читаем
    parent_item_id из АКТУАЛЬНОЙ записи Task, не из payload (тот же принцип,
    что и в _do_sync_files_task).
    """
    payload = row.payload
    subtask = await db.get(Subtask, row.aggregate_id)
    if subtask is None:
        # Подзадача уже удалена локально (delete_subtask/каскад delete_task
        # опередил этот retry) — известное ограничение, симметричное
        # _do_delete_task: без служебного поля в CRM нет надёжного способа
        # найти и подчистить возможную сироту, оставленную не успевшим
        # завершиться create. Не бросаем — дальше в очереди для этого
        # aggregate_id может стоять свой 'delete', который сам не найдёт что
        # удалять и корректно завершится через CRMRecordNotFoundError выше.
        logger.warning(
            "crm_outbox id=%s: subtask id=%s больше не существует локально — create retry пропущен",
            row.id, row.aggregate_id,
        )
        return
    task = await db.get(Task, subtask.task_id)
    if task is None or task.crm_task_id is None:
        raise Exception("create_subtask retry: parent task not yet synced with CRM")

    mgr = SubtaskManager()
    found = await mgr.find_subtask(payload["title"], payload["description"])
    created_here = found is None
    if found is not None:
        crm_id = found.get("id")
    else:
        result = await mgr.create_subtask(
            parent_item_id=task.crm_task_id, title=payload["title"],
            description=payload["description"], completed=payload.get("completed", False),
        )
        crm_id = result.get("id")
    crm_id = int(crm_id) if crm_id is not None else None

    # См. _do_create_task: блокировка строки ПОСЛЕ CRM-вызова, компенсация сироты.
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
    subtask.crm_subtask_id = crm_id
    subtask.sync_status = "synced" if crm_id is not None else "failed"
    if crm_id is None:
        raise Exception("CRM create_subtask retry: no valid id in response")


async def _do_sync_files_subtask(db: AsyncSession, row: CrmOutbox) -> None:
    payload = row.payload
    crm_subtask_id = payload.get("crm_subtask_id")
    if crm_subtask_id is None:
        subtask = await db.get(Subtask, row.aggregate_id)
        crm_subtask_id = subtask.crm_subtask_id if subtask is not None else None
        if crm_subtask_id is None:
            raise Exception("sync_files retry: subtask still has no crm_subtask_id")

    spec_path = payload.get("specification_path")
    # См. пояснение в _do_sync_files_task выше: отсутствие ключа → None (не
    # трогать), [] → явная очистка, непустой список → полная замена.
    other_paths = payload.get("other_file_paths")
    await SubtaskManager().update_subtask(
        subtask_id=crm_subtask_id,
        specification_abs_path=(UPLOAD_ROOT / spec_path) if spec_path else None,
        clear_specification=payload.get("clear_specification", False),
        other_file_abs_paths=(
            [UPLOAD_ROOT / p for p in other_paths] if other_paths is not None else None
        ),
    )


async def _do_update_subtask(db: AsyncSession, row: CrmOutbox) -> None:
    payload = row.payload
    await SubtaskManager().update_subtask(
        subtask_id=payload["crm_subtask_id"],
        title=payload.get("title"),
        description=payload.get("description"),
        completed=payload.get("completed"),
    )


async def _do_delete_subtask(db: AsyncSession, row: CrmOutbox) -> None:
    """Удаление ОДНОЙ подзадачи напрямую (DELETE /subtasks/{id}) — отдельно от
    _do_delete_task, которая обрабатывает каскадное удаление всех подзадач
    ВМЕСТЕ с родительской задачей. Та же идемпотентность, что и там."""
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
            # Уже обработана синхронной попыткой из исходного запроса раньше,
            # чем эта задача успела выполниться — не повторная отправка в CRM.
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
                # pending/processing/blocked зависимость — эта строка остаётся
                # как есть, следующий тик reconcile найдёт её снова.
                return

        handlers = _HANDLERS_BY_AGGREGATE.get(row.aggregate_type)
        handler = handlers.get(row.operation) if handlers else None
        if handler is None:
            logger.error("crm_outbox id=%s: неизвестная пара (%r, %r)", outbox_id, row.aggregate_type, row.operation)
            row.status = "failed"
            await db.commit()
            return

        if not await acquire_slot():
            # Лимит запросов к CRM исчерпан на эту секунду — не тратим попытку
            # (attempts не растёт); строка остаётся pending, её переставит в
            # очередь ближайший тик reconcile_pending_outbox.
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
                if row.operation != "delete":
                    # Попытки исчерпаны — сама сущность не синхронизирована с
                    # CRM (для delete агрегата уже нет, менять нечего). Виден в
                    # /admin/crm-sync; вернуть в работу — действие «Повторить»
                    # в CRM outbox (src/admin/outbox_admin.py).
                    await set_aggregate_sync_status(db, row, "failed")
                logger.error(
                    "crm_outbox id=%s (%s/%s): попытки исчерпаны (%d) — требуется ручное вмешательство: %s",
                    outbox_id, row.aggregate_type, row.operation, row.attempts, exc,
                )
            else:
                logger.warning(
                    "crm_outbox id=%s (%s/%s): попытка %d не удалась, остаётся pending: %s",
                    outbox_id, row.aggregate_type, row.operation, row.attempts, exc,
                )
        if row.status == "done" and row.operation in ("update", "sync_files"):
            # create сам ставит 'synced' в обработчике; update/sync_files
            # восстанавливают статус после ранее исчерпанных попыток
            # ('failed') или повтора из админки ('pending'). Но только если у
            # сущности не осталось ДРУГИХ незавершённых событий — иначе более
            # раннее из двух параллельных update/sync_files преждевременно
            # пометило бы сущность 'synced', пока более позднее ещё pending.
            if not await _other_unfinished_events_exist(db, row):
                await set_aggregate_sync_status(db, row, "synced", only_from=("failed", "pending"))
        await db.commit()


@celery_app.task(name="src.tasks.crm_outbox_tasks.process_outbox_row")
def process_outbox_row(outbox_id: int) -> None:
    run_celery_task(_process_outbox_row_async(outbox_id))


async def _reconcile_pending_outbox_async(dispatch=None) -> list[int]:
    """dispatch — функция постановки одной строки в очередь повторно, вызывается
    как dispatch(outbox_id) (без аргумента шарда — тесты подставляют свою
    однопараметрическую функцию, например list.append, чтобы проверить сам
    ВЫБОР строк без реального похода в Celery/CRM). Настоящий путь (dispatch
    не передан) сам решает очередь по row.shard, читая его вместе с id одним
    запросом — apply_async(..., queue=...) — а не через dispatch."""
    now = datetime.datetime.now(datetime.timezone.utc)
    threshold = now - datetime.timedelta(seconds=_PENDING_GRACE_SECONDS)
    async with async_session_maker() as db:
        candidates = (
            await db.execute(
                select(CrmOutbox.id, CrmOutbox.shard, CrmOutbox.attempts, CrmOutbox.updated_at)
                .where(CrmOutbox.status == "pending", CrmOutbox.created_at < threshold)
                .order_by(CrmOutbox.created_at)
            )
        ).all()

    # Экспоненциальная пауза после неудачной попытки: строка с attempts > 0
    # берётся, только когда с её последнего UPDATE прошло не меньше
    # _retry_delay_seconds(attempts). Строки без попыток (attempts == 0, в т.ч.
    # отложенные rate-limit'ом) — только грейс-период.
    rows = [
        (outbox_id, shard) for outbox_id, shard, attempts, updated_at in candidates
        if attempts <= 0
        or (now - updated_at).total_seconds() >= _retry_delay_seconds(attempts)
    ]

    ids = [r[0] for r in rows]
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
    """Переводит 'blocked' обратно в 'pending', если событие-зависимость с тех
    пор стало 'done'. Не диспетчеризует напрямую — разблокированная строка
    попадёт в обычную выборку reconcile_pending_outbox на следующем тике
    (created_at у неё уже гарантированно старше грейс-периода)."""
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


_CLEANUP_BATCH = 1000  # строк за одну транзакцию — не держит долгую блокировку
                       # таблицы, пока веб-процесс параллельно вставляет новые строки


async def _cleanup_done_outbox_async(retention_days: Optional[int] = None) -> int:
    """Удаляет старые 'done'-строки crm_outbox — иначе таблица растёт
    бесконечно: каждое изменение задачи/подзадачи добавляет строку, а
    'done'-строки сами по себе никогда не удаляются. failed/blocked/pending
    не трогаются НИКОГДА — это активные записи или требующие внимания.

    retention_days=None читает актуальное crm_settings.OUTBOX_RETENTION_DAYS —
    не кэшируется в module-level константу, тот же приём, что и
    src/tasks/sharding.py::shard_names(count) — тесты передают своё значение
    без monkeypatch/reload.

    Исключение по зависимости: depends_on_event_id — self-FK
    (fk_crm_outbox_depends_on_event_id, alembic 0017, ON DELETE не указан →
    Postgres RESTRICT). Строка, на которую ещё ссылается depends_on_event_id
    какой-то другой (ещё не удалённой) строки, из выборки исключается явно —
    без этого DELETE упал бы с IntegrityError на уровне БД (сама по себе
    защита FK не даёт испортить целостность, но заранее исключать такие строки
    дешевле, чем ловить ошибку транзакции). Цепочка «create → update»
    съедается с конца: пока потомок (например, update, ссылающийся на своё же
    create) не удалён, родитель остаётся занят чужой ссылкой и не попадёт в
    выборку; на следующем прогоне, когда потомка уже нет, родитель удалится
    тоже.

    Батчами по _CLEANUP_BATCH, каждая пачка — отдельная транзакция (свой
    async with async_session_maker()); последняя пачка меньше _CLEANUP_BATCH —
    сигнал остановиться. Возвращает суммарное число удалённых строк.
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
