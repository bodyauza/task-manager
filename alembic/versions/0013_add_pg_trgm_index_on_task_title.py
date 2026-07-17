"""Add pg_trgm GIN index on task.title for ILIKE substring search

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-17

task.title уже проиндексирован обычным B-tree (index=True в модели), но
search_tasks() (src/services/tasks.py) ищет через
Task.title.ilike(f"%{title}%") — паттерн с ведущим "%" не может
использовать B-tree (он ускоряет только сравнения с известным префиксом),
поэтому такой поиск всегда делает Seq Scan по всей таблице task независимо
от наличия обычного индекса. pg_trgm разбивает строку на триграммы
(последовательности из 3 символов) и строит по ним GIN-индекс, который
Postgres умеет использовать для ILIKE с шаблоном в любом месте строки,
включая ведущий "%".
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pg_trgm — "доверенное" (trusted) расширение начиная с PostgreSQL 13:
    # устанавливается обычным пользователем с правом CREATE на базу, без
    # необходимости в правах суперпользователя.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_task_title_trgm",
        "task",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_task_title_trgm", table_name="task")
    # Расширение pg_trgm не удаляем: им могут пользоваться другие объекты БД,
    # созданные вручную вне Alembic — DROP EXTENSION был бы неявным и
    # потенциально разрушительным побочным эффектом отката одной узкой миграции.
