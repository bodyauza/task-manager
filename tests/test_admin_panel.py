"""Тесты sqladmin-панели (/admin): доступ только admin, списки, скрытые/read-only поля, отсутствие конфликта с /admin/*."""

import datetime

import pytest
from httpx import AsyncClient

from src.admin.formatters import _encode_upload_path, local_datetime_formatter
from src.database import async_session_maker
from src.task_logic.models import CrmOutbox, Project, Subtask, Task
from tests.conftest import ADMIN_PANEL_EMAIL, login_as_admin, promote_to_admin, register_user

ADMIN_EMAIL = ADMIN_PANEL_EMAIL
USER_EMAIL = "user_panel@example.com"
PASSWORD = "Password1!"


async def _admin_login(client: AsyncClient, email: str, password: str = PASSWORD):
    return await client.post(
        "/admin/login", data={"username": email, "password": password}, follow_redirects=False,
    )


async def test_admin_requires_login(client: AsyncClient):
    r = await client.get("/admin/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/admin/login")


async def test_non_admin_cannot_login(client: AsyncClient, mock_smtp: dict):
    await register_user(client, mock_smtp, USER_EMAIL, PASSWORD)
    r = await _admin_login(client, USER_EMAIL)
    assert r.status_code == 400
    r = await client.get("/admin/user/list", follow_redirects=False)
    assert r.status_code == 302


async def test_wrong_password_rejected(client: AsyncClient, mock_smtp: dict):
    await register_user(client, mock_smtp, ADMIN_EMAIL, PASSWORD)
    await promote_to_admin(ADMIN_EMAIL)
    r = await _admin_login(client, ADMIN_EMAIL, "wrong")
    assert r.status_code == 400


async def test_admin_session_cookie_has_hardened_attributes(client: AsyncClient, mock_smtp: dict):
    """Сессионная кука sqladmin: SameSite=Strict и срок 4 часа — защита от CSRF через GET-действие CrmOutboxAdmin (ссылка с чужого сайта)."""
    await register_user(client, mock_smtp, ADMIN_EMAIL, PASSWORD)
    await promote_to_admin(ADMIN_EMAIL)

    r = await _admin_login(client, ADMIN_EMAIL, PASSWORD)

    set_cookie = r.headers.get("set-cookie", "")
    assert "session=" in set_cookie
    assert "samesite=strict" in set_cookie.lower()
    assert "max-age=14400" in set_cookie.lower()  # 4 часа, а не 14 дней по умолчанию
    # https_only=settings.is_production — тесты идут не в проде, Secure не ожидается;
    # settings.is_production=True в реальном деплое включит его отдельно (не наша ветка).
    from src.config import settings
    assert ("secure" in set_cookie.lower()) == settings.is_production


@pytest.mark.parametrize(
    "path", ["user", "role", "registration-pending", "task", "subtask", "project", "crm-outbox"],
)
async def test_list_pages_ok(admin_client: AsyncClient, path: str):
    r = await admin_client.get(f"/admin/{path}/list")
    assert r.status_code == 200, r.text[:300]


async def test_user_list_hides_password_hash(admin_client: AsyncClient):
    r = await admin_client.get("/admin/user/list")
    assert ADMIN_EMAIL in r.text
    assert "$2b$" not in r.text
    r = await admin_client.get("/admin/user/details/1")
    assert r.status_code == 200
    assert "$2b$" not in r.text


async def test_user_delete_disabled_but_create_available(admin_client: AsyncClient):
    # Создание — через UserManager.create() (tests/test_user_admin.py), удаление
    # остаётся только в продуктовом DELETE /users/{id}.
    assert (await admin_client.get("/admin/user/create")).status_code == 200
    r = await admin_client.delete("/admin/user/delete?pks=1")
    assert r.status_code == 403


