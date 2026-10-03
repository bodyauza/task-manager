"""Add crm_outbox.dispatched_at — cooldown for reconcile_pending_outbox re-dispatch

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-29

reconcile_pending_outbox каждую минуту переставлял в очередь любую pending-строку с attempts == 0, в том числе не двигающуюся
(лимит CRM, более старое событие, зависимость не done): создавал постоянный трафик Celery/Redis без пользы.
dispatched_at фиксирует момент последней постановки в очередь; NULL — реконсайл ещё не ставил строку.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "crm_outbox",
        sa.Column("dispatched_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("crm_outbox", "dispatched_at")
