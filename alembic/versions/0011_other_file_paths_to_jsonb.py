"""Convert other_file_paths from Text to JSONB

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-11

Уточняет тип, оставленный как временный компромисс в 0010: колонка хранила
JSON-массив путей в виде обычной текстовой строки — приложению приходилось
самому вызывать json.loads()/json.dumps() при каждом чтении/записи, а
PostgreSQL не мог ни проиндексировать содержимое, ни провалидировать, что
там вообще лежит валидный JSON. JSONB — нативный бинарный JSON-тип
PostgreSQL: asyncpg десериализует его в Python list автоматически (ORM-код
получает готовый list[str], а не строку для ручного парсинга), содержимое
можно индексировать (GIN) и проверять операторами JSONB (@>, ?, и т.д.),
хотя в этом проекте такие операторы/индексы пока не используются — колонка
всегда читается целиком по PK родителя.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Конвертируем колонку Text → JSONB с приведением данных через USING.
    # USING text::jsonb: PostgreSQL парсит существующие JSON-строки в бинарный JSONB на месте.
    # Если колонка пуста (NULL) — остаётся NULL без изменений.
    # JSONB: бинарное представление JSON в PostgreSQL; поддерживает индексы GIN,
    # автоматически десериализуется asyncpg → Python list/dict без json.loads.
    op.alter_column(
        "task",
        "other_file_paths",
        type_=JSONB,
        postgresql_using="other_file_paths::jsonb",
        existing_nullable=True,
    )
    op.alter_column(
        "subtask",
        "other_file_paths",
        type_=JSONB,
        postgresql_using="other_file_paths::jsonb",
        existing_nullable=True,
    )


def downgrade() -> None:
    # Обратное приведение JSONB → Text: PostgreSQL сериализует JSONB в строку.
    op.alter_column(
        "task",
        "other_file_paths",
        type_=sa.Text(),
        postgresql_using="other_file_paths::text",
        existing_nullable=True,
    )
    op.alter_column(
        "subtask",
        "other_file_paths",
        type_=sa.Text(),
        postgresql_using="other_file_paths::text",
        existing_nullable=True,
    )
