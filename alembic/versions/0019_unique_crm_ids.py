"""Partial unique index on task.crm_task_id / subtask.crm_subtask_id

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-27

Вторая линия защиты от «усыновления» чужой CRM-записи: два локальных агрегата не могут делить один crm_id.
WHERE ... IS NOT NULL — индексируются только строки с присвоенным crm_id.
Backfill не нужен: существующая коллизия оборвёт CREATE UNIQUE INDEX и станет видимой.

Индекс объявлен и в ORM-модели (Task/Subtask.__table_args__): тесты создают схему через create_all, минуя Alembic.
Обе декларации должны совпадать; для реальных БД источник истины — эта миграция.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_task_crm_task_id_unique", "task", ["crm_task_id"],
        unique=True, postgresql_where=sa.text("crm_task_id IS NOT NULL"),
    )
    op.create_index(
        "ix_subtask_crm_subtask_id_unique", "subtask", ["crm_subtask_id"],
        unique=True, postgresql_where=sa.text("crm_subtask_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_subtask_crm_subtask_id_unique", table_name="subtask")
    op.drop_index("ix_task_crm_task_id_unique", table_name="task")
