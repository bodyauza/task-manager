from httpx import AsyncClient

from tests.conftest import register_and_login

VALID_EMAIL    = "pageuser@example.com"
VALID_PASSWORD = "Password1!"


async def _register_login(client: AsyncClient, mock_smtp: dict) -> None:
    await register_and_login(client, mock_smtp, VALID_EMAIL, VALID_PASSWORD)


async def test_login_page_ok(client: AsyncClient):
    r = await client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "loginForm" in r.text


async def test_register_page_ok(client: AsyncClient):
    r = await client.get("/register")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "registerForm" in r.text


async def test_task_board_unauthenticated(client: AsyncClient):
    r = await client.get("/task-board")
    assert r.status_code == 401


async def test_task_board_authenticated(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/task-board")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "taskList" in r.text


_HTML = {"accept": "text/html"}


async def test_missing_task_page_redirects_browser_to_task_board(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/task/999999", headers=_HTML, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/task-board?notice=task_not_found"


async def test_missing_subtask_page_redirects_browser_to_task_board(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/subtask/999999", headers=_HTML, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/task-board?notice=subtask_not_found"


async def test_missing_subtask_board_page_redirects_browser_to_task_board(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/subtask-board/999999", headers=_HTML, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/task-board?notice=task_not_found"


async def test_missing_task_page_returns_json_for_non_html_client(client: AsyncClient, mock_smtp: dict):
    await _register_login(client, mock_smtp)
    r = await client.get("/task/999999", headers={"accept": "*/*"}, follow_redirects=False)
    assert r.status_code == 404
    assert r.json() == {"detail": "Task not found"}


async def test_missing_task_api_call_stays_json_even_with_html_accept(client: AsyncClient, mock_smtp: dict):
    """Редирект — только для страниц (/task/, /subtask/, /subtask-board/); API-путь
    /tasks/{id} (множественное число) остаётся JSON независимо от Accept."""
    await _register_login(client, mock_smtp)
    r = await client.get("/tasks/999999", headers=_HTML, follow_redirects=False)
    assert r.status_code == 404
    assert r.json() == {"detail": "Task not found"}
