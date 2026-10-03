"""Тесты эндпоинтов файлов задач.

POST/DELETE /tasks/{id}/specification, POST /tasks/{id}/files, DELETE /tasks/{id}/files/{name}
(успех, замена, 413, 422 по расширению и MIME, 404, 401, лимит файлов), WS-рассылка task_files_updated и
конкурентная загрузка (FOR NO KEY UPDATE).
"""

import asyncio
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.database import async_session_maker
from src.realtime.connection_manager import connection_manager
from src.task_logic.models import CrmOutbox, Task
from tests.conftest import register_and_login

EMAIL = "file_task@example.com"


async def _set_task_crm_id(task_id: int, crm_task_id: int) -> None:
    """Имитирует выполненный Celery 'create' задачи напрямую в БД (воркера в тестах нет)."""
    async with async_session_maker() as session:
        task = await session.get(Task, task_id)
        task.crm_task_id = crm_task_id
        await session.commit()


async def _outbox_rows_for_task(task_id: int) -> list[CrmOutbox]:
    async with async_session_maker() as session:
        return (
            await session.execute(
                select(CrmOutbox)
                .where(CrmOutbox.aggregate_type == "task", CrmOutbox.aggregate_id == task_id)
                .order_by(CrmOutbox.id)
            )
        ).scalars().all()

# ID заведомо выше любого реального пользователя в тестовой БД (truncate между тестами) —
# используется как "наблюдатель", не совпадающий с exclude_user_id актёра запроса.
_OBSERVER_ID = 999999


class _ObserverWebSocket:
    """Минимальная замена starlette.WebSocket: фиксирует отправленное.

    Проверяет вызов broadcast_task_event из HTTP-эндпоинта через process-wide connection_manager.
    """

    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


def _pdf() -> bytes:
    # %PDF-1.4: magic bytes, по которым mock_magic вернёт "application/pdf"
    return b'%PDF-1.4 fake pdf content for tests'


def _png() -> bytes:
    # \x89PNG\r\n\x1a\n: сигнатура PNG; mock_magic вернёт "image/png"
    return b'\x89PNG\r\n\x1a\n fake png content for tests'


def _garbage() -> bytes:
    # Нет стандартной сигнатуры; mock_magic вернёт "application/octet-stream"
    return b'not any known binary format'


async def _auth(client: AsyncClient, mock_smtp: dict) -> None:
    """Регистрирует и авторизует тестового пользователя."""
    await register_and_login(client, mock_smtp, EMAIL)


async def _make_task(client: AsyncClient) -> dict:
    # POST /create-task/ теперь multipart/form-data — см. tests/test_tasks.py::_create.
    r = await client.post(
        "/create-task/", data={"data": json.dumps({"title": "FileTask", "description": "d"})}
    )
    assert r.status_code == 201
    return r.json()


def _spec_upload(content: bytes, filename: str = "tz.pdf") -> dict:
    # httpx принимает {"field": (name, data, content_type)} для multipart
    return {"file": (filename, content, "application/octet-stream")}


def _other_uploads(*pairs: tuple[bytes, str]) -> list[tuple]:
    # Несколько файлов в одном поле "files": list of ("field", (name, data, ct))
    return [("files", (name, data, "application/octet-stream")) for data, name in pairs]


@pytest.mark.asyncio
async def test_upload_spec_success(client, mock_smtp, mock_magic, upload_root):
    """Валидный PDF принимается; ответ содержит путь с расширением .pdf."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    r = await client.post(
        f"/tasks/{task['id']}/specification",
        files=_spec_upload(_pdf()),
    )
    assert r.status_code == 200
    path = r.json()["specification_path"]
    assert path.endswith(".pdf")
    assert "specification" in path
    # Файл реально записан на диск с теми же байтами (а не только путь в ответе).
    assert (upload_root / path).read_bytes() == _pdf()


@pytest.mark.asyncio
async def test_upload_spec_replaces_existing(client, mock_smtp, mock_magic, upload_root):
    """Повторная загрузка ТЗ заменяет предыдущий файл; путь обновляется."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    first = await client.post(
        f"/tasks/{tid}/specification", files=_spec_upload(_pdf(), "v1.pdf")
    )
    old_path = first.json()["specification_path"]
    r = await client.post(
        f"/tasks/{tid}/specification", files=_spec_upload(_pdf(), "v2.pdf")
    )
    assert r.status_code == 200
    # Имя нового файла содержит "v2" (UUID-префикс не мешает — "v2" есть в исходном имени)
    new_path = r.json()["specification_path"]
    assert "v2" in new_path
    # Старый файл удалён с диска (не копится), новый записан.
    assert not (upload_root / old_path).exists()
    assert (upload_root / new_path).exists()


