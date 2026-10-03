"""Replace global uq_task_title with per-user uq_task_title_owner

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-09

Глобальное UNIQUE(title) заменяется составным UNIQUE(title, owner_id) — как уже сделано у subtask в 0008.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Разные пользователи могут иметь задачи с одним названием; дубли запрещены только внутри одного владельца.
    # DROP CONSTRAINT IF EXISTS через op.execute: Operations API не имеет «drop constraint if exists».
    op.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS uq_task_title")
    # Составной индекс обслуживает предварительный SELECT в create_task и защищает от гонки: второй commit падает с IntegrityError.
    op.create_unique_constraint("uq_task_title_owner", "task", ["title", "owner_id"])


def downgrade() -> None:
    # downgrade упадёт, если у разных пользователей уже есть задачи с одинаковым title: сужение ограничения требует ручной чистки данных.
    op.drop_constraint("uq_task_title_owner", "task", type_="unique")
    op.create_unique_constraint("uq_task_title", "task", ["title"])
