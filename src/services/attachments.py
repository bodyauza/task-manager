"""Файлы вложений (ТЗ + «Иные документы») — общая логика для задач и подзадач.

routers/task_files.py и routers/subtask_files.py были почти буквальным дублем
друг друга (OCP/DRY-нарушение из аудита): валидация, гонка на JSONB-колонке
(FOR NO KEY UPDATE) и порядок commit/CRM-синхронизации/удаления-с-диска
повторялись дважды с разницей только в модели (Task/Subtask) и именах
CRM-полей. Здесь эта логика живёт один раз, параметризованная
AttachmentConfig — конфигом различий на конкретную сущность.

Комментарии про гонки и порядок операций внутри функций объясняют одну и ту
же механику для обеих сущностей — конфигурации не заменяют их, а параметризуют.
"""

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.user_models import User
from src.realtime import broadcast_task_event
from src.task_logic.models import CrmOutbox, Subtask, Task
from src.tasks.crm_outbox_tasks import dispatch_outbox_row
from src.tasks.sharding import ensure_task_shard
from src.utils.file_utils import (
    MAX_OTHER_FILES,     # лимит файлов в «Иных документах» (10 штук)
    UPLOAD_ROOT,         # абсолютный путь к src/uploads/ (единая точка определения)
    parse_other_paths,   # JSONB (list[str] | None) → list[str]; [] при NULL
    read_and_validate,   # чтение + проверка размера/расширения/MIME
    safe_filename,       # добавление UUID-префикса к имени
    save_file,           # запись на диск, возврат rel-пути
)

logger = logging.getLogger(__name__)


class HasAttachments(Protocol):
    """Общая форма Task/Subtask, на которую опирается этот модуль."""

    id: int
    specification_path: Optional[str]
    other_file_paths: Optional[list[str]]


@dataclass(frozen=True)
class AttachmentConfig:
    """Всё, чем задачи и подзадачи различаются в файловом сценарии."""

    model: type                                           # Task | Subtask
    dir_segment: str                                       # "tasks" | "subtasks" — сегмент пути на диске
    singular_name: str                                     # "task" | "subtask" — для логов
    not_found_detail: str                                  # "Task not found" | "Subtask not found"
    event_type: str                                        # "task_files_updated" | "subtask_files_updated"
    aggregate_type: str                                    # "task" | "subtask" — CrmOutbox.aggregate_type
    crm_id_payload_key: str                                 # "crm_task_id" | "crm_subtask_id" — ключ в payload,
        # который читают _do_sync_files_task/_do_sync_files_subtask (crm_outbox_tasks.py)
    get_crm_id: Callable[[Any], Optional[int]]              # entity -> crm_task_id | crm_subtask_id
    get_shard: Callable[[AsyncSession, Any], Awaitable[str]]
        # entity -> шард агрегата: для Task — сама entity (ensure_task_shard), для Subtask —
        # шард родительской задачи (у Subtask своего crm_shard нет, см. sharding.py)
    event_extra: Callable[[AsyncSession, Any], Awaitable[dict]]
        # -> {"title": ..., "task_id": ...} либо {"title": ..., "task_id": ..., "subtask_id": ..., "task_title": ...}
        # вычисляется ДО commit — после expire атрибуты сущности могут стать недоступны


async def _task_shard(db: AsyncSession, task: Task) -> str:
    return ensure_task_shard(task)


async def _subtask_shard(db: AsyncSession, subtask: Subtask) -> str:
    parent_task = await db.get(Task, subtask.task_id)
    return ensure_task_shard(parent_task)


async def _task_event_extra(db: AsyncSession, task: Task) -> dict:
    return {"title": task.title, "task_id": task.id}


async def _subtask_event_extra(db: AsyncSession, subtask: Subtask) -> dict:
    # task_title нужен для payload "[Task-title]" в чате task-board.js — подзадача сама
    # по себе неоднозначна без указания родительской задачи.
    task_title = (await db.get(Task, subtask.task_id)).title
    return {
        "title": subtask.title,
        "task_id": subtask.task_id,
        "subtask_id": subtask.id,
        "task_title": task_title,
    }


