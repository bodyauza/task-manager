"""Add unique constraint on person.email; drop redundant task.description index

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-18

Две правки схемы: уникальность email (в 0001 её не было) и удаление неиспользуемого индекса task.description.
Важно (см. 0012): в реальной БД uq_person_email мог отсутствовать, хотя ревизия отмечена применённой.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # UNIQUE на person.email: без него два одновременных запроса регистрации теоретически могли вставить один email дважды.
    op.create_unique_constraint("uq_person_email", "person", ["email"])
    # Индекс из 0001, не ускоряющий ни один запрос.
    op.drop_index("ix_task_description", table_name="task")


def downgrade() -> None:
    # Порядок обратный upgrade().
    op.create_index("ix_task_description", "task", ["description"])
    op.drop_constraint("uq_person_email", "person", type_="unique")