@pytest.mark.asyncio
async def test_upload_spec_size_limit(client, mock_smtp, mock_magic, upload_root, monkeypatch):
    """Файл больше MAX_FILE_SIZE → 413 Request Entity Too Large."""
    # Снижаем лимит до 5 байт; _pdf() содержит значительно больше.
    monkeypatch.setattr("src.utils.file_utils.MAX_FILE_SIZE", 5)
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    r = await client.post(
        f"/tasks/{task['id']}/specification", files=_spec_upload(_pdf())
    )
    assert r.status_code == 413
    assert [p for p in upload_root.rglob("*") if p.is_file()] == []    # отклонённый файл на диск не попал


@pytest.mark.asyncio
async def test_upload_spec_disk_failure_returns_500_not_unhandled(
    client, mock_smtp, mock_magic, upload_root, monkeypatch,
):
    """Сбой записи ТЗ на диск раньше давал необработанный 500, теперь — HTTPException(500) в штатном JSON-формате."""
    def _boom(dest_dir, filename, content, upload_root):
        raise OSError("simulated disk failure")

    monkeypatch.setattr("src.services.attachments.save_file", _boom)
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    r = await client.post(f"/tasks/{task['id']}/specification", files=_spec_upload(_pdf()))
    assert r.status_code == 500
    assert "detail" in r.json()  # штатный JSON-ответ, а не голая трасса FastAPI