TASK_ATTACHMENTS = AttachmentConfig(
    model=Task,
    dir_segment="tasks",
    singular_name="task",
    not_found_detail="Task not found",
    event_type="task_files_updated",
    aggregate_type="task",
    crm_id_payload_key="crm_task_id",
    get_crm_id=lambda task: task.crm_task_id,
    get_shard=_task_shard,
    event_extra=_task_event_extra,
)

SUBTASK_ATTACHMENTS = AttachmentConfig(
    model=Subtask,
    dir_segment="subtasks",
    singular_name="subtask",
    not_found_detail="Subtask not found",
    event_type="subtask_files_updated",
    aggregate_type="subtask",
    crm_id_payload_key="crm_subtask_id",
    get_crm_id=lambda subtask: subtask.crm_subtask_id,
    get_shard=_subtask_shard,
    event_extra=_subtask_event_extra,
)


async def _enqueue_sync_files(
    db: AsyncSession, config: AttachmentConfig, entity: Any, entity_id: int, crm_id: int, **payload_fields: Any,
) -> CrmOutbox:
    """Строит и добавляет в сессию outbox-строку operation='sync_files' для
    обновления файлового поля УЖЕ существующей в CRM сущности (в отличие от
    sync_files, вставляемой в create-флоу services/tasks.py/subtasks.py — та
    зависит от 'create' того же агрегата через depends_on_event_id, здесь же
    crm_id уже известен, поэтому зависимость не нужна).

    Вызывается ДО db.commit() (в той же транзакции, что и изменение
    specification_path/other_file_paths) — вызывающий код обязан сам
    диспатчить возвращённую строку через dispatch_outbox_row() ПОСЛЕ commit.
    """
    outbox_row = CrmOutbox(
        aggregate_type=config.aggregate_type,
        aggregate_id=entity_id,
        operation="sync_files",
        shard=await config.get_shard(db, entity),
        payload={config.crm_id_payload_key: crm_id, **payload_fields},
    )
    db.add(outbox_row)
    entity.sync_status = "pending"   # вернётся в 'synced' после успешного sync_files в воркере
    return outbox_row


# ════════════════════════════════════════════════════════════
# Атомарное создание сущности с файлами (POST /create-task/, /create-subtask/)
# ════════════════════════════════════════════════════════════

async def validate_files_for_create(
    specification: Optional[UploadFile],
    other_files: Optional[list[UploadFile]],
) -> tuple[Optional[tuple[bytes, str, str]], list[tuple[bytes, str, str]]]:
    """Валидирует spec (если есть) и other_files (если есть) полностью в памяти.

    Вызывается ПЕРВЫМ шагом create-флоу, до создания CRM-записи и до INSERT в БД —
    единственный невалидный файл должен блокировать создание сущности целиком (422),
    ничего не должно быть тронуто. Отдельно от save_files_for_create() ниже: та
    вызывается уже после того, как сущность гарантированно существует, и её сбои
    best-effort, а не 422.

    other_files создаётся "с нуля" (существующих файлов ещё нет), поэтому лимит
    MAX_OTHER_FILES проверяется просто как len(other_files) — в отличие от
    upload_other_files(), где к нему прибавляется количество уже загруженных.
    """
    other_files = other_files or []
    if len(other_files) > MAX_OTHER_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"Превышен лимит файлов ({MAX_OTHER_FILES} штук).",
        )

    spec_validated: Optional[tuple[bytes, str, str]] = None
    if specification is not None:
        content = await read_and_validate(specification)
        spec_validated = (content, safe_filename(specification.filename), specification.filename)

    other_validated = await _validate_other_files(other_files) if other_files else []
    return spec_validated, other_validated


