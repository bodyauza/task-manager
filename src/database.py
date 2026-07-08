from typing import AsyncGenerator
from sqlalchemy import MetaData, NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import settings


metadata = MetaData()


class Base(DeclarativeBase):
    # Явная передача metadata гарантирует, что все модели регистрируются
    # в одном объекте MetaData — Alembic использует его при генерации миграций.
    metadata = metadata


_is_prod = settings.is_production
# NullPool нужен только в тестах: pytest-asyncio создаёт новый event loop на каждый
# тест, а QueuePool держит соединения привязанными к старому loop — при переходе
# соединение оказывается «чужим» и asyncpg бросает RuntimeError.
# В dev-режиме QueuePool оставляем: одиночный event loop uvicorn'а живёт весь запуск,
# и переиспользование соединений даёт ощутимый прирост скорости при разработке.
_is_test = settings.api_mode in ("test", "testing")

# echo=True в dev/test режимах: SQLAlchemy логирует все SQL-запросы.
# В production отключается — в продакшне объём логов SQL нерентабелен.
engine_kwargs = {"echo": not _is_prod}
if _is_test:
    engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(settings.ASYNC_DATABASE_URL, **engine_kwargs)

# expire_on_commit=False: после commit() атрибуты ORM-объектов не инвалидируются.
# Без этого флага обращение к полю объекта после commit вызовет lazy SELECT —
# в async-контексте это приводит к MissingGreenlet, т.к. нет активной сессии.
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    # Генератор-dependency для FastAPI Depends: сессия открывается на время
    # обработки запроса и закрывается автоматически при выходе из контекста.
    async with async_session_maker() as session:
        yield session
