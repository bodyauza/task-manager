"""Эндпоинты загрузки и удаления файлов для подзадач.

Тонкий HTTP-адаптер над src/services/attachments.py — та же логика, что и в
task_files.py, параметризованная SUBTASK_ATTACHMENTS.

Маршруты:
    POST   /subtasks/{subtask_id}/specification     — загрузить/заменить файл ТЗ
    DELETE /subtasks/{subtask_id}/specification     — удалить файл ТЗ
    POST   /subtasks/{subtask_id}/files             — добавить «Иные документы»
    DELETE /subtasks/{subtask_id}/files/{filename}  — удалить один файл
"""

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user
from src.auth.models import User
from src.crm.subtask_service import SubtaskCRMSync, get_subtask_crm_sync
from src.database import get_async_session
from src.services import attachments
from src.services.attachments import SUBTASK_ATTACHMENTS

router = APIRouter(tags=["Subtask files"])


@router.post("/subtasks/{subtask_id}/specification", status_code=200)
async def upload_subtask_specification(
    subtask_id: int,
    file: UploadFile = File(...),                    # multipart/form-data, поле "file"
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    crm: SubtaskCRMSync = Depends(get_subtask_crm_sync),
):
    return await attachments.upload_specification(db, user, subtask_id, file, crm, SUBTASK_ATTACHMENTS)


@router.delete("/subtasks/{subtask_id}/specification", status_code=200)
async def delete_subtask_specification(
    subtask_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    crm: SubtaskCRMSync = Depends(get_subtask_crm_sync),
):
    return await attachments.delete_specification(db, user, subtask_id, crm, SUBTASK_ATTACHMENTS)


@router.post("/subtasks/{subtask_id}/files", status_code=200)
async def upload_subtask_files(
    subtask_id: int,
    files: list[UploadFile] = File(...),             # поле "files" — список файлов
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    crm: SubtaskCRMSync = Depends(get_subtask_crm_sync),
):
    return await attachments.upload_other_files(db, user, subtask_id, files, crm, SUBTASK_ATTACHMENTS)


@router.delete("/subtasks/{subtask_id}/files/{filename}", status_code=200)
async def delete_subtask_file(
    subtask_id: int,
    filename: str,                                    # имя файла с UUID-префиксом
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    crm: SubtaskCRMSync = Depends(get_subtask_crm_sync),
):
    return await attachments.delete_other_file(db, user, subtask_id, filename, crm, SUBTASK_ATTACHMENTS)
