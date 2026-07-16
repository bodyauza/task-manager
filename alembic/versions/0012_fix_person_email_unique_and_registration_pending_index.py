"""Add missing uq_person_email; unify registration_pending.email to one unique index

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-15

ИДЕМПОТЕНТНОСТЬ (добавлено 2026-07-16, после первого прогона в Docker-окружении).
Первая версия этой миграции безусловно вызывала create_unique_constraint/
drop_constraint/drop_index — она была написана под конкретную наблюдаемую БД
(локальный Windows Postgres разработчика), где uq_person_email отсутствовал
несмотря на то что alembic_version показывал 0002 применённой (см. подробный
разбор ниже). При прогоне той же миграции против ДРУГОЙ базы — свежего тома
Docker (`postgres_data` в src/docker-compose.yml), где 0002 отработала штатно
и constraint был создан правильно с первого раза, — upgrade() падал
`DuplicateTableError: relation "uq_person_email" already exists`, потому что
PostgreSQL не поддерживает `ADD CONSTRAINT IF NOT EXISTS` (в отличие от
`DROP CONSTRAINT IF EXISTS`, которым пользуется, например, 0009). Раз миграция
не может заранее знать, в каком из двух состояний окажется конкретная целевая
БД, она обязана проверять текущее состояние сама — через прямой запрос к
pg_constraint/pg_indexes — и выполнять только недостающие операции. Мораль
дополняет мораль исходного расследования ниже: "разные экземпляры БД одного
проекта могут разойтись в фактическом состоянии" — верно не только для
constraint, отсутствующего там, где он должен быть, но и для миграции,
предполагающей его отсутствие там, где он есть.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _constraint_exists(conn, table: str, name: str) -> bool:
    """True, если ограничение с этим именем уже есть на таблице (любого типа).

    CAST(:table AS regclass), а не ":table::regclass" — SQLAlchemy text()
    неоднозначно разбирает связку "двоеточие bind-параметра + двойное
    двоеточие оператора приведения типа PostgreSQL", стоящие вплотную:
    ":table::regclass" не подставляет bind-параметр вовсе и попадает в
    итоговый SQL буквально, что PostgreSQL воспринимает как синтаксическую
    ошибку ("syntax error at or near ':'"). CAST(... AS ...) — эквивалент
    оператора "::", но без символа ":", поэтому конфликта с синтаксисом
    именованных параметров SQLAlchemy не возникает.
    """
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = :name AND conrelid = CAST(:table AS regclass)"
        ),
        {"name": name, "table": table},
    ).scalar())


def _index_state(conn, name: str) -> "str | None":
    """Возвращает 'unique' / 'non_unique', если индекс с этим именем существует,
    иначе None (индекса нет вовсе)."""
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

    # person.email: миграция 0002 должна была создать uq_person_email, но в реальной БД
    # этого ограничения нет (обнаружено через `alembic check` + прямой запрос к pg_constraint —
    # 0 строк с contype='u' для person). До этой миграции уникальность email обеспечивалась
    # только на уровне приложения (SELECT-проверка перед INSERT в registration_endpoints.py) —
    # без БД-ограничения это check-then-insert без защиты от гонки: два одновременных
    # POST /auth/register/complete с одним email оба могли пройти проверку и оба вставиться.
    #
    # Проверка перед созданием: на других экземплярах БД (например, свежий Docker-том,
    # где 0002 отработала корректно с первого раза) constraint уже может существовать —
    # см. docstring ревизии выше.
    if not _constraint_exists(conn, "person", "uq_person_email"):
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
    # дропаем constraint (если есть), приводим индекс к уникальному (если он ещё не такой).
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
    # index_state == "unique": уже в целевом состоянии, ничего делать не нужно.


def downgrade() -> None:
    op.drop_index("ix_registration_pending_email", table_name="registration_pending")
    op.create_index("ix_registration_pending_email", "registration_pending", ["email"])
    op.create_unique_constraint(
        "uq_registration_pending_email", "registration_pending", ["email"]
    )
    op.drop_constraint("uq_person_email", "person", type_="unique")
