"""Файлы вложений (ТЗ и «Иные документы») — общая логика для задач и подзадач.

Различия между сущностями параметризует AttachmentConfig.
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
from src.crm.outbox_queries import pending_create_event_id
from src.tasks.crm_outbox_tasks import dispatch_outbox_row
from src.tasks.sharding import ensure_task_shard
from src.utils.file_utils import (
    MAX_OTHER_FILES,
    UPLOAD_ROOT,
    parse_other_paths,
    read_and_validate,
    safe_filename,
    save_file,
)

logger = logging.getLogger(__name__)


class HasAttachments(Protocol):
    """Общая форма Task/Subtask для этого модуля."""

    id: int
    specification_path: Optional[str]
    other_file_paths: Optional[list[str]]


@dataclass(frozen=True)
class AttachmentConfig:
    """Всё, чем задачи и подзадачи различаются в файловом сценарии."""

    model: type
    dir_segment: str
    singular_name: str
    not_found_detail: str
    event_type: str
    aggregate_type: str
    crm_id_payload_key: str  # ключ crm id в payload (читают _do_sync_files_*)
    get_crm_id: Callable[[Any], Optional[int]]
    get_shard: Callable[[AsyncSession, Any], Awaitable[str]]
        # entity -> шард агрегата (у Subtask свой crm_shard отсутствует — берётся шард родительской задачи)
    event_extra: Callable[[AsyncSession, Any], Awaitable[dict]]
        # -> payload WS-события; вычисляется до commit, пока атрибуты не expired


async def _task_shard(db: AsyncSession, task: Task) -> str:
    return ensure_task_shard(task)


async def _subtask_shard(db: AsyncSession, subtask: Subtask) -> str:
    parent_task = await db.get(Task, subtask.task_id)
    return ensure_task_shard(parent_task)


async def _task_event_extra(db: AsyncSession, task: Task) -> dict:
    return {"title": task.title, "task_id": task.id}


async def _subtask_event_extra(db: AsyncSession, subtask: Subtask) -> dict:
    # task_title нужен для чата: подзадача без родительской задачи неоднозначна.
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
    """Добавляет в сессию outbox-строку 'sync_files' для сущности, уже существующей в CRM.

    Вызывается до commit (в одной транзакции с изменением); строку после commit нужно
    отправить через dispatch_outbox_row().
    """
    outbox_row = CrmOutbox(
        aggregate_type=config.aggregate_type,
        aggregate_id=entity_id,
        operation="sync_files",
        shard=await config.get_shard(db, entity),
        payload={config.crm_id_payload_key: crm_id, **payload_fields},
    )
    db.add(outbox_row)
    entity.sync_status = "pending"
    return outbox_row


async def _enqueue_sync_files_pending_create(
    db: AsyncSession, config: AttachmentConfig, entity: Any, entity_id: int, **payload_fields: Any,
) -> Optional[CrmOutbox]:
    """Как _enqueue_sync_files, но для сущности без CRM-id (create ещё не выполнен).

    Строка зависит от 'create' через depends_on_event_id и несёт crm_id=None — воркер прочитает актуальный
    id из БД после create. Если create-события нет (штатно не бывает) — логируем и ничего не ставим,
    чтобы не ронять запрос пользователя.
    """
    create_event_id = await pending_create_event_id(db, config.aggregate_type, entity_id)
    if create_event_id is None:
        logger.error(
            "%s %s: не найдено событие 'create' в outbox — sync_files не поставлена",
            config.singular_name, entity_id,
        )
        return None

    outbox_row = CrmOutbox(
        aggregate_type=config.aggregate_type,
        aggregate_id=entity_id,
        operation="sync_files",
        shard=await config.get_shard(db, entity),
        depends_on_event_id=create_event_id,
        payload={config.crm_id_payload_key: None, **payload_fields},
    )
    db.add(outbox_row)
    entity.sync_status = "pending"
    return outbox_row


# Атомарное создание сущности с файлами (POST /create-task/, /create-subtask/)

async def validate_files_for_create(
    specification: Optional[UploadFile],
    other_files: Optional[list[UploadFile]],
) -> tuple[Optional[tuple[bytes, str, str]], list[tuple[bytes, str, str]]]:
    """Валидирует spec и other_files в памяти — первым шагом create-флоу.

    Единственный невалидный файл блокирует создание целиком (422). other_files создаётся с нуля,
    поэтому лимит MAX_OTHER_FILES — просто len(other_files).
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
    """Best-effort сохранение провалидированных файлов на диск (после flush, до commit).

    Сбой одного файла не влияет на остальные и попадает в возвращаемый errors по оригинальному имени.
    """
    errors: dict[str, str] = {}

    spec_path: Optional[str] = None
    if spec_validated is not None:
        content, filename, original_name = spec_validated
        dest_dir = UPLOAD_ROOT / config.dir_segment / str(entity_id) / "specification"
        try:
            spec_path = await asyncio.to_thread(save_file, dest_dir, filename, content, UPLOAD_ROOT)
        except Exception as exc:
            logger.error("Create %s %s: spec save failed: %s", config.singular_name, entity_id, exc)
            errors[original_name] = str(exc)

    other_paths: list[str] = []
    if other_validated:
        dest_dir = UPLOAD_ROOT / config.dir_segment / str(entity_id) / "other"

        async def _save_one(content: bytes, filename: str) -> str:
            return await asyncio.to_thread(save_file, dest_dir, filename, content, UPLOAD_ROOT)

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


