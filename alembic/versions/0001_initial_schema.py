"""Initial schema: role, person, task tables

Revision ID: 0001
Revises:
Create Date: 2026-06-18

Первая ревизия. Начальные данные (роли "user"/"admin") не входят: их вставляет create_initial_roles() при старте.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# down_revision=None — первая ревизия цепочки.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # role — справочник ролей; строки вставляются не здесь.
    op.create_table(
        "role",
        sa.Column("id", sa.Integer(), primary_key=True),
        # name не UNIQUE и не индексирован: справочник из нескольких строк.
        sa.Column("name", sa.String(), nullable=False),
        # permissions — JSON-массив строк; nullable.
        sa.Column("permissions", sa.JSON(), nullable=True),
    )

    # Таблица названа "person": "user" — зарезервированное слово PostgreSQL. Реальное имя задаётся через __tablename__.
    op.create_table(
        "person",
        sa.Column("id", sa.Integer(), primary_key=True),
        # email без UNIQUE на этом этапе: ограничение добавляется в 0002 (uq_person_email), фактически — в 0012.
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        # registered_at: nullable; в 0007 переводится на TIMESTAMPTZ.
        sa.Column("registered_at", sa.TIMESTAMP(), nullable=True),
        # role_id: nullable; UserManager.create() проставляет его явно.
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("role.id"), nullable=True),
        # hashed_password: длина 1024 с запасом на смену алгоритма хеширования.
        sa.Column("hashed_password", sa.String(length=1024), nullable=False),
        # is_active/is_superuser/is_verified — поля FastAPI Users; server_default действует и для прямых INSERT в обход ORM.
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # ix_person_id дублирует индекс PRIMARY KEY (index=True на primary_key=True).
    op.create_index("ix_person_id", "person", ["id"])

    # task: owner_id связывает задачу с создателем (person).
    op.create_table(
        "task",
        sa.Column("id", sa.Integer(), primary_key=True),
        # title: VARCHAR(255); сужается до 100 в 0004 (лимит CRM).
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        # owner_id NOT NULL; ON DELETE не задан → RESTRICT.
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("person.id"), nullable=False),
    )
    op.create_index("ix_task_id", "task", ["id"])
    # ix_task_title; в 0002 добавляется UNIQUE, в 0009 — составной ключ.
    op.create_index("ix_task_title", "task", ["title"])
    # ix_task_description избыточен (description не участвует в WHERE/ORDER BY); удаляется в 0002.
    op.create_index("ix_task_description", "task", ["description"])
    # uq_task_title — глобальная уникальность title; слишком строго, заменяется на UNIQUE(title, owner_id) в 0009.
    op.create_unique_constraint("uq_task_title", "task", ["title"])


def downgrade() -> None:
    # Порядок обратный upgrade(): сначала таблицы с FK (task → person → role).
    op.drop_table("task")
    op.drop_table("person")
    op.drop_table("role")
