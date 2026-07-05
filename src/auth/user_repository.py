from fastapi import Depends
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
from src.database import get_async_session


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    # SQLAlchemyUserDatabase — адаптер fastapi-users для SQLAlchemy.
    # Реализует методы get_by_email(), create(), update(), которые UserManager
    # вызывает через self.user_db. Инжектируется через Depends в get_user_manager().
    yield SQLAlchemyUserDatabase(session, User)