async def save_files_for_create(
    entity_id: int,
    config: AttachmentConfig,
    spec_validated: Optional[tuple[bytes, str, str]],
    other_validated: list[tuple[bytes, str, str]],
) -> tuple[Optional[str], list[str], dict[str, str]]:
    """Best-effort сохранение уже провалидированных файлов на диск.

    Вызывается ПОСЛЕ db.flush() (когда entity_id уже известен) и ДО финального
    db.commit() — сущность к этому моменту уже гарантированно вставлена в сессию.
    Сбой сохранения одного файла (диск, права доступа и т.п.) не поднимается наружу
    и не влияет на соседние файлы — попадает в возвращаемый errors по оригинальному
    имени файла. Если один и тот же оригинальный файл упоминается дважды и оба раза
    падает — вторая ошибка перезапишет первую в словаре (редкий косметический случай,
    не устраняется).
    """
    errors: dict[str, str] = {}

    spec_path: Optional[str] = None
    if spec_validated is not None:
        content, filename, original_name = spec_validated
        dest_dir = UPLOAD_ROOT / config.dir_segment / str(entity_id) / "specification"
        try:
            spec_path = await asyncio.to_thread(save_file, dest_dir, filename, content)
        except Exception as exc:
            logger.error("Create %s %s: spec save failed: %s", config.singular_name, entity_id, exc)
            errors[original_name] = str(exc)

    other_paths: list[str] = []
    if other_validated:
        dest_dir = UPLOAD_ROOT / config.dir_segment / str(entity_id) / "other"

        async def _save_one(content: bytes, filename: str) -> str:
            return await asyncio.to_thread(save_file, dest_dir, filename, content)

        results = await asyncio.gather(
            *[_save_one(content, filename) for content, filename, _ in other_validated],
            return_exceptions=True,
        )
        for (_, _, original_name), result in zip(other_validated, results):
            if isinstance(result, Exception):
                logger.error(
                    "Create %s %s: other-file save failed for '%s': %s",
                    config.singular_name, entity_id, original_name, result,
                )
                errors[original_name] = str(result)
            else:
                other_paths.append(result)

    return spec_path, other_paths, errors


# ════════════════════════════════════════════════════════════
# Техническое задание (одиночный файл)
# ════════════════════════════════════════════════════════════

async def upload_specification(
    db: AsyncSession,
    user: User,
    entity_id: int,
    file: UploadFile,
    config: AttachmentConfig,
) -> dict:
    """Загружает (или заменяет) файл ТЗ. При повторной загрузке старый файл удаляется с диска.

    Синхронизация с CRM — через durable outbox (см. _enqueue_sync_files): веб-процесс
    сам CRM не вызывает, ошибка/недоступность CRM не блокирует и не задерживает сохранение.
    """
    entity = (
        await db.execute(select(config.model).where(config.model.id == entity_id))
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail=config.not_found_detail)

    # read_and_validate: читает байты, проверяет размер (≤100 МБ), расширение и MIME.
    # При нарушении — поднимает HTTPException (413 или 422) до записи на диск.
    content = await read_and_validate(file)

    # safe_filename добавляет uuid-префикс: "tz.pdf" → "a1b2c3d4_tz.pdf"
    filename = safe_filename(file.filename)
    dest_dir = UPLOAD_ROOT / config.dir_segment / str(entity_id) / "specification"

    # Старый путь захватываем до commit; удалим файл только после успешного commit.
    old_path = UPLOAD_ROOT / entity.specification_path if entity.specification_path else None

    # asyncio.to_thread: mkdir+write_bytes — синхронный блокирующий I/O, без выноса
    # в поток он держит event loop занятым на время записи (для больших файлов заметно).
    rel_path = await asyncio.to_thread(save_file, dest_dir, filename, content)

    # Outbox — только если сущность зарегистрирована в CRM (иначе синхронизировать
    # нечего: crm_task_id/crm_subtask_id ещё не известен, а create-флоу сам поставит
    # свою sync_files-строку, см. services/tasks.py::create_task). Вставляется в ТОЙ
    # ЖЕ транзакции, что и entity.specification_path ниже.
    crm_id = config.get_crm_id(entity)
    outbox_row: Optional[CrmOutbox] = None
    if crm_id is not None:
        outbox_row = await _enqueue_sync_files(
            db, config, entity, entity_id, crm_id, specification_path=rel_path,
        )

    # extra захватывается ДО commit — иначе MissingGreenlet после expire (title, task_title и т.д.).
    extra = await config.event_extra(db, entity)
    title = extra.pop("title")
    entity.specification_path = rel_path
    await db.commit()

    if outbox_row is not None:
        dispatch_outbox_row(outbox_row)

    # Удаляем старый файл только после успешного commit: если commit упал бы раньше,
    # старый файл остался бы на диске и путь в БД не изменился бы → нет потери данных.
    # asyncio.to_thread: unlink — синхронный блокирующий I/O, как и save_file/mkdir выше.
    if old_path:
        await asyncio.to_thread(old_path.unlink, missing_ok=True)

    # exclude_user_id не передаётся: broadcast идёт всем, включая актора — актор должен увидеть
    # собственное сообщение в чате (см. task-board.js). action="uploaded" различает
    # формулировку "Добавлены файлы"/"Files added" от "Удалены файлы"/"Files removed" на фронте.
    await broadcast_task_event(
        config.event_type, title, sender_email=user.email, actor_id=user.id, action="uploaded", **extra,
    )

    return {"specification_path": rel_path}  # URL: /uploads/{rel_path}