async def test_project_and_outbox_read_only(admin_client: AsyncClient):
    for path in ("project", "crm-outbox"):
        assert (await admin_client.get(f"/admin/{path}/create")).status_code == 403
        assert (await admin_client.get(f"/admin/{path}/edit/1")).status_code == 403
        assert (await admin_client.delete(f"/admin/{path}/delete?pks=1")).status_code == 403


async def test_task_delete_disabled_and_edit_form_excludes_crm_fields(admin_client: AsyncClient):
    async with async_session_maker() as session:
        session.add(Task(title="t", description="d", owner_id=1, crm_task_id=42, sync_status="synced"))
        await session.commit()

    assert (await admin_client.delete("/admin/task/delete?pks=1")).status_code == 403

    r = await admin_client.get("/admin/task/edit/1")
    assert r.status_code == 200
    for hidden in ("crm_task_id", "crm_shard", "sync_status", "specification_path", "other_file_paths"):
        assert f'name="{hidden}"' not in r.text
    assert 'name="title"' in r.text


async def test_task_details_show_file_links(admin_client: AsyncClient):
    async with async_session_maker() as session:
        session.add(Task(
            title="t", description="d", owner_id=1,
            specification_path="tasks/1/specification/a_tz.pdf",
            other_file_paths=["tasks/1/other/b.pdf"],
        ))
        await session.commit()
    r = await admin_client.get("/admin/task/details/1")
    assert r.status_code == 200
    assert "/uploads/tasks/1/specification/a_tz.pdf" in r.text
    assert "/uploads/tasks/1/other/b.pdf" in r.text


async def test_task_details_file_link_with_special_chars_is_url_encoded(admin_client: AsyncClient):
    """URL-кодирование href: без него имя с "#"/"?" обрывало бы ссылку."""
    async with async_session_maker() as session:
        task = Task(
            title="t2", description="d", owner_id=1,
            specification_path="tasks/2/specification/a1b2c3d4_отчёт #1.pdf",
        )
        session.add(task)
        await session.commit()
        task_id = task.id
    r = await admin_client.get(f"/admin/task/details/{task_id}")
    assert r.status_code == 200
    assert 'href="/uploads/tasks/2/specification/a1b2c3d4_%D0%BE%D1%82%D1%87%D1%91%D1%82%20%231.pdf"' in r.text
    # Необработанный "#" в href не встречается — иначе он оборвал бы путь на fragment.
    assert 'href="/uploads/tasks/2/specification/a1b2c3d4_отчёт #1.pdf"' not in r.text


def test_encode_upload_path_percent_encodes_reserved_chars_but_keeps_slash():
    assert _encode_upload_path("tasks/2/specification/a1b2c3d4_отчёт #1.pdf") == (
        "tasks/2/specification/a1b2c3d4_%D0%BE%D1%82%D1%87%D1%91%D1%82%20%231.pdf"
    )
    # Обычное имя без спецсимволов не меняется (не ломает уже существующий тест выше).
    assert _encode_upload_path("tasks/1/specification/a_tz.pdf") == "tasks/1/specification/a_tz.pdf"


async def test_outbox_details_show_payload(admin_client: AsyncClient):
    async with async_session_maker() as session:
        session.add(CrmOutbox(
            aggregate_type="task", aggregate_id=7, operation="create", payload={"marker": "zz9"},
            last_error="RuntimeError: ERRMARK",
        ))
        await session.commit()
    r = await admin_client.get("/admin/crm-outbox/list")
    assert "create" in r.text and "zz9" not in r.text and "ERRMARK" not in r.text
    r = await admin_client.get("/admin/crm-outbox/details/1")
    assert "zz9" in r.text and "ERRMARK" in r.text


async def test_registration_pending_hides_code_hash(admin_client: AsyncClient):
    from src.auth.user_models import RegistrationPending
    async with async_session_maker() as session:
        pending = RegistrationPending(
            email="pend@example.com", code_hash="SECRETHASH",
            expires_at=datetime.datetime.now(datetime.timezone.utc),
        )
        session.add(pending)
        await session.commit()
        pending_id = pending.id
    for url in ("/admin/registration-pending/list", f"/admin/registration-pending/details/{pending_id}"):
        r = await admin_client.get(url)
        assert r.status_code == 200
        assert "pend@example.com" in r.text
        assert "SECRETHASH" not in r.text


