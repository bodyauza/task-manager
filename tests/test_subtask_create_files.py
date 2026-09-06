"""Тесты атомарного создания подзадачи с файлами одним HTTP-запросом (POST /create-subtask/).

Зеркалирует tests/test_task_create_files.py — см. его docstring за описанием покрытия.
Дополнительно: CRM-синхронизация файлов подзадачи зависит не только от собственного
crm_subtask_id, но и от того, зарегистрирована ли в CRM родительская задача.
"""

import json

import pytest
from httpx import AsyncClient

from tests.conftest import register_user

EMAIL = "create_files_subtask@example.com"


def _pdf() -> bytes:
    return b'%PDF-1.4 fake pdf content for subtask create-with-files tests'


async def _auth(client: AsyncClient, mock_smtp: dict) -> None:
    await register_user(client, mock_smtp, EMAIL)
    await client.post("/auth/login", data={"username": EMAIL, "password": "Password1!"})


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
async def test_create_subtask_crm_files_synced(client, mock_smtp, mock_magic, upload_root, mock_crm):
    await _auth(client, mock_smtp)
    task = await _create_task(client)
    data, files = _multipart(task["id"], spec=(_pdf(), "tz.pdf"), other=[(_pdf(), "a.pdf")])
    r = await client.post("/create-subtask/", data=data, files=files)
    assert r.status_code == 201

    mock_crm["subtask_mgr"].create_subtask.assert_called_once()
    mock_crm["subtask_mgr"].update_subtask.assert_called_once()
    kwargs = mock_crm["subtask_mgr"].update_subtask.call_args.kwargs
    assert kwargs["subtask_id"] == r.json()["crm_subtask_id"]
    assert kwargs["specification_abs_path"] is not None
    assert len(kwargs["other_file_abs_paths"]) == 1


@pytest.mark.asyncio
async def test_create_subtask_no_parent_crm_id_skips_file_sync(
    client, mock_smtp, mock_magic, upload_root, mock_crm
):
    """Родительская задача не зарегистрирована в CRM → подзадача тоже не регистрируется
    (существующее правило, см. services/subtasks.py), файлы всё равно сохраняются на
    диск, но update_subtask вообще не вызывается — синхронизировать в CRM нечего.
    """
    mock_crm["task_mgr"].create_task.return_value = {"id": None}
    await _auth(client, mock_smtp)
    task = await _create_task(client)
    assert task["crm_task_id"] is None

    data, files = _multipart(task["id"], spec=(_pdf(), "tz.pdf"))
    r = await client.post("/create-subtask/", data=data, files=files)

    assert r.status_code == 201
    body = r.json()
    assert body["crm_subtask_id"] is None
    assert body["crm_synced"] is False
    assert body["specification_path"] is not None
    mock_crm["subtask_mgr"].create_subtask.assert_not_called()
    mock_crm["subtask_mgr"].update_subtask.assert_not_called()


@pytest.mark.asyncio
async def test_create_subtask_crm_create_fails_files_still_saved(
    client, mock_smtp, mock_magic, upload_root, mock_crm
):
    await _auth(client, mock_smtp)
    task = await _create_task(client)
    mock_crm["subtask_mgr"].create_subtask.side_effect = Exception("CRM down")

    data, files = _multipart(task["id"], spec=(_pdf(), "tz.pdf"))
    r = await client.post("/create-subtask/", data=data, files=files)

    assert r.status_code == 201
    body = r.json()
    assert body["crm_synced"] is False
    assert body["crm_subtask_id"] is None
    assert body["specification_path"] is not None
    mock_crm["subtask_mgr"].update_subtask.assert_not_called()
