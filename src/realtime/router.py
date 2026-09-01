"""WebSocket-эндпоинт /ws/tasks/{client_id}: хэндшейк, аутентификация,
регистрация соединения в ConnectionManager и приём чат-сообщений.

Вынесен из routers/tasks.py: маршрут не является частью CRUD задач и не
должен вынуждать другие роутеры (subtasks.py, файловые роутеры) тянуть
WS-утилиты из «чужого» по смыслу модуля.
"""

import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status

from src.auth.auth_config import get_access_strategy
from src.auth.manager import UserManager, get_user_manager
from src.realtime.connection_manager import connection_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Realtime"])


async def _publish_chat_message(sender_user_id: int, message: str) -> None:
    """Рассылает чат-сообщение всем клиентам, кроме отправителя.

    exclude_user_id=sender_user_id исключает из рассылки все соединения
    отправителя разом (в том числе другие его вкладки, если они открыты) —
    так же, как и для task_*/subtask_* событий.
    """
    sender_email = connection_manager.get_email(sender_user_id)
    if sender_email is None:
        return
    payload = {"type": "chat", "sender": sender_email, "text": message}
    await connection_manager.broadcast(payload, exclude_user_id=sender_user_id)


@router.websocket("/ws/tasks/{client_id}")
async def websocket_endpoint(
    client_id: int,
    websocket: WebSocket,
    user_manager: UserManager = Depends(get_user_manager),
):
    # Браузеры не поддерживают заголовок Authorization при WS-хэндшейке.
    # Аутентификация выполняется через куку access_token, установленную при логине.
    token = websocket.cookies.get("access_token")
    if token is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user = await get_access_strategy().read_token(token, user_manager)
    if user is None or not user.is_active:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    # Несколько одновременных соединений на пользователя разрешены (разные
    # вкладки/устройства) — регистрация нового соединения не закрывает старые.
    connection_manager.register(user.id, websocket, user.email)
    try:
        while True:
            message = await websocket.receive_text()
            await _publish_chat_message(user.id, message)
    except WebSocketDisconnect:
        pass
    except Exception:
        # Любое другое исключение (не штатный разрыв соединения) — не даём ему
        # пройти мимо unregister() в finally: без этого мёртвая запись осталась бы
        # в ConnectionManager до следующего broadcast(), который её случайно подчистит.
        logger.exception("Unexpected error in websocket_endpoint for user_id=%s", user.id)
    finally:
        connection_manager.unregister(user.id, websocket)