def test_local_datetime_formatter_converts_utc_to_admin_timezone():
    value = datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.timezone.utc)
    assert local_datetime_formatter(value) == "2026-01-01 12:00:00"
    naive = datetime.datetime(2026, 1, 1, 9, 0)
    assert local_datetime_formatter(naive) == "2026-01-01 12:00:00"


async def test_other_admin_routes_not_shadowed_by_sqladmin(client: AsyncClient, mock_smtp: dict):
    """/admin/crm-sync* обслуживаются admin_router (подключён до Mount sqladmin),
    а не отвечают 404 от sqladmin."""
    await register_user(client, mock_smtp, USER_EMAIL, PASSWORD)
    await client.post("/auth/login", data={"username": USER_EMAIL, "password": PASSWORD})
    for path in ("/admin/crm-sync", "/admin/crm-sync-status/tasks"):
        r = await client.get(path, follow_redirects=False)
        assert r.status_code == 403, (path, r.status_code)
    r = await client.post("/admin/crm-options/refresh")
    assert r.status_code == 403


async def test_refresh_crm_options_as_admin_queues_sync_project_table(client: AsyncClient, mock_smtp: dict):
    """/admin/crm-options/refresh: .delay() вызывается через asyncio.to_thread — проверяем, что вызов доходит до Celery и ответ 202.
    Эндпоинт защищён require_role("admin") (JWT), а не сессией sqladmin, поэтому нужен client + login_as_admin.
    """
    from unittest.mock import Mock, patch

    await login_as_admin(client, mock_smtp, "refresh_crm_admin@example.com")

    with patch("src.routers.admin.sync_project_table.delay", Mock()) as delay:
        r = await client.post("/admin/crm-options/refresh")
    assert r.status_code == 202
    assert r.json() == {"status": "queued"}
    delay.assert_called_once_with()


async def test_retry_action_requeues_only_failed_rows(admin_client: AsyncClient):
    """failed-строка возвращается в очередь; done-строка другой задачи порядка не касается."""
    from unittest.mock import patch

    async with async_session_maker() as session:
        task = Task(title="t", description="d", owner_id=1, crm_task_id=42, sync_status="failed")
        other_task = Task(title="o", description="d", owner_id=1, crm_task_id=43, sync_status="synced")
        session.add_all([task, other_task])
        await session.flush()
        failed = CrmOutbox(
            aggregate_type="task", aggregate_id=task.id, operation="update", status="failed",
            attempts=5, shard="shard_0", payload={"crm_task_id": 42}, last_error="RuntimeError: x",
        )
        done = CrmOutbox(
            aggregate_type="task", aggregate_id=other_task.id, operation="update", status="done",
            attempts=1, shard="shard_0", payload={"crm_task_id": 43},
        )
        session.add_all([failed, done])
        await session.commit()
        task_id, failed_id, done_id = task.id, failed.id, done.id

    with patch("src.admin.outbox_admin.dispatch_outbox_row") as dispatch:
        r = await admin_client.get(
            f"/admin/crm-outbox/action/retry?pks={failed_id},{done_id}", follow_redirects=False,
        )
    assert r.status_code == 302
    assert [c.args[0].id for c in dispatch.call_args_list] == [failed_id]

    async with async_session_maker() as session:
        f = await session.get(CrmOutbox, failed_id)
        assert (f.status, f.attempts) == ("pending", 0)
        d = await session.get(CrmOutbox, done_id)
        assert (d.status, d.attempts) == ("done", 1)
        assert (await session.get(Task, task_id)).sync_status == "pending"


