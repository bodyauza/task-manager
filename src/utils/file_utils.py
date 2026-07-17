"""Утилиты для валидации, именования и сохранения загружаемых файлов.

Используется роутерами task_files.py и subtask_files.py.
Логика валидации вынесена сюда, а не в роутеры, чтобы не дублировать код.
"""

import asyncio
import magic                        # python-magic-bin: MIME-детектор по сигнатуре байтов
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile

# ── Константы ────────────────────────────────────────────────────────────────

# Максимальный размер одного загружаемого файла: 100 МБ.
# read_and_validate() читает файл целиком в память → превышение даёт 413.
MAX_FILE_SIZE = 100 * 1024 * 1024   # 100 МБ в байтах

# Максимальное количество файлов в поле «Иные документы» на одну запись.
# Проверяется в роутере: len(existing) + len(new_files) > MAX_OTHER_FILES → 422.
MAX_OTHER_FILES = 10

# Единая точка определения корня файлового хранилища.
# Вычисляется относительно этого файла (src/utils/file_utils.py):
#   parent → src/utils/, parent → src/, / "static" / "uploads" → src/static/uploads/
# Импортируется роутерами task_files.py и subtask_files.py — константа не дублируется.
UPLOAD_ROOT = Path(__file__).resolve().parent.parent / "static" / "uploads"

