"""Add ON DELETE CASCADE to task.owner_id -> person.id

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-30

Удаление пользователя работало только через ORM; прямой DELETE FROM person падал, если у пользователя были задачи.
Каскад на уровне БД закрывает этот путь и вместе с passive_deletes=True на User.tasks убирает лишний SELECT и N×DELETE.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("task_owner_id_fkey", "task", type_="foreignkey")
    op.create_foreign_key(
        "task_owner_id_fkey", "task", "person",
        ["owner_id"], ["id"], ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("task_owner_id_fkey", "task", type_="foreignkey")
    op.create_foreign_key(
        "task_owner_id_fkey", "task", "person", ["owner_id"], ["id"],
    )
