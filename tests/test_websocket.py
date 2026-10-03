"""WebSocket-эндпоинт /ws/tasks/{client_id}: проверка куки, закрытие 1008, приём сообщения и рассылка, отписка, лимиты.

Эндпоинт вызывается через starlette TestClient.websocket_connect (его loop — в отдельном потоке). Чтение в TestClient блокирующее
и без таймаута, поэтому сценарий идёт в демоническом потоке с ограничением по времени (_run_with_timeout).
"""

import asyncio
import threading
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.auth.user_models import User
from src.config import settings
from src.database import async_session_maker
from src.main import app
from src.realtime.connection_manager import connection_manager
from tests.conftest import register_and_login

ALICE = "ws_alice@example.com"
BOB = "ws_bob@example.com"
CLOSE_POLICY_VIOLATION = 1008


async def _run_with_timeout(fn, timeout: float = 20.0):
    """Выполняет блокирующий сценарий в демоническом потоке; по таймауту — падение
    теста вместо бесконечного зависания всего прогона."""
    box: dict = {}

    def _target():
        try:
            box["result"] = fn()
        except BaseException as exc:      # noqa: BLE001 — пробрасываем в основной поток
            box["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    waited = 0.0
    while thread.is_alive() and waited < timeout:
        await asyncio.sleep(0.05)
        waited += 0.05
    if thread.is_alive():
        pytest.fail(f"WebSocket-сценарий не завершился за {timeout} с (вероятно, ждёт сообщение, которое не придёт)")
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _cookie(token: str) -> dict:
    return {"cookie": f"access_token={token}"}


async def _register_get_token(client: AsyncClient, mock_smtp: dict, email: str) -> tuple[int, str]:
    """Регистрирует пользователя, возвращает (id, access_token) и сбрасывает куки клиента."""
    await register_and_login(client, mock_smtp, email)
    token = client.cookies.get("access_token")
    client.cookies.clear()
    async with async_session_maker() as session:
        user_id = (await session.execute(select(User.id).where(User.email == email))).scalar_one()
    return user_id, token


def _connect_expecting_close(headers: dict | None, code: int):
    def _scenario():
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with TestClient(app).websocket_connect("/ws/tasks/1", headers=headers or {}):
                pass
        return exc_info.value.code
    return _scenario


async def test_ws_rejects_connection_without_cookie():
    code = await _run_with_timeout(_connect_expecting_close(None, CLOSE_POLICY_VIOLATION))
    assert code == CLOSE_POLICY_VIOLATION


async def test_ws_rejects_invalid_token():
    code = await _run_with_timeout(_connect_expecting_close(_cookie("not-a-valid-jwt"), CLOSE_POLICY_VIOLATION))
    assert code == CLOSE_POLICY_VIOLATION


async def test_ws_rejects_inactive_user(client: AsyncClient, mock_smtp: dict):
    user_id, token = await _register_get_token(client, mock_smtp, ALICE)
    async with async_session_maker() as session:
        (await session.get(User, user_id)).is_active = False
        await session.commit()

    code = await _run_with_timeout(_connect_expecting_close(_cookie(token), CLOSE_POLICY_VIOLATION))
    assert code == CLOSE_POLICY_VIOLATION
    assert connection_manager.get(user_id) is None


async def test_ws_registers_connection_and_unregisters_on_disconnect(client: AsyncClient, mock_smtp: dict):
    user_id, token = await _register_get_token(client, mock_smtp, ALICE)

    def _scenario():
        with TestClient(app).websocket_connect("/ws/tasks/1", headers=_cookie(token)):
            # Число соединений фиксируем внутри сеанса: get() отдаёт ЖИВОЕ множество,
            # которое после разрыва опустеет.
            during = len(connection_manager.get(user_id) or ())
            email = connection_manager.get_email(user_id)
        return during, email

    during, email = await _run_with_timeout(_scenario)
    assert during == 1                                     # соединение зарегистрировано за время сеанса
    assert email == ALICE
    assert connection_manager.get(user_id) is None        # и снято после разрыва


async def test_ws_client_id_in_url_does_not_affect_identity(client: AsyncClient, mock_smtp: dict):
    """Личность берётся из куки, а не из client_id в URL: подставив чужой id,
    нельзя подключиться «как другой пользователь»."""
    user_id, token = await _register_get_token(client, mock_smtp, ALICE)

    def _scenario():
        with TestClient(app).websocket_connect("/ws/tasks/424242", headers=_cookie(token)):
            return connection_manager.get(user_id) is not None, connection_manager.get(424242)

    registered_as_real_user, registered_as_url_id = await _run_with_timeout(_scenario)
    assert registered_as_real_user is True
    assert registered_as_url_id is None


async def test_ws_chat_message_reaches_everyone_with_is_own_flag(client: AsyncClient, mock_smtp: dict):
    alice_id, alice_token = await _register_get_token(client, mock_smtp, ALICE)
    bob_id, bob_token = await _register_get_token(client, mock_smtp, BOB)

    def _scenario():
        with TestClient(app).websocket_connect("/ws/tasks/1", headers=_cookie(alice_token)) as alice_a, \
             TestClient(app).websocket_connect("/ws/tasks/1", headers=_cookie(alice_token)) as alice_b, \
             TestClient(app).websocket_connect("/ws/tasks/2", headers=_cookie(bob_token)) as bob:
            alice_a.send_text("hello everyone")
            return alice_a.receive_json(), alice_b.receive_json(), bob.receive_json()

    from_tab_a, from_tab_b, to_bob = await _run_with_timeout(_scenario)

    for frame in (from_tab_a, from_tab_b, to_bob):
        assert frame["type"] == "chat"
        assert frame["text"] == "hello everyone"
        assert frame["sender"] == ALICE
        assert "id" in frame and "created_at" in frame
        assert "sender_user_id" not in frame               # внутренний id клиентам не отдаётся
    # Обе вкладки автора получают своё сообщение как «своё», собеседник — как чужое.
    assert from_tab_a["is_own"] is True
    assert from_tab_b["is_own"] is True
    assert to_bob["is_own"] is False
    assert connection_manager.get(alice_id) is None and connection_manager.get(bob_id) is None


async def test_ws_chat_message_is_persisted_in_history(client: AsyncClient, mock_smtp: dict, mock_chat_history_redis):
    _, token = await _register_get_token(client, mock_smtp, ALICE)

    def _scenario():
        with TestClient(app).websocket_connect("/ws/tasks/1", headers=_cookie(token)) as ws:
            ws.send_text("persist me")
            return ws.receive_json()

    frame = await _run_with_timeout(_scenario)

    assert frame["text"] == "persist me"
    mock_chat_history_redis.rpush.assert_awaited()          # запись попала в Redis-список истории


async def test_ws_oversized_message_is_rejected_and_connection_stays_alive(
    client: AsyncClient, mock_smtp: dict, monkeypatch
):
    monkeypatch.setattr(settings, "WS_MAX_MESSAGE_CHARS", 20)
    _, token = await _register_get_token(client, mock_smtp, ALICE)

    def _scenario():
        with TestClient(app).websocket_connect("/ws/tasks/1", headers=_cookie(token)) as ws:
            ws.send_text("x" * 21)
            rejected = ws.receive_json()
            ws.send_text("ok")
            accepted = ws.receive_json()
            return rejected, accepted

    rejected, accepted = await _run_with_timeout(_scenario)
    assert rejected["type"] == "error"
    assert accepted["type"] == "chat" and accepted["text"] == "ok"


async def test_ws_message_rate_is_limited_per_connection(
    client: AsyncClient, mock_smtp: dict, monkeypatch
):
    monkeypatch.setattr(settings, "WS_RATE_LIMIT_MESSAGES", 2)
    monkeypatch.setattr(settings, "WS_RATE_LIMIT_WINDOW_SECONDS", 60.0)
    _, token = await _register_get_token(client, mock_smtp, ALICE)

    def _scenario():
        with TestClient(app).websocket_connect("/ws/tasks/1", headers=_cookie(token)) as ws:
            frames = []
            for i in range(3):
                ws.send_text(f"m{i}")
                frames.append(ws.receive_json())
            return frames

    frames = await _run_with_timeout(_scenario)
    assert [f["type"] for f in frames] == ["chat", "chat", "error"]


async def test_ws_survives_redis_failure_on_chat_message(client: AsyncClient, mock_smtp: dict):
    _, token = await _register_get_token(client, mock_smtp, ALICE)

    def _scenario():
        with patch(
            "src.realtime.router.chat_history.append_event",
            AsyncMock(side_effect=ConnectionError("redis down")),
        ):
            with TestClient(app).websocket_connect("/ws/tasks/1", headers=_cookie(token)) as ws:
                ws.send_text("still alive")
                return ws.receive_json()

    frame = await _run_with_timeout(_scenario)
    assert frame["type"] == "chat" and frame["text"] == "still alive"


def test_message_rate_limiter_window_slides(monkeypatch):
    from src.realtime import router as ws_router

    now = [100.0]
    monkeypatch.setattr(ws_router.time, "monotonic", lambda: now[0])
    limiter = ws_router._MessageRateLimiter(2, 10.0)

    assert limiter.allow() and limiter.allow()
    assert limiter.allow() is False
    now[0] += 10.0
    assert limiter.allow() is True
