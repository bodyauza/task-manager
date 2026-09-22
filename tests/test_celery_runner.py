"""Запуск Celery-задач: src/celery_app.py::run_celery_task, src/database.py::run_isolated,
регистрация задач и расписание Beat.

Все Celery-задачи проекта — синхронные обёртки над asyncio.run() (через
run_celery_task). Багов, найденных живым прогоном docker compose и не пойманных
юнит-тестами, было два: (1) второй asyncio.run() в том же процессе падал на
переиспользовании соединения/httpx-клиента от закрытого event loop; (2)
autodiscover_tasks не регистрировал ни одной задачи. Обычные тесты этого не
видят (один event loop на тест, задачи вызываются через _..._async напрямую), а
NullPool в тестовом режиме скрывает проблему пула SQLAlchemy — поэтому здесь
проверяется сам жизненный цикл: два запуска подряд, освобождение ресурсов,
реестр задач.

Тесты СИНХРОННЫЕ (def, не async def): asyncio.run() нельзя вызывать из-под уже
работающего event loop.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.celery_app import celery_app, run_celery_task
from src.database import run_isolated


# ── run_celery_task ──────────────────────────────────────────────────────────

def test_run_celery_task_returns_result_of_coroutine():
    async def _work():
        return 41 + 1

    assert run_celery_task(_work()) == 42


def test_run_celery_task_twice_uses_a_fresh_event_loop_each_time():
    """Второй вызов в том же процессе (воркер Celery живёт долго) должен
    работать, а не падать «attached to a different loop»."""
    loops: list[int] = []

    async def _work():
        loops.append(id(asyncio.get_running_loop()))
        return "ok"

    assert run_celery_task(_work()) == "ok"
    assert run_celery_task(_work()) == "ok"
    assert len(loops) == 2      # оба раза выполнено; каждый — в собственном asyncio.run()


def test_shared_http_client_is_closed_and_recreated_per_task():
    """Разделяемый httpx-клиент CRM, созданный под один event loop, ломается в
    следующем asyncio.run(): run_celery_task обязан закрывать его и обнулять
    singleton, чтобы следующая задача лениво создала новый клиент."""
    import src.crm.client as crm_client

    async def _use_client():
        return crm_client._get_shared_http_client()

    first = run_celery_task(_use_client())
    assert first.is_closed                          # закрыт после задачи
    assert crm_client._shared_http_client is None   # singleton сброшен

    second = run_celery_task(_use_client())
    assert second is not first                      # новая задача получила новый клиент
    assert second.is_closed


def test_run_celery_task_closes_client_and_propagates_when_task_raises():
    async def _fail():
        raise RuntimeError("CRM недоступна")

    with patch("src.celery_app.aclose_http_client", AsyncMock()) as aclose:
        with pytest.raises(RuntimeError, match="CRM недоступна"):
            run_celery_task(_fail())

    aclose.assert_awaited_once()                    # ресурсы освобождены и при сбое


# ── run_isolated ─────────────────────────────────────────────────────────────

def test_run_isolated_disposes_engine_after_each_run():
    """Пул соединений SQLAlchemy освобождается после каждого изолированного
    запуска (иначе следующий asyncio.run() взял бы соединение от закрытого loop).
    NullPool тестового режима скрывает проблему, поэтому проверяется сам факт
    dispose()."""
    async def _work():
        return "done"

    with patch("src.database.engine") as fake_engine:
        fake_engine.dispose = AsyncMock()
        assert run_isolated(_work()) == "done"
        assert run_isolated(_work()) == "done"

    assert fake_engine.dispose.await_count == 2


def test_run_isolated_disposes_engine_even_when_coroutine_raises():
    async def _fail():
        raise ValueError("boom")

    with patch("src.database.engine") as fake_engine:
        fake_engine.dispose = AsyncMock()
        with pytest.raises(ValueError, match="boom"):
            run_isolated(_fail())

    fake_engine.dispose.assert_awaited_once()


# ── синхронные обёртки задач ─────────────────────────────────────────────────

def test_process_outbox_row_wrapper_runs_async_body_with_row_id():
    from src.tasks.crm_outbox_tasks import process_outbox_row

    with patch("src.tasks.crm_outbox_tasks._process_outbox_row_async", AsyncMock()) as body:
        process_outbox_row.run(17)

    body.assert_awaited_once_with(17)


def test_reconcile_wrappers_run_their_async_bodies():
    from src.tasks.crm_outbox_tasks import reconcile_blocked_outbox, reconcile_pending_outbox

    with patch("src.tasks.crm_outbox_tasks._reconcile_pending_outbox_async", AsyncMock()) as pending, \
         patch("src.tasks.crm_outbox_tasks._reconcile_blocked_outbox_async", AsyncMock()) as blocked:
        reconcile_pending_outbox.run()
        reconcile_blocked_outbox.run()

    pending.assert_awaited_once()
    blocked.assert_awaited_once()


def test_sync_project_table_wrapper_runs_async_body():
    from src.tasks.global_lists_tasks import sync_project_table

    with patch("src.tasks.global_lists_tasks._sync_project_table_async", AsyncMock()) as body:
        sync_project_table.run()

    body.assert_awaited_once()


# ── реестр задач и расписание ────────────────────────────────────────────────

EXPECTED_TASKS = {
    "src.tasks.crm_outbox_tasks.process_outbox_row",
    "src.tasks.crm_outbox_tasks.reconcile_pending_outbox",
    "src.tasks.crm_outbox_tasks.reconcile_blocked_outbox",
    "src.tasks.global_lists_tasks.sync_project_table",
}


def test_all_project_tasks_are_registered():
    """Регрессия: autodiscover_tasks(["src.tasks"]) молча регистрировал ноль задач, и любой
    .delay()/тик Beat падал «Received unregistered task»."""
    assert EXPECTED_TASKS <= set(celery_app.tasks.keys())


def test_every_beat_schedule_entry_points_to_a_registered_task():
    schedule = celery_app.conf.beat_schedule
    assert schedule, "расписание Beat пусто"
    for name, entry in schedule.items():
        assert entry["task"] in celery_app.tasks, f"{name}: задача {entry['task']} не зарегистрирована"
        assert entry["schedule"] > 0


def test_reliability_and_monitoring_settings():
    conf = celery_app.conf
    assert conf.task_acks_late is True                  # ack после выполнения — задача не теряется при падении воркера
    assert conf.task_reject_on_worker_lost is True
    assert conf.broker_connection_retry_on_startup is True
    assert conf.worker_send_task_events is True         # без этого Flower не видит задач
    assert conf.task_send_sent_event is True
