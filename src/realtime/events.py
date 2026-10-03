"""Форма доменных WS-событий для задач и подзадач."""

import logging

from src.realtime import chat_history
from src.realtime.connection_manager import Broadcaster, connection_manager

logger = logging.getLogger(__name__)

# Персистируются в Redis List (chat_history) и переживают перезагрузку страницы.
_PERSISTED_EVENT_TYPES = frozenset({
    "task_created", "task_updated", "task_deleted",
    "subtask_created", "subtask_updated", "subtask_deleted",
    "task_files_updated", "subtask_files_updated",
})


async def broadcast_task_event(
    event_type: str,
    title: str,
    exclude_user_id: int | None = None,  # фильтр только живой доставки, в payload не попадает
    sender_email: str = "",
    broadcaster: Broadcaster = connection_manager,
    **extra,
) -> None:
    """Сбой Redis не пробрасывается: к моменту вызова операция уже закоммичена в БД."""
    payload = {"type": event_type, "title": title, "sender": sender_email, **extra}
    if event_type in _PERSISTED_EVENT_TYPES:
        try:
            entry = await chat_history.append_event(payload)
            payload["id"] = entry["id"]
            payload["created_at"] = entry["created_at"]
        except Exception:
            logger.exception("Не удалось сохранить событие %s в историю чата", event_type)
    try:
        await broadcaster.broadcast(payload, exclude_user_id)
    except Exception:
        logger.exception("Не удалось разослать событие %s", event_type)
