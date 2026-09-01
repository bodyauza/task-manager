"""Add file path columns to task and subtask

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-11

Добавляет поддержку файловых вложений (ТЗ + «Иные документы») к задачам
и подзадачам — фундамент для будущих routers/task_files.py, subtask_files.py
(на момент этой ревизии их ещё нет; эндпоинты загрузки появляются позже).
Обе новые колонки хранят пути на диске, а не сами файлы: байты никогда не
попадают в БД, только относительный путь внутри src/uploads/.
other_file_paths здесь — Text (JSON-строка), тип уточняется до нативного
JSONB уже следующей миграцией (0011) — это было промежуточное состояние,
осознанно выбранное для быстрой раскатки функциональности, а не финальный
дизайн.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # specification_path: путь к файлу «Техническое задание» относительно src/uploads/.
    # Хранится как строка, например "tasks/3/specification/a1b2c3d4_tz.pdf".
    # NULL — файл не загружен. Один файл на запись: при повторной загрузке старый удаляется.
    # sa.String() без длины → PostgreSQL TEXT (безлимитная строка) — путь строится
    # из UUID-префикса и имени файла, теоретический максимум не оценивался явно,
    # поэтому лимит длины сознательно не задан (в отличие от, например, task.title).
    op.add_column("task", sa.Column("specification_path", sa.String(), nullable=True))

    # other_file_paths: JSON-список путей к «Иным документам», например:
    # '["tasks/3/other/a1b2_doc.pdf","tasks/3/other/c3d4_img.jpg"]'.
    # Text (не JSON-тип): хранится как строка, десериализуется в роутере/схеме.
    # NULL — ни одного файла. Максимум 10 файлов на запись (проверяется в роутере).
    op.add_column("task", sa.Column("other_file_paths", sa.Text(), nullable=True))

    # Аналогичные колонки для подзадач — тот же смысл, тот же выбор типов и
    # nullable, что и для task выше; параллелизм с 0008 (subtask copies task's
    # column shape для CRM-полей) продолжается и здесь для файловых полей.
    op.add_column("subtask", sa.Column("specification_path", sa.String(), nullable=True))
    op.add_column("subtask", sa.Column("other_file_paths", sa.Text(), nullable=True))


def downgrade() -> None:
    # ВНИМАНИЕ: необратимая потеря данных, если к моменту downgrade колонки
    # уже заполнены — DROP COLUMN уничтожает и сами пути (сами файлы на диске
    # при этом остаются, но БД перестаёт о них знать: осиротевшие файлы в
    # uploads/ придётся находить и чистить вручную). Обычное свойство миграций,
    # добавляющих новые данные: downgrade всегда отбрасывает то, что upgrade
    # позволил накопить.
    for col in ("specification_path", "other_file_paths"):
        op.drop_column("task", col)
        op.drop_column("subtask", col)
