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

Файлы хранятся в src/static/uploads/tasks/{task_id}/.
В БД хранятся только пути относительно uploads/ — байты файлов в БД не попадают.
"""

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import current_user        # DI: текущий аутентифицированный пользователь
from src.auth.models import User
from src.crm.task_service import TaskCRMSync, get_task_crm_sync
from src.database import get_async_session            # DI: асинхронная сессия SQLAlchemy
from src.services import attachments
from src.services.attachments import TASK_ATTACHMENTS

router = APIRouter(tags=["Task files"])

"""
1. POST /create-task/           → создаём задачу, получаем {id: 5}
2. POST /tasks/5/specification  → загружаем файл ТЗ
   POST /tasks/5/files          → загружаем иные документы
"""


@router.post("/tasks/{task_id}/specification", status_code=200)
async def upload_task_specification(
    task_id: int,
    file: UploadFile = File(...),                    # multipart/form-data, поле "file"
    user: User = Depends(current_user),              # 401 если токен отсутствует или просрочен
    db: AsyncSession = Depends(get_async_session),
    crm: TaskCRMSync = Depends(get_task_crm_sync),
):
    return await attachments.upload_specification(db, user, task_id, file, crm, TASK_ATTACHMENTS)


@router.delete("/tasks/{task_id}/specification", status_code=200)
async def delete_task_specification(
    task_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    crm: TaskCRMSync = Depends(get_task_crm_sync),
):
    return await attachments.delete_specification(db, user, task_id, crm, TASK_ATTACHMENTS)


@router.post("/tasks/{task_id}/files", status_code=200)
async def upload_task_files(
    task_id: int,
    files: list[UploadFile] = File(...),             # multipart/form-data, поле "files" (список)
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    crm: TaskCRMSync = Depends(get_task_crm_sync),
):
    return await attachments.upload_other_files(db, user, task_id, files, crm, TASK_ATTACHMENTS)


@router.delete("/tasks/{task_id}/files/{filename}", status_code=200)
async def delete_task_file(
    task_id: int,
    filename: str,                                    # имя файла с UUID-префиксом (path-параметр)
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_session),
    crm: TaskCRMSync = Depends(get_task_crm_sync),
):
    return await attachments.delete_other_file(db, user, task_id, filename, crm, TASK_ATTACHMENTS)