async def delete_specification(
    db: AsyncSession, user: User, entity_id: int, config: AttachmentConfig,
) -> dict:
    """Удаляет файл ТЗ с диска и обнуляет путь в БД."""
    entity = (
        await db.execute(select(config.model).where(config.model.id == entity_id))
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail=config.not_found_detail)

    if not entity.specification_path:
        # 404: удалять нечего — файл не загружен
        raise HTTPException(status_code=404, detail="Specification file not found")

    # Путь на диске захватываем до commit; удалим файл только после успешного commit —
    # симметрично upload_specification: если commit упадёт, путь в БД не изменится,
    # а файл на диске останется на месте (нет расхождения БД↔диск).
    old_path = UPLOAD_ROOT / entity.specification_path

    crm_id = config.get_crm_id(entity)
    outbox_row: Optional[CrmOutbox] = None
    if crm_id is not None:
        outbox_row = await _enqueue_sync_files(
            db, config, entity, entity_id, crm_id, clear_specification=True,
        )

    extra = await config.event_extra(db, entity)  # захватить до commit — иначе MissingGreenlet после expire
    title = extra.pop("title")
    entity.specification_path = None
    await db.commit()

    if outbox_row is not None:
        dispatch_outbox_row(outbox_row)

    # asyncio.to_thread: unlink — синхронный блокирующий I/O; без выноса в поток
    # он держит event loop занятым на время удаления, как и запись файла в upload_specification.
    await asyncio.to_thread(old_path.unlink, missing_ok=True)

    await broadcast_task_event(
        config.event_type, title, sender_email=user.email, actor_id=user.id, action="deleted", **extra,
    )

    return {"specification_path": None}


# ════════════════════════════════════════════════════════════
# Иные документы (множественные файлы)
# ════════════════════════════════════════════════════════════

async def _validate_other_files(files: list[UploadFile]) -> list[tuple[bytes, str, str]]:
    """Валидирует все файлы параллельно, ничего не сохраняя на диск.

    Возвращает (content, safe_filename, original_filename) для каждого файла —
    original_filename нужен вызывающему коду create-флоу для ключей file_upload_errors,
    upload_other_files его игнорирует.

    return_exceptions=True вместо того чтобы дать gather самому оборвать ожидание на
    первой ошибке: поток ОС, уже занятый magic.from_buffer() для другого файла, всё
    равно не остановить снаружи — он доработает сам по себе, просто впустую. Дожидаемся
    всех результатов и поднимаем первую ошибку сами — так на диске не остаётся частично
    сохранённых файлов (сохранение начинается только после этой проверки).
    """
    async def _validate_one(upload: UploadFile) -> tuple[bytes, str, str]:
        content = await read_and_validate(upload)
        filename = safe_filename(upload.filename)
        return content, filename, upload.filename

    validation_results = await asyncio.gather(
        *[_validate_one(upload) for upload in files], return_exceptions=True
    )
    for result in validation_results:
        if isinstance(result, BaseException):
            raise result
    return validation_results  # после цикла выше — только tuple


