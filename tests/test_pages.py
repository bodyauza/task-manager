from httpx import AsyncClient

from tests.conftest import register_user

VALID_EMAIL    = "pageuser@example.com"
VALID_PASSWORD = "Password1!"


async def _register_login(client: AsyncClient, mock_smtp: dict) -> None:
    await register_user(client, mock_smtp, VALID_EMAIL, VALID_PASSWORD)
    await client.post("/auth/login", data={"username": VALID_EMAIL, "password": VALID_PASSWORD})


# ── GET / (login page) ───────────────────────────────────────────────────────

async def test_login_page_ok(client: AsyncClient):
    r = await client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "loginForm" in r.text


# ── GET /register ─────────────────────────────────────────────────────────────

async def test_register_page_ok(client: AsyncClient):
    r = await client.get("/register")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "registerForm" in r.text


# ── GET /task-board ───────────────────────────────────────────────────────────

async def test_task_board_unauthenticated(client: AsyncClient):
    r = await client.get("/task-board")
    assert r.status_code == 401


async def test_task_board_authenticated(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/task-board")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "taskList" in r.text