@pytest.mark.asyncio
async def test_upload_spec_commit_failure_deletes_orphaned_file(
    client, mock_smtp, mock_magic, upload_root, monkeypatch,
):
    """Файл уже на диске до commit; при падении commit транзакция откатывается, и файл остался бы сиротой. Теперь он удаляется в except-ветке,
    а сбой всплывает клиенту.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    await _auth(client, mock_smtp)
    task = await _make_task(client)

    original_commit = AsyncSession.commit
    calls = {"n": 0}

    async def _commit_raises_once(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated commit failure")
        return await original_commit(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "commit", _commit_raises_once)

    # Голое исключение долетает до httpx как исключение (ASGITransport raise_app_exceptions=True), а не response(500); важно, что сбой не проглочен.
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        await client.post(f"/tasks/{task['id']}/specification", files=_spec_upload(_pdf()))

    # Файл был записан на диск ДО неудавшегося commit — except-ветка должна была его удалить.
    assert [p for p in upload_root.rglob("*") if p.is_file()] == []


@pytest.mark.asyncio
async def test_upload_other_files_commit_failure_deletes_orphaned_files(
    client, mock_smtp, mock_magic, upload_root, monkeypatch,
):
    """Симметрично test_upload_spec_commit_failure_deletes_orphaned_file для «Иных документов»: save_errors прикрывали сбой save_file,
    но не сбой commit после успешной записи всех файлов.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    await _auth(client, mock_smtp)
    task = await _make_task(client)

    original_commit = AsyncSession.commit
    calls = {"n": 0}

    async def _commit_raises_once(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated commit failure")
        return await original_commit(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "commit", _commit_raises_once)

    # См. комментарий про pytest.raises в test_upload_spec_commit_failure_deletes_orphaned_file
    # выше — ASGITransport тестового клиента, не поведение продакшена.
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        await client.post(
            f"/tasks/{task['id']}/files", files=_other_uploads((_pdf(), "a.pdf"), (_pdf(), "b.pdf")),
        )

    # Оба файла успели сохраниться на диск ДО неудавшегося commit — оба должны быть удалены.
    assert [p for p in upload_root.rglob("*") if p.is_file()] == []


@pytest.mark.asyncio
async def test_upload_other_files_partial_disk_failure_rolls_back(
    client, mock_smtp, mock_magic, upload_root, monkeypatch,
):
    """Если один из нескольких файлов не сохраняется, весь батч откатывается: записанные файлы удаляются, ответ — понятный 500."""
    import src.services.attachments as attachments_module

    real_save_file = attachments_module.save_file
    call_count = {"n": 0}

    def _flaky_save_file(dest_dir, filename, content, upload_root):
        call_count["n"] += 1
        if filename.endswith("_bad.pdf"):
            raise OSError("simulated disk failure")
        return real_save_file(dest_dir, filename, content, upload_root)

    monkeypatch.setattr(attachments_module, "save_file", _flaky_save_file)

    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    r = await client.post(
        f"/tasks/{tid}/files",
        files=_other_uploads((_pdf(), "good.pdf"), (_pdf(), "bad.pdf")),
    )

    assert r.status_code == 500
    assert call_count["n"] == 2  # оба файла реально пытались сохраниться (gather, не короткое замыкание)
    # good.pdf успел сохраниться в этой же попытке — должен быть удалён при откате.
    assert [p for p in upload_root.rglob("*") if p.is_file()] == []

    # Задача осталась в исходном состоянии — ни одного файла не подтверждено.
    detail = await client.get(f"/tasks/{tid}")
    assert detail.json()["other_file_paths"] is None


@pytest.mark.asyncio
async def test_upload_spec_forbidden_filename_chars(client, mock_smtp, mock_magic, upload_root):
    """Имя с запрещённым символом (FORBIDDEN_FILENAME_CHARS) → 422 до проверки расширения/MIME, файл на диск не пишется."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    r = await client.post(
        f"/tasks/{task['id']}/specification",
        files={"file": ("tz?.pdf", _pdf(), "application/octet-stream")},
    )
    assert r.status_code == 422
    assert "?" in r.json()["detail"]
    assert [p for p in upload_root.rglob("*") if p.is_file()] == []


@pytest.mark.asyncio
async def test_upload_other_files_forbidden_filename_chars(client, mock_smtp, mock_magic, upload_root):
    """То же для «Иных документов»: один файл с запрещённым символом блокирует весь батч.

    Символ "*", а не '"': httpx сам percent-кодирует кавычку в Content-Disposition, и тест не нашёл бы нарушения.
    """
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    r = await client.post(
        f"/tasks/{task['id']}/files",
        files=_other_uploads((_pdf(), "good.pdf"), (_pdf(), "bad*name.pdf")),
    )
    assert r.status_code == 422
    assert [p for p in upload_root.rglob("*") if p.is_file()] == []


@pytest.mark.asyncio
async def test_upload_spec_bad_extension(client, mock_smtp, mock_magic, upload_root):
    """Расширение не из белого списка (.exe) → 422, сообщение упоминает "Расширение"."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    r = await client.post(
        f"/tasks/{task['id']}/specification",
        files={"file": ("virus.exe", _pdf(), "application/octet-stream")},
    )
    assert r.status_code == 422
    assert "Расширение" in r.json()["detail"]


@pytest.mark.asyncio
async def test_upload_spec_real_size_limit_is_10_mb(client, mock_smtp, mock_magic, upload_root):
    """Без monkeypatch: ровно 10 МБ принимается, 10 МБ + 1 байт → 413 с «10 МБ» в тексте."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    limit = 10 * 1024 * 1024
    exact = _pdf() + b"0" * (limit - len(_pdf()))

    r = await client.post(f"/tasks/{task['id']}/specification", files=_spec_upload(exact))
    assert r.status_code == 200

    r = await client.post(f"/tasks/{task['id']}/specification", files=_spec_upload(exact + b"0"))
    assert r.status_code == 413
    assert "10 МБ" in r.json()["detail"]


@pytest.mark.asyncio
async def test_upload_spec_jpeg_accepted(client, mock_smtp, mock_magic, upload_root):
    """JPEG — один из трёх поддерживаемых форматов (PDF, JPEG, PNG)."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    r = await client.post(
        f"/tasks/{task['id']}/specification",
        files=_spec_upload(b"\xff\xd8\xff\xe0 fake jpeg", "scan.jpg"),
    )
    assert r.status_code == 200
    assert r.json()["specification_path"].endswith("_scan.jpg")


