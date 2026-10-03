"""Глобальные обработчики исключений (register_errors_handlers)."""
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

# HTML-страницы сущностей, отвечающие 404 для удалённой задачи/подзадачи; только их редиректим, 404 на других путях остаётся JSON.
_ENTITY_PAGE_PREFIXES = ("/task/", "/subtask/", "/subtask-board/")

# detail → ключ уведомления для task-board.js. Ключ, а не текст: текст не попадает в URL (reflected injection).
_NOT_FOUND_NOTICES = {
    "Task not found": "task_not_found",
    "Subtask not found": "subtask_not_found",
}


def register_errors_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # Навигация браузера шлёт Accept: text/html, JS fetch — */*. Для навигации 401 → редирект на логин,
        # 404 страницы задачи/подзадачи → на доску задач с уведомлением; JS fetch получает JSON.
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
