"""Тесты эндпоинтов файлов подзадач.

POST/DELETE /subtasks/{id}/specification, POST /subtasks/{id}/files, DELETE /subtasks/{id}/files/{name}
(успех, 413, 422 по расширению и MIME, 404, 401, лимит файлов), WS-рассылка subtask_files_updated и конкурентная загрузка
(FOR NO KEY UPDATE).
"""

import asyncio
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.database import async_session_maker
from src.realtime.connection_manager import connection_manager
from src.task_logic.models import CrmOutbox, Subtask
from tests.conftest import register_and_login

EMAIL = "file_subtask@example.com"


async def _set_subtask_crm_id(subtask_id: int, crm_subtask_id: int) -> None:
    """Имитирует выполненный Celery 'create' подзадачи напрямую в БД (воркера в тестах нет)."""
    async with async_session_maker() as session:
        subtask = await session.get(Subtask, subtask_id)
        subtask.crm_subtask_id = crm_subtask_id
        await session.commit()


async def _outbox_rows_for_subtask(subtask_id: int) -> list[CrmOutbox]:
    async with async_session_maker() as session:
        return (
            await session.execute(
                select(CrmOutbox)
                .where(CrmOutbox.aggregate_type == "subtask", CrmOutbox.aggregate_id == subtask_id)
                .order_by(CrmOutbox.id)
            )
        ).scalars().all()

# ID заведомо выше любого реального пользователя в тестовой БД (truncate между тестами) —
# используется как "наблюдатель", не совпадающий с exclude_user_id актёра запроса.
_OBSERVER_ID = 999999


class _ObserverWebSocket:
    """Минимальная замена starlette.WebSocket: фиксирует отправленное; проверяет вызов broadcast_task_event из HTTP-эндпоинта (см. test_task_files.py)."""

    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


def _pdf() -> bytes:
    return b'%PDF-1.4 fake pdf content for subtask tests'


def _png() -> bytes:
    return b'\x89PNG\r\n\x1a\n fake png for subtask tests'


async def _auth(client: AsyncClient, mock_smtp: dict) -> None:
    """Регистрирует и авторизует тестового пользователя."""
    await register_and_login(client, mock_smtp, EMAIL)


async def _make_task(client: AsyncClient) -> dict:
    # POST /create-task/ теперь multipart/form-data — см. tests/test_tasks.py::_create.
    r = await client.post(
        "/create-task/", data={"data": json.dumps({"title": "SubFileTask", "description": "d"})}
    )
    assert r.status_code == 201
    return r.json()


async def _make_subtask(client: AsyncClient, task_id: int) -> dict:
    r = await client.post(
        "/create-subtask/",
        data={"data": json.dumps({"task_id": task_id, "title": "SubFileSubtask", "description": "d"})},
    )
    assert r.status_code == 201
    return r.json()


def _spec_upload(content: bytes, filename: str = "tz.pdf") -> dict:
    return {"file": (filename, content, "application/octet-stream")}


def _other_uploads(*pairs: tuple[bytes, str]) -> list[tuple]:
    return [("files", (name, data, "application/octet-stream")) for data, name in pairs]


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
    assert path.startswith("subtasks/")
    assert (upload_root / path).read_bytes() == _pdf()     # файл реально записан на диск


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
async def test_upload_spec_forbidden_filename_chars(client, mock_smtp, mock_magic, upload_root):
    """Симметрично test_task_files.py::test_upload_spec_forbidden_filename_chars: проверка FORBIDDEN_FILENAME_CHARS общая для задач и подзадач."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    r = await client.post(
        f"/subtasks/{subtask['id']}/specification",
        files={"file": ("tz<1>.pdf", _pdf(), "application/octet-stream")},
    )
    assert r.status_code == 422
    assert [p for p in upload_root.rglob("*") if p.is_file()] == []


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


@pytest.mark.asyncio
async def test_delete_spec_success(client, mock_smtp, mock_magic, upload_root):
    """DELETE после загрузки → 200, specification_path=None."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    uploaded = await client.post(f"/subtasks/{sid}/specification", files=_spec_upload(_pdf()))
    path = uploaded.json()["specification_path"]
    assert (upload_root / path).exists()
    r = await client.delete(f"/subtasks/{sid}/specification")
    assert r.status_code == 200
    assert r.json()["specification_path"] is None
    assert not (upload_root / path).exists()                         # файл удалён с диска
    assert (await client.get(f"/subtasks/{sid}")).json()["specification_path"] is None


@pytest.mark.asyncio
async def test_upload_spec_replaces_existing(client, mock_smtp, mock_magic, upload_root):
    """Повторная загрузка ТЗ подзадачи заменяет файл: старый удаляется с диска."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    sid = (await _make_subtask(client, task["id"]))["id"]
    first = await client.post(f"/subtasks/{sid}/specification", files=_spec_upload(_pdf(), "v1.pdf"))
    old_path = first.json()["specification_path"]
    r = await client.post(f"/subtasks/{sid}/specification", files=_spec_upload(_pdf(), "v2.pdf"))
    assert r.status_code == 200
    new_path = r.json()["specification_path"]
    assert "v2" in new_path
    assert not (upload_root / old_path).exists()
    assert (upload_root / new_path).exists()


@pytest.mark.asyncio
async def test_delete_spec_no_file_uploaded(client, mock_smtp, upload_root):
    """DELETE когда ТЗ не загружен → 404."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    r = await client.delete(f"/subtasks/{subtask['id']}/specification")
    assert r.status_code == 404


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
    assert all((upload_root / p).read_bytes() == _pdf() for p in paths)   # оба файла реально на диске