async def test_retry_action_skips_failed_row_with_newer_done_sibling(admin_client: AsyncClient):
    """failed-строка той же задачи при более новом 'done' — устарела: не переставляется в очередь, статус не меняется."""
    from unittest.mock import patch

    async with async_session_maker() as session:
        task = Task(title="t", description="d", owner_id=1, crm_task_id=42, sync_status="synced")
        session.add(task)
        await session.flush()
        failed = CrmOutbox(
            aggregate_type="task", aggregate_id=task.id, operation="update", status="failed",
            attempts=5, shard="shard_0", payload={"crm_task_id": 42}, last_error="RuntimeError: x",
        )
        session.add(failed)
        await session.commit()
        newer_done = CrmOutbox(  # добавлена ПОСЛЕ failed → гарантированно больший id
            aggregate_type="task", aggregate_id=task.id, operation="update", status="done",
            attempts=1, shard="shard_0", payload={"crm_task_id": 42},
        )
        session.add(newer_done)
        await session.commit()
        task_id, failed_id, newer_done_id = task.id, failed.id, newer_done.id

    with patch("src.admin.outbox_admin.dispatch_outbox_row") as dispatch:
        r = await admin_client.get(f"/admin/crm-outbox/action/retry?pks={failed_id}", follow_redirects=False)
    assert r.status_code == 302
    assert dispatch.call_args_list == []  # ничего не диспетчеризовано

    async with async_session_maker() as session:
        f = await session.get(CrmOutbox, failed_id)
        assert (f.status, f.attempts) == ("failed", 5)  # не тронуто
        d = await session.get(CrmOutbox, newer_done_id)
        assert d.status == "done"
        assert (await session.get(Task, task_id)).sync_status == "synced"  # не тронуто

    page = await admin_client.get(r.headers["location"])
    assert "более новое успешно синхронизированное событие" in page.text
    assert "alert-warning" in page.text


async def test_retry_action_requeues_failed_row_when_done_sibling_is_older(admin_client: AsyncClient):
    """done-строка той же задачи, но старше, повтору не мешает: failed-строка новее и возвращается в очередь."""
    from unittest.mock import patch

    async with async_session_maker() as session:
        task = Task(title="t", description="d", owner_id=1, crm_task_id=42, sync_status="failed")
        session.add(task)
        await session.flush()
        older_done = CrmOutbox(
            aggregate_type="task", aggregate_id=task.id, operation="update", status="done",
            attempts=1, shard="shard_0", payload={"crm_task_id": 42},
        )
        session.add(older_done)
        await session.commit()
        failed = CrmOutbox(  # добавлена ПОСЛЕ older_done → гарантированно больший id
            aggregate_type="task", aggregate_id=task.id, operation="update", status="failed",
            attempts=5, shard="shard_0", payload={"crm_task_id": 42}, last_error="RuntimeError: x",
        )
        session.add(failed)
        await session.commit()
        failed_id = failed.id

    with patch("src.admin.outbox_admin.dispatch_outbox_row") as dispatch:
        r = await admin_client.get(f"/admin/crm-outbox/action/retry?pks={failed_id}", follow_redirects=False)
    assert r.status_code == 302
    assert [c.args[0].id for c in dispatch.call_args_list] == [failed_id]

    async with async_session_maker() as session:
        f = await session.get(CrmOutbox, failed_id)
        assert (f.status, f.attempts) == ("pending", 0)


async def _make_outbox_rows(statuses: list[str], *, same_task: bool = True) -> list[int]:
    async with async_session_maker() as session:
        rows: list[CrmOutbox] = []
        shared_task_id: int | None = None
        for i, st in enumerate(statuses):
            if same_task and shared_task_id is not None:
                task_id = shared_task_id
            else:
                # title уникален на (title, owner_id), crm_task_id — на ix_task_crm_task_id_unique: у каждой задачи своё название и свой crm_task_id.
                task = Task(title=f"t{i}", description="d", owner_id=1, crm_task_id=42 + i, sync_status="failed")
                session.add(task)
                await session.flush()
                task_id = task.id
                if same_task:
                    shared_task_id = task_id
            row = CrmOutbox(aggregate_type="task", aggregate_id=task_id, operation="update", status=st,
                             attempts=5 if st == "failed" else 1, shard="shard_0", payload={"crm_task_id": 42})
            session.add(row)
            rows.append(row)
        await session.commit()
        return [r.id for r in rows]


