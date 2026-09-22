"""Тесты атомарного создания подзадачи с файлами одним HTTP-запросом (POST /create-subtask/).

Зеркалирует tests/test_task_create_files.py — см. его docstring за описанием покрытия
и за тем, почему CRM-синхронизация проверяется через содержимое outbox-строк, а не
через факт прямого CRM-вызова (тот теперь целиком в Celery-воркере).
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.database import async_session_maker
from src.task_logic.models import CrmOutbox
from tests.conftest import register_and_login

EMAIL = "create_files_subtask@example.com"


async def _outbox_rows_for_subtask(subtask_id: int) -> list[CrmOutbox]:
    async with async_session_maker() as session:
        return (
            await session.execute(
                select(CrmOutbox)
                .where(CrmOutbox.aggregate_type == "subtask", CrmOutbox.aggregate_id == subtask_id)
                .order_by(CrmOutbox.id)
            )
        ).scalars().all()


def _pdf() -> bytes:
    return b'%PDF-1.4 fake pdf content for subtask create-with-files tests'


async def _auth(client: AsyncClient, mock_smtp: dict) -> None:
    await register_and_login(client, mock_smtp, EMAIL)


async def _create_task(client: AsyncClient, title: str = "Parent") -> dict:
    r = await client.post(
        "/create-task/", data={"data": json.dumps({"title": title, "description": "d"})}
    )
    assert r.status_code == 201
    return r.json()


def _multipart(
    task_id: int,
    title: str = "Subtask With Files",
    description: str = "d",
    spec: tuple[bytes, str] | None = None,
    other: list[tuple[bytes, str]] | None = None,
):
    data = {"data": json.dumps({"task_id": task_id, "title": title, "description": description})}
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    if spec is not None:
        content, filename = spec
        files.append(("specification", (filename, content, "application/octet-stream")))
    for content, filename in (other or []):
        files.append(("other_files", (filename, content, "application/octet-stream")))
    return data, (files or None)


# ── Атомарное создание с файлами ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_subtask_with_spec_and_other_files_success(client, mock_smtp, mock_magic, upload_root):
    await _auth(client, mock_smtp)
    task = await _create_task(client)
    data, files = _multipart(task["id"], spec=(_pdf(), "tz.pdf"), other=[(_pdf(), "a.pdf"), (_pdf(), "b.pdf")])
    r = await client.post("/create-subtask/", data=data, files=files)
    assert r.status_code == 201
    body = r.json()
    assert body["specification_path"] is not None
    assert len(body["other_file_paths"]) == 2
    assert body.get("file_upload_errors") is None


@pytest.mark.asyncio
async def test_create_subtask_without_files_still_works(client, mock_smtp, upload_root):
    await _auth(client, mock_smtp)
    task = await _create_task(client)
    data, files = _multipart(task["id"])
    r = await client.post("/create-subtask/", data=data, files=files)
    assert r.status_code == 201
    body = r.json()
    assert body["specification_path"] is None
    assert body["other_file_paths"] is None


@pytest.mark.asyncio
async def test_create_subtask_invalid_file_blocks_creation(client, mock_smtp, mock_magic, upload_root):
    await _auth(client, mock_smtp)
    task = await _create_task(client)
    data, files = _multipart(
        task["id"], title="Should Not Exist", other=[(_pdf(), "ok.pdf"), (_pdf(), "virus.exe")]
    )
    r = await client.post("/create-subtask/", data=data, files=files)
    assert r.status_code == 422

    listed = await client.get(f"/subtasks/?task_id={task['id']}")
    assert "Should Not Exist" not in [s["title"] for s in listed.json()]


@pytest.mark.asyncio
async def test_create_subtask_disk_save_failure_partial(
    client, mock_smtp, mock_magic, upload_root, monkeypatch
):
    import src.services.attachments as attachments_module

    real_save_file = attachments_module.save_file

    def _flaky_save_file(dest_dir, filename, content):
        if filename.endswith("_bad.pdf"):
            raise OSError("simulated disk failure")
        return real_save_file(dest_dir, filename, content)

    monkeypatch.setattr(attachments_module, "save_file", _flaky_save_file)

    await _auth(client, mock_smtp)
    task = await _create_task(client)
    data, files = _multipart(task["id"], other=[(_pdf(), "good.pdf"), (_pdf(), "bad.pdf")])
    r = await client.post("/create-subtask/", data=data, files=files)

    assert r.status_code == 201
    body = r.json()
    assert len(body["other_file_paths"]) == 1
    assert "good.pdf" in body["other_file_paths"][0]
    assert body["file_upload_errors"] is not None
    assert "bad.pdf" in body["file_upload_errors"]


# ── Паритет валидации ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_subtask_multipart_empty_title(client, mock_smtp, upload_root):
    await _auth(client, mock_smtp)
    task = await _create_task(client)
    data, files = _multipart(task["id"], title="")
    r = await client.post("/create-subtask/", data=data, files=files)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_subtask_multipart_duplicate_title(client, mock_smtp, upload_root):
    await _auth(client, mock_smtp)
    task = await _create_task(client)
    data, files = _multipart(task["id"], title="Dup")
    r1 = await client.post("/create-subtask/", data=data, files=files)
    assert r1.status_code == 201
    data2, files2 = _multipart(task["id"], title="Dup")
    r2 = await client.post("/create-subtask/", data=data2, files=files2)
    assert r2.status_code == 409


# ── CRM-синхронизация ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_subtask_with_files_enqueues_create_and_sync_files_rows(
    client, mock_smtp, mock_magic, upload_root,
):
    await _auth(client, mock_smtp)
    task = await _create_task(client)
    data, files = _multipart(task["id"], spec=(_pdf(), "tz.pdf"), other=[(_pdf(), "a.pdf")])
    r = await client.post("/create-subtask/", data=data, files=files)
    assert r.status_code == 201
    body = r.json()
    # crm_subtask_id/crm_synced не в ответе (см. SubtaskResponse) — статус
    # синхронизации проверяется через outbox-строки ниже, не через ответ.
    rows = await _outbox_rows_for_subtask(body["id"])
    assert [row.operation for row in rows] == ["create", "sync_files"]
    create_row, sync_row = rows
    assert sync_row.depends_on_event_id == create_row.id
    assert sync_row.payload["crm_subtask_id"] is None
    assert sync_row.payload["specification_path"] is not None
    assert len(sync_row.payload["other_file_paths"]) == 1


@pytest.mark.asyncio
async def test_create_subtask_no_files_skips_sync_files_row(client, mock_smtp, upload_root):
    await _auth(client, mock_smtp)
    task = await _create_task(client)
    data, files = _multipart(task["id"])
    r = await client.post("/create-subtask/", data=data, files=files)
    assert r.status_code == 201

    rows = await _outbox_rows_for_subtask(r.json()["id"])
    assert [row.operation for row in rows] == ["create"]
