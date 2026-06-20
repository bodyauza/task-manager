import logging
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from src.auth.auth_config import fastapi_users
from src.auth.endpoints import auth_router
from src.auth.models import Role
from src.auth.user_schemas import UserCreate, UserRead
from src.config import settings
from src.database import async_session_maker
from src.routers.pages import router as pages_router
from src.routers.tasks import router as tasks_router
from src.routers.users import router as users_router


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
    await create_initial_roles()
    yield


app = FastAPI(title="Task Manager", lifespan=lifespan)

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

app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["Authentication"],
)
app.include_router(auth_router)
app.include_router(tasks_router)
app.include_router(users_router)
app.include_router(pages_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, ws="auto")
