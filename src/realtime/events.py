"""Форма доменных WS-событий для задач и подзадач.

Единственная функция здесь знает, как выглядит payload события
("type"/"title"/"sender" + произвольные доп. поля) — ConnectionManager
этого не знает и знать не должен (см. manager.py).
"""

from src.realtime.manager import Broadcaster, connection_manager


async def broadcast_task_event(
    event_type: str,                    # тип события: "task_created", "subtask_updated" и т.д.
    title: str,                         # основное поле payload: title задачи или подзадачи
    exclude_user_id: int | None = None, # серверный фильтр: broadcaster пропустит этот uid
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
    await broadcaster.broadcast(
        {"type": event_type, "title": title, "sender": sender_email, **extra},
        exclude_user_id,
    )
