"""Тесты src.tasks.crm_rate_limit — token-bucket ограничитель запросов к CRM.

Реального Redis нет (тот же принцип, что и для CRM/Celery — мокается прямая
зависимость, не внешний сервис): redis.asyncio.Redis патчится фейковым
клиентом, который ведёт себя как реальный INCR/EXPIRE NX, чтобы проверить
именно то, что вызвало живой баг при docker compose up — ключ без TTL
("EXPIRE NX" должен САМОВОССТАНАВЛИВАТЬ TTL, даже когда count > 1), а не
только happy path.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.tasks.crm_rate_limit import acquire_slot


class FakeRedis:
    """Минимальная имитация redis.asyncio.Redis: INCR + EXPIRE(nx=True) с
    тем же поведением, что и у реального Redis — EXPIRE с nx=True не
    перезаписывает уже существующий TTL, но устанавливает его, если TTL нет.
    """

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
    """Регрессионный тест на живой баг (docker compose up): если предыдущий
    вызов успел INCR, но упал до EXPIRE (сеть, падение процесса, класс ошибок
    "Event loop is closed" при переиспользовании соединения между
    asyncio.run() — см. докстринг acquire_slot), ключ остаётся без TTL и
    БЕЗ nx=True на EXPIRE навсегда заблокировал бы все последующие вызовы
    (TTL ключа был обнаружен равным -1 после серии таких сбоев). Эта функция
    должна пытаться выставить TTL на КАЖДОМ вызове (не только когда count==1),
    и nx=True гарантирует самовосстановление, ничего не ломая для уже
    здорового ключа (см. test_acquire_slot_denies_when_over_limit выше —
    там EXPIRE тоже вызывается, но no-op благодаря nx)."""
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
