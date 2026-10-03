"""Схемы admin-only ответа о статусе CRM-синхронизации; отдельный файл, чтобы поля не попали в публичные TaskResponse/SubtaskResponse."""

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
    # Task.sync_status: 'unsynced' | 'pending' | 'synced' | 'failed'.
    sync_status: str
    # Снимок последней строки crm_outbox агрегата (по updated_at); None, если синхронизация не запускалась.
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
