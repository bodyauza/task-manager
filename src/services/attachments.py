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

from src.auth.models import User
from src.crm.subtask_service import SubtaskCRMSync
from src.crm.task_service import TaskCRMSync
from src.realtime import broadcast_task_event
from src.task_logic.models import Subtask, Task
from src.utils.file_utils import (
    MAX_OTHER_FILES,     # лимит файлов в «Иных документах» (10 штук)
    UPLOAD_ROOT,         # абсолютный путь к src/static/uploads/ (единая точка определения)
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
    get_crm_id: Callable[[Any], Optional[int]]              # entity -> crm_task_id | crm_subtask_id
    crm_sync: Callable[..., Awaitable[dict]]                # (crm, crm_id, **kwargs) -> await crm.update_task/update_subtask
    event_extra: Callable[[AsyncSession, Any], Awaitable[dict]]
        # -> {"title": ..., "task_id": ...} либо {"title": ..., "task_id": ..., "subtask_id": ..., "task_title": ...}
        # вычисляется ДО commit — после expire атрибуты сущности могут стать недоступны


async def _sync_task(crm: TaskCRMSync, crm_id: int, **kwargs) -> dict:
    return await crm.update_task(task_id=crm_id, **kwargs)


async def _sync_subtask(crm: SubtaskCRMSync, crm_id: int, **kwargs) -> dict:
    return await crm.update_subtask(subtask_id=crm_id, **kwargs)


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
    get_crm_id=lambda task: task.crm_task_id,
    crm_sync=_sync_task,
    event_extra=_task_event_extra,
)

SUBTASK_ATTACHMENTS = AttachmentConfig(
    model=Subtask,
    dir_segment="subtasks",
    singular_name="subtask",
    not_found_detail="Subtask not found",
    event_type="subtask_files_updated",
    get_crm_id=lambda subtask: subtask.crm_subtask_id,
    crm_sync=_sync_subtask,
    event_extra=_subtask_event_extra,
)


# ════════════════════════════════════════════════════════════
# Техническое задание (одиночный файл)
# ════════════════════════════════════════════════════════════