@pytest.mark.asyncio
@pytest.mark.parametrize("filename, content", [
    ("tz.docx", b"PK\x03\x04 fake docx"),
    ("tz.doc", b"\xd0\xcf\x11\xe0 fake doc"),
    ("tz.xlsx", b"PK\x03\x04 fake xlsx"),
    ("tz.xls", b"\xd0\xcf\x11\xe0 fake xls"),
    ("tz.txt", b"plain text"),
])
async def test_upload_spec_formats_no_longer_allowed(
    client, mock_smtp, mock_magic, upload_root, filename, content,
):
    """Word, Excel и TXT больше не поддерживаются — отклоняются по расширению (422)."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    r = await client.post(
        f"/tasks/{task['id']}/specification", files=_spec_upload(content, filename)
    )
    assert r.status_code == 422
    assert r.json()["detail"] == (
        f"Расширение '.{filename.rsplit('.', 1)[1]}' не разрешено. Допустимые: .jpeg, .jpg, .pdf, .png"
    )
    assert [p for p in upload_root.rglob("*") if p.is_file()] == []


@pytest.mark.asyncio
async def test_upload_spec_mime_mismatch(client, mock_smtp, mock_magic, upload_root):
    """PNG-байты + расширение .pdf → MIME-проверка отклоняет файл (422)."""
    # mock_magic вернёт "image/png" для PNG-байтов; .pdf ожидает "application/pdf"
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    r = await client.post(
        f"/tasks/{task['id']}/specification",
        files=_spec_upload(_png(), "tz.pdf"),  # PNG bytes, .pdf расширение
    )
    assert r.status_code == 422
    assert "MIME" in r.json()["detail"]


@pytest.mark.asyncio
async def test_upload_spec_task_not_found(client, mock_smtp, mock_magic, upload_root):
    """Задача не существует → 404."""
    await _auth(client, mock_smtp)
    r = await client.post(
        "/tasks/99999/specification", files=_spec_upload(_pdf())
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_upload_spec_unauthenticated(client, upload_root):
    """Запрос без куки авторизации → 401."""
    r = await client.post("/tasks/1/specification", files=_spec_upload(_pdf()))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_delete_spec_success(client, mock_smtp, mock_magic, upload_root):
    """DELETE после загрузки → 200, specification_path=None."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    uploaded = await client.post(f"/tasks/{tid}/specification", files=_spec_upload(_pdf()))
    path = uploaded.json()["specification_path"]
    assert (upload_root / path).exists()
    r = await client.delete(f"/tasks/{tid}/specification")
    assert r.status_code == 200
    assert r.json()["specification_path"] is None
    assert not (upload_root / path).exists()                         # файл удалён с диска
    assert (await client.get(f"/tasks/{tid}")).json()["specification_path"] is None


