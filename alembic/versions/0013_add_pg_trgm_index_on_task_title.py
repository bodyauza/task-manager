"""Add pg_trgm GIN index on task.title for ILIKE substring search

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-17

search_tasks() ищет через Task.title.ilike(f"%{title}%"): паттерн с ведущим "%" не использует B-tree и даёт Seq Scan.
GIN-индекс по триграммам (pg_trgm) ускоряет ILIKE с шаблоном в любом месте строки.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pg_trgm — trusted-расширение с PostgreSQL 13: ставится пользователем с правом CREATE на базу, без суперпользователя.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_task_title_trgm",
        "task",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_task_title_trgm", table_name="task")
    # Расширение не удаляем: им могут пользоваться объекты вне Alembic, а DROP EXTENSION был бы разрушительным побочным эффектом отката.
