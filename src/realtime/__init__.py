"""Публичная поверхность realtime-модуля: broadcast_task_event и websocket_router."""

from src.realtime.events import broadcast_task_event
from src.realtime.connection_manager import Broadcaster, ConnectionManager, connection_manager
from src.realtime.router import router as websocket_router

__all__ = [
    "broadcast_task_event",
    "Broadcaster",
    "ConnectionManager",
    "connection_manager",
    "websocket_router",
]
