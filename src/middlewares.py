"""Регистрация middleware приложения: CORS, GZip, кеш статики, CSP, лимит размера тела."""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config import settings
from src.utils.file_utils import MAX_FILE_SIZE, MAX_OTHER_FILES

# Лимит на суммарный размер тела запроса — отклоняет заведомо чрезмерный запрос до разбора формы (MAX_FILE_SIZE по-прежнему
# проверяется на каждый файл). Рассчитан на самый большой легитимный запрос: ТЗ + MAX_OTHER_FILES файлов; +2 МБ на поля формы
# и multipart-разметку.
MAX_REQUEST_BODY_SIZE = (MAX_OTHER_FILES + 1) * MAX_FILE_SIZE + 2 * 1024 * 1024


class _BodyTooLarge(Exception):
    """Внутренний сигнал превышения лимита: поднимается из receive() внутри MaxBodySizeMiddleware и там же перехватывается."""


class MaxBodySizeMiddleware:
    """Ограничивает размер тела запроса до разбора multipart/JSON.

    Starlette читает тело (в том числе файлы во временные файлы на диске) до зависимостей эндпоинта, поэтому анонимный запрос
    с телом в десятки ГБ был бы записан на диск и только потом получил 401/413. Это чистая ASGI-мидлварь (не BaseHTTPMiddleware,
    которая буферизует тело): receive() оборачивается напрямую, лимит проверяется по Content-Length (отказ до чтения тела) и по
    фактическим байтам (chunked или заниженный заголовок).

    Регистрируется последней: Starlette оборачивает мидлвари в обратном порядке, поэтому она самая внешняя и получает receive()
    от сервера раньше BaseHTTPMiddleware. Цена — в ранний 413 не попадают CORS-заголовки.
    """

    def __init__(self, app: ASGIApp, max_body_size: int = MAX_REQUEST_BODY_SIZE) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                declared_too_large = int(content_length) > self.max_body_size
            except ValueError:
                # Невалидный Content-Length оставляем обычному стеку.
                declared_too_large = False
            if declared_too_large:
                await self._reject(scope, receive, send)
                return

        total = 0
        limit = self.max_body_size

        async def limited_receive() -> dict:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            # self.app ещё не начинал ответ (исключение пришло из чтения тела), поэтому безопасно отправить свой.
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": f"Тело запроса превышает лимит {self.max_body_size // (1024 * 1024)} МБ"},
            status_code=413,
        )
        await response(scope, receive, send)


def register_middlewares(app: FastAPI) -> None:
    # settings.cors_origins — из CORS_ORIGINS_CSV; в production переопределить реальным доменом.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS", "DELETE", "PATCH", "PUT"],
        # allow_headers — заголовки, которые браузер может слать в запросе. Фронтенд Authorization не шлёт (httpOnly-куки),
        # заголовок оставлен для внешних API-клиентов с Bearer-токеном.
        allow_headers=["Content-Type", "Authorization"],
    )

    # minimum_size=1000: маленькие ответы не сжимаем — накладные расходы не окупаются.
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.middleware("http")
    async def add_static_cache_header(request: Request, call_next):
        response = await call_next(request)
        # Cache-Control только для /static/*: имена файлов без хеша, поэтому immutable был бы ловушкой — после деплоя браузер
        # отдавал бы старый JS. max-age=3600 — компромисс.
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    @app.middleware("http")
    async def add_csp_header(request: Request, call_next):
        response = await call_next(request)

        # Документацию (Swagger UI с инлайн-скриптом и внешними доменами) исключаем из CSP: 'unsafe-inline' только для /docs
        # добавить нельзя, CSP действует на всю страницу.
        if request.url.path in ("/docs", "/redoc", "/openapi.json"):
            return response

        response.headers["Content-Security-Policy"] = (
            # По умолчанию запрещает ресурсы со сторонних доменов
            "default-src 'self'; "
            # JS вынесен в /static/js, inline-обработчики заменены на addEventListener — 'unsafe-inline' для скриптов не нужен.
            "script-src 'self'; "
            # Inline <style>-блоки; Bootstrap раздаётся локально
            "style-src 'self' 'unsafe-inline'; "
            # fetch и WebSocket к своему серверу
            "connect-src 'self' ws: wss:; "
            # data: — для data-URI (иконки, аватары)
            "img-src 'self' data:; "
            # Запрещает встраивание в <iframe> на других сайтах (clickjacking); 'self', а не 'none' — собственное встраивание не мешает.
            "frame-ancestors 'self'"
        )
        return response

    # Регистрируется последним — самая внешняя среди user-added middleware (см. докстринг MaxBodySizeMiddleware).
    app.add_middleware(MaxBodySizeMiddleware)
