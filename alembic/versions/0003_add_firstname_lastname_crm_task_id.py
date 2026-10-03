"""Add firstname, lastname to person; add crm_task_id to task

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-01

Первая ревизия интеграции с CRM: имя/фамилия пользователя и локальное хранение CRM-ID задачи.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # firstname/lastname NOT NULL на таблице с данными: server_default="" заполняет существующие строки на уровне БД;
    # содержательно пустые значения старых пользователей заполняются вручную.
    op.add_column(
        "person",
        sa.Column("firstname", sa.String(255), nullable=False, server_default=""),
    )
    op.add_column(
        "person",
        sa.Column("lastname", sa.String(255), nullable=False, server_default=""),
    )
    # crm_task_id nullable без server_default: NULL — постоянное состояние «не синхронизировано с CRM».
    op.add_column(
        "task",
        sa.Column("crm_task_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    # Порядок обратный upgrade().
    op.drop_column("task", "crm_task_id")
    op.drop_column("person", "lastname")
    op.drop_column("person", "firstname")
