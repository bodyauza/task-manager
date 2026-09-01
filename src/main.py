import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.openapi.docs import (
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
    get_redoc_html,
)
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from src.auth.endpoints import auth_router
from src.auth.user_models import Role
from src.auth.registration_endpoints import registration_router
from src.crm.client import aclose_http_client
from src.database import async_session_maker
from src.errors_handlers import register_errors_handlers
from src.middlewares import register_middlewares
from src.realtime import websocket_router
from src.routers.pages import router as pages_router
from src.routers.subtask_routers import router as subtasks_router
from src.routers.subtask_files import router as subtask_files_router   # файлы подзадач
from src.routers.task_routers import router as tasks_router
from src.routers.task_files import router as task_files_router         # файлы задач
from src.routers.uploads import router as uploads_router               # раздача /uploads/*
from src.routers.users import router as users_router

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


def register_docs_routes(app: FastAPI) -> None:
    """Swagger UI/ReDoc с бандлами, раздаваемыми локально из /static, а не с CDN.

    Иначе пришлось бы разрешать cdn.jsdelivr.net/unpkg.com в CSP (см. register_middlewares) —
    для страниц документации, которые в production обычно вообще отключены (docs_url=None).
    """

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


def create_app() -> FastAPI:
    """Собирает и настраивает FastAPI-приложение.

    Application Factory: main.py — только сборочное место (создать app, вызвать
    регистрирующие функции, подключить роутеры), а не файл, где вперемешку
    определены middleware, обработчики ошибок и docs-маршруты. Сами middleware/
    обработчики ошибок вынесены в src/middlewares.py и src/errors_handlers.py —
    каждая забота в своём модуле, main.py их только связывает.
    """
    app = FastAPI(
        title="Task Manager",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    register_docs_routes(app)
    register_errors_handlers(app)

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

    register_middlewares(app)

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

    return app


# Module-level app — нужен для `uvicorn src.main:app` (см. Dockerfile CMD) и для
# `from src.main import app` в тестах (tests/conftest.py); create_app() выше делает
# всю реальную работу, здесь только один вызов.
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, ws="auto")
