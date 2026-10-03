"""Реестр WebSocket-соединений и доставка сообщений.

ConnectionManager не знает о доменных событиях — их форму строит src.realtime.events.
broadcast() сразу доставляет локальным соединениям процесса и публикует событие в Redis
Pub/Sub, чтобы его доставили остальные uvicorn-воркеры. Сообщение несёт origin, по нему
процесс отбрасывает собственное эхо. Публикация best-effort: сбой Redis не отменяет
локальную доставку.
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
_PUBSUB_BACKOFF_START = 0.5
_PUBSUB_BACKOFF_MAX = 30.0
_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    # Один клиент на процесс; функция, а не метод — чтобы тесты патчили её одной точкой.
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL)
    return _redis_client


class Broadcaster(Protocol):
    """Абстракция рассылки: events.py зависит от неё, а не от ConnectionManager (в тестах подменяется)."""

    async def broadcast(self, payload: dict, exclude_user_id: int | None = None) -> None: ...


class ConnectionManager:
    """Реестр WS-соединений: user_id → набор WebSocket (несколько вкладок/устройств на пользователя)."""

    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}
        self._emails: dict[int, str] = {}
        # Метка origin публикуемых сообщений — чтобы не доставить своё же событие повторно.
        self._origin = uuid.uuid4().hex
        self._pubsub_task: asyncio.Task | None = None

    def register(self, user_id: int, websocket: WebSocket, email: str) -> None:
        """Добавляет соединение в набор пользователя (идемпотентно)."""
        self._connections.setdefault(user_id, set()).add(websocket)
        self._emails[user_id] = email

    def unregister(self, user_id: int, websocket: WebSocket) -> None:
        """Удаляет соединение; безопасно при повторном вызове. Пустой набор удаляет и email."""
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
        """Рассылает payload локальным соединениям, кроме exclude_user_id.

        Мёртвые соединения удаляются после рассылки, а не во время итерации.
        """
        # sender_user_id — внутренний признак чат-сообщения: получатели видят вариант без него, но с is_own.
        # Сериализаций две на рассылку, а не по одной на получателя.
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
        """Доставляет локально, затем публикует в Redis для других воркеров; сбой публикации не критичен."""
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
        """Обрабатывает одно сообщение подписки (вынесено для тестов без бесконечного listen())."""
        if message.get("type") != "message":
            return
        try:
            data = json.loads(message["data"])
        except Exception:
            logger.exception("Не удалось разобрать сообщение из Redis-канала %s", _CHANNEL)
            return
        if data.get("origin") == self._origin:
            return
        await self._deliver_local(data.get("payload", {}), data.get("exclude_user_id"))

    async def _pubsub_loop(self) -> None:
        """Подписка с переподключением и экспоненциальным backoff."""
        backoff = _PUBSUB_BACKOFF_START
        while True:
            pubsub = None
            try:
                pubsub = _get_redis().pubsub()
                await pubsub.subscribe(_CHANNEL)
                backoff = _PUBSUB_BACKOFF_START
                async for message in pubsub.listen():
                    await self._handle_pubsub_message(message)
                logger.warning("Redis Pub/Sub listen() завершился — переподключение")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Сбой Redis Pub/Sub — переподключение через %.1f с", backoff, exc_info=True
                )
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(_CHANNEL)
                        await pubsub.aclose()
                    except Exception:
                        pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _PUBSUB_BACKOFF_MAX)

    def start_listening(self) -> None:
        """Запускает фоновую подписку (из lifespan); повторный вызов ничего не делает."""
        if self._pubsub_task is None:
            self._pubsub_task = asyncio.create_task(self._pubsub_loop())

    async def stop_listening(self) -> None:
        """Останавливает фоновую подписку."""
        if self._pubsub_task is not None:
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except asyncio.CancelledError:
                pass
            self._pubsub_task = None


connection_manager = ConnectionManager()
