"""Fix TIMESTAMP → TIMESTAMPTZ; person.username TEXT → VARCHAR(255)

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # TIMESTAMP → TIMESTAMP WITH TIME ZONE.
    # Все хранимые значения записаны как UTC (datetime.now(timezone.utc)),
    # поэтому интерпретируем их как UTC при конвертации через USING.
    op.alter_column(
        "person", "registered_at",
        type_=sa.TIMESTAMP(timezone=True),
        existing_type=sa.TIMESTAMP(timezone=False),
        postgresql_using="registered_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "registration_pending", "expires_at",
        type_=sa.TIMESTAMP(timezone=True),
        existing_type=sa.TIMESTAMP(timezone=False),
        postgresql_using="expires_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "registration_pending", "created_at",
        type_=sa.TIMESTAMP(timezone=True),
        existing_type=sa.TIMESTAMP(timezone=False),
        postgresql_using="created_at AT TIME ZONE 'UTC'",
    )
    # TEXT → VARCHAR(255): implicit cast, USING не требуется.
    op.alter_column(
        "person", "username",
        type_=sa.String(255),
        existing_type=sa.String(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "person", "username",
        type_=sa.String(),
        existing_type=sa.String(255),
        existing_nullable=False,
    )
    op.alter_column(
        "registration_pending", "created_at",
        type_=sa.TIMESTAMP(timezone=False),
        existing_type=sa.TIMESTAMP(timezone=True),
        postgresql_using="created_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "registration_pending", "expires_at",
        type_=sa.TIMESTAMP(timezone=False),
        existing_type=sa.TIMESTAMP(timezone=True),
        postgresql_using="expires_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "person", "registered_at",
        type_=sa.TIMESTAMP(timezone=False),
        existing_type=sa.TIMESTAMP(timezone=True),
        postgresql_using="registered_at AT TIME ZONE 'UTC'",
    )
