"""Эндпоинты загрузки и удаления файлов для задач.

Маршруты:
    POST   /tasks/{task_id}/specification          — загрузить/заменить файл ТЗ
    DELETE /tasks/{task_id}/specification          — удалить файл ТЗ
    POST   /tasks/{task_id}/files                  — добавить «Иные документы» (до 10 файлов)
    DELETE /tasks/{task_id}/files/{filename}       — удалить один файл из «Иных документов»

Файлы хранятся в src/static/uploads/tasks/{task_id}/.
В БД хранятся только пути относительно uploads/ — байты файлов в БД не попадают.
"""

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user        # DI: текущий аутентифицированный пользователь
from src.auth.models import User
from src.database import get_async_session            # DI: асинхронная сессия SQLAlchemy
from src.task_logic.models import Task
from src.utils.file_utils import (
    MAX_OTHER_FILES,     # лимит файлов в «Иных документах» (10 штук)
    parse_other_paths,   # JSONB (list[str] | None) → list[str]; [] при NULL
    read_and_validate,   # чтение + проверка размера/расширения/MIME
    safe_filename,       # добавление UUID-префикса к имени
    save_file,           # запись на диск, возврат rel-пути
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Task files"])

# Корневая директория для хранения файлов.
# Абсолютный путь вычисляется относительно этого файла (src/routers/task_files.py):
#   __file__ → src/routers/task_files.py
#   .parent   → src/routers/
#   .parent   → src/
#   / "static" / "uploads" → src/static/uploads/
# Абсолютный путь нужен, чтобы роутер работал независимо от CWD при запуске.
UPLOAD_ROOT = Path(__file__).resolve().parent.parent / "static" / "uploads"

"""
1. POST /create-task/           → создаём задачу, получаем {id: 5}
2. POST /tasks/5/specification  → загружаем файл ТЗ
   POST /tasks/5/files          → загружаем иные документы
"""

# ════════════════════════════════════════════════════════════
# Техническое задание (одиночный файл, field_395 в CRM)
# ════════════════════════════════════════════════════════════

@router.post("/tasks/{task_id}/specification", status_code=200)
async def upload_task_specification(
    task_id: int,
    file: UploadFile = File(...),                    # multipart/form-data, поле "file"
    user: User = Depends(current_user),              # 401 если токен отсутствует или просрочен
    db: AsyncSession = Depends(get_async_session),
):
    """Загружает (или заменяет) файл ТЗ задачи.

    При повторной загрузке старый файл удаляется с диска.
    Синхронизация с CRM — best-effort: ошибка CRM не блокирует сохранение.
    """
    # Получаем задачу; 404 если не найдена
    task = (
        await db.execute(select(Task).where(Task.id == task_id))
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # read_and_validate: читает байты, проверяет размер (≤100 МБ), расширение и MIME.
    # При нарушении — поднимает HTTPException (413 или 422) до записи на диск.
    content  = await read_and_validate(file)

    # safe_filename добавляет uuid-префикс: "tz.pdf" → "a1b2c3d4_tz.pdf"
    filename = safe_filename(file.filename)
    dest_dir = UPLOAD_ROOT / "tasks" / str(task_id) / "specification"

    # Удаляем предыдущий файл ТЗ если он был загружен ранее.
    # unlink(missing_ok=True): не падает если файл уже удалён вручную или с диска.
    if task.specification_path:
        old_path = UPLOAD_ROOT / task.specification_path
        old_path.unlink(missing_ok=True)

    # save_file: создаёт директорию и записывает байты; возвращает rel-путь от uploads/.
    rel_path = save_file(dest_dir, filename, content)

    # CRM-синхронизация — best-effort: только если задача зарегистрирована в CRM.
    # Ошибка CRM логируется, но не блокирует сохранение файла локально.
    if task.crm_task_id is not None:
        from src.crm.task_service import TaskManager  # отложенный импорт: избегает циклов
        try:
            await TaskManager().update_task(
                task_id=task.crm_task_id,
                specification_abs_path=dest_dir / filename,  # абсолютный путь для чтения
            )
            logger.info("CRM: task %s specification synced", task_id)
        except Exception as exc:
            logger.error("CRM: task %s specification sync failed: %s", task_id, exc)

    # Обновляем путь в БД и фиксируем транзакцию.
    task.specification_path = rel_path   # "tasks/3/specification/a1b2c3d4_tz.pdf"
    await db.commit()

    return {"specification_path": rel_path}  # URL: /uploads/{rel_path}


@router.delete("/tasks/{task_id}/specification", status_code=200)
async def delete_task_specification(
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Удаляет файл ТЗ задачи с диска и обнуляет путь в БД."""
    task = (
        await db.execute(select(Task).where(Task.id == task_id))
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if not task.specification_path:
        # 404: удалять нечего — файл не загружен
        raise HTTPException(status_code=404, detail="Specification file not found")

    (UPLOAD_ROOT / task.specification_path).unlink(missing_ok=True)

    crm_task_id = task.crm_task_id
    task.specification_path = None
    await db.commit()

    if crm_task_id is not None:
        from src.crm.task_service import TaskManager
        try:
            await TaskManager().update_task(task_id=crm_task_id, clear_specification=True)
            logger.info("CRM: task %s specification cleared", task_id)
        except Exception as exc:
            logger.error("CRM: task %s specification clear failed: %s", task_id, exc)

    return {"specification_path": None}


# ════════════════════════════════════════════════════════════
# Иные документы (множественные файлы, field_396 в CRM)
# ════════════════════════════════════════════════════════════

@router.post("/tasks/{task_id}/files", status_code=200)
async def upload_task_files(
    task_id: int,
    files: list[UploadFile] = File(...),             # multipart/form-data, поле "files" (список)
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Добавляет файлы в «Иные документы» задачи (максимум 10 файлов суммарно)."""
    task = (
        await db.execute(select(Task).where(Task.id == task_id))
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Парсим текущий список файлов; parse_other_paths возвращает [] при NULL
    existing: list[str] = parse_other_paths(task.other_file_paths)

    if len(existing) + len(files) > MAX_OTHER_FILES:
        # 422: лимит файлов превышен. Сообщаем сколько уже есть и сколько можно ещё.
        raise HTTPException(
            status_code=422,
            detail=(
                f"Превышен лимит файлов ({MAX_OTHER_FILES} штук). "
                f"Уже загружено: {len(existing)}. "
                f"Добавить можно не более {MAX_OTHER_FILES - len(existing)} файл(ов)."
            ),
        )

    dest_dir  = UPLOAD_ROOT / "tasks" / str(task_id) / "other"
    new_paths: list[str] = []

    for upload in files:
        # Каждый файл валидируется отдельно; первый невалидный прерывает цикл с HTTPException.
        content  = await read_and_validate(upload)
        filename = safe_filename(upload.filename)      # UUID-префикс → нет коллизий
        rel_path = save_file(dest_dir, filename, content)
        new_paths.append(rel_path)                    # накапливаем пути новых файлов

    updated = existing + new_paths
    crm_task_id = task.crm_task_id   # захватить до commit: после expire атрибут недоступен
    # JSONB: передаём list[str] напрямую; asyncpg сериализует в бинарный JSON при INSERT/UPDATE.
    task.other_file_paths = updated
    await db.commit()

    if crm_task_id is not None:
        from src.crm.task_service import TaskManager
        try:
            # field_396 в CRM заменяется целиком: передаём все текущие файлы поля.
            # Передать только new_paths — CRM потеряет ранее загруженные файлы записи.
            all_abs = [UPLOAD_ROOT / p for p in updated]
            await TaskManager().update_task(task_id=crm_task_id, other_file_abs_paths=all_abs)
            logger.info("CRM: task %s other files synced (%d files)", task_id, len(all_abs))
        except Exception as exc:
            logger.error("CRM: task %s other files sync failed: %s", task_id, exc)

    return {"other_file_paths": updated}


@router.delete("/tasks/{task_id}/files/{filename}", status_code=200)
async def delete_task_file(
    task_id: int,
    filename: str,                                    # имя файла с UUID-префиксом (path-параметр)
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Удаляет один файл из «Иных документов» задачи по имени файла."""
    task = (
        await db.execute(select(Task).where(Task.id == task_id))
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    existing = parse_other_paths(task.other_file_paths)

    # Ищем в списке путь, чьё имя файла совпадает с запрошенным.
    # Path(p).name отрезает директорию: "tasks/3/other/a1b2_doc.pdf" → "a1b2_doc.pdf".
    target = next((p for p in existing if Path(p).name == filename), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Файл '{filename}' не найден")

    (UPLOAD_ROOT / target).unlink(missing_ok=True)

    updated = [p for p in existing if p != target]
    crm_task_id = task.crm_task_id
    # NULL вместо [] при пустом списке: соответствует начальному состоянию колонки.
    task.other_file_paths = updated if updated else None
    await db.commit()

    if crm_task_id is not None:
        from src.crm.task_service import TaskManager
        try:
            # [] очищает field_396 в CRM; [p1,…] заменяет всё содержимое поля.
            remaining_abs = [UPLOAD_ROOT / p for p in updated]
            await TaskManager().update_task(task_id=crm_task_id, other_file_abs_paths=remaining_abs)
            logger.info("CRM: task %s other files synced after delete", task_id)
        except Exception as exc:
            logger.error("CRM: task %s other files sync failed: %s", task_id, exc)

    return {"other_file_paths": updated}


# ════════════════════════════════════════════════════════════
# Каскадное удаление файлов при удалении задачи
# ════════════════════════════════════════════════════════════

def cleanup_task_files(task_id: int) -> None:
    """Удаляет директорию uploads/tasks/{task_id}/ со всем содержимым.

    Вызывается из роутера delete_task (tasks.py) ПОСЛЕ db.commit(),
    когда задача уже удалена из БД (и PostgreSQL CASCADE удалил подзадачи).
    shutil.rmtree: рекурсивное удаление; ignore_errors=True — не падает
    если директория не существует (задача без файлов).
    """
    task_dir = UPLOAD_ROOT / "tasks" / str(task_id)
    shutil.rmtree(task_dir, ignore_errors=True)       # удалить всё дерево папки задачи
    logger.info("Cleaned up files for task %s", task_id)
