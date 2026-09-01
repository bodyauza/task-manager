"""Публичная поверхность модуля реального времени (WebSocket).

Namespace-пакет вместо одного файла: ConnectionManager (транспорт),
broadcast_task_event (форма доменных событий) и сам WS-эндпоинт — три
разные причины для изменения (SRP), поэтому три разных файла.

Наружу отдаём только то, что нужно вызывающему коду (ISP):
  - broadcast_task_event — единственное, что нужно routers/tasks.py,
    routers/subtasks.py и файловым роутерам;
  - websocket_router — нужен только main.py для регистрации маршрута.
Внутренности ConnectionManager (реестр соединений, low-level send)
наружу не протекают — импортируются напрямую из src.realtime.connection_manager
только там, где это действительно требуется (тесты, сам router.py).
"""

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
