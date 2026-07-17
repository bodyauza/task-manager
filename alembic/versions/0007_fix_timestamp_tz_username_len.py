"""Fix TIMESTAMP → TIMESTAMPTZ; person.username TEXT → VARCHAR(255)

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-09

Две независимые правки типов колонок, объединённые в одну ревизию:
1. TIMESTAMP (без часового пояса) на трёх временных колонках — источник
   потенциальной путаницы: PostgreSQL хранит "naive" значение без каких-либо
   гарантий о часовом поясе, а сравнение с offset-aware datetime из Python
   (datetime.now(timezone.utc), см. RegistrationPending._now_utc()) может дать
   TypeError или тихо неверный результат при смене серверного часового пояса.
   TIMESTAMPTZ хранит момент времени однозначно (внутри — всегда UTC).
2. person.username был объявлен как sa.String() без длины — PostgreSQL
   транслирует это в TEXT (безлимитную строку); ORM-модель (User.username)
   ожидает VARCHAR(255), несовпадение типов между схемой и моделью — источник
   путаницы при чтении миграций и потенциальных проблем на некоторых клиентах,
   которые иначе трактуют TEXT и VARCHAR(N).
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
    # postgresql_using — сырое SQL-выражение, которое ALTER COLUMN TYPE применяет
    # к каждому существующему значению при конвертации; без него PostgreSQL
    # использовал бы неявный CAST, который для TIMESTAMP → TIMESTAMPTZ трактует
    # исходное значение как время В ЧАСОВОМ ПОЯСЕ СЕРВЕРА, а не как UTC — на
    # сервере с часовым поясом, отличным от UTC, это молча сдвинуло бы все
    # хранимые моменты времени на величину смещения.
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
    # TEXT → VARCHAR(255): implicit cast, USING не требуется — VARCHAR(255)
    # это TEXT с добавленным ограничением длины, PostgreSQL проверяет его на
    # существующих данных автоматически (упадёт, если где-то username длиннее
    # 255 символов, но такого на практике не бывает — email короче этого лимита,
    # а username = email.split("@")[0]).
    op.alter_column(
        "person", "username",
        type_=sa.String(255),
        existing_type=sa.String(),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Порядок обратный upgrade() (username, затем три TIMESTAMPTZ-колонки в
    # порядке, зеркальном их появлению выше).
    op.alter_column(
        "person", "username",
        type_=sa.String(),
        existing_type=sa.String(255),
        existing_nullable=False,
    )
    # AT TIME ZONE 'UTC' здесь конвертирует TIMESTAMPTZ обратно в naive TIMESTAMP,
    # интерпретируя хранимый момент как UTC — симметрично upgrade(), без сдвига.
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
