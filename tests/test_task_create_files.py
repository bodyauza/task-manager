"""Тесты атомарного создания задачи с файлами одним HTTP-запросом (POST /create-task/).

Покрытие:
  Создание с ТЗ + иными документами в одном запросе — пути в ответе
  Создание без файлов по-прежнему работает (файлы полностью опциональны)
  Невалидный файл блокирует создание целиком (422, задача НЕ создана)
  Best-effort сбой сохранения одного файла на диске — задача всё равно создаётся,
    успешные файлы сохранены, сбойный — в file_upload_errors
  Паритет валидации (пустой title, дубликат title) через multipart
  Двухшаговая CRM-синхронизация: create (текст) → update (файлы)
  CRM create не удался → update для файлов не вызывается вовсе
"""

import json

import pytest
from httpx import AsyncClient

from tests.conftest import register_user

EMAIL = "create_files_task@example.com"


def _pdf() -> bytes:
    return b'%PDF-1.4 fake pdf content for create-with-files tests'


async def _auth(client: AsyncClient, mock_smtp: dict) -> None:
    await register_user(client, mock_smtp, EMAIL)
    await client.post("/auth/login", data={"username": EMAIL, "password": "Password1!"})


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
async def test_create_task_crm_files_synced(client, mock_smtp, mock_magic, upload_root, mock_crm):
    await _auth(client, mock_smtp)
    data, files = _multipart(spec=(_pdf(), "tz.pdf"), other=[(_pdf(), "a.pdf")])
    r = await client.post("/create-task/", data=data, files=files)
    assert r.status_code == 201

    mock_crm["task_mgr"].create_task.assert_called_once()
    mock_crm["task_mgr"].update_task.assert_called_once()
    kwargs = mock_crm["task_mgr"].update_task.call_args.kwargs
    assert kwargs["task_id"] == r.json()["crm_task_id"]
    assert kwargs["specification_abs_path"] is not None
    assert len(kwargs["other_file_abs_paths"]) == 1


@pytest.mark.asyncio
async def test_create_task_no_files_skips_crm_file_sync(client, mock_smtp, mock_crm, upload_root):
    """Без файлов create_task-текстовый вызов происходит, а update_task (файлы) — нет."""
    await _auth(client, mock_smtp)
    data, files = _multipart()
    r = await client.post("/create-task/", data=data, files=files)
    assert r.status_code == 201
    mock_crm["task_mgr"].create_task.assert_called_once()
    mock_crm["task_mgr"].update_task.assert_not_called()


@pytest.mark.asyncio
async def test_create_task_crm_create_fails_files_still_saved(
    client, mock_smtp, mock_magic, upload_root, mock_crm
):
    """CRM create недоступен → задача всё равно создаётся, файлы сохраняются на диск,
    но update_task (синхронизация путей файлов) вообще не вызывается — синхронизировать
    в CRM нечего, crm_task_id отсутствует.
    """
    mock_crm["task_mgr"].create_task.side_effect = Exception("CRM down")
    await _auth(client, mock_smtp)
    data, files = _multipart(spec=(_pdf(), "tz.pdf"))
    r = await client.post("/create-task/", data=data, files=files)

    assert r.status_code == 201
    body = r.json()
    assert body["crm_synced"] is False
    assert body["crm_task_id"] is None
    assert body["specification_path"] is not None
    mock_crm["task_mgr"].update_task.assert_not_called()
