"""Add unique constraint on person.email; drop redundant task.description index

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-18

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint("uq_person_email", "person", ["email"])
    op.drop_index("ix_task_description", table_name="task")


def downgrade() -> None:
    op.create_index("ix_task_description", "task", ["description"])
    op.drop_constraint("uq_person_email", "person", type_="unique")
