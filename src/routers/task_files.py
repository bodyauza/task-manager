"""Эндпоинты загрузки и удаления файлов для задач.

Тонкий HTTP-адаптер над src/services/attachments.py: вся логика валидации,
защиты от гонок (FOR NO KEY UPDATE) и CRM-синхронизации живёт там один раз,
параметризованная TASK_ATTACHMENTS — этот роутер лишь достаёт зависимости
FastAPI и передаёт их дальше.

Маршруты:
    POST   /tasks/{task_id}/specification          — загрузить/заменить файл ТЗ
    DELETE /tasks/{task_id}/specification          — удалить файл ТЗ
    POST   /tasks/{task_id}/files                  — добавить «Иные документы» (до 10 файлов)
    DELETE /tasks/{task_id}/files/{filename}       — удалить один файл из «Иных документов»

Файлы хранятся в src/uploads/tasks/{task_id}/.
В БД хранятся только пути относительно uploads/ — байты файлов в БД не попадают.
"""

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user        # DI: текущий аутентифицированный пользователь
from src.auth.user_models import User
from src.database import get_async_session            # DI: асинхронная сессия SQLAlchemy
from src.openapi_responses import responses
from src.services import attachments
from src.services.attachments import TASK_ATTACHMENTS

router = APIRouter(tags=["Task files"])

"""
1. POST /create-task/           → создаём задачу, получаем {id: 5}
2. POST /tasks/5/specification  → загружаем файл ТЗ
   POST /tasks/5/files          → загружаем иные документы
"""


@router.post(
    "/tasks/{task_id}/specification",
    status_code=200,
    summary="Загрузить или заменить ТЗ задачи",
    description="`multipart/form-data`, поле `file`. Форматы: pdf, doc, docx, xls, xlsx, jpg, jpeg, png, txt; до 100 МБ.",
    responses=responses(401, 404, 413, 422),
)
async def upload_task_specification(
    task_id: int,
    file: UploadFile = File(...),                    # multipart/form-data, поле "file"
    user: User = Depends(current_user),              # 401 если токен отсутствует или просрочен
    db: AsyncSession = Depends(get_async_session),
):
    return await attachments.upload_specification(db, user, task_id, file, TASK_ATTACHMENTS)


@router.delete(
    "/tasks/{task_id}/specification",
    status_code=200,
    summary="Удалить ТЗ задачи",
    responses=responses(401, 404, c404="Объект не найден или файл ТЗ не загружен"),
)
async def delete_task_specification(
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await attachments.delete_specification(db, user, task_id, TASK_ATTACHMENTS)


@router.post(
    "/tasks/{task_id}/files",
    status_code=200,
    summary="Добавить «иные документы» задачи",
    description="`multipart/form-data`, поле `files` (несколько файлов). Всего не более 10 файлов.",
    responses=responses(401, 404, 413, 422, c422="Превышен лимит файлов, недопустимое расширение или MIME-тип"),
)
async def upload_task_files(
    task_id: int,
    files: list[UploadFile] = File(...),             # multipart/form-data, поле "files" (список)
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await attachments.upload_other_files(db, user, task_id, files, TASK_ATTACHMENTS)


@router.delete(
    "/tasks/{task_id}/files/{filename}",
    status_code=200,
    summary="Удалить один из «иных документов» задачи",
    responses=responses(401, 404, c404="Объект или файл не найден"),
)
async def delete_task_file(
    task_id: int,
    filename: str,                                    # имя файла с UUID-префиксом (path-параметр)
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await attachments.delete_other_file(db, user, task_id, filename, TASK_ATTACHMENTS)
