"""WebSocket-эндпоинт /ws/tasks/{client_id}: хэндшейк, аутентификация,
регистрация соединения в ConnectionManager и приём чат-сообщений.

Вынесен из routers/tasks.py: маршрут не является частью CRUD задач и не
должен вынуждать другие роутеры (subtasks.py, файловые роутеры) тянуть
WS-утилиты из «чужого» по смыслу модуля.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status

from src.auth.auth_config import current_user, get_access_strategy
from src.openapi_responses import responses
from src.auth.manager import UserManager, get_user_manager
from src.auth.user_models import User
from src.realtime import chat_history
from src.realtime.connection_manager import connection_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Realtime"])


async def _publish_chat_message(sender_user_id: int, message: str) -> None:
    """Персистирует сообщение в Redis List (chat_history.append_event),
    затем рассылает его ВСЕМ клиентам, включая отправителя и все его вкладки.

    Отправитель больше не рисует своё сообщение сам (оптимистичного рендера в
    task-board.js нет): он получает то же эхо, что и остальные, но с
    is_own=True — так «You: ...» одинаково видно во всех вкладках автора и в
    восстановленной истории. sender_user_id (внутренний id) кладётся в payload
    ТОЛЬКО для ConnectionManager._deliver_local: тот для каждого соединения
    подставляет is_own и вырезает sender_user_id из фрейма, клиентам id не
    уходит. Через Redis Pub/Sub (другие uvicorn-воркеры) payload проходит с
    sender_user_id — каждый воркер сам решает is_own для своих сокетов.
    Персистентность в Redis List не зависит от того, доставлено ли
    сообщение живым соединениям.
    """
    sender_email = connection_manager.get_email(sender_user_id)
    if sender_email is None:
        return
    entry = await chat_history.append_event(
        {"type": "chat", "sender": sender_email, "text": message, "sender_user_id": sender_user_id}
    )
    payload = {
        "type": "chat",
        "sender": sender_email,
        "text": message,
        "sender_user_id": sender_user_id,
        "id": entry["id"],
        "created_at": entry["created_at"],
    }
    await connection_manager.broadcast(payload)


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
    """Одна страница истории панели WS — чат-сообщения (`type: "chat"`) вперемешку
    с CRUD-событиями задач/подзадач (`task_created`/`task_updated`/`task_deleted`/
    `subtask_created`/`subtask_updated`/`subtask_deleted` — см. src.realtime.events)
    в хронологическом порядке. Самая свежая страница (без before_id) либо более
    старая относительно курсора (пролистывание #messages вверх, см.
    src/static/js/task-board.js). Обычная аутентификация HTTP-запроса
    (current_user, кука access_token) — отдельного WS-контекста здесь нет,
    GET ничего не знает про конкретное соединение client_id.

    user не используется в теле — только как проверка, что запрос
    аутентифицирован. Без строгого response_model: разные типы записей несут
    разные наборы полей (chat — sender/text, task_* — title/task_id, subtask_* —
    ещё и task_title/subtask_id/actor_id) — единая Pydantic-схема на все типы
    добавила бы сложности без пользы. sender_user_id (внутреннее поле чат-записей,
    src.realtime.chat_history) в публичный ответ не входит: вместо него чат-записи
    получают is_own (написано ли сообщение текущим пользователем) — клиент по нему
    рисует «You: ...» вместо email. Записи, сохранённые до появления
    sender_user_id, сопоставляются по email отправителя (уникален в проекте).
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
