"""Эндпоинты загрузки и удаления файлов для подзадач.

Маршруты:
    POST   /subtasks/{subtask_id}/specification     — загрузить/заменить файл ТЗ
    DELETE /subtasks/{subtask_id}/specification     — удалить файл ТЗ
    POST   /subtasks/{subtask_id}/files             — добавить «Иные документы»
    DELETE /subtasks/{subtask_id}/files/{filename}  — удалить один файл

Структура идентична task_files.py, отличия:
  - модель Subtask вместо Task
  - пути: subtasks/{subtask_id}/...
  - CRM-поля: field_400 (ТЗ) и field_401 (иные документы)
"""

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user
from src.auth.models import User
from src.database import get_async_session
from src.task_logic.models import Subtask
from src.utils.file_utils import (
    MAX_OTHER_FILES,
    parse_other_paths,
    read_and_validate,
    safe_filename,
    save_file,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Subtask files"])

# Аналогично task_files.py: абсолютный путь к uploads/ вычисляется от __file__.
# src/routers/subtask_files.py → parent → src/routers/ → parent → src/ → /static/uploads
UPLOAD_ROOT = Path(__file__).resolve().parent.parent / "static" / "uploads"


# ════════════════════════════════════════════════════════════
# Техническое задание (одиночный файл, field_400 в CRM)
# ════════════════════════════════════════════════════════════

@router.post("/subtasks/{subtask_id}/specification", status_code=200)
async def upload_subtask_specification(
    subtask_id: int,
    file: UploadFile = File(...),                    # multipart/form-data, поле "file"
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Загружает (или заменяет) файл ТЗ подзадачи.

    При повторной загрузке старый файл удаляется с диска.
    Синхронизация с CRM — best-effort: только если crm_subtask_id не NULL.
    """
    subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id))
    ).scalar_one_or_none()
    if subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")

    content  = await read_and_validate(file)          # размер + расширение + MIME
    filename = safe_filename(file.filename)            # "spec.pdf" → "a1b2c3d4_spec.pdf"
    dest_dir = UPLOAD_ROOT / "subtasks" / str(subtask_id) / "specification"

    # Удалить старый файл ТЗ если был (замена)
    if subtask.specification_path:
        (UPLOAD_ROOT / subtask.specification_path).unlink(missing_ok=True)

    rel_path = save_file(dest_dir, filename, content)  # записать на диск → rel-путь

    # CRM best-effort: синхронизируем только если подзадача зарегистрирована в CRM.
    if subtask.crm_subtask_id is not None:
        from src.crm.subtask_service import SubtaskManager
        try:
            await SubtaskManager().update_subtask(
                subtask_id=subtask.crm_subtask_id,
                specification_abs_path=dest_dir / filename,  # абсолютный путь → _file_to_crm
            )
            logger.info("CRM: subtask %s specification synced", subtask_id)
        except Exception as exc:
            logger.error("CRM: subtask %s specification sync failed: %s", subtask_id, exc)

    subtask.specification_path = rel_path   # "subtasks/7/specification/a1b2_spec.pdf"
    await db.commit()

    return {"specification_path": rel_path}


@router.delete("/subtasks/{subtask_id}/specification", status_code=200)
async def delete_subtask_specification(
    subtask_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Удаляет файл ТЗ подзадачи с диска и обнуляет путь в БД."""
    subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id))
    ).scalar_one_or_none()
    if subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")

    if not subtask.specification_path:
        raise HTTPException(status_code=404, detail="Specification file not found")

    (UPLOAD_ROOT / subtask.specification_path).unlink(missing_ok=True)

    crm_subtask_id = subtask.crm_subtask_id
    subtask.specification_path = None
    await db.commit()

    if crm_subtask_id is not None:
        from src.crm.subtask_service import SubtaskManager
        try:
            await SubtaskManager().update_subtask(subtask_id=crm_subtask_id, clear_specification=True)
            logger.info("CRM: subtask %s specification cleared", subtask_id)
        except Exception as exc:
            logger.error("CRM: subtask %s specification clear failed: %s", subtask_id, exc)

    return {"specification_path": None}


