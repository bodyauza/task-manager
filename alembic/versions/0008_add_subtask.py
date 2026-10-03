"""Add subtask table

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-09

Подзадачи (task → subtask, cascade delete). Колонки повторяют task: тот же паттерн CRM-синхронизации для второй сущности.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subtask",
        sa.Column("id", sa.Integer(), primary_key=True),
        # title сразу VARCHAR(100) — лимит CRM известен с начала.
        sa.Column("title", sa.String(100), nullable=False),
        # description: server_default="" — описание необязательно в API (SubtaskCreate), и то же поведение на уровне БД для прямых INSERT.
        sa.Column("description", sa.String(2000), nullable=False, server_default=""),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default="false"),
        # task_id — FK с ondelete="CASCADE": подзадачи удаляет PostgreSQL при DELETE FROM task (на это опирается delete_task).
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("task.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # crm_subtask_id — как task.crm_task_id (0003): NULL — «не синхронизировано», server_default не нужен.
        sa.Column("crm_subtask_id", sa.Integer(), nullable=True),
        # UNIQUE(title, task_id): одинаковое название допустимо в разных задачах, но не в одной.
        sa.UniqueConstraint("title", "task_id", name="uq_subtask_title_task"),
    )
    # ix_subtask_task_id ускоряет «все подзадачи задачи» и JOIN по FK.
    op.create_index("ix_subtask_task_id", "subtask", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_subtask_task_id", table_name="subtask")
    op.drop_table("subtask")
