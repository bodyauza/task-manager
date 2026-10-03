"""Запросы к crm_outbox, общие для сервисов задач, подзадач и вложений."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.task_logic.models import CrmOutbox


async def pending_create_event_id(
    db: AsyncSession, aggregate_type: str, aggregate_id: int,
) -> Optional[int]:
    """id ещё не завершённого 'create'-события агрегата; None, если его нет или оно done.

    Зависимые события (depends_on_event_id) не обгоняют create, пока у сущности нет CRM-id.
    """
    return (
        await db.execute(
            select(CrmOutbox.id).where(
                CrmOutbox.aggregate_type == aggregate_type,
                CrmOutbox.aggregate_id == aggregate_id,
                CrmOutbox.operation == "create",
                CrmOutbox.status != "done",
            ).order_by(CrmOutbox.id).limit(1)
        )
    ).scalar_one_or_none()
