"""Add file path columns to task and subtask

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # specification_path: путь к файлу «Техническое задание» относительно src/static/uploads/.
    # Хранится как строка, например "tasks/3/specification/a1b2c3d4_tz.pdf".
    # NULL — файл не загружен. Один файл на запись: при повторной загрузке старый удаляется.
    op.add_column("task", sa.Column("specification_path", sa.String(), nullable=True))

    # other_file_paths: JSON-список путей к «Иным документам», например:
    # '["tasks/3/other/a1b2_doc.pdf","tasks/3/other/c3d4_img.jpg"]'.
    # Text (не JSON-тип): хранится как строка, десериализуется в роутере/схеме.
    # NULL — ни одного файла. Максимум 10 файлов на запись (проверяется в роутере).
    op.add_column("task", sa.Column("other_file_paths", sa.Text(), nullable=True))

    # Аналогичные колонки для подзадач.
    op.add_column("subtask", sa.Column("specification_path", sa.String(), nullable=True))
    op.add_column("subtask", sa.Column("other_file_paths", sa.Text(), nullable=True))


def downgrade() -> None:
    for col in ("specification_path", "other_file_paths"):
        op.drop_column("task", col)
        op.drop_column("subtask", col)
