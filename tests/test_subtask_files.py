"""Тесты эндпоинтов загрузки файлов для подзадач.

Покрытие:
  POST   /subtasks/{id}/specification  — загрузка ТЗ: успех, 413, 422 (ext), 422 (MIME), 404, 401
  DELETE /subtasks/{id}/specification  — удаление ТЗ: успех, 404
  POST   /subtasks/{id}/files          — добавление иных документов: успех, превышение лимита
  DELETE /subtasks/{id}/files/{name}   — удаление одного файла: успех, 404
  WS-рассылка broadcast_task_event ("subtask_files_updated") — все 4 эндпоинта выше
  Конкурентная загрузка (FOR NO KEY UPDATE) — lost update на other_file_paths
"""

import asyncio
import json

import pytest
from httpx import AsyncClient

from src.realtime.manager import connection_manager
from tests.conftest import register_user

EMAIL = "file_subtask@example.com"

# ID заведомо выше любого реального пользователя в тестовой БД (truncate между тестами) —
# используется как "наблюдатель", не совпадающий с exclude_user_id актёра запроса.
_OBSERVER_ID = 999999


class _ObserverWebSocket:
    """Минимальная замена starlette.WebSocket — только фиксирует отправленное.

    Проверяет факт вызова broadcast_task_event из реального HTTP-эндпоинта
    через process-wide connection_manager (см. test_task_files.py).
    """

    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


# ── Тестовые байты с правильными magic-сигнатурами ───────────────────────────

def _pdf() -> bytes:
    return b'%PDF-1.4 fake pdf content for subtask tests'

def _png() -> bytes:
    return b'\x89PNG\r\n\x1a\n fake png for subtask tests'


# ── Вспомогательные функции ──────────────────────────────────────────────────

async def _auth(client: AsyncClient, mock_smtp: dict) -> None:
    """Регистрирует и авторизует тестового пользователя."""
    await register_user(client, mock_smtp, EMAIL)
    # Логин принимает form-data с полем "username" (OAuth2PasswordRequestForm), а не JSON.
    await client.post("/auth/login", data={"username": EMAIL, "password": "Password1!"})

async def _make_task(client: AsyncClient) -> dict:
    r = await client.post("/create-task/", json={"title": "SubFileTask", "description": "d"})
    assert r.status_code == 201
    return r.json()

async def _make_subtask(client: AsyncClient, task_id: int) -> dict:
    r = await client.post(
        "/create-subtask/",
        json={"task_id": task_id, "title": "SubFileSubtask", "description": "d"},
    )
    assert r.status_code == 201
    return r.json()

def _spec_upload(content: bytes, filename: str = "tz.pdf") -> dict:
    return {"file": (filename, content, "application/octet-stream")}

def _other_uploads(*pairs: tuple[bytes, str]) -> list[tuple]:
    return [("files", (name, data, "application/octet-stream")) for data, name in pairs]


# ── Техническое задание: загрузка ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_spec_success(client, mock_smtp, mock_magic, upload_root):
    """Валидный PDF принимается; ответ содержит путь внутри папки subtasks."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    r = await client.post(
        f"/subtasks/{subtask['id']}/specification",
        files=_spec_upload(_pdf()),
    )
    assert r.status_code == 200
    path = r.json()["specification_path"]
    assert path.endswith(".pdf")
    assert "specification" in path


@pytest.mark.asyncio
async def test_upload_spec_size_limit(client, mock_smtp, mock_magic, upload_root, monkeypatch):
    """Файл больше MAX_FILE_SIZE → 413."""
    monkeypatch.setattr("src.utils.file_utils.MAX_FILE_SIZE", 5)
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    r = await client.post(
        f"/subtasks/{subtask['id']}/specification",
        files=_spec_upload(_pdf()),
    )
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_upload_spec_bad_extension(client, mock_smtp, mock_magic, upload_root):
    """Расширение вне белого списка → 422."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    r = await client.post(
        f"/subtasks/{subtask['id']}/specification",
        files={"file": ("virus.exe", _pdf(), "application/octet-stream")},
    )
    assert r.status_code == 422
    assert "Расширение" in r.json()["detail"]


