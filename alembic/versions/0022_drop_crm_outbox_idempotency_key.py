"""Drop crm_outbox.idempotency_key — unused dead column

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-30

Колонка из 0017 нигде не читалась (только получала UUID при INSERT). Повторное выполнение задачи после сбоя воркера
уже закрыто проверкой status='done' в начале _process_outbox_row_async и атомарностью транзакции.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("crm_outbox", "idempotency_key")


def downgrade() -> None:
    op.add_column(
        "crm_outbox",
        sa.Column(
            "idempotency_key", sa.String(length=36), nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
