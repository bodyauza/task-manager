"""Тесты src.tasks.global_lists_tasks: синхронизация локальной таблицы project
со списком «Проект» CRM (upsert по crm_id, деактивация пропавших опций).

Вызывает _upsert_project_rows/_sync_project_table_async напрямую (await), а не
через sync_project_table.delay() — Celery-обёртка синхронна и вызывает
asyncio.run(...) внутри, что упало бы RuntimeError изнутри уже работающего
event loop pytest-asyncio (см. предупреждение в src/celery_app.py).
"""

from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from src.database import async_session_maker
from src.task_logic.models import Project
from src.tasks.global_lists_tasks import _sync_project_table_async, _upsert_project_rows


async def _all_projects(session) -> dict[str, Project]:
    rows = (await session.execute(select(Project))).scalars().all()
    return {p.crm_id: p for p in rows}


async def test_upsert_creates_new_rows():
    async with async_session_maker() as session:
        await _upsert_project_rows(session, {"3": "Альфа", "7": "Бета"})

    async with async_session_maker() as session:
        projects = await _all_projects(session)
        assert set(projects.keys()) == {"3", "7"}
        assert projects["3"].label == "Альфа"
        assert projects["3"].is_active is True


async def test_upsert_updates_existing_label_without_duplicate():
    async with async_session_maker() as session:
        await _upsert_project_rows(session, {"3": "Альфа"})
    async with async_session_maker() as session:
        await _upsert_project_rows(session, {"3": "Альфа (переименован)"})

    async with async_session_maker() as session:
        rows = (await session.execute(select(Project).where(Project.crm_id == "3"))).scalars().all()
        assert len(rows) == 1  # UNIQUE(crm_id) — upsert, не дубль
        assert rows[0].label == "Альфа (переименован)"


async def test_upsert_deactivates_options_missing_from_latest_response():
    async with async_session_maker() as session:
        await _upsert_project_rows(session, {"3": "Альфа", "7": "Бета"})
    async with async_session_maker() as session:
        # "7" пропал из ответа CRM — удалили/переименовали список так, что его больше нет.
        await _upsert_project_rows(session, {"3": "Альфа"})

    async with async_session_maker() as session:
        projects = await _all_projects(session)
        assert projects["3"].is_active is True
        assert projects["7"].is_active is False  # не удалена — деактивирована


async def test_upsert_reactivates_option_that_reappears():
    async with async_session_maker() as session:
        await _upsert_project_rows(session, {"3": "Альфа"})
    async with async_session_maker() as session:
        await _upsert_project_rows(session, {})  # список временно пуст (сбой на стороне CRM?)
    async with async_session_maker() as session:
        await _upsert_project_rows(session, {"3": "Альфа"})  # снова появилась

    async with async_session_maker() as session:
        projects = await _all_projects(session)
        assert projects["3"].is_active is True


async def test_upsert_empty_choices_deactivates_all_existing():
    async with async_session_maker() as session:
        await _upsert_project_rows(session, {"3": "Альфа", "7": "Бета"})
    async with async_session_maker() as session:
        await _upsert_project_rows(session, {})

    async with async_session_maker() as session:
        projects = await _all_projects(session)
        assert all(not p.is_active for p in projects.values())


async def test_sync_project_table_async_calls_crm_list_and_upserts():
    fake_manager = AsyncMock()
    fake_manager.get_choices.return_value = {"3": "Альфа"}
    with patch("src.tasks.global_lists_tasks.GlobalListsManager", return_value=fake_manager):
        await _sync_project_table_async()

    fake_manager.get_choices.assert_called_once()
    async with async_session_maker() as session:
        projects = await _all_projects(session)
        assert projects["3"].label == "Альфа"
