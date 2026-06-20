"""Initial schema: role, person, task tables

Revision ID: 0001
Revises:
Create Date: 2026-06-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "role",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=True),
    )

    op.create_table(
        "person",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("registered_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("role.id"), nullable=True),
        sa.Column("hashed_password", sa.String(length=1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_person_id", "person", ["id"])

    op.create_table(
        "task",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("person.id"), nullable=False),
    )
    op.create_index("ix_task_id", "task", ["id"])
    op.create_index("ix_task_title", "task", ["title"])
    op.create_index("ix_task_description", "task", ["description"])
    op.create_unique_constraint("uq_task_title", "task", ["title"])


def downgrade() -> None:
    op.drop_table("task")
    op.drop_table("person")
    op.drop_table("role")
