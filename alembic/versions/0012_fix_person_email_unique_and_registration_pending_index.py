"""Add missing uq_person_email; unify registration_pending.email to one unique index

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-15

Идемпотентна: PostgreSQL не поддерживает ADD CONSTRAINT IF NOT EXISTS, а у разных экземпляров БД фактическое состояние расходится
(в одной uq_person_email отсутствовал, в другой — уже был), поэтому миграция сама проверяет pg_constraint/pg_indexes
и выполняет только недостающие операции.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _constraint_exists(conn, table: str, name: str) -> bool:
    """True, если ограничение с этим именем уже есть на таблице.

    CAST(:table AS regclass), а не ":table::regclass": SQLAlchemy text() не подставляет bind-параметр, когда ":" и "::" стоят вплотную.
    """
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = :name AND conrelid = CAST(:table AS regclass)"
        ),
        {"name": name, "table": table},
    ).scalar())


def _index_state(conn, name: str) -> "str | None":
    """'unique' / 'non_unique', если индекс с этим именем существует, иначе None."""
    row = conn.execute(
        sa.text(
            "SELECT ix.indisunique FROM pg_index ix "
            "JOIN pg_class i ON i.oid = ix.indexrelid "
            "WHERE i.relname = :name"
        ),
        {"name": name},
    ).first()
    if row is None:
        return None
    return "unique" if row[0] else "non_unique"


def upgrade() -> None:
    conn = op.get_bind()

    # person.email: 0002 должна была создать uq_person_email, но в реальной БД его не было — уникальность держалась только SELECT-проверкой
    # приложения (гонка двух регистраций). Перед созданием проверяем наличие: на других БД ограничение уже может быть.
    if not _constraint_exists(conn, "person", "uq_person_email"):
        op.create_unique_constraint("uq_person_email", "person", ["email"])

    # registration_pending.email: 0005 создала UniqueConstraint и отдельный неуникальный индекс — лишний оверхед. Модель (unique=True, index=True)
    # ожидает один уникальный индекс ix_registration_pending_email: дропаем constraint (если есть) и приводим индекс к уникальному.
    if _constraint_exists(conn, "registration_pending", "uq_registration_pending_email"):
        op.drop_constraint(
            "uq_registration_pending_email", "registration_pending", type_="unique"
        )

    index_state = _index_state(conn, "ix_registration_pending_email")
    if index_state == "non_unique":
        op.drop_index("ix_registration_pending_email", table_name="registration_pending")
        op.create_index(
            "ix_registration_pending_email", "registration_pending", ["email"], unique=True
        )
    elif index_state is None:
        op.create_index(
            "ix_registration_pending_email", "registration_pending", ["email"], unique=True
        )
    # Индекс уже уникальный — ничего делать не нужно.


def downgrade() -> None:
    op.drop_index("ix_registration_pending_email", table_name="registration_pending")
    op.create_index("ix_registration_pending_email", "registration_pending", ["email"])
    op.create_unique_constraint(
        "uq_registration_pending_email", "registration_pending", ["email"]
    )
    op.drop_constraint("uq_person_email", "person", type_="unique")
