"""Outbox: aggregate_type/depends_on_event_id/idempotency_key + Task/Subtask sync_status/crm_shard

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-10

Схема для шардирования outbox-очереди (id % N): распространяет outbox (0016) на Subtask и операцию create.

1. crm_outbox.task_id → aggregate_id + aggregate_type ('task' | 'subtask', server_default='task': старые строки относятся к Task)
   + shard (снимок Task.crm_shard на момент вставки).
2. crm_outbox.depends_on_event_id — self-FK для межагрегатной и внутриагрегатной зависимостей.
3. crm_outbox.idempotency_key — server_default gen_random_uuid() (встроен с PostgreSQL 13).
4. task.crm_shard (nullable, присваивается лениво), task.sync_status/subtask.sync_status (server_default='unsynced')
   вместо вычисления crm_synced на лету.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_crm_outbox_task_id", table_name="crm_outbox")
    op.alter_column("crm_outbox", "task_id", new_column_name="aggregate_id")
    op.create_index("ix_crm_outbox_aggregate_id", "crm_outbox", ["aggregate_id"])

    op.add_column(
        "crm_outbox",
        sa.Column("aggregate_type", sa.String(length=20), nullable=False, server_default="task"),
    )
    op.add_column(
        "crm_outbox",
        sa.Column("shard", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "crm_outbox",
        sa.Column("depends_on_event_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_crm_outbox_depends_on_event_id", "crm_outbox", "crm_outbox",
        ["depends_on_event_id"], ["id"],
    )
    op.add_column(
        "crm_outbox",
        sa.Column(
            "idempotency_key", sa.String(length=36), nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )

    op.add_column("task", sa.Column("crm_shard", sa.String(length=20), nullable=True))
    op.add_column(
        "task",
        sa.Column("sync_status", sa.String(length=20), nullable=False, server_default="unsynced"),
    )
    op.add_column(
        "subtask",
        sa.Column("sync_status", sa.String(length=20), nullable=False, server_default="unsynced"),
    )


def downgrade() -> None:
    op.drop_column("subtask", "sync_status")
    op.drop_column("task", "sync_status")
    op.drop_column("task", "crm_shard")

    op.drop_column("crm_outbox", "idempotency_key")
    op.drop_constraint("fk_crm_outbox_depends_on_event_id", "crm_outbox", type_="foreignkey")
    op.drop_column("crm_outbox", "depends_on_event_id")
    op.drop_column("crm_outbox", "shard")
    op.drop_column("crm_outbox", "aggregate_type")

    op.drop_index("ix_crm_outbox_aggregate_id", table_name="crm_outbox")
    op.alter_column("crm_outbox", "aggregate_id", new_column_name="task_id")
    op.create_index("ix_crm_outbox_task_id", "crm_outbox", ["task_id"])