async def upload_specification(
    db: AsyncSession,
    user: User,
    entity_id: int,
    file: UploadFile,
    crm,
    config: AttachmentConfig,
) -> dict:
    """Загружает (или заменяет) файл ТЗ. При повторной загрузке старый файл удаляется с диска.

    Синхронизация с CRM — best-effort: ошибка CRM не блокирует сохранение.
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

    # CRM-синхронизация — best-effort: только если сущность зарегистрирована в CRM.
    crm_id = config.get_crm_id(entity)
    if crm_id is not None:
        try:
            await config.crm_sync(crm, crm_id, specification_abs_path=dest_dir / filename)
            logger.info("CRM: %s %s specification synced", config.singular_name, entity_id)
        except Exception as exc:
            logger.error("CRM: %s %s specification sync failed: %s", config.singular_name, entity_id, exc)

    # extra захватывается ДО commit — иначе MissingGreenlet после expire (title, task_title и т.д.).
    extra = await config.event_extra(db, entity)
    title = extra.pop("title")
    entity.specification_path = rel_path
    await db.commit()

    # Удаляем старый файл только после успешного commit: если commit упал бы раньше,
    # старый файл остался бы на диске и путь в БД не изменился бы → нет потери данных.
    if old_path:
        old_path.unlink(missing_ok=True)

    # exclude_user_id не передаётся: broadcast идёт всем, включая актора — актор должен увидеть
    # собственное сообщение в чате (см. task-board.js). action="uploaded" различает
    # формулировку "Добавлены файлы"/"Files added" от "Удалены файлы"/"Files removed" на фронте.
    await broadcast_task_event(
        config.event_type, title, sender_email=user.email, actor_id=user.id, action="uploaded", **extra,
    )

    return {"specification_path": rel_path}  # URL: /uploads/{rel_path}


async def delete_specification(
    db: AsyncSession, user: User, entity_id: int, crm, config: AttachmentConfig,
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

    (UPLOAD_ROOT / entity.specification_path).unlink(missing_ok=True)

    crm_id = config.get_crm_id(entity)
    extra = await config.event_extra(db, entity)  # захватить до commit — иначе MissingGreenlet после expire
    title = extra.pop("title")
    entity.specification_path = None
    await db.commit()

    if crm_id is not None:
        try:
            await config.crm_sync(crm, crm_id, clear_specification=True)
            logger.info("CRM: %s %s specification cleared", config.singular_name, entity_id)
        except Exception as exc:
            logger.error("CRM: %s %s specification clear failed: %s", config.singular_name, entity_id, exc)

    await broadcast_task_event(
        config.event_type, title, sender_email=user.email, actor_id=user.id, action="deleted", **extra,
    )

    return {"specification_path": None}


# ════════════════════════════════════════════════════════════
# Иные документы (множественные файлы)
# ════════════════════════════════════════════════════════════

async def upload_other_files(
    db: AsyncSession,
    user: User,
    entity_id: int,
    files: list[UploadFile],
    crm,
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

    # Проход 1: валидируем все файлы до записи на диск — параллельно через asyncio.gather.
    # return_exceptions=True вместо того чтобы дать gather самому оборвать ожидание на первой
    # ошибке: поток ОС, уже занятый magic.from_buffer() для другого файла, всё равно не
    # остановить снаружи — он доработает сам по себе, просто впустую. Дожидаемся всех
    # результатов и поднимаем первую ошибку сами — так на диске по-прежнему не остаётся
    # частично сохранённых файлов (сохранение всё ещё начинается только после этой проверки).
    async def _validate_one(upload: UploadFile) -> tuple[bytes, str]:
        content = await read_and_validate(upload)
        filename = safe_filename(upload.filename)
        return content, filename

    validation_results = await asyncio.gather(
        *[_validate_one(upload) for upload in files], return_exceptions=True
    )
    for result in validation_results:
        if isinstance(result, BaseException):
            raise result
    validated: list[tuple[bytes, str]] = validation_results  # после цикла выше — только tuple

    # Проход 2: все файлы валидны — сохраняем на диск параллельно. В отличие от MIME-проверки
    # выше (сериализована общим локом внутри python-magic), запись на диск такого ограничения
    # не имеет — у каждого файла свой UUID-префикс от safe_filename(), коллизий имён нет.
    new_paths: list[str] = list(await asyncio.gather(
        *[asyncio.to_thread(save_file, dest_dir, filename, content) for content, filename in validated]
    ))

    updated = existing + new_paths
    crm_id = config.get_crm_id(entity)                        # захватить до commit
    extra = await config.event_extra(db, entity)               # захватить до commit
    title = extra.pop("title")
    # JSONB: передаём list[str] напрямую; asyncpg сериализует в бинарный JSON при INSERT/UPDATE.
    entity.other_file_paths = updated
    await db.commit()

    if crm_id is not None:
        try:
            # CRM-поле заменяется целиком: передаём все текущие файлы поля.
            # Передать только new_paths — CRM потеряет ранее загруженные файлы записи.
            all_abs = [UPLOAD_ROOT / p for p in updated]
            await config.crm_sync(crm, crm_id, other_file_abs_paths=all_abs)
            logger.info("CRM: %s %s other files synced (%d files)", config.singular_name, entity_id, len(all_abs))
        except Exception as exc:
            logger.error("CRM: %s %s other files sync failed: %s", config.singular_name, entity_id, exc)

    await broadcast_task_event(
        config.event_type, title, sender_email=user.email, actor_id=user.id, action="uploaded", **extra,
    )

    return {"other_file_paths": updated}


async def delete_other_file(
    db: AsyncSession, user: User, entity_id: int, filename: str, crm, config: AttachmentConfig,
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

    (UPLOAD_ROOT / target).unlink(missing_ok=True)

    updated = [p for p in existing if p != target]
    crm_id = config.get_crm_id(entity)
    extra = await config.event_extra(db, entity)   # захватить до commit — иначе MissingGreenlet после expire
    title = extra.pop("title")
    # NULL вместо [] при пустом списке: соответствует начальному состоянию колонки.
    entity.other_file_paths = updated if updated else None
    await db.commit()

    if crm_id is not None:
        try:
            # [] очищает CRM-поле; [p1,…] заменяет всё содержимое поля.
            remaining_abs = [UPLOAD_ROOT / p for p in updated]
            await config.crm_sync(crm, crm_id, other_file_abs_paths=remaining_abs)
            logger.info("CRM: %s %s other files synced after delete", config.singular_name, entity_id)
        except Exception as exc:
            logger.error("CRM: %s %s other files sync failed: %s", config.singular_name, entity_id, exc)

    await broadcast_task_event(
        config.event_type, title, sender_email=user.email, actor_id=user.id, action="deleted", **extra,
    )

    return {"other_file_paths": updated}


# ════════════════════════════════════════════════════════════
# Каскадное удаление файлов при удалении задачи/подзадачи
# ════════════════════════════════════════════════════════════

def cleanup(entity_id: int, config: AttachmentConfig) -> None:
    """Удаляет директорию uploads/{dir_segment}/{entity_id}/ со всем содержимым.

    Вызывается ПОСЛЕ db.commit(), когда сущность уже удалена из БД (и для задачи —
    PostgreSQL CASCADE уже удалил подзадачи). shutil.rmtree: рекурсивное удаление;
    ignore_errors=True — не падает, если директория не существует (сущность без файлов).
    """
    entity_dir = UPLOAD_ROOT / config.dir_segment / str(entity_id)
    shutil.rmtree(entity_dir, ignore_errors=True)
    logger.info("Cleaned up files for %s %s", config.singular_name, entity_id)
