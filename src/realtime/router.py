"""WebSocket-эндпоинт /ws/tasks/{client_id}: аутентификация, регистрация соединения и приём чат-сообщений."""

import logging
import time
from collections import deque
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status

from src.auth.auth_config import current_user, get_access_strategy
from src.openapi_responses import responses
from src.auth.manager import UserManager, get_user_manager
from src.auth.user_models import User
from src.config import settings
from src.realtime import chat_history
from src.realtime.connection_manager import connection_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Realtime"])


class _MessageRateLimiter:
    """Скользящее окно по одному соединению (в памяти процесса, без Redis)."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._stamps: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        while self._stamps and now - self._stamps[0] >= self._window:
            self._stamps.popleft()
        if len(self._stamps) >= self._limit:
            return False
        self._stamps.append(now)
        return True


async def _publish_chat_message(sender_user_id: int, message: str) -> None:
    """Сохраняет сообщение в историю и рассылает его всем, включая отправителя.

    sender_user_id нужен только ConnectionManager._deliver_local: он подставляет is_own
    и вырезает это поле из фрейма. Сбой Redis не мешает живой доставке.
    """
    sender_email = connection_manager.get_email(sender_user_id)
    if sender_email is None:
        return
    payload = {
        "type": "chat",
        "sender": sender_email,
        "text": message,
        "sender_user_id": sender_user_id,
    }
    try:
        entry = await chat_history.append_event(dict(payload))
        payload["id"] = entry["id"]
        payload["created_at"] = entry["created_at"]
    except Exception:
        logger.exception("Не удалось сохранить сообщение чата в историю")
    try:
        await connection_manager.broadcast(payload)
    except Exception:
        logger.exception("Не удалось разослать сообщение чата")


@router.get(
    "/chat/history",
    summary="История WebSocket-чата",
    description="Страница истории из Redis (сообщения чата и события действий). `before_id` — курсор для более старых записей.",
    responses=responses(401),
)
async def get_chat_history(
    before_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(chat_history.DEFAULT_PAGE_SIZE, ge=1, le=chat_history.MAX_PAGE_SIZE),
    user: User = Depends(current_user),
) -> list[dict]:
    """Страница истории WS-панели: чат-сообщения и события задач/подзадач в хронологическом порядке.

    sender_user_id наружу не отдаётся — вместо него чат-записи получают is_own.
    Старые записи без sender_user_id сопоставляются по email отправителя.
    """
    rows = await chat_history.get_history_page(before_id, limit)
    for row in rows:
        sender_user_id = row.pop("sender_user_id", None)
        if row.get("type") == "chat":
            row["is_own"] = (
                sender_user_id == user.id if sender_user_id is not None
                else row.get("sender") == user.email
            )
    return rows


@router.websocket("/ws/tasks/{client_id}")
async def websocket_endpoint(
    client_id: int,
    websocket: WebSocket,
    user_manager: UserManager = Depends(get_user_manager),
):
    # Браузеры не умеют Authorization при WS-хэндшейке — токен берётся из куки.
    token = websocket.cookies.get("access_token")
    if token is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user = await get_access_strategy().read_token(token, user_manager)
    if user is None or not user.is_active:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    connection_manager.register(user.id, websocket, user.email)
    limiter = _MessageRateLimiter(
        settings.WS_RATE_LIMIT_MESSAGES, settings.WS_RATE_LIMIT_WINDOW_SECONDS
    )
    try:
        while True:
            message = await websocket.receive_text()
            if len(message) > settings.WS_MAX_MESSAGE_CHARS:
                logger.warning("WS message too long from user_id=%s (%d chars)", user.id, len(message))
                await websocket.send_json({"type": "error", "detail": "Сообщение слишком длинное"})
                continue
            if not limiter.allow():
                logger.warning("WS rate limit exceeded for user_id=%s", user.id)
                await websocket.send_json({"type": "error", "detail": "Слишком много сообщений"})
                continue
            await _publish_chat_message(user.id, message)
    except WebSocketDisconnect:
        pass
    except Exception:
        # Любое другое исключение не должно пропустить unregister() в finally.
        logger.exception("Unexpected error in websocket_endpoint for user_id=%s", user.id)
    finally:
        connection_manager.unregister(user.id, websocket)
