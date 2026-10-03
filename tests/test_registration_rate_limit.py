"""Лимит request-code по IP (src/auth/registration_rate_limit.py): счёт окна и fail-closed при недоступном Redis."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.auth.registration_rate_limit import MAX_REQUESTS_PER_WINDOW, acquire_request_code_slot
from src.auth.user_models import RegistrationPending
from src.database import async_session_maker


def _fake_redis() -> AsyncMock:
    fake = AsyncMock()
    counter = {"n": 0}

    async def _incr(_key):
        counter["n"] += 1
        return counter["n"]

    fake.incr.side_effect = _incr
    return fake


async def test_slots_are_granted_up_to_the_limit_then_denied():
    with patch("src.auth.registration_rate_limit._get_redis", return_value=_fake_redis()):
        results = [await acquire_request_code_slot("1.2.3.4") for _ in range(MAX_REQUESTS_PER_WINDOW + 1)]

    assert results == [True] * MAX_REQUESTS_PER_WINDOW + [False]


async def test_redis_incr_failure_raises_503_and_logs_error(caplog):
    fake = AsyncMock()
    fake.incr.side_effect = ConnectionError("redis down")

    with patch("src.auth.registration_rate_limit._get_redis", return_value=fake):
        with caplog.at_level("ERROR", logger="src.auth.registration_rate_limit"):
            with pytest.raises(HTTPException) as exc_info:
                await acquire_request_code_slot("1.2.3.4")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "RATE_LIMITER_UNAVAILABLE"
    assert isinstance(exc_info.value.__cause__, ConnectionError)  # исходная причина сохранена
    assert "fail-closed" in caplog.text
    assert "redis down" in caplog.text


async def test_redis_expire_failure_raises_503():
    fake = _fake_redis()
    fake.expire.side_effect = TimeoutError("timeout")

    with patch("src.auth.registration_rate_limit._get_redis", return_value=fake):
        with pytest.raises(HTTPException) as exc_info:
            await acquire_request_code_slot("1.2.3.4")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "RATE_LIMITER_UNAVAILABLE"


async def test_client_creation_failure_raises_503():
    with patch("src.auth.registration_rate_limit._get_redis", side_effect=ValueError("bad REDIS_URL")):
        with pytest.raises(HTTPException) as exc_info:
            await acquire_request_code_slot("1.2.3.4")

    assert exc_info.value.status_code == 503


async def test_request_code_endpoint_returns_503_and_does_nothing_when_redis_is_down(client, mock_smtp):
    """Не 500 и без побочных эффектов: письмо не отправляется, запись registration_pending не создаётся."""
    fake = AsyncMock()
    fake.incr.side_effect = ConnectionError("redis down")

    with patch("src.auth.registration_rate_limit._get_redis", return_value=fake):
        r = await client.post("/auth/register/request-code", json={"email": "newuser@example.com"})

    assert r.status_code == 503
    assert r.json()["detail"] == "RATE_LIMITER_UNAVAILABLE"
    assert mock_smtp == {}
    async with async_session_maker() as session:
        assert (await session.execute(select(RegistrationPending))).scalars().all() == []


async def test_request_code_endpoint_recovers_when_redis_is_back(client, mock_smtp):
    """Отказ временный: после восстановления Redis тот же запрос проходит, перезапуск не нужен."""
    broken = AsyncMock()
    broken.incr.side_effect = ConnectionError("redis down")

    with patch("src.auth.registration_rate_limit._get_redis", return_value=broken):
        assert (await client.post("/auth/register/request-code", json={"email": "newuser@example.com"})).status_code == 503

    with patch("src.auth.registration_rate_limit._get_redis", return_value=_fake_redis()):
        r = await client.post("/auth/register/request-code", json={"email": "newuser@example.com"})

    assert r.status_code == 200
    assert "newuser@example.com" in mock_smtp
