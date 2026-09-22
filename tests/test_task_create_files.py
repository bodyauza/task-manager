"""Тесты атомарного создания задачи с файлами одним HTTP-запросом (POST /create-task/).

Покрытие:
  Создание с ТЗ + иными документами в одном запросе — пути в ответе
  Создание без файлов по-прежнему работает (файлы полностью опциональны)
  Невалидный файл блокирует создание целиком (422, задача НЕ создана)
  Best-effort сбой сохранения одного файла на диске — задача всё равно создаётся,
    успешные файлы сохранены, сбойный — в file_upload_errors
  Паритет валидации (пустой title, дубликат title) через multipart
  CRM-синхронизация (текст + файлы) целиком отправлена в фон (Celery) — веб-
    процесс сам CRM не вызывает, здесь проверяется, что появились нужные
    outbox-строки ('create' + 'sync_files' с зависимостью на неё), а не факт
    прямого CRM-вызова (для этого — tests/test_crm_outbox.py)
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.database import async_session_maker
from src.task_logic.models import CrmOutbox
from tests.conftest import register_and_login

EMAIL = "create_files_task@example.com"


async def _outbox_rows_for_task(task_id: int) -> list[CrmOutbox]:
    async with async_session_maker() as session:
        return (
            await session.execute(
                select(CrmOutbox)
                .where(CrmOutbox.aggregate_type == "task", CrmOutbox.aggregate_id == task_id)
                .order_by(CrmOutbox.id)
            )
        ).scalars().all()


def _pdf() -> bytes:
    return b'%PDF-1.4 fake pdf content for create-with-files tests'


async def _auth(client: AsyncClient, mock_smtp: dict) -> None:
    await register_and_login(client, mock_smtp, EMAIL)


def _multipart(
    title: str = "Task With Files",
    description: str = "d",
    spec: tuple[bytes, str] | None = None,
    other: list[tuple[bytes, str]] | None = None,
):
    """Собирает (data, files) для multipart-запроса на /create-task/."""
    data = {"data": json.dumps({"title": title, "description": description})}
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    if spec is not None:
        content, filename = spec
        files.append(("specification", (filename, content, "application/octet-stream")))
    for content, filename in (other or []):
        files.append(("other_files", (filename, content, "application/octet-stream")))
    return data, (files or None)


# ── Атомарное создание с файлами ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_task_with_spec_and_other_files_success(client, mock_smtp, mock_magic, upload_root):
    await _auth(client, mock_smtp)
    data, files = _multipart(
        spec=(_pdf(), "tz.pdf"),
        other=[(_pdf(), "a.pdf"), (_pdf(), "b.pdf")],
    )
    r = await client.post("/create-task/", data=data, files=files)
    assert r.status_code == 201
    body = r.json()
    assert body["specification_path"] is not None
    assert body["specification_path"].endswith(".pdf")
    assert len(body["other_file_paths"]) == 2
    assert body.get("file_upload_errors") is None


@pytest.mark.asyncio
async def test_create_task_without_files_still_works(client, mock_smtp, upload_root):
    await _auth(client, mock_smtp)
    data, files = _multipart()
    r = await client.post("/create-task/", data=data, files=files)
    assert r.status_code == 201
    body = r.json()
    assert body["specification_path"] is None
    assert body["other_file_paths"] is None
    assert body.get("file_upload_errors") is None


@pytest.mark.asyncio
async def test_create_task_invalid_file_blocks_creation(client, mock_smtp, mock_magic, upload_root):
    """Один невалидный файл среди валидных → 422, задача НЕ создана."""
    await _auth(client, mock_smtp)
    data, files = _multipart(
        title="Should Not Exist",
        other=[(_pdf(), "ok.pdf"), (_pdf(), "virus.exe")],
    )
    r = await client.post("/create-task/", data=data, files=files)
    assert r.status_code == 422

    listed = await client.get("/tasks/")
    assert "Should Not Exist" not in [t["title"] for t in listed.json()]


@pytest.mark.asyncio
async def test_create_task_disk_save_failure_partial(client, mock_smtp, mock_magic, upload_root, monkeypatch):
    """Один из нескольких валидных other_files не сохраняется на диск (не вина клиента) —
    задача всё равно создаётся, успешный файл сохранён, сбойный — в file_upload_errors.
    """
    import src.services.attachments as attachments_module

    real_save_file = attachments_module.save_file

    def _flaky_save_file(dest_dir, filename, content):
        if filename.endswith("_bad.pdf"):
            raise OSError("simulated disk failure")
        return real_save_file(dest_dir, filename, content)

    monkeypatch.setattr(attachments_module, "save_file", _flaky_save_file)

    await _auth(client, mock_smtp)
    data, files = _multipart(other=[(_pdf(), "good.pdf"), (_pdf(), "bad.pdf")])
    r = await client.post("/create-task/", data=data, files=files)

    assert r.status_code == 201
    body = r.json()
    assert len(body["other_file_paths"]) == 1
    assert "good.pdf" in body["other_file_paths"][0]
    assert body["file_upload_errors"] is not None
    assert "bad.pdf" in body["file_upload_errors"]


# ── Паритет валидации ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_task_multipart_empty_title(client, mock_smtp, upload_root):
    await _auth(client, mock_smtp)
    data, files = _multipart(title="")
    r = await client.post("/create-task/", data=data, files=files)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_task_multipart_duplicate_title(client, mock_smtp, upload_root):
    await _auth(client, mock_smtp)
    data, files = _multipart(title="Dup")
    r1 = await client.post("/create-task/", data=data, files=files)
    assert r1.status_code == 201
    data2, files2 = _multipart(title="Dup")
    r2 = await client.post("/create-task/", data=data2, files=files2)
    assert r2.status_code == 409


# ── CRM-синхронизация ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_task_with_files_enqueues_create_and_sync_files_rows(
    client, mock_smtp, mock_magic, upload_root,
):
    await _auth(client, mock_smtp)
    data, files = _multipart(spec=(_pdf(), "tz.pdf"), other=[(_pdf(), "a.pdf")])
    r = await client.post("/create-task/", data=data, files=files)
    assert r.status_code == 201
    body = r.json()
    # crm_task_id/crm_synced не в ответе (см. TaskResponse) — CRM ещё не
    # тронута на момент ответа, синхронизация целиком в фоне; статус
    # проверяется через outbox-строки ниже, не через ответ.
    rows = await _outbox_rows_for_task(body["id"])
    assert [r.operation for r in rows] == ["create", "sync_files"]
    create_row, sync_row = rows
    assert create_row.status == "pending"
    # sync_files зависит от create (crm_task_id ещё не известен на момент вставки —
    # см. src/services/tasks.py::create_task): обработчик прочитает его из БД позже.
    assert sync_row.depends_on_event_id == create_row.id
    assert sync_row.payload["crm_task_id"] is None
    assert sync_row.payload["specification_path"] is not None
    assert len(sync_row.payload["other_file_paths"]) == 1


@pytest.mark.asyncio
async def test_create_task_no_files_skips_sync_files_row(client, mock_smtp, upload_root):
    """Без файлов вставляется только outbox-строка 'create' — 'sync_files' не нужна."""
    await _auth(client, mock_smtp)
    data, files = _multipart()
    r = await client.post("/create-task/", data=data, files=files)
    assert r.status_code == 201

    rows = await _outbox_rows_for_task(r.json()["id"])
    assert [row.operation for row in rows] == ["create"]
