"""Limit task.title to 100 chars; drop ix_task_description if exists

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-03

CRM ограничивает название задачи 100 символами, поэтому схема и валидация сужены до того же предела (422 при валидации, а не сбой синхронизации).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ix_task_description должен быть удалён в 0002; DROP INDEX IF EXISTS безопасен независимо от фактического состояния БД.
    op.execute("DROP INDEX IF EXISTS ix_task_description")

    # Сужение VARCHAR(255) → VARCHAR(100) безопасно только если данные укладываются в предел; на момент миграции длина проверена вручную.
    op.alter_column(
        "task", "title",
        type_=sa.String(100),
        existing_type=sa.String(255),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Расширение обратно до 255 безопасно всегда.
    op.alter_column(
        "task", "title",
        type_=sa.String(255),
        existing_type=sa.String(100),
        existing_nullable=False,
    )
    # Восстанавливаем индекс, который удалял upgrade(): downgrade симметричен объявленному действию, а не состоянию конкретной БД.
    op.create_index("ix_task_description", "task", ["description"])
