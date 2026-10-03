"""Тесты подписчика на сигнал task_failure (src/celery_app.py).

Тест не async: Task.apply() сам делает asyncio.run(), а из работающего event loop это упало бы. apply() не пробрасывает
исключение (task_eager_propagates выключен), а возвращает EagerResult со state=FAILURE. Проверка ищет запись именно от логгера
"src.celery_app": у Celery есть свой логгер celery.app.trace, и поиск по всему caplog.text прошёл бы даже без подписки.
"""

import logging

from unittest.mock import patch

from src.tasks import global_lists_tasks


def test_task_failure_signal_logs_unhandled_celery_errors(caplog):
    """Исключение в периодической задаче должно попасть в лог через сигнал task_failure (имя задачи и причина сбоя)."""
    with patch.object(
        global_lists_tasks, "_sync_project_table_async", side_effect=RuntimeError("CRM недоступна"),
    ):
        with caplog.at_level(logging.ERROR):
            result = global_lists_tasks.sync_project_table.apply()

    assert result.failed()

    own_records = [r for r in caplog.records if r.name == "src.celery_app"]
    assert len(own_records) == 1, (
        f"ожидалась ровно одна запись от логгера src.celery_app, получено: "
        f"{[(r.name, r.getMessage()) for r in caplog.records]}"
    )
    message = own_records[0].getMessage()
    assert "src.tasks.global_lists_tasks.sync_project_table" in message
    assert "CRM недоступна" in message
