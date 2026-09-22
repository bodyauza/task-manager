"""crm_outbox.last_error — причина последнего сбоя CRM-события

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-20

Nullable Text: заполняется обработчиком при неудачной попытке
(src/tasks/crm_outbox_tasks.py::_process_outbox_row_async), очищается при
успехе. Backfill не нужен — у существующих строк причина сбоя неизвестна (NULL).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("crm_outbox", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("crm_outbox", "last_error")
