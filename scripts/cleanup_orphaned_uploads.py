"""Разовая сверка диска с БД: находит каталоги uploads/tasks/* и uploads/subtasks/*, которым не соответствует строка task/subtask
(файлы, осиротевшие при удалении пользователей до исправления каскада; attachments.cleanup() их уже не найдёт).

Имя подкаталога первого уровня — локальный PK (task.id/subtask.id), а не crm_task_id.

По умолчанию — только отчёт (какие каталоги осиротели и сколько весят). Удаление — с явным --apply и --yes.
Используется БД, которую резолвит текущий API_MODE.

Использование:
    .venv/Scripts/python.exe scripts/cleanup_orphaned_uploads.py            # отчёт (dry-run)
    .venv/Scripts/python.exe scripts/cleanup_orphaned_uploads.py --apply --yes   # удаление
"""

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

# Регистрирует все ORM-модели (включая User) в Base.registry: Task.owner ссылается на "User" строкой и без этого падает InvalidRequestError.
import src.models  # noqa: F401,E402
from src.database import async_session_maker  # noqa: E402
from src.task_logic.models import Subtask, Task  # noqa: E402
from src.utils.file_utils import UPLOAD_ROOT  # noqa: E402

# (dir_segment на диске, ORM-модель, колонка id) — узкий аналог AttachmentConfig.
_ENTITY_KINDS = [("tasks", Task), ("subtasks", Subtask)]


def _dir_size_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


async def _existing_ids(session, model) -> set[int]:
    rows = (await session.execute(select(model.id))).scalars().all()
    return set(rows)


async def find_orphaned_dirs() -> list[Path]:
    """Каталоги на диске без соответствующей строки task/subtask; ничего не удаляет."""
    orphaned: list[Path] = []
    async with async_session_maker() as session:
        for dir_segment, model in _ENTITY_KINDS:
            base = UPLOAD_ROOT / dir_segment
            if not base.is_dir():
                continue
            existing_ids = await _existing_ids(session, model)
            for child in base.iterdir():
                if not child.is_dir():
                    continue
                try:
                    entity_id = int(child.name)
                except ValueError:
                    continue
                if entity_id not in existing_ids:
                    orphaned.append(child)
    return orphaned


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="удалить найденные осиротевшие каталоги (по умолчанию — только отчёт)")
    parser.add_argument("--yes", action="store_true", help="подтверждение для --apply — без него --apply ничего не удалит")
    args = parser.parse_args()

    orphaned = await find_orphaned_dirs()
    if not orphaned:
        print("Осиротевших каталогов не найдено.")
        return

    total_bytes = 0
    for path in orphaned:
        size = _dir_size_bytes(path)
        total_bytes += size
        print(f"{'[БУДЕТ УДАЛЕНО] ' if args.apply and args.yes else ''}{path}  ({size / 1024:.1f} КБ)")
    print(f"\nВсего: {len(orphaned)} каталог(ов), {total_bytes / 1024:.1f} КБ.")

    if not args.apply:
        print("\nЭто отчёт (dry-run). Для реального удаления — запустить с --apply --yes.")
        return
    if not args.yes:
        print("\n--apply указан без --yes — ничего не удалено. Добавьте --yes, чтобы подтвердить удаление.")
        return

    for path in orphaned:
        shutil.rmtree(path, ignore_errors=True)
    print(f"\nУдалено {len(orphaned)} каталог(ов).")


if __name__ == "__main__":
    asyncio.run(main())
