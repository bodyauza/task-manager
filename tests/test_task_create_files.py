"""Тесты атомарного создания задачи с файлами (POST /create-task/).

Покрытие: ТЗ и иные документы одним запросом; создание без файлов; невалидный файл блокирует создание (422);
best-effort сбой сохранения одного файла (задача создаётся, ошибка в file_upload_errors); паритет валидации;
CRM-синхронизация проверяется по outbox-строкам ('create' и зависимая 'sync_files'), а не по прямому вызову CRM.
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
async def test_create_task_forbidden_filename_chars_blocks_creation(client, mock_smtp, mock_magic, upload_root):
    """Запрещённый символ в имени ТЗ блокирует создание целиком: проверка идёт до вставки задачи (validate_files_for_create)."""
    await _auth(client, mock_smtp)
    data, files = _multipart(title="Should Not Exist Either", spec=(_pdf(), "tz|1.pdf"))
    r = await client.post("/create-task/", data=data, files=files)
    assert r.status_code == 422

    listed = await client.get("/tasks/")
    assert "Should Not Exist Either" not in [t["title"] for t in listed.json()]


@pytest.mark.asyncio
async def test_create_task_disk_save_failure_partial(client, mock_smtp, mock_magic, upload_root, monkeypatch):
    """Один из нескольких валидных other_files не сохраняется на диск (не вина клиента) —
    задача всё равно создаётся, успешный файл сохранён, сбойный — в file_upload_errors.
    """
    import src.services.attachments as attachments_module

    real_save_file = attachments_module.save_file

    def _flaky_save_file(dest_dir, filename, content, upload_root):
        if filename.endswith("_bad.pdf"):
            raise OSError("simulated disk failure")
        return real_save_file(dest_dir, filename, content, upload_root)

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


@pytest.mark.asyncio
async def test_create_task_commit_failure_deletes_orphaned_files(
    client, mock_smtp, mock_magic, upload_root, monkeypatch,
):
    """Файлы уже на диске до commit; при его падении транзакция откатывается целиком, и файлы остались бы сиротами. Теперь они удаляются,
    а сбой всплывает клиенту.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    await _auth(client, mock_smtp)

    original_commit = AsyncSession.commit
    calls = {"n": 0}

    async def _commit_raises_once(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated commit failure")
        return await original_commit(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "commit", _commit_raises_once)

    data, files = _multipart(spec=(_pdf(), "tz.pdf"), other=[(_pdf(), "a.pdf")])
    # Голое исключение долетает до httpx как исключение (ASGITransport raise_app_exceptions=True), а не response(500); важно, что сбой не проглочен.
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        await client.post("/create-task/", data=data, files=files)

    # Оба файла (ТЗ и «иной документ») успели сохраниться на диск до сбоя commit.
    assert [p for p in upload_root.rglob("*") if p.is_file()] == []


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


@pytest.mark.asyncio
async def test_create_task_with_files_enqueues_create_and_sync_files_rows(
    client, mock_smtp, mock_magic, upload_root,
):
    await _auth(client, mock_smtp)
    data, files = _multipart(spec=(_pdf(), "tz.pdf"), other=[(_pdf(), "a.pdf")])
    r = await client.post("/create-task/", data=data, files=files)
    assert r.status_code == 201
    body = r.json()
    # crm_task_id/crm_synced не в ответе; CRM ещё не тронута, статус проверяем по outbox-строкам.
    rows = await _outbox_rows_for_task(body["id"])
    assert [r.operation for r in rows] == ["create", "sync_files"]
    create_row, sync_row = rows
    assert create_row.status == "pending"
    # sync_files зависит от create (crm_task_id ещё не известен на момент вставки —
    # см. src/services/tasks.py::create_task): обработчик прочитает его из БД позже.
    assert sync_row.depends_on_event_id == create_row.id
    assert sync_row.payload["crm_task_id"] is None
    # payload несёт флаги затронутых слотов, а не пути.
    assert sync_row.payload["sync_specification"] is True
    assert sync_row.payload["sync_other_files"] is True


@pytest.mark.asyncio
async def test_create_task_no_files_skips_sync_files_row(client, mock_smtp, upload_root):
    """Без файлов вставляется только outbox-строка 'create' — 'sync_files' не нужна."""
    await _auth(client, mock_smtp)
    data, files = _multipart()
    r = await client.post("/create-task/", data=data, files=files)
    assert r.status_code == 201

    rows = await _outbox_rows_for_task(r.json()["id"])
    assert [row.operation for row in rows] == ["create"]
