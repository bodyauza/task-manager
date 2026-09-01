"""Глобальные обработчики исключений приложения.

Вынесено из main.py в register_errors_handlers(app) — тот же принцип
композиции, что и у register_middlewares (src/middlewares.py).
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse


def register_errors_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # Браузерная навигация посылает Accept: text/html — отвечаем редиректом на логин.
        # JS fetch посылает Accept: */* — получает JSON 401 и сам запускает цикл обновления токена.
        # Без этого разделения браузер показывал бы сырой JSON {"detail":"Unauthorized"}
        # при переходе на защищённый URL без авторизации.
        if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(url="/", status_code=302)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
