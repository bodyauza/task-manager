"""Add subtask table

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-09

Вторая сущность верхнего уровня после task/person/role: подзадачи, дочерние
по отношению к задаче (One-to-Many task → subtask, cascade delete). Схема
колонок сознательно повторяет task (title/description/completed/crm_*_id) —
это тот же паттерн CRM-синхронизации, применённый ко второй CRM-сущности
(entity_id=30 в CRM «Руководитель», entity_id=29 — «Задачи»).
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
        # title: сразу VARCHAR(100) — в отличие от task.title, который стартовал
        # с VARCHAR(255) и был сужен отдельной миграцией (0004). У subtask с
        # самого начала известно ограничение CRM (100 символов), поэтому
        # промежуточного шага не потребовалось.
        sa.Column("title", sa.String(100), nullable=False),
        # description: server_default="" — в отличие от task.description (NOT
        # NULL без default), у подзадачи описание необязательно на уровне API
        # (SubtaskCreate.description имеет Pydantic-default ""), и это же
        # поведение продублировано на уровне БД для прямых INSERT в обход ORM.
        sa.Column("description", sa.String(2000), nullable=False, server_default=""),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default="false"),
        # task_id — обязательный FK на родительскую задачу. ondelete="CASCADE"
        # выполняет удаление строк subtask СИЛАМИ PostgreSQL при DELETE FROM task,
        # а не кодом приложения — src/services/tasks.py::delete_task полагается
        # именно на этот каскад (см. docs/task-manager-documentation.md, раздел
        # про delete_task и снимок subtask_ids до commit).
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("task.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # crm_subtask_id — тот же смысл, что и task.crm_task_id (0003):
        # NULL = подзадача не синхронизирована с CRM, нет server_default —
        # это постоянное легитимное состояние, а не временная заглушка.
        sa.Column("crm_subtask_id", sa.Integer(), nullable=True),
        # UNIQUE(title, task_id), а не глобальный UNIQUE(title): одинаковое
        # название подзадачи допустимо в разных задачах, запрещено только
        # повторное название внутри одной и той же задачи. В отличие от task,
        # где к такой же составной уникальности пришли только в 0009 (после
        # промежуточного глобального UNIQUE(title) из 0001) — subtask сразу
        # спроектирован с правильной составной уникальностью.
        sa.UniqueConstraint("title", "task_id", name="uq_subtask_title_task"),
    )
    # ix_subtask_task_id — ускоряет самый частый запрос над этой таблицей:
    # "все подзадачи данной задачи" (GET /subtasks/?task_id=...) и одновременно
    # обслуживает JOIN/поиск по FK, который иначе потребовал бы Seq Scan.
    op.create_index("ix_subtask_task_id", "subtask", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_subtask_task_id", table_name="subtask")
    op.drop_table("subtask")  # UniqueConstraint и FK падают вместе с таблицей
