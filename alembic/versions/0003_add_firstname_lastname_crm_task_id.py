"""Add firstname, lastname to person; add crm_task_id to task

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default='' позволяет применить NOT NULL к существующим строкам;
    # после миграции администратор заполняет данные вручную или через скрипт.
    op.add_column(
        "person",
        sa.Column("firstname", sa.String(255), nullable=False, server_default=""),
    )
    op.add_column(
        "person",
        sa.Column("lastname", sa.String(255), nullable=False, server_default=""),
    )
    # crm_task_id — nullable: существующие задачи не имеют CRM-записи.
    op.add_column(
        "task",
        sa.Column("crm_task_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("task", "crm_task_id")
    op.drop_column("person", "lastname")
    op.drop_column("person", "firstname")
