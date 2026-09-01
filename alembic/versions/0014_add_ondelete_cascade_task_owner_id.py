"""Add ON DELETE CASCADE to task.owner_id -> person.id

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-30

До этой миграции task.owner_id -> person.id не имел ondelete: удаление
пользователя работало только через ORM (session.delete(user) запускает
cascade="all, delete-orphan" на User.tasks), а прямой SQL
DELETE FROM person WHERE id=... падал с IntegrityError, если у пользователя
оставались задачи. ON DELETE CASCADE на уровне БД закрывает этот путь и,
в паре с passive_deletes=True на User.tasks (src/auth/user_models.py), убирает
лишний SELECT+N×DELETE, которые ORM иначе выполняет в Python при удалении
пользователя через session.delete().
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