# Белый список допустимых расширений и соответствующих им MIME-типов.
# Ключ — расширение в нижнем регистре (с точкой).
# Значение — множество допустимых MIME-типов для этого расширения.
# Двойная проверка (расширение + MIME) защищает от переименованных файлов:
# файл virus.exe, переименованный в virus.pdf, будет отклонён по MIME.
ALLOWED: dict[str, set[str]] = {
    ".pdf":  {"application/pdf"},
    ".doc":  {"application/msword"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".xls":  {"application/vnd.ms-excel"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".jpg":  {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png":  {"image/png"},
    ".txt":  {"text/plain"},
}


# ── Валидация ────────────────────────────────────────────────────────────────

async def read_and_validate(file: UploadFile) -> bytes:
    """Читает файл из запроса и проверяет размер, расширение и MIME-тип.

    Последовательность проверок:
    1. Размер: файл читается чанками с накоплением счётчика; как только он
       превышает MAX_FILE_SIZE → немедленный 413, без чтения и буферизации
       остатка файла.
    2. Расширение: суффикс имени файла должен быть в ALLOWED → иначе 422.
    3. MIME-тип: magic.from_buffer анализирует первые байты файла по сигнатуре
       (magic bytes) — не зависит от расширения. Должен совпадать с допустимым
       множеством для данного расширения → иначе 422.

    Возвращает байты файла для последующей записи на диск.
    """
    # Потоковое чтение чанками вместо file.read() целиком: при файле, заметно
    # превышающем лимит (например, отправленном намеренно — DoS-паттерн из
    # нескольких конкурентных запросов на «Иные документы»), проверка размера
    # срабатывает на середине чтения, а не после того как весь файл уже лежит
    # в памяти процесса. Пиковое потребление памяти ограничено ~MAX_FILE_SIZE
    # + один чанк, а не размером присланного файла.
    _CHUNK_SIZE = 1024 * 1024  # 1 МБ — компромисс между числом чтений и пиковой памятью
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FILE_SIZE:
            # 413 Request Entity Too Large: файл превышает лимит.
            # Обрываем чтение немедленно — оставшаяся часть файла в поток не читается.
            raise HTTPException(
                status_code=413,
                detail=f"Размер файла превышает лимит {MAX_FILE_SIZE // (1024 * 1024)} МБ",
            )
        chunks.append(chunk)
    content: bytes = b"".join(chunks)

    # UploadFile.filename типизирован как Optional[str]: клиент может прислать
    # multipart-часть без Content-Disposition filename → filename=None.
    # Path(None) бросает TypeError → 500; явная проверка даёт 422.
    if not file.filename:
        raise HTTPException(status_code=422, detail="Имя файла не указано")

    suffix = Path(file.filename).suffix.lower()  # ".PDF" → ".pdf"; суффикс с точкой
    if suffix not in ALLOWED:
        # 422 Unprocessable Entity: расширение не в белом списке.
        allowed_exts = ", ".join(sorted(ALLOWED))
        raise HTTPException(
            status_code=422,
            detail=f"Расширение '{suffix}' не разрешено. Допустимые: {allowed_exts}",
        )

    # magic.from_buffer определяет MIME по сигнатуре байтов файла (не по расширению).
    # Например, PDF начинается с %PDF-1.x; PNG — с \x89PNG\r\n\x1a\n.
    # mime=True: возвращает строку MIME-типа, а не текстовое описание.
    # asyncio.to_thread: magic.from_buffer — синхронный CPU-bound вызов libmagic;
    # без выноса в поток он блокирует event loop на время анализа сигнатуры байтов,
    # замораживая вообще все остальные запросы приложения в этот момент. Сам
    # python-magic потокобезопасен (Magic.from_buffer использует внутренний
    # threading.Lock — общий на процесс, т.к. magic.from_buffer() переиспользует
    # один и тот же закешированный экземпляр Magic), поэтому конкурентные вызовы
    # из разных потоков не портят результат — но и не ускоряют друг друга: лок
    # сериализует их между собой. Выигрыш здесь — не в скорости самого MIME-анализа,
    # а в том, что во время его ожидания event loop свободен для других запросов.
    detected_mime: str = await asyncio.to_thread(magic.from_buffer, content, mime=True)

    if detected_mime not in ALLOWED[suffix]:
        # 422: MIME не совпадает с ожидаемым для этого расширения.
        # Типичная причина: файл переименован (logo.png → logo.pdf).
        raise HTTPException(
            status_code=422,
            detail=(
                f"MIME-тип файла '{detected_mime}' не соответствует расширению '{suffix}'. "
                f"Ожидается: {', '.join(ALLOWED[suffix])}"
            ),
        )

    return content  # валидация пройдена; возвращаем байты для сохранения


# ── Именование ───────────────────────────────────────────────────────────────

def safe_filename(original: str) -> str:
    """Добавляет UUID-префикс к имени файла для предотвращения коллизий.

    Пример: "report.pdf" → "a1b2c3d4_report.pdf"
    uuid4().hex[:8]: 8 шестнадцатеричных символов = 32^8 = ~4 млрд вариантов;
    достаточно для предотвращения коллизий даже при параллельных загрузках.
    Исходное имя сохраняется читаемым для пользователя.
    """
    return f"{uuid4().hex[:8]}_{Path(original).name}"


# ── Запись на диск ───────────────────────────────────────────────────────────

def save_file(dest_dir: Path, filename: str, content: bytes) -> str:
    """Создаёт директорию (если нужно), записывает файл и возвращает относительный путь.

    Возвращаемый путь — относительно src/static/uploads/, например:
    "tasks/3/specification/a1b2c3d4_tz.pdf"
    Используется для хранения в БД и формирования URL: /uploads/<rel_path>.

    dest_dir должен быть абсолютным путём внутри UPLOAD_ROOT.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)  # создать директорию рекурсивно, если нет
    (dest_dir / filename).write_bytes(content)   # записать байты в файл

    # Строим относительный путь: всё, что идёт после директории "uploads/" в abs-пути.
    # Пример: .../src/static/uploads/tasks/3/specification → "tasks/3/specification/file.pdf"
    parts = dest_dir.parts
    uploads_idx = next((i for i, p in enumerate(parts) if p == "uploads"), None)
    if uploads_idx is None:
        raise ValueError(f"Директория 'uploads' не найдена в пути: {dest_dir}")
    rel = "/".join(parts[uploads_idx + 1:]) + f"/{filename}"  # "tasks/3/specification/file.pdf"
    return rel


# ── Десериализация ───────────────────────────────────────────────────────────

def parse_other_paths(raw: list[str] | None) -> list[str]:
    """JSONB-колонка PostgreSQL → list[str]. asyncpg десериализует JSONB в list автоматически.
    Возвращает [] при NULL (нет файлов).
    """
    return raw if raw is not None else []
