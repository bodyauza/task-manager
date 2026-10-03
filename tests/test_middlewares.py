"""Тесты MaxBodySizeMiddleware (src/middlewares.py).

Юнит-тесты работают с ASGI-интерфейсом мидлвари с маленьким max_body_size (Content-Length, потоковый счётчик, одиночный send()).
Интеграционный тест проверяет тот же путь Content-Length поверх настоящего приложения.
"""

import json

import pytest

from src.middlewares import MAX_REQUEST_BODY_SIZE, MaxBodySizeMiddleware


def _make_receive(chunks: list[bytes]):
    """ASGI receive(), отдающий чанки http.request и затем пустое сообщение с more_body=False (конец тела без Content-Length)."""
    it = iter(chunks)

    async def receive():
        try:
            body = next(it)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": body, "more_body": True}

    return receive


def _http_scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {"type": "http", "method": "POST", "path": "/x", "headers": headers or []}


@pytest.mark.asyncio
async def test_streaming_body_over_limit_rejected_before_downstream_finishes():
    """Без Content-Length: превышение лимита обнаруживается по факту пришедших
    байт, downstream-приложение не успевает получить все чанки и не отвечает."""
    downstream_reached_end = False

    async def downstream_app(scope, receive, send):
        nonlocal downstream_reached_end
        while True:
            message = await receive()
            if not message.get("more_body", True):
                downstream_reached_end = True
                break
        # Сюда дойти не должно — receive() должен упасть раньше.
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    middleware = MaxBodySizeMiddleware(downstream_app, max_body_size=10)
    chunks = [b"a" * 6, b"b" * 6, b"c" * 6]  # 12 > 10 уже после второго чанка

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await middleware(_http_scope(), _make_receive(chunks), send)

    assert downstream_reached_end is False  # downstream не дочитал тело до конца
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413
    body = b"".join(m["body"] for m in sent if m["type"] == "http.response.body")
    assert "МБ" in json.loads(body)["detail"]
    # downstream не успел отправить свой 200 — единственный ответ здесь наш 413.
    assert sum(1 for m in sent if m["type"] == "http.response.start") == 1


@pytest.mark.asyncio
async def test_content_length_over_limit_rejected_without_calling_downstream():
    """Content-Length больше лимита → downstream-приложение не вызывается вообще
    (запрос отклоняется до какого-либо чтения тела)."""
    downstream_called = False

    async def downstream_app(scope, receive, send):
        nonlocal downstream_called
        downstream_called = True

    middleware = MaxBodySizeMiddleware(downstream_app, max_body_size=10)
    scope = _http_scope(headers=[(b"content-length", b"999")])

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        raise AssertionError("receive() не должен вызываться — отказ по заголовку до чтения тела")

    await middleware(scope, receive, send)

    assert downstream_called is False
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413


@pytest.mark.asyncio
async def test_body_under_limit_passes_through_unchanged():
    """Тело в пределах лимита доходит до downstream-приложения целиком, ответ
    мидлварь не трогает."""
    received_bodies: list[bytes] = []

    async def downstream_app(scope, receive, send):
        while True:
            message = await receive()
            received_bodies.append(message.get("body", b""))
            if not message.get("more_body", True):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    middleware = MaxBodySizeMiddleware(downstream_app, max_body_size=100)
    chunks = [b"a" * 10, b"b" * 10]

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await middleware(_http_scope(), _make_receive(chunks), send)

    assert b"".join(received_bodies) == b"a" * 10 + b"b" * 10
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200


@pytest.mark.asyncio
async def test_non_http_scope_passed_through():
    """scope['type'] != 'http' (например, websocket/lifespan) — мидлварь не
    вмешивается вообще, никаких проверок Content-Length/потока."""
    called_with = {}

    async def downstream_app(scope, receive, send):
        called_with["scope"] = scope

    middleware = MaxBodySizeMiddleware(downstream_app, max_body_size=10)
    scope = {"type": "websocket"}

    async def receive():
        raise AssertionError("не должен понадобиться")

    async def send(message):
        raise AssertionError("не должен понадобиться")

    await middleware(scope, receive, send)
    assert called_with["scope"] is scope


@pytest.mark.asyncio
async def test_declared_content_length_over_real_limit_rejected_via_full_app(client):
    """Интеграционный путь через приложение и полный стек мидлварей: реальный MAX_REQUEST_BODY_SIZE, тело крошечное, а Content-Length врёт."""
    r = await client.post(
        "/auth/register/request-code",
        content=b"{}",
        headers={
            "content-length": str(MAX_REQUEST_BODY_SIZE + 1),
            "content-type": "application/json",
        },
    )
    assert r.status_code == 413
    assert "МБ" in r.json()["detail"]
