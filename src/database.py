from typing import AsyncGenerator
from sqlalchemy import MetaData, NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import settings


metadata = MetaData()

class Base(DeclarativeBase):
    metadata = metadata  # Явная привязка


_is_prod = settings.api_mode in ("prod", "production")

engine_kwargs = {"echo": not _is_prod}
if not _is_prod:
    engine_kwargs["poolclass"] = NullPool

# Создаем асинхронный движок для подключения к базе данных
engine = create_async_engine(settings.ASYNC_DATABASE_URL, **engine_kwargs)

# Создаем фабрику асинхронных сессий с использованием ранее созданного движка
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Асинхронная функция для получения сессии базы данных
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
