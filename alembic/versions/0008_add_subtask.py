"""Add subtask table

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subtask",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False, server_default=""),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("task.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("crm_subtask_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("title", "task_id", name="uq_subtask_title_task"),
    )
    op.create_index("ix_subtask_task_id", "subtask", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_subtask_task_id", table_name="subtask")
    op.drop_table("subtask")