@pytest.mark.asyncio
async def test_delete_spec_no_file_uploaded(client, mock_smtp, upload_root):
    """DELETE когда ТЗ не загружен → 404."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    r = await client.delete(f"/tasks/{task['id']}/specification")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_upload_other_files_success(client, mock_smtp, mock_magic, upload_root):
    """Загрузка 2 файлов → 200, список из 2 путей с расширением .pdf."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    r = await client.post(
        f"/tasks/{task['id']}/files",
        files=_other_uploads((_pdf(), "a.pdf"), (_pdf(), "b.pdf")),
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
    # Устанавливаем лимит = 1 для предсказуемого теста
    monkeypatch.setattr("src.services.attachments.MAX_OTHER_FILES", 1)
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    # Загружаем 1 файл — достигаем лимит
    await client.post(f"/tasks/{tid}/files", files=_other_uploads((_pdf(), "a.pdf")))
    # Ещё один файл — превышение
    r = await client.post(f"/tasks/{tid}/files", files=_other_uploads((_pdf(), "b.pdf")))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_one_other_file(client, mock_smtp, mock_magic, upload_root):
    """Удаление одного файла → список уменьшается на 1."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    r_upload = await client.post(
        f"/tasks/{tid}/files",
        files=_other_uploads((_pdf(), "a.pdf"), (_pdf(), "b.pdf")),
    )
    paths = r_upload.json()["other_file_paths"]
    # Берём имя первого файла (UUID-префикс + исходное имя)
    filename = paths[0].split("/")[-1]   # "a1b2c3d4_a.pdf"
    r_del = await client.delete(f"/tasks/{tid}/files/{filename}")
    assert r_del.status_code == 200
    # Удалён именно выбранный файл: в списке остался другой, на диске первого нет, второй цел.
    assert r_del.json()["other_file_paths"] == [paths[1]]
    assert not (upload_root / paths[0]).exists()
    assert (upload_root / paths[1]).exists()


@pytest.mark.asyncio
async def test_delete_other_file_not_found(client, mock_smtp, upload_root):
    """Удаление несуществующего файла из пустого списка → 404."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    r = await client.delete(f"/tasks/{task['id']}/files/nonexistent.pdf")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_concurrent_uploads_do_not_lose_files(client, mock_smtp, mock_magic, upload_root):
    """FOR NO KEY UPDATE в upload_task_files: два параллельных запроса на загрузку разных файлов не теряют пути в other_file_paths."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]

    r1, r2 = await asyncio.gather(
        client.post(f"/tasks/{tid}/files", files=_other_uploads((_pdf(), "concurrent_a.pdf"))),
        client.post(f"/tasks/{tid}/files", files=_other_uploads((_pdf(), "concurrent_b.pdf"))),
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    r = await client.get(f"/tasks/{tid}")
    assert len(r.json()["other_file_paths"]) == 2


async def test_concurrent_spec_uploads_do_not_orphan_loser_file(client, mock_smtp, mock_magic, upload_root):
    """upload_specification без блокировки: два параллельных запроса читают один old_path и оба пишут файл; файл «проигравшего»
    остаётся сиротой. FOR NO KEY UPDATE сериализует запросы: второй видит specification_path первого как свой old_path
    и удаляет его при замене.
    """
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]

    r1, r2 = await asyncio.gather(
        client.post(f"/tasks/{tid}/specification", files=_spec_upload(_pdf(), "spec_a.pdf")),
        client.post(f"/tasks/{tid}/specification", files=_spec_upload(_pdf(), "spec_b.pdf")),
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    detail = await client.get(f"/tasks/{tid}")
    winner_path = detail.json()["specification_path"]
    assert winner_path is not None

    on_disk = [p for p in upload_root.rglob("*") if p.is_file()]
    # Ровно один файл на диске — тот, на который ссылается БД. Без блокировки
    # здесь остались бы ДВА файла (spec_a и spec_b), а в БД — только один из них.
    assert len(on_disk) == 1
    assert on_disk[0] == upload_root / winner_path


# CRM-синхронизация: проверяем outbox-строку 'sync_files' с нужным payload, а не прямой CRM-вызов. Задача «синхронизирована»
# через _set_task_crm_id (воркера в тестах нет).

@pytest.mark.asyncio
async def test_upload_spec_enqueues_sync_files_row(client, mock_smtp, mock_magic, upload_root):
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    await _set_task_crm_id(task["id"], 42)

    await client.post(f"/tasks/{task['id']}/specification", files=_spec_upload(_pdf()))

    rows = await _outbox_rows_for_task(task["id"])
    sync_rows = [r for r in rows if r.operation == "sync_files"]
    assert len(sync_rows) == 1
    assert sync_rows[0].payload["crm_task_id"] == 42
    # payload несёт флаг «слот ТЗ затронут», а не путь.
    assert sync_rows[0].payload["sync_specification"] is True
    assert sync_rows[0].depends_on_event_id is None  # crm_task_id уже известен — не зависит от create


@pytest.mark.asyncio
async def test_upload_spec_enqueues_dependent_row_when_task_not_in_crm(client, mock_smtp, mock_magic, upload_root):
    """Задача ещё не в CRM (crm_task_id is None): загрузка ТЗ ставит sync_files-строку, зависимую от 'create' через depends_on_event_id;
    она дождётся create и прочитает актуальные crm_task_id/specification_path из БД (раньше строки не было).
    """
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    await client.post(f"/tasks/{task['id']}/specification", files=_spec_upload(_pdf()))

    rows = await _outbox_rows_for_task(task["id"])
    create_rows = [r for r in rows if r.operation == "create"]
    sync_rows = [r for r in rows if r.operation == "sync_files"]
    assert len(create_rows) == 1
    assert len(sync_rows) == 1
    assert sync_rows[0].payload["crm_task_id"] is None
    assert sync_rows[0].payload["sync_specification"] is True
    assert sync_rows[0].depends_on_event_id == create_rows[0].id


@pytest.mark.asyncio
async def test_delete_spec_enqueues_sync_specification_row(client, mock_smtp, mock_magic, upload_root):
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    await _set_task_crm_id(tid, 42)
    await client.post(f"/tasks/{tid}/specification", files=_spec_upload(_pdf()))

    await client.delete(f"/tasks/{tid}/specification")

    rows = await _outbox_rows_for_task(tid)
    sync_rows = [r for r in rows if r.operation == "sync_files"]
    assert len(sync_rows) == 2  # upload, затем delete
    # Тот же флаг sync_specification, что у upload: обработчик сам увидит, что specification_path None, и очистит поле в CRM.
    assert sync_rows[-1].payload["sync_specification"] is True


@pytest.mark.asyncio
async def test_upload_other_files_enqueues_sync_files_row(client, mock_smtp, mock_magic, upload_root):
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    await _set_task_crm_id(task["id"], 42)

    await client.post(
        f"/tasks/{task['id']}/files",
        files=_other_uploads((_pdf(), "a.pdf"), (_pdf(), "b.pdf")),
    )

    rows = await _outbox_rows_for_task(task["id"])
    sync_rows = [r for r in rows if r.operation == "sync_files"]
    assert len(sync_rows) == 1
    # payload несёт флаг «слот иных документов затронут», а не список; фактический список проверяется через ответ API.
    assert sync_rows[0].payload["sync_other_files"] is True


@pytest.mark.asyncio
async def test_delete_other_file_enqueues_sync_files_row(client, mock_smtp, mock_magic, upload_root):
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    await _set_task_crm_id(tid, 42)
    r_up = await client.post(
        f"/tasks/{tid}/files",
        files=_other_uploads((_pdf(), "a.pdf"), (_pdf(), "b.pdf")),
    )
    filename = r_up.json()["other_file_paths"][0].split("/")[-1]

    await client.delete(f"/tasks/{tid}/files/{filename}")

    rows = await _outbox_rows_for_task(tid)
    sync_rows = [r for r in rows if r.operation == "sync_files"]
    assert len(sync_rows) == 2  # upload, затем delete
    assert sync_rows[-1].payload["sync_other_files"] is True


# WS-рассылка событий. exclude_user_id исключает актора, поэтому наблюдатель регистрируется под другим user_id.

@pytest.mark.asyncio
async def test_upload_spec_broadcasts_ws_event(client, mock_smtp, mock_magic, upload_root):
    """Загрузка ТЗ рассылает task_files_updated с task_id задачи и её title."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]

    observer = _ObserverWebSocket()
    connection_manager.register(_OBSERVER_ID, observer, "observer@example.com")
    try:
        await client.post(f"/tasks/{tid}/specification", files=_spec_upload(_pdf()))
    finally:
        connection_manager.unregister(_OBSERVER_ID, observer)

    assert len(observer.sent) == 1
    payload = json.loads(observer.sent[0])
    assert payload["type"] == "task_files_updated"
    assert payload["task_id"] == tid
    assert payload["title"] == task["title"]


