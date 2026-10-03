"""Тесты ограничителя запросов к CRM (src/tasks/crm_rate_limit.py).

redis.asyncio.Redis патчится фейком с поведением INCR/EXPIRE NX: проверяем самовосстановление TTL (ключ без TTL), а не только happy path.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.tasks.crm_rate_limit import acquire_slot


class FakeRedis:
    """Минимальная имитация Redis: INCR + EXPIRE(nx=True); nx не перезаписывает существующий TTL, но выставляет отсутствующий."""

    def __init__(self, initial_count: int = 0, has_ttl: bool = False):
        self.count = initial_count
        self.has_ttl = has_ttl
        self.expire_calls: list[tuple] = []

    async def incr(self, key: str) -> int:
        self.count += 1
        return self.count

    async def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
        self.expire_calls.append((key, seconds, nx))
        if nx and self.has_ttl:
            return False  # TTL уже был — NX не перезаписывает
        self.has_ttl = True
        return True

    async def aclose(self) -> None:
        pass


@pytest.fixture
def fake_redis():
    return FakeRedis()


async def test_acquire_slot_allows_when_under_limit(fake_redis):
    with patch("src.tasks.crm_rate_limit.redis.from_url", return_value=fake_redis):
        assert await acquire_slot() is True
    assert fake_redis.count == 1


async def test_acquire_slot_denies_when_over_limit():
    fake = FakeRedis(initial_count=5, has_ttl=True)  # уже на лимите (RATE_LIMIT_PER_SECOND=5 по умолчанию)
    with patch("src.tasks.crm_rate_limit.redis.from_url", return_value=fake):
        assert await acquire_slot() is False  # инкремент до 6 — превышает лимит


async def test_acquire_slot_sets_ttl_with_nx_on_first_call(fake_redis):
    """Первый вызов (count станет 1) — EXPIRE вызывается с nx=True, TTL
    устанавливается (has_ttl был False)."""
    with patch("src.tasks.crm_rate_limit.redis.from_url", return_value=fake_redis):
        await acquire_slot()
    assert fake_redis.expire_calls == [("crm_rate_limit", 1, True)]
    assert fake_redis.has_ttl is True


async def test_acquire_slot_self_heals_missing_ttl_on_later_call():
    """Регрессия живого бага: если вызов успел INCR, но упал до EXPIRE, ключ остаётся без TTL и без nx=True навсегда блокировал бы CRM-вызовы.
    EXPIRE пробуется на каждом вызове, а nx=True не ломает уже здоровый ключ.
    """
    fake = FakeRedis(initial_count=3, has_ttl=False)  # count>1, но TTL почему-то отсутствует
    with patch("src.tasks.crm_rate_limit.redis.from_url", return_value=fake):
        result = await acquire_slot()
    assert result is True  # count становится 4, <= 5 (лимит по умолчанию)
    assert fake.expire_calls == [("crm_rate_limit", 1, True)]
    assert fake.has_ttl is True  # самовосстановилось


async def test_acquire_slot_closes_client_even_on_error():
    fake = AsyncMock()
    fake.incr.side_effect = ConnectionError("Redis unreachable")
    with patch("src.tasks.crm_rate_limit.redis.from_url", return_value=fake):
        with pytest.raises(ConnectionError):
            await acquire_slot()
    fake.aclose.assert_called_once()
