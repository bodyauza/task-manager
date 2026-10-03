"""Add project reference table (Task.project_id FK) and crm_outbox durable retry table

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-10

1. `project` — локальное зеркало глобального списка «Проект» CRM; наполняется Celery-задачей sync_project_table.
   task.project_id — nullable FK (поле новое и опциональное, backfill не нужен).
2. `crm_outbox` — durable retry для CRM-вызовов после основного commit; таблица новая, backfill не нужен.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("crm_id", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("synced_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("crm_id", name="uq_project_crm_id"),
    )
    op.add_column("task", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_task_project_id", "task", "project", ["project_id"], ["id"])

    op.create_table(
        "crm_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_crm_outbox_task_id", "crm_outbox", ["task_id"])
    # Индекс под запрос reconcile_pending_outbox (WHERE status='pending' ORDER BY created_at): без него полный скан при росте таблицы.
    op.create_index("ix_crm_outbox_status_created_at", "crm_outbox", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_crm_outbox_status_created_at", table_name="crm_outbox")
    op.drop_index("ix_crm_outbox_task_id", table_name="crm_outbox")
    op.drop_table("crm_outbox")

    op.drop_constraint("fk_task_project_id", "task", type_="foreignkey")
    op.drop_column("task", "project_id")
    op.drop_table("project")
