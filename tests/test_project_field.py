"""Тесты поля «Проект» (docs/project_field_crm_implementation_guide.md):
резолвинг CRM-ID → project_id через локальную таблицу project (без похода в
CRM, см. src/services/tasks.py::_resolve_project), и сквозной HTTP-флоу
create/update/list/search/get с этим полем.
"""

import json

import pytest
from sqlalchemy import select

from src.database import async_session_maker
from src.services import tasks as task_service
from src.task_logic.models import CrmOutbox, Project, Task
from src.task_logic.task_schemas import TaskCreate, TaskUpdate
from tests.conftest import register_user
from tests.test_task_service import _make_user, _outbox_rows_for

EMAIL = "project_field@example.com"


async def _seed_project(crm_id: str = "3", label: str = "Альфа", is_active: bool = True) -> Project:
    # session.refresh() не нужен: async_session_maker сконфигурирован с
    # expire_on_commit=False (src/database.py) — project.id уже заполнен через
    # INSERT ... RETURNING id при commit(), тот же принцип, что и везде в
    # services/tasks.py (см. комментарии там про db.refresh()).
    async with async_session_maker() as session:
        project = Project(crm_id=crm_id, label=label, is_active=is_active)
        session.add(project)
        await session.commit()
        return project


# ── _resolve_project (юнит, без HTTP) ───────────────────────────────────────────

async def test_resolve_project_none_returns_none():
    async with async_session_maker() as session:
        assert await task_service._resolve_project(None, session) is None


async def test_resolve_project_empty_string_returns_none_without_query():
    async with async_session_maker() as session:
        # "" не должно совпасть ни с одной строкой project, даже пустой crm_id
        # там в принципе невозможен (NOT NULL) — но проверяем именно поведение,
        # не полагаясь на отсутствие данных.
        assert await task_service._resolve_project("", session) is None


async def test_resolve_project_unknown_crm_id_raises_422():
    from fastapi import HTTPException

    async with async_session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await task_service._resolve_project("999", session)
        assert exc_info.value.status_code == 422


async def test_resolve_project_found_returns_row():
    project = await _seed_project(crm_id="3", label="Альфа")
    async with async_session_maker() as session:
        row = await task_service._resolve_project("3", session)
        assert row is not None
        assert row.id == project.id
        assert row.label == "Альфа"


async def test_resolve_project_inactive_raises_422():
    """Деактивированная опция (пропала из последнего ответа CRM,
    src/tasks/global_lists_tasks.py::_upsert_project_rows) не должна быть
    выбираемой для НОВОГО назначения — is_active=False фильтруется в WHERE."""
    from fastapi import HTTPException

    await _seed_project(crm_id="5", label="Устаревший", is_active=False)
    async with async_session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await task_service._resolve_project("5", session)
        assert exc_info.value.status_code == 422


# ── create_task / update_task (сервисный слой) ──────────────────────────────────

async def test_create_task_with_project_sets_project_id_and_response_fields():
    project = await _seed_project(crm_id="3", label="Альфа")
    async with async_session_maker() as session:
        user = await _make_user(session)

        result = await task_service.create_task(
            session, user, TaskCreate(title="With project", description="d", project="3"),
        )

        assert result.project == "Альфа"
        assert result.project_option_id == "3"
        # CRM получает исходный CRM-ID как есть, не резолвленную метку/id — через
        # payload outbox-строки 'create', т.к. сам CRM-вызов теперь в Celery.
        rows = await _outbox_rows_for(session, "task", result.id)
        assert rows[0].payload["project"] == "3"

        db_task = (
            await session.execute(select(Task).where(Task.id == result.id))
        ).scalar_one()
        assert db_task.project_id == project.id


async def test_create_task_with_unknown_project_returns_422_and_creates_nothing():
    async with async_session_maker() as session:
        user = await _make_user(session)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await task_service.create_task(
                session, user, TaskCreate(title="Should not exist", description="d", project="404"),
            )
        assert exc_info.value.status_code == 422
        # Резолвинг — первая строка функции, до вставки: ни задачи, ни outbox-строки.
        remaining = (
            await session.execute(select(Task).where(Task.title == "Should not exist"))
        ).scalar_one_or_none()
        assert remaining is None
        assert (await session.execute(select(CrmOutbox))).scalar_one_or_none() is None