async def delete_orphaned_files(
    specification_path: Optional[str], other_file_paths: Optional[list[str]],
) -> None:
    """Удаляет с диска файлы, записанные в этом запросе, если flush()/commit() упал.

    Файлы пишутся до commit, и при откате ссылки на них в БД не будет никогда. Исключение не поднимает
    (не должно маскировать исходное), только логирует.
    """
    paths = list(other_file_paths or [])
    if specification_path:
        paths.append(specification_path)
    for rel_path in paths:
        try:
            await asyncio.to_thread((UPLOAD_ROOT / rel_path).unlink, missing_ok=True)
        except OSError as exc:
            logger.error(
                "Не удалось удалить осиротевший файл %s после сбоя commit/flush: %s", rel_path, exc,
            )


# Техническое задание (одиночный файл)

async def upload_specification(
    db: AsyncSession,
    user: User,
    entity_id: int,
    file: UploadFile,
    config: AttachmentConfig,
) -> dict:
    """Загружает или заменяет файл ТЗ; старый файл удаляется с диска после commit.

    CRM синхронизируется через outbox. FOR NO KEY UPDATE (key_share=True) сериализует конкурентные
    upload/delete одного слота и не конфликтует с FOR KEY SHARE при INSERT подзадачи.
    """
    entity = (
        await db.execute(
            select(config.model).where(config.model.id == entity_id).with_for_update(key_share=True)
        )
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail=config.not_found_detail)

    # read_and_validate проверяет размер, расширение и MIME до записи на диск (413/422).
    content = await read_and_validate(file)

    filename = safe_filename(file.filename)
    dest_dir = UPLOAD_ROOT / config.dir_segment / str(entity_id) / "specification"

    # Старый путь захватываем до commit; удаляем файл только после успешного commit.
    old_path = UPLOAD_ROOT / entity.specification_path if entity.specification_path else None

    # Запись вынесена в поток (блокирующий I/O). OSError перехватываем: ничего ещё не закоммичено.
    try:
        rel_path = await asyncio.to_thread(save_file, dest_dir, filename, content, UPLOAD_ROOT)
    except OSError as exc:
        logger.error("%s %s: spec save failed: %s", config.singular_name, entity_id, exc)
        raise HTTPException(status_code=500, detail="Не удалось сохранить файл на диск")

    # Outbox вставляется в той же транзакции. Если CRM-id ещё нет — строка зависит от 'create'
    # (см. _enqueue_sync_files_pending_create).
    crm_id = config.get_crm_id(entity)
    # sync_specification — флаг «слот ТЗ затронут»; путь воркер читает из БД.
    if crm_id is not None:
        outbox_row = await _enqueue_sync_files(
            db, config, entity, entity_id, crm_id, sync_specification=True,
        )
    else:
        outbox_row = await _enqueue_sync_files_pending_create(
            db, config, entity, entity_id, sync_specification=True,
        )

    # extra захватывается до commit — иначе MissingGreenlet после expire.
    extra = await config.event_extra(db, entity)
    title = extra.pop("title")
    entity.specification_path = rel_path
    # commit мог упасть после записи файла: откат оставит файл сиротой, удаляем его. old_path не трогаем.
    try:
        await db.commit()
    except Exception:
        await delete_orphaned_files(rel_path, None)
        raise

    if outbox_row is not None:
        await dispatch_outbox_row(outbox_row)

    # Старый файл удаляем только после успешного commit (unlink в потоке).
    if old_path:
        await asyncio.to_thread(old_path.unlink, missing_ok=True)

    # exclude_user_id не передаётся: актор тоже видит событие в чате.
    await broadcast_task_event(
        config.event_type, title, sender_email=user.email, actor_id=user.id, action="uploaded", **extra,
    )

    return {"specification_path": rel_path}


