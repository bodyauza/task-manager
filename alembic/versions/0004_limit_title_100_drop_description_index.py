"""Limit task.title to 100 chars; drop ix_task_description if exists

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ix_task_description создан в 0001 и должен быть удалён в 0002.
    # IF EXISTS — защита от повторного применения при нестандартной истории миграций.
    op.execute("DROP INDEX IF EXISTS ix_task_description")

    # Сужение VARCHAR(255) → VARCHAR(100): значения длиннее 100 символов в БД отсутствуют
    # (Pydantic-валидация с предыдущей версией допускала до 255, но CRM принимает до 100).
    op.alter_column(
        "task", "title",
        type_=sa.String(100),
        existing_type=sa.String(255),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "task", "title",
        type_=sa.String(255),
        existing_type=sa.String(100),
        existing_nullable=False,
    )
    op.create_index("ix_task_description", "task", ["description"])
