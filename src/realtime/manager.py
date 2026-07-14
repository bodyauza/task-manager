"""Реестр активных WebSocket-соединений и низкоуровневая доставка сообщений.

ConnectionManager отвечает за одну вещь (SRP): кто сейчас подключён и как
безопасно отправить ему байты. Он ничего не знает о том, что такое
«задача» или «подзадача» — форму payload для конкретных доменных событий
строит src.realtime.events, а не этот модуль. Это разделение и есть
Open/Closed на практике: чтобы завтра добавить, например,
"comment_created", ConnectionManager менять не придётся — достаточно
новой функции в events.py, использующей уже существующий broadcast().
"""

import json
import logging
from typing import Protocol

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class Broadcaster(Protocol):
    """Абстракция рассылки, на которую опирается src.realtime.events.

    events.py зависит от этого протокола, а не от конкретного класса
    ConnectionManager (DIP) — модуль высокого уровня (формирование событий)
    не завязан на детали транспорта. На практике это даёт замену реализации
    в тестах: достаточно передать любой объект с методом broadcast()
    подходящей сигнатуры, не трогая внутренности ConnectionManager.
    """

    async def broadcast(self, payload: dict, exclude_user_id: int | None = None) -> None: ...


class ConnectionManager:
    """Реестр WS-соединений: user_id → множество WebSocket-соединений.

    Один пользователь может держать несколько одновременных соединений
    (несколько вкладок браузера, несколько устройств) — `register` добавляет
    соединение в набор, а не вытесняет предыдущее. Email хранится отдельно,
    на уровне user_id, а не на уровне отдельного соединения: все сокеты
    одного пользователя относятся к одному и тому же email, дублировать
    его на каждое соединение незачем.
    """

    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}
        self._emails: dict[int, str] = {}

    def register(self, user_id: int, websocket: WebSocket, email: str) -> None:
        """Добавляет соединение в набор пользователя, не трогая остальные.

        set.add идемпотентен: повторная регистрация уже присутствующего
        websocket ничего не меняет.
        """
        self._connections.setdefault(user_id, set()).add(websocket)
        self._emails[user_id] = email

    def unregister(self, user_id: int, websocket: WebSocket) -> None:
        """Удаляет одно конкретное соединение из набора пользователя.

        set.discard не бросает исключение, если сокета уже нет в наборе —
        безопасно при повторном вызове (например, если broadcast уже
        удалил это же мёртвое соединение раньше, чем WebSocketDisconnect
        дошёл до вызывающего кода). Когда набор пользователя опустевает,
        удаляется и сама запись, и привязанный email.
        """
        sockets = self._connections.get(user_id)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            del self._connections[user_id]
            self._emails.pop(user_id, None)

    def get(self, user_id: int) -> set[WebSocket] | None:
        """Возвращает набор активных соединений пользователя (или None)."""
        return self._connections.get(user_id)

    def get_email(self, user_id: int) -> str | None:
        """Email пользователя, если у него есть хотя бы одно активное соединение."""
        return self._emails.get(user_id)

    async def _send_safe(self, connection: WebSocket, payload: str) -> bool:
        """Отправляет текстовый фрейм; возвращает False, если соединение мертво."""
        try:
            await connection.send_text(payload)
            return True
        except Exception:
            return False

    async def broadcast(self, payload: dict, exclude_user_id: int | None = None) -> None:
        """Рассылает payload всем подключённым соединениям, кроме exclude_user_id.

        Если у пользователя открыто несколько вкладок — событие уходит в
        каждую из них независимо. Мёртвые соединения (send_text бросил
        исключение) собираются в отдельный список пар (uid, connection) и
        удаляются через unregister после завершения итерации — изменять
        набор во время итерации по нему запрещено в Python.
        """
        data = json.dumps(payload)
        dead: list[tuple[int, WebSocket]] = []
        for uid, sockets in list(self._connections.items()):
            if uid == exclude_user_id:
                continue
            for connection in list(sockets):
                if not await self._send_safe(connection, data):
                    dead.append((uid, connection))
        for uid, connection in dead:
            self.unregister(uid, connection)


# Единственный экземпляр на процесс — как и раньше в routers/tasks.py,
# состояние соединений общее для всего приложения, а не per-request.
connection_manager = ConnectionManager()
