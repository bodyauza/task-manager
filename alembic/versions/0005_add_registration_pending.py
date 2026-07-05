"""Add registration_pending table

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "registration_pending",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("code_hash", sa.String(1024), nullable=False),
        # attempts — сколько раз пользователь ввёл неверный код.
        # При достижении лимита запись удаляется: повторная отправка кода обязательна.
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
        sa.UniqueConstraint("email", name="uq_registration_pending_email"),
    )
    op.create_index("ix_registration_pending_email", "registration_pending", ["email"])


def downgrade() -> None:
    op.drop_index("ix_registration_pending_email", table_name="registration_pending")
    op.drop_table("registration_pending")
