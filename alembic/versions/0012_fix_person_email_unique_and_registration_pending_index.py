"""Add missing uq_person_email; unify registration_pending.email to one unique index

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # person.email: миграция 0002 должна была создать uq_person_email, но в реальной БД
    # этого ограничения нет (обнаружено через `alembic check` + прямой запрос к pg_constraint —
    # 0 строк с contype='u' для person). До этой миграции уникальность email обеспечивалась
    # только на уровне приложения (SELECT-проверка перед INSERT в registration_endpoints.py) —
    # без БД-ограничения это check-then-insert без защиты от гонки: два одновременных
    # POST /auth/register/complete с одним email оба могли пройти проверку и оба вставиться.
    op.create_unique_constraint("uq_person_email", "person", ["email"])

    # registration_pending.email: миграция 0005 создала ДВА объекта на одну колонку —
    # UniqueConstraint("uq_registration_pending_email") и отдельный обычный (НЕуникальный)
    # op.create_index("ix_registration_pending_email", ...) — чистый оверхед, обновляется
    # на каждый INSERT/UPDATE/DELETE без выигрыша в SELECT (уникальный constraint уже
    # реализован PostgreSQL как unique index и полностью покрывает те же запросы).
    #
    # Модель (RegistrationPending.email с unique=True, index=True вместе) ожидает не
    # "constraint + отдельный индекс", а ОДИН объект — уникальный индекс с именем
    # ix_registration_pending_email (стандартное имя для index=True). Поэтому вместо
    # удаления одного из двух старых объектов заменяем оба на то, что реально хочет модель:
    # дропаем constraint, дропаем старый неуникальный индекс, создаём один unique index
    # с ожидаемым именем.
    op.drop_constraint("uq_registration_pending_email", "registration_pending", type_="unique")
    op.drop_index("ix_registration_pending_email", table_name="registration_pending")
    op.create_index(
        "ix_registration_pending_email", "registration_pending", ["email"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_registration_pending_email", table_name="registration_pending")
    op.create_index("ix_registration_pending_email", "registration_pending", ["email"])
    op.create_unique_constraint(
        "uq_registration_pending_email", "registration_pending", ["email"]
    )
    op.drop_constraint("uq_person_email", "person", type_="unique")
