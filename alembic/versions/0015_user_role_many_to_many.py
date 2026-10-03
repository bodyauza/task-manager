"""Migrate person.role_id (one-to-many) to user_role many-to-many; drop role.permissions

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-03

Поддержка нескольких ролей на пользователя: таблица user_role(person_id, role_id) с составным PK (он же гарантирует уникальность пары).
role.permissions удаляется: require_permission() заменена на require_role() (по role.name).

Внимание (downgrade): откат лоссовый.
1. person.role_id восстанавливается как MIN(role_id): при 2+ ролях остаётся одна (с наименьшим id).
2. role.permissions возвращается пустой колонкой — прежние значения не сохраняются.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_role",
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("person.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id",   sa.Integer(), sa.ForeignKey("role.id",   ondelete="CASCADE"), primary_key=True),
    )

    # Backfill до удаления person.role_id, иначе назначения ролей потерялись бы безвозвратно.
    op.execute(
        "INSERT INTO user_role (person_id, role_id) "
        "SELECT id, role_id FROM person WHERE role_id IS NOT NULL"
    )

    # Имя ограничения — то, что PostgreSQL присвоил в 0001 (inline FK без name=): person_role_id_fkey.
    op.drop_constraint("person_role_id_fkey", "person", type_="foreignkey")
    op.drop_column("person", "role_id")

    op.drop_column("role", "permissions")


def downgrade() -> None:
    op.add_column("role", sa.Column("permissions", sa.JSON(), nullable=True))

    op.add_column("person", sa.Column("role_id", sa.Integer(), nullable=True))
    op.create_foreign_key("person_role_id_fkey", "person", "role", ["role_id"], ["id"])

    # MIN(role_id): лоссовое сведение many-to-many к одной роли на пользователя (см. докстринг).
    op.execute(
        "UPDATE person SET role_id = ("
        "  SELECT MIN(user_role.role_id) FROM user_role WHERE user_role.person_id = person.id"
        ")"
    )

    op.drop_table("user_role")
