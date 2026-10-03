"""Маршрутизация outbox-событий по шардам Redis-очередей: шард задачи = id % N.

shard_for_id() вызывается один раз за жизнь Task — на первом outbox-событии; результат хранится в Task.crm_shard
и не пересчитывается (смена числа шардов между событиями развела бы их по разным очередям). Поэтому consistent hashing
не нужен. id % N раздаёт задачи по кругу.

Ограничения:
- число шардов можно увеличивать (нужен сервис celery-worker-shard-N в docker-compose.yml);
- уменьшать нельзя, пока в task.crm_shard есть значения удаляемых шардов — их очереди останутся без воркера;
- равномерность по числу задач, а не по весу: подзадачи наследуют шард родителя.
"""

from src.crm.crm_config import crm_settings


def shard_names(count: int | None = None) -> list[str]:
    """Имена шардов shard_0..shard_{count-1}; очередь продюсер строит как f"crm_sync.{shard}". count=None читает актуальный
    crm_settings.OUTBOX_SHARD_COUNT (не кэшируется, чтобы тесты могли его менять).
    """
    n = count if count is not None else crm_settings.OUTBOX_SHARD_COUNT
    return [f"shard_{i}" for i in range(n)]


def shard_for_id(entity_id: int, count: int | None = None) -> str:
    """Шард для нового ключа: id % N (чистая функция)."""
    names = shard_names(count)
    return names[entity_id % len(names)]


def ensure_task_shard(task) -> str:
    """Возвращает назначенный task.crm_shard или лениво назначает его задачам, созданным до шардирования (без backfill-миграции).

    У Subtask своего crm_shard нет — вызывающий код передаёт сюда родительскую задачу. Принимает любой объект с id/crm_shard,
    чтобы не зависеть от доменных моделей.
    """
    if task.crm_shard is None:
        task.crm_shard = shard_for_id(task.id)
    return task.crm_shard
