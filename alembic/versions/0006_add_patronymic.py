"""Add patronymic column to person

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-04

Отчество — необязательное поле анкеты на шаге 3 регистрации
(complete-registration.html): не у всех пользователей оно есть или
традиционно указывается, поэтому колонка nullable, в отличие от
firstname/lastname (0003), которые обязательны.
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # nullable=True, БЕЗ server_default: в отличие от firstname/lastname в 0003
    # (которым понадобился server_default="", чтобы обойти NOT NULL на непустой
    # таблице), patronymic изначально nullable — существующие строки получают
    # NULL автоматически, никакого временного значения-заглушки не требуется.
    # NULL здесь — не «данные для заполнения администратором», а постоянное
    # легитимное состояние «отчество не указано».
    op.add_column(
        'person',
        sa.Column('patronymic', sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('person', 'patronymic')