async def delete_specification(
    db: AsyncSession, user: User, entity_id: int, config: AttachmentConfig,
) -> dict:
    """Удаляет файл ТЗ с диска и обнуляет путь в БД (FOR NO KEY UPDATE — как в upload_specification)."""
    entity = (
        await db.execute(
            select(config.model).where(config.model.id == entity_id).with_for_update(key_share=True)
        )
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail=config.not_found_detail)

    if not entity.specification_path:
        raise HTTPException(status_code=404, detail="Specification file not found")

    # Путь захватываем до commit; файл удаляем только после успешного commit.
    old_path = UPLOAD_ROOT / entity.specification_path

    # sync_specification — флаг «слот затронут»; воркер увидит пустой путь и очистит поле в CRM.
    crm_id = config.get_crm_id(entity)
    if crm_id is not None:
        outbox_row = await _enqueue_sync_files(
            db, config, entity, entity_id, crm_id, sync_specification=True,
        )
    else:
        # CRM-id ещё нет — зависимая от 'create' строка (см. _enqueue_sync_files_pending_create).
        outbox_row = await _enqueue_sync_files_pending_create(
            db, config, entity, entity_id, sync_specification=True,
        )

    extra = await config.event_extra(db, entity)
    title = extra.pop("title")
    entity.specification_path = None
    await db.commit()

    if outbox_row is not None:
        await dispatch_outbox_row(outbox_row)

    await asyncio.to_thread(old_path.unlink, missing_ok=True)

    await broadcast_task_event(
        config.event_type, title, sender_email=user.email, actor_id=user.id, action="deleted", **extra,
    )

    return {"specification_path": None}


# Иные документы (множественные файлы)

