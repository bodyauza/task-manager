"""Convert other_file_paths from Text to JSONB

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-11

Текстовая колонка требовала ручного json.loads()/dumps() и не проверяла валидность JSON. JSONB asyncpg десериализует в list автоматически.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Text → JSONB через USING text::jsonb; NULL остаётся NULL.
    op.alter_column(
        "task",
        "other_file_paths",
        type_=JSONB,
        postgresql_using="other_file_paths::jsonb",
        existing_nullable=True,
    )
    op.alter_column(
        "subtask",
        "other_file_paths",
        type_=JSONB,
        postgresql_using="other_file_paths::jsonb",
        existing_nullable=True,
    )


def downgrade() -> None:
    # Обратное приведение JSONB → Text.
    op.alter_column(
        "task",
        "other_file_paths",
        type_=sa.Text(),
        postgresql_using="other_file_paths::text",
        existing_nullable=True,
    )
    op.alter_column(
        "subtask",
        "other_file_paths",
        type_=sa.Text(),
        postgresql_using="other_file_paths::text",
        existing_nullable=True,
    )