@pytest.mark.asyncio
async def test_delete_spec_broadcasts_ws_event(client, mock_smtp, mock_magic, upload_root):
    """Удаление ТЗ рассылает task_files_updated с task_id задачи."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    await client.post(f"/tasks/{tid}/specification", files=_spec_upload(_pdf()))

    observer = _ObserverWebSocket()
    connection_manager.register(_OBSERVER_ID, observer, "observer@example.com")
    try:
        await client.delete(f"/tasks/{tid}/specification")
    finally:
        connection_manager.unregister(_OBSERVER_ID, observer)

    assert len(observer.sent) == 1
    payload = json.loads(observer.sent[0])
    assert payload["type"] == "task_files_updated"
    assert payload["task_id"] == tid


@pytest.mark.asyncio
async def test_upload_other_files_broadcasts_ws_event(client, mock_smtp, mock_magic, upload_root):
    """Загрузка «иных документов» рассылает task_files_updated с task_id задачи."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]

    observer = _ObserverWebSocket()
    connection_manager.register(_OBSERVER_ID, observer, "observer@example.com")
    try:
        await client.post(f"/tasks/{tid}/files", files=_other_uploads((_pdf(), "a.pdf")))
    finally:
        connection_manager.unregister(_OBSERVER_ID, observer)

    assert len(observer.sent) == 1
    payload = json.loads(observer.sent[0])
    assert payload["type"] == "task_files_updated"
    assert payload["task_id"] == tid


