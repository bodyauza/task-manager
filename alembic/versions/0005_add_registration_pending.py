"""Add registration_pending table

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-03

Таблица трёхшаговой регистрации: запись живёт между «код отправлен» и «код подтверждён/истёк», одна на email (UniqueConstraint).
Примечание (см. 0012): на email создаются и UniqueConstraint, и отдельный неуникальный индекс — избыточно и не совпадает с ORM-моделью.
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
        # email NOT NULL; уникальность — отдельным UniqueConstraint (итоговая схема после 0012 другая).
        sa.Column("email", sa.String(255), nullable=False),
        # code_hash — bcrypt-хеш 6-значного кода, а не сам код; длина 1024 как у person.hashed_password.
        sa.Column("code_hash", sa.String(1024), nullable=False),
        # attempts — число неверных вводов кода; при лимите запись блокируется.
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        # expires_at — момент, после которого код недействителен; TIMESTAMP без пояса, исправляется в 0007.
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
        # Именованный UniqueConstraint; unique=True на колонке дал бы то же через уникальный индекс.
        sa.UniqueConstraint("email", name="uq_registration_pending_email"),
    )
    # Отдельный неуникальный индекс на email избыточен поверх constraint (см. 0012); сохранён для соответствия исторической ревизии.
    op.create_index("ix_registration_pending_email", "registration_pending", ["email"])


def downgrade() -> None:
    op.drop_index("ix_registration_pending_email", table_name="registration_pending")
    op.drop_table("registration_pending")
