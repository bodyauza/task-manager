"""Глобальные обработчики исключений приложения.

Вынесено из main.py в register_errors_handlers(app) — тот же принцип
композиции, что и у register_middlewares (src/middlewares.py).
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

# HTML-страницы сущностей (src/routers/pages.py), которые отвечают 404, если
# задача/подзадача уже удалена. Только они редиректятся — 404 на других путях
# (например, прямая ссылка на несуществующий файл в /uploads) остаётся JSON.
_ENTITY_PAGE_PREFIXES = ("/task/", "/subtask/", "/subtask-board/")

# detail из pages.py → ключ уведомления, которое task-board.js показывает тостом.
# Ключ, а не готовый текст, в query-параметре: текст не попадает в URL и не может
# быть подставлен произвольной ссылкой (reflected-injection).
_NOT_FOUND_NOTICES = {
    "Task not found": "task_not_found",
    "Subtask not found": "subtask_not_found",
}


def register_errors_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # Браузерная навигация посылает Accept: text/html; JS fetch — Accept: */*.
        # Для навигации сырой JSON {"detail": ...} бесполезен — редиректим:
        #   401 → на логин (JS fetch получает JSON 401 и сам запускает цикл обновления токена);
        #   404 страницы задачи/подзадачи (удалена в другой вкладке/другим пользователем,
        #       пока пользователь был на другой странице) → на доску задач с уведомлением.
        wants_html = "text/html" in request.headers.get("accept", "")
        if exc.status_code == 401 and wants_html:
            return RedirectResponse(url="/", status_code=302)
        if (
            exc.status_code == 404
            and wants_html
            and request.url.path.startswith(_ENTITY_PAGE_PREFIXES)
        ):
            notice = _NOT_FOUND_NOTICES.get(exc.detail, "not_found")
            return RedirectResponse(url=f"/task-board?notice={notice}", status_code=302)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