async def upload_other_files(
    db: AsyncSession,
    user: User,
    entity_id: int,
    files: list[UploadFile],
    config: AttachmentConfig,
) -> dict:
    """Добавляет файлы в «Иные документы» (максимум MAX_OTHER_FILES суммарно)."""
    # Race condition (lost update) на JSONB-колонке other_file_paths: без блокировки строки
    # между SELECT и последующим UPDATE (entity.other_file_paths = updated ниже) два параллельных
    # запроса к одной и той же сущности читают один и тот же "existing" ещё до commit друг друга —
    # итоговый UPDATE второго запроса молча затирает результат первого:
    #
    #   Запрос A: existing=[], сохраняет a1b2c3d4_doc.pdf → updated=["a1b2c3d4_doc.pdf"] → commit
    #   Запрос B: читал existing=[] ещё до commit A → сохраняет e5f6a801_doc.pdf → updated=["e5f6a801_doc.pdf"] → commit
    #
    # После обоих commit в БД остаётся только ["e5f6a801_doc.pdf"] — путь a1b2c3d4_doc.pdf
    # потерян из JSONB, хотя сам файл остался лежать на диске (не отдаётся, не удаляется при чистке).
    #
    # Устраняется пессимистичной блокировкой строки перед чтением: with_for_update(key_share=True)
    # рендерит FOR NO KEY UPDATE (не FOR UPDATE) — этого достаточно, чтобы сериализовать
    # запись other_file_paths, но НЕ конфликтует с FOR KEY SHARE, которую PostgreSQL
    # автоматически берёт на родительскую задачу при INSERT подзадачи с FK на неё —
    # параллельное создание подзадач (create_subtask) не блокируется. Обычный FOR UPDATE
    # здесь был бы избыточен: other_file_paths не входит ни в PK, ни в UNIQUE-ограничение.
    entity = (
        await db.execute(
            select(config.model).where(config.model.id == entity_id).with_for_update(key_share=True)
        )
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail=config.not_found_detail)

    existing: list[str] = parse_other_paths(entity.other_file_paths)

    if len(existing) + len(files) > MAX_OTHER_FILES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Превышен лимит файлов ({MAX_OTHER_FILES} штук). "
                f"Уже загружено: {len(existing)}. "
                f"Добавить можно не более {MAX_OTHER_FILES - len(existing)} файл(ов)."
            ),
        )

    dest_dir = UPLOAD_ROOT / config.dir_segment / str(entity_id) / "other"

    # Проход 1: валидируем все файлы до записи на диск (см. _validate_other_files выше).
    validated = await _validate_other_files(files)

    # Проход 2: все файлы валидны — сохраняем на диск параллельно. В отличие от MIME-проверки
    # выше (сериализована общим локом внутри python-magic), запись на диск такого ограничения
    # не имеет — у каждого файла свой UUID-префикс от safe_filename(), коллизий имён нет.
    # gather() уже возвращает list — оборачивать в list() не нужно.
    new_paths: list[str] = await asyncio.gather(
        *[asyncio.to_thread(save_file, dest_dir, filename, content) for content, filename, _ in validated]
    )

    updated = existing + new_paths
    crm_id = config.get_crm_id(entity)                        # захватить до commit
    # Outbox — CRM-поле заменяется целиком (передаём ВСЕ текущие файлы поля, не
    # только new_paths — иначе CRM потеряет ранее загруженные файлы записи); та же
    # транзакция, что и entity.other_file_paths ниже.
    outbox_row: Optional[CrmOutbox] = None
    if crm_id is not None:
        outbox_row = await _enqueue_sync_files(
            db, config, entity, entity_id, crm_id, other_file_paths=updated,
        )
    extra = await config.event_extra(db, entity)               # захватить до commit
    title = extra.pop("title")
    # JSONB: передаём list[str] напрямую; asyncpg сериализует в бинарный JSON при INSERT/UPDATE.
    entity.other_file_paths = updated
    await db.commit()

    if outbox_row is not None:
        dispatch_outbox_row(outbox_row)

    await broadcast_task_event(
        config.event_type, title, sender_email=user.email, actor_id=user.id, action="uploaded", **extra,
    )

    return {"other_file_paths": updated}