@pytest.mark.asyncio
async def test_upload_spec_mime_mismatch(client, mock_smtp, mock_magic, upload_root):
    """PNG-байты + расширение .pdf → 422 (MIME-мисматч)."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    r = await client.post(
        f"/subtasks/{subtask['id']}/specification",
        files=_spec_upload(_png(), "tz.pdf"),
    )
    assert r.status_code == 422
    assert "MIME" in r.json()["detail"]


@pytest.mark.asyncio
async def test_upload_spec_subtask_not_found(client, mock_smtp, mock_magic, upload_root):
    """Несуществующая подзадача → 404."""
    await _auth(client, mock_smtp)
    r = await client.post(
        "/subtasks/99999/specification",
        files=_spec_upload(_pdf()),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_upload_spec_unauthenticated(client, upload_root):
    """Запрос без куки авторизации → 401."""
    r = await client.post("/subtasks/1/specification", files=_spec_upload(_pdf()))
    assert r.status_code == 401


# ── Техническое задание: удаление ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_spec_success(client, mock_smtp, mock_magic, upload_root):
    """DELETE после загрузки → 200, specification_path=None."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    await client.post(f"/subtasks/{sid}/specification", files=_spec_upload(_pdf()))
    r = await client.delete(f"/subtasks/{sid}/specification")
    assert r.status_code == 200
    assert r.json()["specification_path"] is None


@pytest.mark.asyncio
async def test_delete_spec_no_file_uploaded(client, mock_smtp, upload_root):
    """DELETE когда ТЗ не загружен → 404."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    r = await client.delete(f"/subtasks/{subtask['id']}/specification")
    assert r.status_code == 404


# ── Иные документы ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_other_files_success(client, mock_smtp, mock_magic, upload_root):
    """Загрузка 2 файлов → 200, список из 2 путей."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    r = await client.post(
        f"/subtasks/{subtask['id']}/files",
        files=_other_uploads((_pdf(), "x.pdf"), (_pdf(), "y.pdf")),
    )
    assert r.status_code == 200
    paths = r.json()["other_file_paths"]
    assert len(paths) == 2
    assert all(p.endswith(".pdf") for p in paths)


@pytest.mark.asyncio
async def test_upload_other_files_limit_exceeded(
    client, mock_smtp, mock_magic, upload_root, monkeypatch
):
    """Суммарное количество файлов > MAX_OTHER_FILES → 422."""
    monkeypatch.setattr("src.routers.subtask_files.MAX_OTHER_FILES", 1)
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    await client.post(f"/subtasks/{sid}/files", files=_other_uploads((_pdf(), "a.pdf")))
    r = await client.post(f"/subtasks/{sid}/files", files=_other_uploads((_pdf(), "b.pdf")))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_one_other_file(client, mock_smtp, mock_magic, upload_root):
    """Удаление одного файла → список уменьшается на 1."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    r_upload = await client.post(
        f"/subtasks/{sid}/files",
        files=_other_uploads((_pdf(), "p.pdf"), (_pdf(), "q.pdf")),
    )
    paths = r_upload.json()["other_file_paths"]
    filename = paths[0].split("/")[-1]   # "a1b2c3d4_p.pdf"
    r_del = await client.delete(f"/subtasks/{sid}/files/{filename}")
    assert r_del.status_code == 200
    assert len(r_del.json()["other_file_paths"]) == 1


@pytest.mark.asyncio
async def test_delete_other_file_not_found(client, mock_smtp, upload_root):
    """Удаление несуществующего файла → 404."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    r = await client.delete(f"/subtasks/{subtask['id']}/files/ghost.pdf")
    assert r.status_code == 404


