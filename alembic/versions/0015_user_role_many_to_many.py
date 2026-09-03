"""Migrate person.role_id (one-to-many) to user_role many-to-many; drop role.permissions

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-03

До этой миграции person.role_id был обычным FK — один пользователь = ровно одна
роль. Задача: поддержать несколько ролей на одного пользователя. Новая
таблица-связка user_role(person_id, role_id) с составным первичным ключом
одновременно даёт уникальность пары — назначить одну и ту же роль пользователю
дважды физически нельзя, отдельный UniqueConstraint не нужен.

role.permissions удаляется в этой же миграции: require_permission() (проверка
по списку permissions) заменена на require_role() (проверка по role.name, см.
src/auth/auth_config.py) — колонка стала неиспользуемыми данными, решено не
оставлять мёртвую схему.

ВНИМАНИЕ (downgrade): откат лоссовый в двух независимых местах.
1. person.role_id восстанавливается как MIN(role_id) по каждому пользователю —
   если у пользователя стало 2+ роли уже после этой миграции, откат произвольно
   оставит только одну (с наименьшим id), фактические права пользователя после
   отката изменятся относительно того, что было до отката.
2. role.permissions восстанавливается как пустая колонка (nullable, без данных) —
   значения, которые были в ней ДО апгрейда, нигде не сохраняются и не могут
   быть восстановлены этим downgrade.
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

    # Backfill ДО удаления person.role_id — иначе существующие назначения ролей
    # были бы потеряны безвозвратно, а не просто лоссово свёрнуты при откате.
    op.execute(
        "INSERT INTO user_role (person_id, role_id) "
        "SELECT id, role_id FROM person WHERE role_id IS NOT NULL"
    )

    # Имя ограничения — то, что Postgres присвоил ему сам при создании в 0001
    # (inline sa.ForeignKey() внутри sa.Column(), без явного name=): проверено
    # эмпирически (SELECT conname FROM pg_constraint WHERE conrelid='person'::regclass),
    # действующее имя — person_role_id_fkey.
    op.drop_constraint("person_role_id_fkey", "person", type_="foreignkey")
    op.drop_column("person", "role_id")

    op.drop_column("role", "permissions")


def downgrade() -> None:
    op.add_column("role", sa.Column("permissions", sa.JSON(), nullable=True))

    op.add_column("person", sa.Column("role_id", sa.Integer(), nullable=True))
    op.create_foreign_key("person_role_id_fkey", "person", "role", ["role_id"], ["id"])

    # MIN(role_id): лоссовое сведение many-to-many к одной роли на пользователя —
    # см. докстринг выше. Пользователи с единственной ролью восстанавливаются точно;
    # пользователи с несколькими ролями теряют все, кроме роли с наименьшим id.
    op.execute(
        "UPDATE person SET role_id = ("
        "  SELECT MIN(user_role.role_id) FROM user_role WHERE user_role.person_id = person.id"
        ")"
    )

    op.drop_table("user_role")
