"""Add file path columns to task and subtask

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-11

Файловые вложения (ТЗ и «Иные документы») для задач и подзадач. Колонки хранят относительные пути внутри src/uploads/, а не файлы.
other_file_paths здесь Text (JSON-строка) — промежуточное состояние, в 0011 тип уточняется до JSONB.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # specification_path: относительный путь к ТЗ, например "tasks/3/specification/a1b2c3d4_tz.pdf"; NULL — файла нет. Без длины (TEXT).
    op.add_column("task", sa.Column("specification_path", sa.String(), nullable=True))

    # other_file_paths: JSON-список путей к «Иным документам» (Text); NULL — файлов нет; максимум 10 проверяется в роутере.
    op.add_column("task", sa.Column("other_file_paths", sa.Text(), nullable=True))

    # Те же колонки для подзадач.
    op.add_column("subtask", sa.Column("specification_path", sa.String(), nullable=True))
    op.add_column("subtask", sa.Column("other_file_paths", sa.Text(), nullable=True))


def downgrade() -> None:
    # Внимание: downgrade уничтожает пути, файлы на диске остаются сиротами.
    for col in ("specification_path", "other_file_paths"):
        op.drop_column("task", col)
        op.drop_column("subtask", col)