# ── Конкурентность / блокировки ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_uploads_do_not_lose_files(client, mock_smtp, mock_magic, upload_root):
    """Регрессионный тест на FOR NO KEY UPDATE в upload_subtask_files: два
    по-настоящему параллельных запроса на загрузку разных файлов в одну и ту
    же подзадачу не должны терять ни один из путей в other_file_paths (lost
    update).
    """
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]

    r1, r2 = await asyncio.gather(
        client.post(f"/subtasks/{sid}/files", files=_other_uploads((_pdf(), "concurrent_a.pdf"))),
        client.post(f"/subtasks/{sid}/files", files=_other_uploads((_pdf(), "concurrent_b.pdf"))),
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    r = await client.get(f"/subtasks/{sid}")
    assert len(r.json()["other_file_paths"]) == 2


# ── CRM синхронизация ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_spec_crm_synced(client, mock_smtp, mock_magic, upload_root, mock_crm):
    """Загрузка ТЗ → update_subtask вызван ровно один раз с subtask_id и specification_abs_path."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    await client.post(f"/subtasks/{subtask['id']}/specification", files=_spec_upload(_pdf()))
    mock_crm["subtask_mgr"].update_subtask.assert_called_once()
    kwargs = mock_crm["subtask_mgr"].update_subtask.call_args.kwargs
    assert kwargs["subtask_id"] == subtask["crm_subtask_id"]
    assert "specification_abs_path" in kwargs


@pytest.mark.asyncio
async def test_delete_spec_crm_cleared(client, mock_smtp, mock_magic, upload_root, mock_crm):
    """Удаление ТЗ → update_subtask вызван с clear_specification=True."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    await client.post(f"/subtasks/{sid}/specification", files=_spec_upload(_pdf()))
    mock_crm["subtask_mgr"].update_subtask.reset_mock()
    await client.delete(f"/subtasks/{sid}/specification")
    mock_crm["subtask_mgr"].update_subtask.assert_called_once()
    kwargs = mock_crm["subtask_mgr"].update_subtask.call_args.kwargs
    assert kwargs["clear_specification"] is True


@pytest.mark.asyncio
async def test_upload_other_files_crm_synced(client, mock_smtp, mock_magic, upload_root, mock_crm):
    """Загрузка 2 иных документов → update_subtask вызван с other_file_abs_paths длиной 2."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    await client.post(
        f"/subtasks/{subtask['id']}/files",
        files=_other_uploads((_pdf(), "x.pdf"), (_pdf(), "y.pdf")),
    )
    mock_crm["subtask_mgr"].update_subtask.assert_called_once()
    kwargs = mock_crm["subtask_mgr"].update_subtask.call_args.kwargs
    assert len(kwargs["other_file_abs_paths"]) == 2


@pytest.mark.asyncio
async def test_delete_other_file_crm_synced(client, mock_smtp, mock_magic, upload_root, mock_crm):
    """Удаление одного из двух файлов → update_subtask вызван с other_file_abs_paths длиной 1."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    r_up = await client.post(
        f"/subtasks/{sid}/files",
        files=_other_uploads((_pdf(), "p.pdf"), (_pdf(), "q.pdf")),
    )
    filename = r_up.json()["other_file_paths"][0].split("/")[-1]
    mock_crm["subtask_mgr"].update_subtask.reset_mock()
    await client.delete(f"/subtasks/{sid}/files/{filename}")
    mock_crm["subtask_mgr"].update_subtask.assert_called_once()
    kwargs = mock_crm["subtask_mgr"].update_subtask.call_args.kwargs
    assert len(kwargs["other_file_abs_paths"]) == 1


# ── WS-рассылка событий (broadcast_task_event) ───────────────────────────────
# exclude_user_id=user.id в эндпоинте исключает из рассылки самого актёра —
# поэтому наблюдатель регистрируется под отдельным (заведомо иным) user_id.

