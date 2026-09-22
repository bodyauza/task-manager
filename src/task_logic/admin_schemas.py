"""Схемы admin-only ответа о статусе CRM-синхронизации задач/подзадач.

Намеренно отдельный файл, не task_schemas.py/subtask_schemas.py: эти поля
не должны быть видны обычному клиенту (см. TaskResponse/SubtaskResponse —
там их больше нет) — только администратору через
src/routers/pages.py::admin_tasks_sync_status/admin_subtasks_sync_status.
"""

import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class TaskSyncStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    owner_id: int
    owner_email: str
    crm_task_id: Optional[int] = None
    # 'unsynced' | 'pending' | 'synced' | 'failed' — Task.sync_status,
    # обновляется Celery-обработчиком (src/tasks/crm_outbox_tasks.py).
    sync_status: str
    # Снимок ПОСЛЕДНЕЙ строки crm_outbox для этого агрегата (по updated_at) —
    # None во всех четырёх полях ниже, если синхронизация ни разу не запускалась.
    last_operation: Optional[str] = None
    last_outbox_status: Optional[str] = None
    last_attempts: Optional[int] = None
    last_updated_at: Optional[datetime.datetime] = None


class SubtaskSyncStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    task_id: int
    task_title: str
    crm_subtask_id: Optional[int] = None
    sync_status: str
    last_operation: Optional[str] = None
    last_outbox_status: Optional[str] = None
    last_attempts: Optional[int] = None
    last_updated_at: Optional[datetime.datetime] = None
