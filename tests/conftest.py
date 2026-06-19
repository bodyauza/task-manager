import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, text

from src.auth.models import Role
from src.database import async_session_maker, engine, Base
from src.main import app


@pytest_asyncio.fixture(autouse=True)
async def setup_and_reset():
    """
    Runs before every test:
      - creates tables (idempotent create_all)
      - seeds role rows 1 and 2 if missing

    Runs after every test:
      - truncates user-data tables so tests are independent
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        existing_ids = {
            r.id for r in (
                await session.execute(select(Role).where(Role.id.in_([1, 2])))
            ).scalars().all()
        }
        roles = []
        if 1 not in existing_ids:
            roles.append(Role(id=1, name="user", permissions=["read", "write"]))
        if 2 not in existing_ids:
            roles.append(Role(id=2, name="admin", permissions=["read", "write", "delete"]))
        if roles:
            session.add_all(roles)
            await session.commit()

    yield

    async with async_session_maker() as session:
        await session.execute(text("TRUNCATE TABLE task, person RESTART IDENTITY CASCADE"))
        await session.commit()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
