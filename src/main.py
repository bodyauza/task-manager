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

from src.admin import setup_admin
from src.auth.endpoints import auth_router
from src.auth.user_models import Role
from src.auth.registration_endpoints import registration_router
from src.config import settings
from src.crm.client import aclose_http_client
from src.database import async_session_maker
from src.errors_handlers import register_errors_handlers
from src.middlewares import register_middlewares
from src.realtime import connection_manager, websocket_router
from src.routers.admin import admin_router
from src.routers.pages import router as pages_router
from src.routers.subtask_routers import router as subtasks_router
from src.routers.subtask_files import router as subtask_files_router
from src.routers.task_routers import router as tasks_router
from src.routers.task_files import router as task_files_router
from src.routers.uploads import router as uploads_router
from src.routers.users import router as users_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def create_initial_roles():
    # Миграции не создают начальные роли; INSERT только для отсутствующих id (идемпотентно).
    try:
        async with async_session_maker() as session:
            result = await session.execute(select(Role).where(Role.id.in_([1, 2])))
            existing_ids = {role.id for role in result.scalars().all()}

            roles_to_add = []
            if 1 not in existing_ids:
                roles_to_add.append(Role(id=1, name="user"))
            if 2 not in existing_ids:
                roles_to_add.append(Role(id=2, name="admin"))

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
    await create_initial_roles()
    connection_manager.start_listening()
    yield
    await connection_manager.stop_listening()
    # Закрываем общий httpx.AsyncClient CRM, иначе соединения пула остаются открытыми.
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
    """Swagger UI/ReDoc с локальными бандлами из /static (без CDN в CSP).

    Регистрируется только при settings.docs_enabled.
    """
    if not settings.docs_enabled:
        return

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
            swagger_favicon_url=str(request.url_for("static", path="/img/favicon.svg")),
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
            redoc_favicon_url=str(request.url_for("static", path="/img/favicon.svg")),
            with_google_fonts=False,
        )


def create_app() -> FastAPI:
    """Собирает FastAPI-приложение: middleware, обработчики ошибок и роутеры."""
    app = FastAPI(
        title="Task Manager",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        # openapi_url=None отключает и /openapi.json: схема раскрывает все маршруты.
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    register_docs_routes(app)
    register_errors_handlers(app)

    _static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

    app.mount("/static",
              StaticFiles(directory=_static_dir),
              name="static")

    register_middlewares(app)

    app.include_router(registration_router)
    app.include_router(auth_router)
    app.include_router(tasks_router)
    app.include_router(websocket_router)
    app.include_router(task_files_router)
    app.include_router(subtasks_router)
    app.include_router(subtask_files_router)
    app.include_router(uploads_router)
    app.include_router(users_router)
    # admin_router — ДО setup_admin(): Mount на /admin перехватил бы /admin/* и ответил 404.
    app.include_router(admin_router)
    setup_admin(app)
    app.include_router(pages_router)

    return app


# Нужен для `uvicorn src.main:app` и тестов.
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, ws="auto")