@pytest.mark.asyncio
async def test_delete_other_file_broadcasts_ws_event(client, mock_smtp, mock_magic, upload_root):
    """Удаление одного из «иных документов» рассылает task_files_updated."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    r_up = await client.post(f"/tasks/{tid}/files", files=_other_uploads((_pdf(), "a.pdf")))
    filename = r_up.json()["other_file_paths"][0].split("/")[-1]

    observer = _ObserverWebSocket()
    connection_manager.register(_OBSERVER_ID, observer, "observer@example.com")
    try:
        await client.delete(f"/tasks/{tid}/files/{filename}")
    finally:
        connection_manager.unregister(_OBSERVER_ID, observer)

    assert len(observer.sent) == 1
    payload = json.loads(observer.sent[0])
    assert payload["type"] == "task_files_updated"
    assert payload["task_id"] == tid


@pytest.mark.asyncio
async def test_spec_appears_in_get_task(client, mock_smtp, mock_magic, upload_root):
    """После загрузки ТЗ GET /tasks/{id} возвращает непустой specification_path."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    await client.post(f"/tasks/{tid}/specification", files=_spec_upload(_pdf()))
    r = await client.get(f"/tasks/{tid}")
    assert r.status_code == 200
    assert r.json()["specification_path"] is not None
    assert r.json()["specification_path"].endswith(".pdf")


@pytest.mark.asyncio
async def test_other_files_appear_in_get_task(client, mock_smtp, mock_magic, upload_root):
    """После загрузки файлов GET /tasks/{id} возвращает список other_file_paths."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    await client.post(f"/tasks/{tid}/files", files=_other_uploads((_pdf(), "a.pdf")))
    r = await client.get(f"/tasks/{tid}")
    assert r.status_code == 200
    paths = r.json()["other_file_paths"]
    assert paths is not None
    assert len(paths) == 1


@pytest.mark.asyncio
async def test_delete_last_other_file(client, mock_smtp, mock_magic, upload_root):
    """Удаление последнего файла из other_file_paths → ответ содержит пустой список."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    r_up = await client.post(f"/tasks/{tid}/files", files=_other_uploads((_pdf(), "only.pdf")))
    filename = r_up.json()["other_file_paths"][0].split("/")[-1]
    r_del = await client.delete(f"/tasks/{tid}/files/{filename}")
    assert r_del.status_code == 200
    assert r_del.json()["other_file_paths"] == []


@pytest.mark.asyncio
async def test_delete_task_removes_subtask_files(client, mock_smtp, mock_magic, upload_root):
    """Удаление задачи каскадно удаляет директорию с файлами подзадач с диска."""
    await _auth(client, mock_smtp)
    task = await _make_task(client)
    tid = task["id"]
    subtask = (await client.post(
        "/create-subtask/",
        data={"data": json.dumps({"task_id": tid, "title": "SubWithFiles", "description": ""})},
    )).json()
    sid = subtask["id"]
    await client.post(f"/subtasks/{sid}/specification", files=_spec_upload(_pdf()))
    subtask_dir = upload_root / "subtasks" / str(sid)
    assert subtask_dir.exists()
    await client.delete(f"/delete-task/{tid}")
    assert not subtask_dir.exists()
