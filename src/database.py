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


_is_prod = settings.api_mode in ("prod", "production")

# echo=True в dev/test режимах: SQLAlchemy логирует все SQL-запросы.
# В production отключается — в продакшне объём логов SQL нерентабелен.
engine_kwargs = {"echo": not _is_prod}
if not _is_prod:
    # NullPool отключает пул соединений: каждый запрос открывает и закрывает
    # соединение явно. Нужно для тестов — pytest-asyncio открывает несколько
    # event loop подряд, а пул держит соединения открытыми между ними,
    # что приводит к ошибкам «connection already closed».
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