async def delete_other_file(
    db: AsyncSession, user: User, entity_id: int, filename: str, config: AttachmentConfig,
) -> dict:
    """Удаляет один файл из «Иных документов» по имени файла."""
    # Тот же lost-update race, что и в upload_other_files — только в обратную сторону: если это
    # удаление racing-ит с параллельной загрузкой нового файла, финальный UPDATE может отменить
    # чужое добавление или воскресить путь, который параллельно удалили. Устраняется той же
    # блокировкой FOR NO KEY UPDATE — разбор выбора между FOR UPDATE и FOR NO KEY UPDATE
    # см. в upload_other_files.
    entity = (
        await db.execute(
            select(config.model).where(config.model.id == entity_id).with_for_update(key_share=True)
        )
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail=config.not_found_detail)

    existing = parse_other_paths(entity.other_file_paths)

    # Ищем в списке путь, чьё имя файла совпадает с запрошенным.
    # Path(p).name отрезает директорию: "tasks/3/other/a1b2_doc.pdf" → "a1b2_doc.pdf".
    target = next((p for p in existing if Path(p).name == filename), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Файл '{filename}' не найден")

    # Путь на диске захватываем до commit; удалим файл только после успешного commit —
    # та же инвариантность, что и в upload_specification/delete_specification: если
    # commit упадёт, JSONB-запись не изменится, а файл на диске останется на месте.
    target_path = UPLOAD_ROOT / target

    updated = [p for p in existing if p != target]
    crm_id = config.get_crm_id(entity)
    # Outbox — [] в payload означает «очистить поле в CRM» (обработчик различает
    # отсутствие ключа от [] — см. _do_sync_files_task/_do_sync_files_subtask),
    # [p1,…] — полную замену содержимого. Та же транзакция, что и other_file_paths ниже.
    outbox_row: Optional[CrmOutbox] = None
    if crm_id is not None:
        outbox_row = await _enqueue_sync_files(
            db, config, entity, entity_id, crm_id, other_file_paths=updated,
        )
    extra = await config.event_extra(db, entity)   # захватить до commit — иначе MissingGreenlet после expire
    title = extra.pop("title")
    # NULL вместо [] при пустом списке: соответствует начальному состоянию колонки.
    entity.other_file_paths = updated if updated else None
    await db.commit()

    if outbox_row is not None:
        dispatch_outbox_row(outbox_row)

    # asyncio.to_thread: unlink — синхронный блокирующий I/O, тот же принцип, что и в save_file.
    await asyncio.to_thread(target_path.unlink, missing_ok=True)

    await broadcast_task_event(
        config.event_type, title, sender_email=user.email, actor_id=user.id, action="deleted", **extra,
    )

    return {"other_file_paths": updated}


# ════════════════════════════════════════════════════════════
# Каскадное удаление файлов при удалении задачи/подзадачи
# ════════════════════════════════════════════════════════════

async def cleanup(entity_id: int, config: AttachmentConfig) -> None:
    """Удаляет директорию uploads/{dir_segment}/{entity_id}/ со всем содержимым.

    Вызывается ПОСЛЕ db.commit(), когда сущность уже удалена из БД (и для задачи —
    PostgreSQL CASCADE уже удалил подзадачи). shutil.rmtree: рекурсивное удаление;
    ignore_errors=True — не падает, если директория не существует (сущность без файлов).

    asyncio.to_thread: shutil.rmtree — синхронный блокирующий I/O по дереву каталогов;
    без выноса в поток удаление задачи с большим деревом подзадач/файлов держало бы
    event loop занятым на всё время обхода файловой системы, замораживая остальные
    запросы приложения — тот же принцип, что и у save_file/unlink в этом модуле.
    """
    entity_dir = UPLOAD_ROOT / config.dir_segment / str(entity_id)
    await asyncio.to_thread(shutil.rmtree, entity_dir, ignore_errors=True)
    logger.info("Cleaned up files for %s %s", config.singular_name, entity_id)
