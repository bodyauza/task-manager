"""Replace global uq_task_title with per-user uq_task_title_owner

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-09
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Глобальный UNIQUE(title) заменяется составным UNIQUE(title, owner_id).
    # Разные пользователи могут иметь задачи с одинаковым названием;
    # дубликаты запрещены только в рамках одного владельца.
    # IF EXISTS: constraint мог быть удалён вручную или отсутствовать в БД
    # (несмотря на то что migration 0001 его создаёт) — безопасный DROP.
    op.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS uq_task_title")
    op.create_unique_constraint("uq_task_title_owner", "task", ["title", "owner_id"])


def downgrade() -> None:
    op.drop_constraint("uq_task_title_owner", "task", type_="unique")
    op.create_unique_constraint("uq_task_title", "task", ["title"])