async def _validate_other_files(files: list[UploadFile]) -> list[tuple[bytes, str, str]]:
    """Параллельно валидирует все файлы, ничего не сохраняя.

    Возвращает (content, safe_filename, original_filename); original_filename нужен create-флоу для ключей
    file_upload_errors. return_exceptions=True: дожидаемся всех результатов и поднимаем первую ошибку сами,
    чтобы на диске не оставалось частично сохранённых файлов.
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
    return validation_results


async def upload_other_files(
    db: AsyncSession,
    user: User,
    entity_id: int,
    files: list[UploadFile],
    config: AttachmentConfig,
) -> dict:
    """Добавляет файлы в «Иные документы» (максимум MAX_OTHER_FILES суммарно)."""
    # Блокировка строки (FOR NO KEY UPDATE) защищает JSONB other_file_paths от lost update: два параллельных
    # запроса иначе читают один и тот же existing, и второй UPDATE затирает результат первого, оставляя файл
    # на диске сиротой. key_share=True не конфликтует с FOR KEY SHARE при INSERT подзадачи.
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

    # Проход 1: валидируем все файлы до записи на диск.
    validated = await _validate_other_files(files)

    # Проход 2: сохраняем на диск параллельно (у каждого файла свой UUID-префикс). При сбое любого файла
    # откатываем уже сохранённые и не обновляем entity — иначе они остались бы сиротами.
    save_results = await asyncio.gather(
        *[asyncio.to_thread(save_file, dest_dir, filename, content, UPLOAD_ROOT) for content, filename, _ in validated],
        return_exceptions=True,
    )
    new_paths: list[str] = [r for r in save_results if not isinstance(r, BaseException)]
    save_errors = [r for r in save_results if isinstance(r, BaseException)]
    if save_errors:
        for rel_path in new_paths:
            await asyncio.to_thread((UPLOAD_ROOT / rel_path).unlink, missing_ok=True)
        logger.error(
            "%s %s: не удалось сохранить %d из %d файлов на диск: %s",
            config.singular_name, entity_id, len(save_errors), len(validated), save_errors[0],
        )
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось сохранить {len(save_errors)} из {len(validated)} файл(ов) на диск",
        )

    updated = existing + new_paths
    crm_id = config.get_crm_id(entity)
    # sync_other_files — флаг «слот затронут»; воркер отправит в CRM весь текущий список из БД.
    if crm_id is not None:
        outbox_row = await _enqueue_sync_files(
            db, config, entity, entity_id, crm_id, sync_other_files=True,
        )
    else:
        # CRM-id ещё нет — зависимая от 'create' строка (см. _enqueue_sync_files_pending_create).
        outbox_row = await _enqueue_sync_files_pending_create(
            db, config, entity, entity_id, sync_other_files=True,
        )
    extra = await config.event_extra(db, entity)
    title = extra.pop("title")
    entity.other_file_paths = updated
    # commit мог упасть после записи файлов: откат оставит new_paths сиротами, удаляем их (existing в БД не затронуты).
    try:
        await db.commit()
    except Exception:
        await delete_orphaned_files(None, new_paths)
        raise

    if outbox_row is not None:
        await dispatch_outbox_row(outbox_row)

    await broadcast_task_event(
        config.event_type, title, sender_email=user.email, actor_id=user.id, action="uploaded", **extra,
    )

    return {"other_file_paths": updated}


async def delete_other_file(
    db: AsyncSession, user: User, entity_id: int, filename: str, config: AttachmentConfig,
) -> dict:
    """Удаляет один файл из «Иных документов» по имени."""
    # Та же блокировка, что в upload_other_files: защита от lost update при параллельной загрузке/удалении.
    entity = (
        await db.execute(
            select(config.model).where(config.model.id == entity_id).with_for_update(key_share=True)
        )
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail=config.not_found_detail)

    existing = parse_other_paths(entity.other_file_paths)

    # Path(p).name отрезает директорию: "tasks/3/other/a1b2_doc.pdf" → "a1b2_doc.pdf".
    target = next((p for p in existing if Path(p).name == filename), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Файл '{filename}' не найден")

    # Путь захватываем до commit; файл удаляем только после успешного commit.
    target_path = UPLOAD_ROOT / target

    updated = [p for p in existing if p != target]
    crm_id = config.get_crm_id(entity)
    # sync_other_files — флаг «слот затронут»; воркер сам прочитает актуальный список.
    if crm_id is not None:
        outbox_row = await _enqueue_sync_files(
            db, config, entity, entity_id, crm_id, sync_other_files=True,
        )
    else:
        # CRM-id ещё нет — зависимая от 'create' строка (см. _enqueue_sync_files_pending_create).
        outbox_row = await _enqueue_sync_files_pending_create(
            db, config, entity, entity_id, sync_other_files=True,
        )
    extra = await config.event_extra(db, entity)
    title = extra.pop("title")
    # NULL вместо [] при пустом списке — как начальное состояние колонки.
    entity.other_file_paths = updated if updated else None
    await db.commit()

    if outbox_row is not None:
        await dispatch_outbox_row(outbox_row)

    await asyncio.to_thread(target_path.unlink, missing_ok=True)

    await broadcast_task_event(
        config.event_type, title, sender_email=user.email, actor_id=user.id, action="deleted", **extra,
    )

    return {"other_file_paths": updated}


# Каскадное удаление файлов при удалении задачи/подзадачи

async def cleanup(entity_id: int, config: AttachmentConfig) -> None:
    """Удаляет uploads/{dir_segment}/{entity_id}/ целиком (после commit).

    rmtree вынесен в поток; ignore_errors=True — каталога может не быть.
    """
    entity_dir = UPLOAD_ROOT / config.dir_segment / str(entity_id)
    await asyncio.to_thread(shutil.rmtree, entity_dir, ignore_errors=True)
    logger.info("Cleaned up files for %s %s", config.singular_name, entity_id)