async def test_update_task_project_empty_string_clears_it():
    project = await _seed_project(crm_id="3", label="Альфа")
    async with async_session_maker() as session:
        user = await _make_user(session)
        created = await task_service.create_task(
            session, user, TaskCreate(title="Orig", description="d", project="3"),
        )
        assert created.project == "Альфа"

        result = await task_service.update_task(session, user, created.id, TaskUpdate(project=""))

        assert result.project is None
        assert result.project_option_id is None
        db_task = (await session.execute(select(Task).where(Task.id == created.id))).scalar_one()
        assert db_task.project_id is None
        # update-outbox-строка не создаётся: задача ещё не синхронизирована с CRM
        # (crm_task_id is None) — синхронизировать в CRM пока нечего.
        rows = await _outbox_rows_for(session, "task", created.id)
        assert not any(r.operation == "update" for r in rows)


async def test_update_task_without_project_key_leaves_it_unchanged():
    await _seed_project(crm_id="3", label="Альфа")
    async with async_session_maker() as session:
        user = await _make_user(session)
        created = await task_service.create_task(
            session, user, TaskCreate(title="Orig", description="d", project="3"),
        )

        result = await task_service.update_task(session, user, created.id, TaskUpdate(title="Renamed"))

        assert result.title == "Renamed"
        assert result.project == "Альфа"
        assert result.project_option_id == "3"


# ── list_tasks / search_tasks / get_task — project_option_id для предзаполнения ─

async def test_list_tasks_includes_project_option_id():
    await _seed_project(crm_id="3", label="Альфа")
    async with async_session_maker() as session:
        user = await _make_user(session)
        await task_service.create_task(session, user, TaskCreate(title="T1", description="d", project="3"))

        results, _total = await task_service.list_tasks(session, skip=0, limit=10)
        assert results[0].project == "Альфа"
        assert results[0].project_option_id == "3"


async def test_search_tasks_returns_task_response_with_project():
    await _seed_project(crm_id="3", label="Альфа")
    async with async_session_maker() as session:
        user = await _make_user(session)
        await task_service.create_task(session, user, TaskCreate(title="Findme", description="d", project="3"))

        results, total = await task_service.search_tasks(session, "Findme", skip=0, limit=10)
        assert total == 1
        assert results[0].project == "Альфа"
        assert results[0].project_option_id == "3"


async def test_get_task_deactivated_project_still_resolves_via_relationship():
    """Строка project с is_active=False не удаляется (см. Project.__doc__) —
    задача, созданная ДО деактивации, должна продолжать показывать метку через
    уже установленный FK, а не терять её."""
    await _seed_project(crm_id="9", label="Скоро устареет")
    async with async_session_maker() as session:
        user = await _make_user(session)
        created = await task_service.create_task(session, user, TaskCreate(title="Historic", description="d", project="9"))

        # Опция пропала из CRM после того, как задача её выбрала. Перечитываем
        # строку в ЭТОЙ сессии, а не мутируем detached-объект из _seed_project
        # (объект другой, уже закрытой сессии) — добавление detached-инстанса с
        # уже существующим PK в новую сессию через session.add() не гарантирует
        # чистое переприсоединение в SQLAlchemy, это анти-паттерн.
        project = (
            await session.execute(select(Project).where(Project.crm_id == "9"))
        ).scalar_one()
        project.is_active = False
        await session.commit()

        result = await task_service.get_task(session, created.id)
        assert result.project == "Скоро устареет"
        assert result.project_option_id == "9"


# ── HTTP-уровень ─────────────────────────────────────────────────────────────

async def test_http_create_task_with_project(client, mock_smtp):
    await _seed_project(crm_id="3", label="Альфа")
    await register_user(client, mock_smtp, EMAIL)
    await client.post("/auth/login", data={"username": EMAIL, "password": "Password1!"})

    resp = await client.post(
        "/create-task/",
        data={"data": json.dumps({"title": "HTTP task", "description": "d", "project": "3"})},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["project"] == "Альфа"
    assert body["project_option_id"] == "3"


async def test_http_create_task_with_unknown_project_returns_422(client, mock_smtp):
    await register_user(client, mock_smtp, EMAIL)
    await client.post("/auth/login", data={"username": EMAIL, "password": "Password1!"})

    resp = await client.post(
        "/create-task/",
        data={"data": json.dumps({"title": "Bad project", "description": "d", "project": "404"})},
    )
    assert resp.status_code == 422


async def test_http_patch_task_project(client, mock_smtp):
    await _seed_project(crm_id="3", label="Альфа")
    await register_user(client, mock_smtp, EMAIL)
    await client.post("/auth/login", data={"username": EMAIL, "password": "Password1!"})

    create_resp = await client.post(
        "/create-task/", data={"data": json.dumps({"title": "To patch", "description": "d"})},
    )
    task_id = create_resp.json()["id"]

    resp = await client.patch(f"/tasks/{task_id}", json={"project": "3"})
    assert resp.status_code == 200
    assert resp.json()["project"] == "Альфа"
