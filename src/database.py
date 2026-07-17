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
else:
    # pool_size/max_overflow: явно заданы через settings (см. src/config.py) вместо
    # неявных дефолтов SQLAlchemy — конкретное значение настраивается per-deployment
    # через DB_POOL_SIZE/DB_MAX_OVERFLOW в .env, не хардкодится здесь. NullPool
    # (тестовый режим, ветка выше) не принимает pool_size/max_overflow вовсе —
    # у него нет пула соединений как такового, поэтому это только для dev/prod.
    engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
    engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW

engine = create_async_engine(settings.ASYNC_DATABASE_URL, **engine_kwargs)

"""
**engine_kwargs при вызове функции распаковывает словарь в именованные аргументы. Интерпретатор превращает это в:
create_async_engine(settings.ASYNC_DATABASE_URL, echo=True, poolclass=NullPool)
"""

# expire_on_commit=False: после commit() атрибуты ORM-объектов не инвалидируются.
# Без этого флага обращение к полю объекта после commit вызовет lazy SELECT —
# в async-контексте это приводит к MissingGreenlet, т.к. нет активной сессии.
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    # Генератор-dependency для FastAPI Depends: сессия открывается на время
    # обработки запроса и закрывается автоматически при выходе из контекста.
    async with async_session_maker() as session:
        yield session
