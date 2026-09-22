"""Реестр активных WebSocket-соединений и низкоуровневая доставка сообщений.

ConnectionManager отвечает за одну вещь (SRP): кто сейчас подключён и как
безопасно отправить ему байты. Он ничего не знает о том, что такое
«задача» или «подзадача» — форму payload для конкретных доменных событий
строит src.realtime.events, а не этот модуль. Это разделение и есть
Open/Closed на практике: чтобы завтра добавить, например,
"comment_created", ConnectionManager менять не придётся — достаточно
новой функции в events.py, использующей уже существующий broadcast().

Redis Pub/Sub — рассылка между несколькими uvicorn-воркерами. broadcast()
делает ДВЕ вещи: (1) доставляет локальным соединениям ЭТОГО процесса сразу
же, синхронно, как и раньше — низкая латентность и точное сохранение
поведения для процесса-инициатора; (2) публикует то же событие в Redis-канал
"task_events", чтобы ДРУГИЕ процессы (другие uvicorn-воркеры) тоже доставили
его своим локальным соединениям. Сообщение несёт `origin` — случайный id,
сгенерированный этим экземпляром при создании: получив обратно СВОЁ ЖЕ
сообщение через подписку (Redis рассылает всем подписчикам, включая
публикующего), процесс узнаёт его по origin и не доставляет повторно (уже
доставил в п. 1). Публикация в Redis — best-effort (try/except): сбой
публикации не должен ронять локальную доставку, которая к этому моменту уже
произошла.
"""

import asyncio
import json
import logging
import uuid
from typing import Protocol

import redis.asyncio as redis
from fastapi import WebSocket

from src.config import settings

logger = logging.getLogger(__name__)

_CHANNEL = "task_events"
_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    # Module-level singleton — тот же приём, что и _get_shared_http_client()
    # в src/crm/client.py и _get_redis() в src/tasks/crm_rate_limit.py/
    # crm_shard_lock.py: один клиент на процесс. Функция (не метод), чтобы
    # тесты могли патчить её одной точкой независимо от того, сколько
    # экземпляров ConnectionManager создано (см. tests/test_realtime.py).
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL)
    return _redis_client


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
        # Случайный id ЭТОГО экземпляра/процесса — метка "origin" в
        # публикуемых Redis-сообщениях, чтобы не доставить своё же
        # событие повторно, получив его обратно через подписку (см.
        # докстринг модуля и broadcast()/_handle_pubsub_message() ниже).
        self._origin = uuid.uuid4().hex
        self._pubsub_task: asyncio.Task | None = None

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

    async def _deliver_local(self, payload: dict, exclude_user_id: int | None = None) -> None:
        """Рассылает payload соединениям ЭТОГО процесса, кроме exclude_user_id.

        Если у пользователя открыто несколько вкладок — событие уходит в
        каждую из них независимо. Мёртвые соединения (send_text бросил
        исключение) собираются в отдельный список пар (uid, connection) и
        удаляются через unregister после завершения итерации — изменять
        набор во время итерации по нему запрещено в Python.

        asyncio.gather вместо вложенного цикла с последовательным await:
        время рассылки одного события раньше росло линейно с числом открытых
        соединений (N последовательных await send_text) — тот же приём, что
        уже применён для cascade-удаления подзадач в CRM
        (services/tasks.py::delete_task). return_exceptions=True не даёт
        падению отправки в одно соединение прервать сбор результатов для
        остальных (хотя _send_safe и так перехватывает исключения сама —
        дополнительная защита на случай, если это изменится в будущем).
        """
        # sender_user_id — внутренний признак «это сообщение чата от пользователя
        # N» (см. router.py::_publish_chat_message): каждому получателю уходит
        # вариант БЕЗ этого поля, но с is_own (True для всех соединений
        # отправителя — в т.ч. других его вкладок, False для остальных). Всего
        # две сериализации на рассылку, а не по одной на получателя. Остальные
        # события (без sender_user_id) идут как раньше — одна сериализация на всех.
        sender_id = payload.get("sender_user_id")
        if sender_id is None:
            data_all = json.dumps(payload)
            data_own = data_other = data_all
        else:
            base = {k: v for k, v in payload.items() if k != "sender_user_id"}
            data_own = json.dumps({**base, "is_own": True})
            data_other = json.dumps({**base, "is_own": False})
        targets: list[tuple[int, WebSocket, str]] = [
            (uid, connection, data_own if uid == sender_id else data_other)
            for uid, sockets in list(self._connections.items())
            if uid != exclude_user_id
            for connection in list(sockets)
        ]
        if not targets:
            return
        results = await asyncio.gather(
            *(self._send_safe(connection, data) for _, connection, data in targets),
            return_exceptions=True,
        )
        for (uid, connection, _), ok in zip(targets, results):
            if ok is not True:
                self.unregister(uid, connection)

    async def broadcast(self, payload: dict, exclude_user_id: int | None = None) -> None:
        """Доставляет локальным соединениям СРАЗУ (см. _deliver_local — точно
        то же поведение, что и раньше, для процесса-инициатора), затем
        публикует то же событие в Redis-канал, чтобы другие uvicorn-воркеры
        доставили его своим собственным локальным соединениям (см. докстринг
        модуля). Публикация — best-effort: сбой Redis не должен ронять то,
        что уже доставлено локально несколькими строками выше.
        """
        await self._deliver_local(payload, exclude_user_id)
        try:
            message = json.dumps(
                {"origin": self._origin, "payload": payload, "exclude_user_id": exclude_user_id}
            )
            await _get_redis().publish(_CHANNEL, message)
        except Exception:
            logger.warning(
                "Не удалось опубликовать событие в Redis Pub/Sub — доставлено только этому процессу",
                exc_info=True,
            )

    async def _handle_pubsub_message(self, message: dict) -> None:
        """Обрабатывает одно сообщение из redis.pubsub().listen() — вынесено
        из _pubsub_loop отдельной функцией, чтобы тестировать без реального
        бесконечного async-итератора (см. tests/test_realtime.py)."""
        if message.get("type") != "message":
            return  # подтверждения subscribe/unsubscribe и т.п. — не события
        try:
            data = json.loads(message["data"])
        except Exception:
            logger.exception("Не удалось разобрать сообщение из Redis-канала %s", _CHANNEL)
            return
        if data.get("origin") == self._origin:
            return  # своё же сообщение — уже доставлено локально в broadcast()
        await self._deliver_local(data.get("payload", {}), data.get("exclude_user_id"))

    async def _pubsub_loop(self) -> None:
        pubsub = _get_redis().pubsub()
        await pubsub.subscribe(_CHANNEL)
        try:
            async for message in pubsub.listen():
                await self._handle_pubsub_message(message)
        finally:
            await pubsub.unsubscribe(_CHANNEL)
            await pubsub.aclose()

    def start_listening(self) -> None:
        """Запускает фоновую подписку на Redis-канал — вызывается из
        lifespan() (src/main.py) при старте приложения. Идемпотентно:
        повторный вызов при уже запущенной задаче не создаёт вторую."""
        if self._pubsub_task is None:
            self._pubsub_task = asyncio.create_task(self._pubsub_loop())

    async def stop_listening(self) -> None:
        """Останавливает фоновую подписку — вызывается из lifespan() при
        остановке приложения, парная операция к start_listening()."""
        if self._pubsub_task is not None:
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except asyncio.CancelledError:
                pass
            self._pubsub_task = None


# Единственный экземпляр на процесс — как и раньше в routers/tasks.py,
# состояние соединений общее для всего приложения, а не per-request.
connection_manager = ConnectionManager()
