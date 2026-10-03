"""Add patronymic column to person

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-04

Отчество — необязательное поле анкеты (шаг 3 регистрации), поэтому колонка nullable.
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # nullable=True без server_default: существующие строки получают NULL, NULL — легитимное состояние «не указано».
    op.add_column(
        'person',
        sa.Column('patronymic', sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('person', 'patronymic')
