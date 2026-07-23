import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.gzip import GZipMiddleware

from src.auth.auth_config import fastapi_users
from src.auth.endpoints import auth_router
from src.auth.models import Role
from src.auth.registration_endpoints import registration_router
from src.auth.user_schemas import UserCreate, UserRead
from src.config import settings
from src.crm.client import aclose_http_client
from src.database import async_session_maker
from src.realtime import websocket_router
from src.routers.pages import router as pages_router
from src.routers.subtasks import router as subtasks_router
from src.routers.subtask_files import router as subtask_files_router   # файлы подзадач
from src.routers.tasks import router as tasks_router
from src.routers.task_files import router as task_files_router         # файлы задач
from src.routers.uploads import router as uploads_router               # раздача /uploads/*
from src.routers.users import router as users_router

from fastapi.openapi.docs import (
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
    get_redoc_html,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# async def create_tables():
#     """
#     Создаёт таблицы через Base.metadata.create_all.
#
#     НЕ использовать вместе с Alembic: create_all обходит таблицу
#     alembic_version, и следующий `alembic upgrade head` упадёт с ошибкой
#     "table already exists".
#
#     Для инициализации схемы использовать:
#         alembic upgrade head
#
#     Если схема уже создана через create_all и нужно перейти на Alembic:
#         alembic stamp head   # зафиксировать текущую ревизию без применения
#     """
#     async with engine.begin() as conn:
#         await conn.run_sync(Base.metadata.create_all)


async def create_initial_roles():
    # Alembic управляет схемой, не данными — начальные роли не закладываются в миграции.
    # Идемпотентность: INSERT выполняется только для отсутствующих id;
    # повторный запуск приложения не дублирует записи.
    try:
        async with async_session_maker() as session:
            result = await session.execute(select(Role).where(Role.id.in_([1, 2])))
            existing_ids = {role.id for role in result.scalars().all()}

            roles_to_add = []
            if 1 not in existing_ids:
                roles_to_add.append(Role(id=1, name="user", permissions=["read", "write"]))
            if 2 not in existing_ids:
                roles_to_add.append(Role(id=2, name="admin", permissions=["read", "write", "delete"]))

            if roles_to_add:
                session.add_all(roles_to_add)
                await session.commit()
                logger.info("Базовые роли созданы: %s", [r.name for r in roles_to_add])
            else:
                logger.info("Базовые роли уже существуют")
    except Exception as e:
        logger.error("Ошибка при создании базовых ролей: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # lifespan заменяет устаревший on_event("startup"/"shutdown") начиная с FastAPI 0.93.
    # Код до yield — инициализация при старте; после yield — завершение при остановке.
    #
    # Директория uploads/ отдельно здесь не создаётся: src.routers.uploads (импортирован
    # выше, до определения lifespan) уже гарантирует её существование на уровне модуля —
    # через UPLOAD_ROOT.mkdir(...), путь к которому вычисляется от __file__, а не от cwd
    # процесса.
    await create_initial_roles()
    yield
    # Закрываем разделяемый httpx.AsyncClient CRM-модуля, иначе TCP-соединения
    # из его пула остаются открытыми до завершения процесса. Парная операция к
    # ленивому созданию клиента в src/crm/client.py::_get_shared_http_client().
    await aclose_http_client()

"""
uvicorn запускает приложение
        │
        ▼
[1] create_initial_roles()   ← INSERT roles если не существуют
        │
        ▼
[2] yield ─────────────────── приложение работает, принимает HTTP-запросы
        │
        │   (Ctrl+C / SIGTERM)
        ▼
[3] сюда можно добавить cleanup: закрыть пул, flush логов
"""


app = FastAPI(title="Task Manager",
              lifespan=lifespan,
              docs_url=None,
              redoc_url=None)

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html(request: Request):
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url=str(
            request.url_for(
                "static",
                path="/js/swagger-ui-bundle.js",
            ),
        ),
        swagger_css_url=str(
            request.url_for(
                "static",
                path="/css/swagger-ui.css",
            ),
        ),
    )


@app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
async def swagger_ui_redirect():
    return get_swagger_ui_oauth2_redirect_html()


@app.get("/redoc", include_in_schema=False)
async def redoc_html(request: Request):
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=app.title + " - ReDoc",
        redoc_js_url=str(
            request.url_for(
                "static",
                path="/js/redoc.standalone.js",
            ),
        ),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Браузерная навигация посылает Accept: text/html — отвечаем редиректом на логин.
    # JS fetch посылает Accept: */* — получает JSON 401 и сам запускает цикл обновления токена.
    # Без этого разделения браузер показывал бы сырой JSON {"detail":"Unauthorized"}
    # при переходе на защищённый URL без авторизации.
    if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(url="/", status_code=302)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

# __file__ — абсолютный путь к main.py в файловой системе.
# os.path.abspath(__file__) защищает от случаев, когда __file__ содержит
# относительный путь (зависит от способа запуска: uvicorn, pytest, IDE).
# os.path.dirname(...) обрезает имя файла, оставляя директорию src/.
# os.path.join(..., "static") добавляет подпапку → абсолютный путь к src/static/.
_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# app.mount регистрирует отдельное ASGI-приложение (StaticFiles) на префиксе /static.
# Все запросы вида GET /static/js/task-board.js перехватываются StaticFiles
# и не доходят до обычных FastAPI-роутеров.
# name="static" — псевдоним для url_path_for("static", path="...") в шаблонах Jinja2.
app.mount("/static",
          StaticFiles(directory=_static_dir),
          name="static")

# cors_origins задан для dev-окружения. В production список нужно сузить
# до реального домена приложения и убрать все localhost-адреса.
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
        # Inline <style>-блоки и Bootstrap CSS с jsdelivr CDN
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        # Разрешает fetch-запросы и WebSocket к своему серверу; cdn.jsdelivr.net нужен для source map Bootstrap
        "connect-src 'self' ws: wss: https://cdn.jsdelivr.net; "
        # data: — для возможных data-URI (иконки, аватары)
        "img-src 'self' data:"
    )
    return response


app.include_router(registration_router)   # /auth/register/request-code, verify-code, complete
app.include_router(auth_router)           # /auth/login, /auth/logout, /auth/access-token
app.include_router(tasks_router)
app.include_router(websocket_router)      # /ws/tasks/{client_id}
app.include_router(task_files_router)     # /tasks/{id}/specification, /tasks/{id}/files
app.include_router(subtasks_router)
app.include_router(subtask_files_router)  # /subtasks/{id}/specification, /subtasks/{id}/files
app.include_router(uploads_router)        # /uploads/{file_path} — аутентифицированная раздача файлов
app.include_router(users_router)
app.include_router(pages_router)          # HTML-страницы монтируются последними


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, ws="auto")
