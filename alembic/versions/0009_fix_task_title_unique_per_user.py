"""Replace global uq_task_title with per-user uq_task_title_owner

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-09

Задачи из 0001 задумывались как принадлежащие конкретному пользователю
(owner_id есть с самого начала), но UNIQUE-ограничение на title было
глобальным — то же несоответствие между "уже спроектированной" схемой
subtask (0008, сразу составной UNIQUE) и унаследованной от начальной
версии схемой task, которую эта миграция приводит к тому же виду.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Глобальный UNIQUE(title) заменяется составным UNIQUE(title, owner_id).
    # Разные пользователи могут иметь задачи с одинаковым названием;
    # дубликаты запрещены только в рамках одного владельца.
    # IF EXISTS: constraint мог быть удалён вручную или отсутствовать в БД
    # (несмотря на то что migration 0001 его создаёт) — безопасный DROP.
    # op.execute(), а не op.drop_constraint(): Alembic/SQLAlchemy не предоставляют
    # прямого "drop constraint if exists" на уровне Operations API — сырой SQL
    # с IF EXISTS проще, чем try/except вокруг op.drop_constraint().
    op.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS uq_task_title")
    # Новый составной уникальный индекс — обслуживает и проверку дублей при
    # создании/переименовании задачи (src/services/tasks.py::create_task делает
    # предварительный SELECT по этой же паре колонок), и служит защитой от
    # race condition на уровне БД, если два одновременных запроса одного
    # пользователя минуют предварительную SELECT-проверку одновременно —
    # тогда commit() второго из них падает с IntegrityError.
    op.create_unique_constraint("uq_task_title_owner", "task", ["title", "owner_id"])


def downgrade() -> None:
    # Внимание: если к моменту downgrade в БД уже есть две задачи разных
    # пользователей с одинаковым title (что после upgrade() разрешено), этот
    # downgrade упадёт с ошибкой при попытке создать глобальный UNIQUE(title) —
    # даунгрейд предполагает, что данные, накопленные под новой схемой, всё ещё
    # удовлетворяют более строгому старому ограничению. Это стандартное свойство
    # DDL-миграций, сужающих ограничения: откат назад не всегда возможен без
    # ручной чистки данных.
    op.drop_constraint("uq_task_title_owner", "task", type_="unique")
    op.create_unique_constraint("uq_task_title", "task", ["title"])
