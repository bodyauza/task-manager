"""Partial index on crm_outbox.depends_on_event_id

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-29

self-FK depends_on_event_id был без индекса: каждый DELETE в cleanup_done_outbox проверял ссылки полным сканированием,
а NOT EXISTS по той же колонке — ещё и в самой выборке. Partial-индекс (WHERE IS NOT NULL): у большинства строк зависимости нет.

Объявлен и в ORM-модели (CrmOutbox.__table_args__): тесты создают схему через create_all, минуя Alembic.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_crm_outbox_depends_on_event_id", "crm_outbox", ["depends_on_event_id"],
        unique=False, postgresql_where=sa.text("depends_on_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_crm_outbox_depends_on_event_id", table_name="crm_outbox")
