import asyncio
from typing import Awaitable, AsyncGenerator, TypeVar
from sqlalchemy import MetaData, NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import settings

_T = TypeVar("_T")


metadata = MetaData()


class Base(DeclarativeBase):
    # Единый MetaData для всех моделей (использует Alembic).
    metadata = metadata


_is_prod = settings.is_production
# NullPool только в тестах: pytest-asyncio создаёт новый event loop на тест, а QueuePool держал бы соединения старого loop.
_is_test = settings.api_mode in ("test", "testing")

# echo=True в dev/test; в production отключено.
engine_kwargs = {"echo": not _is_prod}
if _is_test:
    engine_kwargs["poolclass"] = NullPool
else:
    # pool_size/max_overflow задаются через settings; NullPool их не принимает.
    engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
    engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW

engine = create_async_engine(settings.ASYNC_DATABASE_URL, **engine_kwargs)

"""
**engine_kwargs при вызове функции распаковывает словарь в именованные аргументы. Интерпретатор превращает это в:
create_async_engine(settings.ASYNC_DATABASE_URL, echo=True, poolclass=NullPool)
"""

# expire_on_commit=False: иначе атрибуты после commit() подгружались бы лениво (MissingGreenlet в async).
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    # Сессия на время запроса.
    async with async_session_maker() as session:
        yield session


def run_isolated(coro: Awaitable[_T]) -> _T:
    """Выполняет корутину в своём event loop (asyncio.run) и освобождает пул `engine` после.

    Celery-воркер делает много asyncio.run() подряд, и пул иначе переиспользовал бы asyncpg-соединение закрытого loop
    (RuntimeError «attached to a different loop»). engine общий с веб-процессом, где QueuePool нужен, поэтому
    чиним явным dispose(), а не сменой poolclass.
    """
    async def _wrapper() -> _T:
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_wrapper())
