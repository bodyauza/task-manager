"""Аутентифицированная раздача загруженных файлов (ТЗ, «Иные документы»).

Вынесен из main.py: раньше здесь стоял StaticFiles mount на /uploads, но
Starlette-mount (app.mount()) обходит FastAPI Depends — добавить
Depends(current_user) через mount невозможно. Вместо mount — обычный роутер
с зависимостью, который читает файл с диска и возвращает FileResponse.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.auth.auth_config import current_user
from src.utils.file_utils import UPLOAD_ROOT

router = APIRouter(tags=["Uploads"])

# Директория должна существовать до первого запроса — создаётся при импорте модуля,
# т.е. до того как uvicorn начнёт принимать соединения.
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


@router.get("/uploads/{file_path:path}", include_in_schema=False)
async def serve_upload(
    file_path: str,
    user=Depends(current_user),   # 401 без токена; StaticFiles mount это не умеет
):
    abs_path = UPLOAD_ROOT / file_path
    # Защита от path-traversal: resolved path должен оставаться внутри UPLOAD_ROOT.
    try:
        abs_path = abs_path.resolve()
        uploads_root = UPLOAD_ROOT.resolve()
        abs_path.relative_to(uploads_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(abs_path)
