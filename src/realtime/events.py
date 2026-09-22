"""Форма доменных WS-событий для задач и подзадач.

Единственная функция здесь знает, как выглядит payload события
("type"/"title"/"sender" + произвольные доп. поля) — ConnectionManager
этого не знает и знать не должен (см. manager.py).
"""

from src.realtime import chat_history
from src.realtime.connection_manager import Broadcaster, connection_manager

# События задач/подзадач — CRUD и файловые — персистируются в Redis List
# (chat_history), переживают перезагрузку страницы и выводятся в WS-чат в
# одном формате «You: ...» (свои) / «email: ...» (чужие), см.
# docs/chat_history_redis_list_guide.md. Файловые события раньше были
# эфемерными и рисовались отдельной английской фразой только для актора; теперь
# они такие же записи чата, как и остальные действия.
_PERSISTED_EVENT_TYPES = frozenset({
    "task_created", "task_updated", "task_deleted",
    "subtask_created", "subtask_updated", "subtask_deleted",
    "task_files_updated", "subtask_files_updated",
})


async def broadcast_task_event(
    event_type: str,                    # тип события: "task_created", "subtask_updated" и т.д.
    title: str,                         # основное поле payload: title задачи или подзадачи
    exclude_user_id: int | None = None,  # серверный фильтр: broadcaster пропустит этот uid
    sender_email: str = "",             # email инициатора → data.sender в payload клиента
    broadcaster: Broadcaster = connection_manager,
    # DIP: зависимость от абстракции Broadcaster, а не от конкретного
    # ConnectionManager. Значение по умолчанию — единственный экземпляр
    # приложения (connection_manager); тесты и любой другой вызывающий код
    # могут передать свою реализацию без патчинга модуля.
    **extra,                            # дополнительные поля payload: task_title, actor_id и др.
) -> None:
    # Разделение намеренное: dict — «что отправить»; exclude_user_id — «кому не отправлять».
    # Если бы exclude_user_id лежал внутри dict — он попал бы в JSON-ответ клиента,
    # но никого бы не исключил из рассылки: Broadcaster.broadcast читает его отдельным аргументом.
    payload = {"type": event_type, "title": title, "sender": sender_email, **extra}
    if event_type in _PERSISTED_EVENT_TYPES:
        # Персистируется ВСЕГДА, независимо от exclude_user_id — это фильтр
        # только живой доставки; история должна одинаково пережить перезагрузку
        # и для актора (он увидит "Вы: ..." при повторном рендере на клиенте
        # по actor_id), и для остальных.
        entry = await chat_history.append_event(payload)
        payload["id"] = entry["id"]
        payload["created_at"] = entry["created_at"]
    await broadcaster.broadcast(payload, exclude_user_id)