async def _retry_and_read_flash(admin_client: AsyncClient, pks: str) -> str:
    """Вызывает действие и возвращает текст alert на странице, куда оно редиректит."""
    from unittest.mock import patch

    with patch("src.admin.outbox_admin.dispatch_outbox_row"):
        r = await admin_client.get(f"/admin/crm-outbox/action/retry?pks={pks}", follow_redirects=False)
    assert r.status_code == 302
    page = await admin_client.get(r.headers["location"])
    return page.text


async def test_retry_flash_success_when_all_failed(admin_client: AsyncClient):
    ids = await _make_outbox_rows(["failed", "failed"])
    html = await _retry_and_read_flash(admin_client, ",".join(map(str, ids)))
    assert "Возвращено в очередь: 2." in html
    assert "alert-success" in html


async def test_retry_flash_reports_skipped_rows(admin_client: AsyncClient):
    # same_task=False: done другой задачи — проверяем категорию «статус не failed» без защиты порядка.
    failed_id, done_id = await _make_outbox_rows(["failed", "done"], same_task=False)
    html = await _retry_and_read_flash(admin_client, f"{failed_id},{done_id}")
    assert "Возвращено в очередь: 1." in html
    assert "Пропущено (статус не failed или не найдено): 1" in html
    assert "alert-warning" in html


async def test_retry_flash_reports_stale_rows(admin_client: AsyncClient):
    # same_task=True (по умолчанию): done создаётся ПОСЛЕ failed → больший id →
    # failed считается устаревшей относительно уже синхронизированного done.
    failed_id, _done_id = await _make_outbox_rows(["failed", "done"])
    html = await _retry_and_read_flash(admin_client, str(failed_id))
    assert "Ничего не возвращено в очередь" in html
    assert "более новое успешно синхронизированное событие" in html
    assert "alert-warning" in html


async def test_retry_flash_nothing_requeued_when_none_failed(admin_client: AsyncClient):
    (done_id,) = await _make_outbox_rows(["done"])
    html = await _retry_and_read_flash(admin_client, str(done_id))
    assert "Ничего не возвращено в очередь" in html
    assert "alert-warning" in html


async def test_retry_flash_nothing_selected(admin_client: AsyncClient):
    html = await _retry_and_read_flash(admin_client, "")
    assert "Ничего не выбрано" in html


async def test_retry_flash_is_shown_only_once(admin_client: AsyncClient):
    (done_id,) = await _make_outbox_rows(["done"])
    from unittest.mock import patch

    with patch("src.admin.outbox_admin.dispatch_outbox_row"):
        r = await admin_client.get(f"/admin/crm-outbox/action/retry?pks={done_id}", follow_redirects=False)
    first = await admin_client.get(r.headers["location"])
    second = await admin_client.get(r.headers["location"])
    assert "Ничего не возвращено в очередь" in first.text
    assert "Ничего не возвращено в очередь" not in second.text


async def test_retry_action_requires_admin_login(client: AsyncClient):
    r = await client.get("/admin/crm-outbox/action/retry?pks=1", follow_redirects=False)
    assert r.status_code in (302, 401, 403)
    assert "retry" not in (await client.get("/admin/crm-outbox/list", follow_redirects=True)).text


async def test_retry_button_present_on_outbox_pages(admin_client: AsyncClient):
    async with async_session_maker() as session:
        row = CrmOutbox(aggregate_type="task", aggregate_id=1, operation="update", payload={})
        session.add(row)
        await session.commit()
        row_id = row.id
    assert "Повторить" in (await admin_client.get("/admin/crm-outbox/list")).text
    assert "Повторить" in (await admin_client.get(f"/admin/crm-outbox/details/{row_id}")).text
