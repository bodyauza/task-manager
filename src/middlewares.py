"""Регистрация middleware приложения — CORS, GZip, кеш статики, CSP.

Вынесено из main.py в отдельный register_middlewares(app): main.py остаётся
местом сборки приложения (создать FastAPI, вызвать регистрирующие функции,
подключить роутеры), а не файлом, где вперемешку живут все заботы сразу.
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from src.config import settings


def register_middlewares(app: FastAPI) -> None:
    # settings.cors_origins (src/config.py) — свойство над CORS_ORIGINS_CSV, читается из
    # переменной окружения. Дефолт — только localhost/127.0.0.1 для dev/test; в production
    # ОБЯЗАТЕЛЬНО переопределить CORS_ORIGINS_CSV в .env реальным доменом приложения.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS", "DELETE", "PATCH", "PUT"],
        allow_headers=[
            "Content-Type",
            "Set-Cookie",
            "Access-Control-Allow-Headers",
            "Access-Control-Allow-Origin",
            "Authorization",
        ],
    )

    # minimum_size=1000: не сжимать совсем маленькие ответы — сам overhead gzip-заголовков
    # и CPU на сжатие/разжатие для них не окупается. JS/CSS/JSON-ответы обычно заметно
    # больше этого порога и от сжатия реально выигрывают.
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.middleware("http")
    async def add_static_cache_header(request: Request, call_next):
        response = await call_next(request)
        # Cache-Control только для /static/*: имена файлов НЕ версионируются (нет хеша
        # в пути вроде task-board.abcd1234.js), поэтому immutable/год кеша здесь были бы
        # ловушкой — после деплоя новой версии JS браузер продолжал бы отдавать старый
        # файл из кеша до истечения срока. max-age=3600 — разумный компромисс: ощутимо
        # снижает число повторных запросов статики внутри одной сессии пользователя, но
        # не рискует держать устаревший JS сутками после деплоя.
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    @app.middleware("http")
    async def add_csp_header(request: Request, call_next):
        response = await call_next(request)

        # Swagger UI (FastAPI 0.115+) генерирует HTML с инлайн-<script> для инициализации
        # SwaggerUIBundle и загружает JS/CSS/favicon с внешних доменов (cdn.jsdelivr.net,
        # fastapi.tiangolo.com). Добавить 'unsafe-inline' только для /docs — нельзя: CSP
        # применяется ко всей странице. Исключаем маршруты документации из CSP полностью:
        # в production Swagger обычно отключается через app = FastAPI(docs_url=None).
        if request.url.path in ("/docs", "/redoc", "/openapi.json"):
            return response

        response.headers["Content-Security-Policy"] = (
            # Запрещает загрузку любых ресурсов со сторонних доменов по умолчанию
            "default-src 'self'; "
            # JS вынесен в /static/js/*.js; inline onclick-обработчики заменены на
            # addEventListener — 'unsafe-inline' больше не требуется.
            "script-src 'self'; "
            # Inline <style>-блоки; Bootstrap CSS теперь раздаётся локально из /static
            "style-src 'self' 'unsafe-inline'; "
            # Разрешает fetch-запросы и WebSocket к своему серверу
            "connect-src 'self' ws: wss:; "
            # data: — для возможных data-URI (иконки, аватары)
            "img-src 'self' data:"
        )
        return response
