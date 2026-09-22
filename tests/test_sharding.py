"""Тесты src.tasks.sharding — шард задачи = id % N со sticky-присвоением
(маршрутизация outbox-событий в шарды, см. модульный докстринг sharding.py).
"""

from types import SimpleNamespace

import pytest

from src.tasks.sharding import ensure_task_shard, shard_for_id, shard_names


def test_shard_names_generates_expected_count():
    assert shard_names(4) == ["shard_0", "shard_1", "shard_2", "shard_3"]
    assert shard_names(1) == ["shard_0"]


def test_shard_for_id_is_id_modulo_n():
    assert shard_for_id(1, 4) == "shard_1"
    assert shard_for_id(4, 4) == "shard_0"
    assert shard_for_id(7, 4) == "shard_3"
    assert shard_for_id(10, 3) == "shard_1"


def test_shard_for_id_is_deterministic():
    assert {shard_for_id(123, 4) for _ in range(10)} == {shard_for_id(123, 4)}


def test_sequential_ids_are_distributed_exactly_evenly():
    counts: dict[str, int] = {}
    for i in range(1, 401):
        shard = shard_for_id(i, 4)
        counts[shard] = counts.get(shard, 0) + 1
    assert counts == {f"shard_{k}": 100 for k in range(4)}


def test_first_shards_cover_all_shards():
    # Кольцо на малой выборке могло не попасть ни в один из шардов; modulo —
    # гарантированно покрывает все шарды уже на первых N последовательных id.
    assert {shard_for_id(i, 4) for i in range(1, 5)} == set(shard_names(4))


def test_single_shard_always_returns_it():
    for i in range(50):
        assert shard_for_id(i, 1) == "shard_0"


def test_ensure_task_shard_assigns_once_and_is_sticky():
    task = SimpleNamespace(id=6, crm_shard=None)
    first = ensure_task_shard(task)
    assert first == shard_for_id(6)
    assert task.crm_shard == first
    # Смена числа шардов не переназначает уже присвоенный шард (sticky).
    assert ensure_task_shard(task) == first
    assert ensure_task_shard(SimpleNamespace(id=6, crm_shard="shard_9")) == "shard_9"


@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_shard_for_id_stays_within_shard_names(count):
    names = set(shard_names(count))
    assert all(shard_for_id(i, count) in names for i in range(1, 200))
