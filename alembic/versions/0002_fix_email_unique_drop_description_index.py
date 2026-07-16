"""Add unique constraint on person.email; drop redundant task.description index

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-18

Две независимые правки схемы, объединённые в одну ревизию (обе — быстрые
DDL-операции без изменения данных, разносить по отдельным миграциям смысла нет):
1. email должен быть уникален — 0001 создала колонку без этого ограничения.
2. индекс на task.description, созданный в 0001, никогда не используется ни
   одним запросом проекта (description не участвует в WHERE/ORDER BY) — только
   занимает место на диске и замедляет каждый INSERT/UPDATE task.

ВАЖНО (см. миграцию 0012): несмотря на то что эта ревизия создаёт
uq_person_email и `alembic current` в дальнейшем показывает её применённой,
прямая проверка через `pg_constraint` в реальной БД проекта обнаружила
отсутствие этого ограничения — застрять оно могло на любом этапе (ручной
DROP CONSTRAINT, восстановление БД из бэкапа без этой ревизии и т.п.).
0012 создаёт constraint заново идемпотентно. Мораль остаётся в силе:
"ревизия отмечена применённой в alembic_version" — не то же самое, что
"каждая DDL-операция внутри неё гарантированно выполнена в текущей БД".
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # UNIQUE-ограничение на person.email. До этой миграции повторная регистрация
    # с уже занятым email отклонялась только приложением (SELECT-проверка перед
    # INSERT в UserManager.create()) — без гарантии на уровне БД два одновременных
    # запроса регистрации теоретически могли пройти проверку оба и вставиться оба.
    op.create_unique_constraint("uq_person_email", "person", ["email"])
    # Индекс из 0001, который не ускоряет ни один реальный запрос — description
    # нигде не фильтруется и не сортируется, только читается целиком по PK/title.
    op.drop_index("ix_task_description", table_name="task")


def downgrade() -> None:
    # Порядок обратный upgrade(): сначала воссоздаём то, что было удалено последним.
    op.create_index("ix_task_description", "task", ["description"])
    op.drop_constraint("uq_person_email", "person", type_="unique")
