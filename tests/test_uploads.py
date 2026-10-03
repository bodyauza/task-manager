"""GET /uploads/{file_path}: аутентифицированная раздача файлов и защита от path-traversal (resolve() + relative_to() относительно UPLOAD_ROOT)."""

from unittest.mock import patch
from urllib.parse import quote

import pytest
from httpx import AsyncClient

from tests.conftest import register_and_login

EMAIL = "uploads_user@example.com"
SECRET = b"TOP-SECRET-OUTSIDE-UPLOADS"


@pytest.fixture
def uploads_dir(tmp_path):
    """UPLOAD_ROOT во временной директории + «секретный» файл РЯДОМ с ней (вне
    корня раздачи) — цель для попыток path-traversal."""
    root = tmp_path / "uploads"
    (root / "tasks" / "1").mkdir(parents=True)
    (root / "tasks" / "1" / "note.txt").write_bytes(b"hello uploads")
    (tmp_path / "secret.txt").write_bytes(SECRET)
    with patch("src.routers.uploads.UPLOAD_ROOT", root):
        yield root


async def test_serve_upload_requires_authentication(client: AsyncClient, uploads_dir):
    r = await client.get("/uploads/tasks/1/note.txt")
    assert r.status_code == 401


async def test_serve_upload_returns_file_content(client: AsyncClient, mock_smtp: dict, uploads_dir):
    await register_and_login(client, mock_smtp, EMAIL)
    r = await client.get("/uploads/tasks/1/note.txt")
    assert r.status_code == 200
    assert r.content == b"hello uploads"


async def test_serve_upload_missing_file_is_404(client: AsyncClient, mock_smtp: dict, uploads_dir):
    await register_and_login(client, mock_smtp, EMAIL)
    r = await client.get("/uploads/tasks/1/nope.txt")
    assert r.status_code == 404


async def test_serve_upload_directory_is_404(client: AsyncClient, mock_smtp: dict, uploads_dir):
    await register_and_login(client, mock_smtp, EMAIL)
    assert (await client.get("/uploads/tasks/1")).status_code == 404
    assert (await client.get("/uploads/")).status_code == 404


@pytest.mark.parametrize("path", [
    "..%2fsecret.txt",                     # закодированный слэш доходит до роутера как "../secret.txt"
    "%2e%2e%2fsecret.txt",                 # то же, с закодированными точками
    "tasks%2f1%2f..%2f..%2f..%2fsecret.txt",
    "tasks/1/..%2f..%2f..%2fsecret.txt",
])
async def test_serve_upload_blocks_path_traversal(client: AsyncClient, mock_smtp: dict, uploads_dir, path: str):
    await register_and_login(client, mock_smtp, EMAIL)
    r = await client.get(f"/uploads/{path}")
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid file path"
    assert SECRET not in r.content


@pytest.mark.parametrize("path", ["../secret.txt", "tasks/1/../../../secret.txt"])
async def test_serve_upload_plain_dotdot_never_leaks_outside_file(
    client: AsyncClient, mock_smtp: dict, uploads_dir, path: str,
):
    """Незакодированный «..» httpx может схлопнуть на клиенте (тогда обычный 404), иначе его отклонит роутер (400); файл вне UPLOAD_ROOT не отдаётся."""
    await register_and_login(client, mock_smtp, EMAIL)
    r = await client.get(f"/uploads/{path}")
    assert r.status_code in (400, 404)
    assert SECRET not in r.content


async def test_serve_upload_blocks_absolute_path(client: AsyncClient, mock_smtp: dict, uploads_dir, tmp_path):
    """UPLOAD_ROOT / "/abs/path" в pathlib даёт сам абсолютный путь — он
    остаётся вне корня и должен быть отклонён."""
    await register_and_login(client, mock_smtp, EMAIL)
    r = await client.get("/uploads/" + quote(str(tmp_path / "secret.txt"), safe=""))
    assert r.status_code == 400
    assert SECRET not in r.content


async def test_serve_upload_blocks_symlink_escape(client: AsyncClient, mock_smtp: dict, uploads_dir, tmp_path):
    """Символическая ссылка внутри UPLOAD_ROOT на файл снаружи: resolve() идёт по
    ссылке, поэтому итоговый путь вне корня — отказ."""
    link = uploads_dir / "tasks" / "1" / "link.txt"
    try:
        link.symlink_to(tmp_path / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("символические ссылки недоступны в этой среде (на Windows нужны права)")
    await register_and_login(client, mock_smtp, EMAIL)
    r = await client.get("/uploads/tasks/1/link.txt")
    assert r.status_code == 400
    assert SECRET not in r.content
