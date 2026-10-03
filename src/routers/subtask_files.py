"""Эндпоинты загрузки и удаления файлов подзадач — тонкий адаптер над src/services/attachments.py.

POST/DELETE /subtasks/{subtask_id}/specification, POST /subtasks/{subtask_id}/files,
DELETE /subtasks/{subtask_id}/files/{filename}.
"""

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user
from src.auth.user_models import User
from src.database import get_async_session
from src.openapi_responses import responses
from src.services import attachments
from src.services.attachments import SUBTASK_ATTACHMENTS

router = APIRouter(tags=["Subtask files"])


@router.post(
    "/subtasks/{subtask_id}/specification",
    status_code=200,
    summary="Загрузить или заменить ТЗ подзадачи",
    description="`multipart/form-data`, поле `file`. Форматы: pdf, jpg, jpeg, png; до 10 МБ.",
    responses=responses(401, 404, 413, 422),
)
async def upload_subtask_specification(
    subtask_id: int,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await attachments.upload_specification(db, user, subtask_id, file, SUBTASK_ATTACHMENTS)


@router.delete(
    "/subtasks/{subtask_id}/specification",
    status_code=200,
    summary="Удалить ТЗ подзадачи",
    responses=responses(401, 404, c404="Объект не найден или файл ТЗ не загружен"),
)
async def delete_subtask_specification(
    subtask_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await attachments.delete_specification(db, user, subtask_id, SUBTASK_ATTACHMENTS)


@router.post(
    "/subtasks/{subtask_id}/files",
    status_code=200,
    summary="Добавить «иные документы» подзадачи",
    description="`multipart/form-data`, поле `files` (несколько файлов). Всего не более 10 файлов.",
    responses=responses(401, 404, 413, 422, c422="Превышен лимит файлов, недопустимое расширение или MIME-тип"),
)
async def upload_subtask_files(
    subtask_id: int,
    files: list[UploadFile] = File(...),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await attachments.upload_other_files(db, user, subtask_id, files, SUBTASK_ATTACHMENTS)


@router.delete(
    "/subtasks/{subtask_id}/files/{filename}",
    status_code=200,
    summary="Удалить один из «иных документов» подзадачи",
    responses=responses(401, 404, c404="Объект или файл не найден"),
)
async def delete_subtask_file(
    subtask_id: int,
    filename: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await attachments.delete_other_file(db, user, subtask_id, filename, SUBTASK_ATTACHMENTS)