@pytest.mark.asyncio
async def test_upload_spec_broadcasts_ws_event(client, mock_smtp, mock_magic, upload_root):
    """Загрузка ТЗ рассылает subtask_files_updated с subtask_id и title подзадачи."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]

    observer = _ObserverWebSocket()
    connection_manager.register(_OBSERVER_ID, observer, "observer@example.com")
    try:
        await client.post(f"/subtasks/{sid}/specification", files=_spec_upload(_pdf()))
    finally:
        connection_manager.unregister(_OBSERVER_ID, observer)

    assert len(observer.sent) == 1
    payload = json.loads(observer.sent[0])
    assert payload["type"] == "subtask_files_updated"
    assert payload["subtask_id"] == sid
    assert payload["title"] == subtask["title"]


@pytest.mark.asyncio
async def test_delete_spec_broadcasts_ws_event(client, mock_smtp, mock_magic, upload_root):
    """Удаление ТЗ рассылает subtask_files_updated с subtask_id подзадачи."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    await client.post(f"/subtasks/{sid}/specification", files=_spec_upload(_pdf()))

    observer = _ObserverWebSocket()
    connection_manager.register(_OBSERVER_ID, observer, "observer@example.com")
    try:
        await client.delete(f"/subtasks/{sid}/specification")
    finally:
        connection_manager.unregister(_OBSERVER_ID, observer)

    assert len(observer.sent) == 1
    payload = json.loads(observer.sent[0])
    assert payload["type"] == "subtask_files_updated"
    assert payload["subtask_id"] == sid


@pytest.mark.asyncio
async def test_upload_other_files_broadcasts_ws_event(client, mock_smtp, mock_magic, upload_root):
    """Загрузка «иных документов» рассылает subtask_files_updated с subtask_id."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]

    observer = _ObserverWebSocket()
    connection_manager.register(_OBSERVER_ID, observer, "observer@example.com")
    try:
        await client.post(f"/subtasks/{sid}/files", files=_other_uploads((_pdf(), "x.pdf")))
    finally:
        connection_manager.unregister(_OBSERVER_ID, observer)

    assert len(observer.sent) == 1
    payload = json.loads(observer.sent[0])
    assert payload["type"] == "subtask_files_updated"
    assert payload["subtask_id"] == sid


@pytest.mark.asyncio
async def test_delete_other_file_broadcasts_ws_event(client, mock_smtp, mock_magic, upload_root):
    """Удаление одного из «иных документов» рассылает subtask_files_updated."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    r_up = await client.post(f"/subtasks/{sid}/files", files=_other_uploads((_pdf(), "x.pdf")))
    filename = r_up.json()["other_file_paths"][0].split("/")[-1]

    observer = _ObserverWebSocket()
    connection_manager.register(_OBSERVER_ID, observer, "observer@example.com")
    try:
        await client.delete(f"/subtasks/{sid}/files/{filename}")
    finally:
        connection_manager.unregister(_OBSERVER_ID, observer)

    assert len(observer.sent) == 1
    payload = json.loads(observer.sent[0])
    assert payload["type"] == "subtask_files_updated"
    assert payload["subtask_id"] == sid


# ── Интеграция GET ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spec_appears_in_get_subtask(client, mock_smtp, mock_magic, upload_root):
    """После загрузки ТЗ GET /subtasks/{id} возвращает непустой specification_path."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    await client.post(f"/subtasks/{sid}/specification", files=_spec_upload(_pdf()))
    r = await client.get(f"/subtasks/{sid}")
    assert r.status_code == 200
    assert r.json()["specification_path"] is not None
    assert r.json()["specification_path"].endswith(".pdf")


@pytest.mark.asyncio
async def test_other_files_appear_in_get_subtask(client, mock_smtp, mock_magic, upload_root):
    """После загрузки файлов GET /subtasks/{id} возвращает список other_file_paths."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    await client.post(f"/subtasks/{sid}/files", files=_other_uploads((_pdf(), "f.pdf")))
    r = await client.get(f"/subtasks/{sid}")
    assert r.status_code == 200
    paths = r.json()["other_file_paths"]
    assert paths is not None
    assert len(paths) == 1


# ── Граничный случай: последний файл ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_last_other_file(client, mock_smtp, mock_magic, upload_root):
    """Удаление последнего файла из other_file_paths → ответ содержит пустой список."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    r_up = await client.post(f"/subtasks/{sid}/files", files=_other_uploads((_pdf(), "only.pdf")))
    filename = r_up.json()["other_file_paths"][0].split("/")[-1]
    r_del = await client.delete(f"/subtasks/{sid}/files/{filename}")
    assert r_del.status_code == 200
    assert r_del.json()["other_file_paths"] == []