# ════════════════════════════════════════════════════════════
# Иные документы (множественные файлы, field_401 в CRM)
# ════════════════════════════════════════════════════════════

@router.post("/subtasks/{subtask_id}/files", status_code=200)
async def upload_subtask_files(
    subtask_id: int,
    files: list[UploadFile] = File(...),             # поле "files" — список файлов
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Добавляет файлы в «Иные документы» подзадачи (максимум 10 суммарно)."""
    subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id))
    ).scalar_one_or_none()
    if subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")

    existing = parse_other_paths(subtask.other_file_paths)  # [] если NULL

    if len(existing) + len(files) > MAX_OTHER_FILES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Превышен лимит файлов ({MAX_OTHER_FILES} штук). "
                f"Уже загружено: {len(existing)}. "
                f"Добавить можно не более {MAX_OTHER_FILES - len(existing)} файл(ов)."
            ),
        )

    dest_dir  = UPLOAD_ROOT / "subtasks" / str(subtask_id) / "other"
    new_paths: list[str] = []

    for upload in files:
        content  = await read_and_validate(upload)
        filename = safe_filename(upload.filename)
        rel_path = save_file(dest_dir, filename, content)
        new_paths.append(rel_path)

    updated = existing + new_paths
    crm_subtask_id = subtask.crm_subtask_id
    subtask.other_file_paths = updated
    await db.commit()

    if crm_subtask_id is not None:
        from src.crm.subtask_service import SubtaskManager
        try:
            all_abs = [UPLOAD_ROOT / p for p in updated]
            await SubtaskManager().update_subtask(subtask_id=crm_subtask_id, other_file_abs_paths=all_abs)
            logger.info("CRM: subtask %s other files synced (%d files)", subtask_id, len(all_abs))
        except Exception as exc:
            logger.error("CRM: subtask %s other files sync failed: %s", subtask_id, exc)

    return {"other_file_paths": updated}


@router.delete("/subtasks/{subtask_id}/files/{filename}", status_code=200)
async def delete_subtask_file(
    subtask_id: int,
    filename: str,                                    # имя файла с UUID-префиксом
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Удаляет один файл из «Иных документов» подзадачи по имени."""
    subtask = (
        await db.execute(select(Subtask).where(Subtask.id == subtask_id))
    ).scalar_one_or_none()
    if subtask is None:
        raise HTTPException(status_code=404, detail="Subtask not found")

    existing = parse_other_paths(subtask.other_file_paths)

    # Path(p).name: "subtasks/7/other/a1b2_doc.pdf" → "a1b2_doc.pdf"
    target = next((p for p in existing if Path(p).name == filename), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Файл '{filename}' не найден")

    (UPLOAD_ROOT / target).unlink(missing_ok=True)

    updated = [p for p in existing if p != target]
    crm_subtask_id = subtask.crm_subtask_id
    subtask.other_file_paths = updated if updated else None
    await db.commit()

    if crm_subtask_id is not None:
        from src.crm.subtask_service import SubtaskManager
        try:
            remaining_abs = [UPLOAD_ROOT / p for p in updated]
            await SubtaskManager().update_subtask(subtask_id=crm_subtask_id, other_file_abs_paths=remaining_abs)
            logger.info("CRM: subtask %s other files synced after delete", subtask_id)
        except Exception as exc:
            logger.error("CRM: subtask %s other files sync failed: %s", subtask_id, exc)

    return {"other_file_paths": updated}


# ════════════════════════════════════════════════════════════
# Каскадное удаление файлов при удалении подзадачи
# ════════════════════════════════════════════════════════════

def cleanup_subtask_files(subtask_id: int) -> None:
    """Удаляет директорию uploads/subtasks/{subtask_id}/ со всем содержимым.

    Вызывается из роутера delete_subtask (subtasks.py) после db.commit().
    ignore_errors=True — безопасен для подзадач без загруженных файлов.
    """
    subtask_dir = UPLOAD_ROOT / "subtasks" / str(subtask_id)
    shutil.rmtree(subtask_dir, ignore_errors=True)
    logger.info("Cleaned up files for subtask %s", subtask_id)