@pytest.mark.asyncio
async def test_upload_other_files_limit_exceeded(
    client, mock_smtp, mock_magic, upload_root, monkeypatch
):
    """Суммарное количество файлов > MAX_OTHER_FILES → 422."""
    monkeypatch.setattr("src.services.attachments.MAX_OTHER_FILES", 1)
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
    # Удалён именно выбранный файл: остался другой; на диске первого нет, второй цел.
    assert r_del.json()["other_file_paths"] == [paths[1]]
    assert not (upload_root / paths[0]).exists()
    assert (upload_root / paths[1]).exists()


@pytest.mark.asyncio
async def test_delete_other_file_not_found(client, mock_smtp, upload_root):
    """Удаление несуществующего файла → 404."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    r = await client.delete(f"/subtasks/{subtask['id']}/files/ghost.pdf")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_concurrent_uploads_do_not_lose_files(client, mock_smtp, mock_magic, upload_root):
    """FOR NO KEY UPDATE в upload_subtask_files: два параллельных запроса на загрузку разных файлов в одну подзадачу не теряют пути в other_file_paths."""
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


# CRM-синхронизация: проверяем outbox-строку 'sync_files' с нужным payload, а не прямой CRM-вызов. Подзадача «синхронизирована»
# через _set_subtask_crm_id (воркера в тестах нет).

@pytest.mark.asyncio
async def test_upload_spec_enqueues_sync_files_row(client, mock_smtp, mock_magic, upload_root):
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    await _set_subtask_crm_id(subtask["id"], 55)

    await client.post(f"/subtasks/{subtask['id']}/specification", files=_spec_upload(_pdf()))

    rows = await _outbox_rows_for_subtask(subtask["id"])
    sync_rows = [r for r in rows if r.operation == "sync_files"]
    assert len(sync_rows) == 1
    assert sync_rows[0].payload["crm_subtask_id"] == 55
    # payload несёт флаг «слот ТЗ затронут», а не путь.
    assert sync_rows[0].payload["sync_specification"] is True


@pytest.mark.asyncio
async def test_upload_spec_enqueues_dependent_row_when_subtask_not_in_crm(client, mock_smtp, mock_magic, upload_root):
    """Загрузка файла до завершения 'create' подзадачи ставит зависимую sync_files-строку (раньше строки не было и файл не попадал в CRM);
    см. test_task_files.py::test_upload_spec_enqueues_dependent_row_when_task_not_in_crm.
    """
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    await client.post(f"/subtasks/{subtask['id']}/specification", files=_spec_upload(_pdf()))

    rows = await _outbox_rows_for_subtask(subtask["id"])
    create_rows = [r for r in rows if r.operation == "create"]
    sync_rows = [r for r in rows if r.operation == "sync_files"]
    assert len(create_rows) == 1
    assert len(sync_rows) == 1
    assert sync_rows[0].payload["crm_subtask_id"] is None
    assert sync_rows[0].payload["sync_specification"] is True
    assert sync_rows[0].depends_on_event_id == create_rows[0].id


@pytest.mark.asyncio
async def test_delete_spec_enqueues_sync_specification_row(client, mock_smtp, mock_magic, upload_root):
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    await _set_subtask_crm_id(sid, 55)
    await client.post(f"/subtasks/{sid}/specification", files=_spec_upload(_pdf()))

    await client.delete(f"/subtasks/{sid}/specification")

    rows = await _outbox_rows_for_subtask(sid)
    sync_rows = [r for r in rows if r.operation == "sync_files"]
    assert len(sync_rows) == 2  # upload, затем delete
    assert sync_rows[-1].payload["sync_specification"] is True


@pytest.mark.asyncio
async def test_upload_other_files_enqueues_sync_files_row(client, mock_smtp, mock_magic, upload_root):
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    await _set_subtask_crm_id(subtask["id"], 55)

    await client.post(
        f"/subtasks/{subtask['id']}/files",
        files=_other_uploads((_pdf(), "x.pdf"), (_pdf(), "y.pdf")),
    )

    rows = await _outbox_rows_for_subtask(subtask["id"])
    sync_rows = [r for r in rows if r.operation == "sync_files"]
    assert len(sync_rows) == 1
    assert sync_rows[0].payload["sync_other_files"] is True


@pytest.mark.asyncio
async def test_delete_other_file_enqueues_sync_files_row(client, mock_smtp, mock_magic, upload_root):
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    subtask = await _make_subtask(client, task["id"])
    sid = subtask["id"]
    await _set_subtask_crm_id(sid, 55)
    r_up = await client.post(
        f"/subtasks/{sid}/files",
        files=_other_uploads((_pdf(), "p.pdf"), (_pdf(), "q.pdf")),
    )
    filename = r_up.json()["other_file_paths"][0].split("/")[-1]

    await client.delete(f"/subtasks/{sid}/files/{filename}")

    rows = await _outbox_rows_for_subtask(sid)
    sync_rows = [r for r in rows if r.operation == "sync_files"]
    assert len(sync_rows) == 2  # upload, затем delete
    assert sync_rows[-1].payload["sync_other_files"] is True


# WS-рассылка событий. exclude_user_id исключает актора, поэтому наблюдатель регистрируется под другим user_id.

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
