"""Валидация, именование и сохранение загружаемых файлов (для task_files.py и subtask_files.py)."""

import asyncio
import magic
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile


# Максимальный размер одного файла: 10 МБ; превышение даёт 413. Дублируется на клиенте: OTHER_FILES_MAX_SIZE в common.js.
MAX_FILE_SIZE = 10 * 1024 * 1024

# Максимум файлов в «Иных документах» на запись: len(existing) + len(new_files) > MAX_OTHER_FILES → 422.
MAX_OTHER_FILES = 10

# Корень файлового хранилища (src/uploads/). Не внутри src/static/: тот каталог отдаётся публично, а uploads/ — только
# через аутентифицированный роутер src/routers/uploads.py.
UPLOAD_ROOT = Path(__file__).resolve().parent.parent / "uploads"

# Символы, запрещённые в имени файла (набор Windows Explorer): \ / : * ? " < > |. Защищает от проблем при работе с каталогом
# uploads/ в обход приложения. Дублируется на клиенте: FORBIDDEN_FILENAME_CHARS в common.js.
FORBIDDEN_FILENAME_CHARS = set('\\/:*?"<>|')

# Белый список расширений и допустимых MIME-типов (PDF, JPEG, PNG). Двойная проверка отсекает переименованные файлы
# (virus.exe → virus.pdf). Дублируется на клиенте: OTHER_FILES_ALLOWED_EXT в common.js и accept= у <input type="file">.
ALLOWED: dict[str, set[str]] = {
    ".pdf":  {"application/pdf"},
    ".jpg":  {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png":  {"image/png"},
}


async def read_and_validate(file: UploadFile) -> bytes:
    """Читает файл и проверяет размер, символы имени, расширение и MIME; возвращает байты.

    Порядок: размер (чтение чанками, 413 сразу при превышении) → запрещённые символы (422) → расширение (422) →
    MIME по сигнатуре magic (422).
    """
    # Чтение чанками: слишком большой файл обрывается на середине, пиковая память ≈ MAX_FILE_SIZE + один чанк.
    _CHUNK_SIZE = 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FILE_SIZE:
            # 413: файл превышает лимит; остаток не читаем.
            raise HTTPException(
                status_code=413,
                detail=f"Размер файла превышает лимит {MAX_FILE_SIZE // (1024 * 1024)} МБ",
            )
        chunks.append(chunk)
    content: bytes = b"".join(chunks)

    # UploadFile.filename может быть None (multipart-часть без filename): Path(None) дал бы 500, поэтому явная проверка (422).
    if not file.filename:
        raise HTTPException(status_code=422, detail="Имя файла не указано")

    forbidden_found = sorted(set(file.filename) & FORBIDDEN_FILENAME_CHARS)
    if forbidden_found:
        # 422: в имени есть запрещённые символы — перечисляем найденные.
        raise HTTPException(
            status_code=422,
            detail=f"Имя файла содержит недопустимые символы: {' '.join(forbidden_found)}",
        )

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED:
        # 422: расширение не в белом списке.
        allowed_exts = ", ".join(sorted(ALLOWED))
        raise HTTPException(
            status_code=422,
            detail=f"Расширение '{suffix}' не разрешено. Допустимые: {allowed_exts}",
        )

    # MIME определяется по сигнатуре байтов (mime=True). libmagic синхронный и CPU-bound — выносим в asyncio.to_thread,
    # чтобы не блокировать event loop (вызовы сериализуются внутренним локом, выигрыш — в свободном loop).
    detected_mime: str = await asyncio.to_thread(magic.from_buffer, content, mime=True)

    if detected_mime not in ALLOWED[suffix]:
        # 422: MIME не совпадает с расширением (например, logo.png → logo.pdf).
        raise HTTPException(
            status_code=422,
            detail=(
                f"MIME-тип файла '{detected_mime}' не соответствует расширению '{suffix}'. "
                f"Ожидается: {', '.join(ALLOWED[suffix])}"
            ),
        )

    return content


# Запас под лимит имени в ФС: 255 БАЙТ на компонент пути (кириллица — 2 байта на символ). 200 оставляет место под UUID-префикс
# (9 байт) и расширение.
_MAX_SAFE_FILENAME_BYTES = 200


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Обрезает строку до max_bytes в UTF-8, не разрывая многобайтовый символ (text[:n] режет по символам)."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # errors="ignore" отбрасывает оборванный хвост многобайтового символа.
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def safe_filename(original: str) -> str:
    """Добавляет UUID-префикс (uuid4().hex[:8]) против коллизий и ограничивает длину имени в байтах.

    "report.pdf" → "a1b2c3d4_report.pdf". Длинное имя обрезается по основе, а не по расширению (иначе не пройдёт ALLOWED).
    Без ограничения длинное имя давало OSError «File name too long» на записи.
    """
    name = Path(original).name
    prefix = f"{uuid4().hex[:8]}_"
    suffix = Path(name).suffix
    stem = Path(name).stem
    budget = _MAX_SAFE_FILENAME_BYTES - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
    stem = _truncate_utf8(stem, max(budget, 1))
    return f"{prefix}{stem}{suffix}"


def save_file(dest_dir: Path, filename: str, content: bytes, upload_root: Path = UPLOAD_ROOT) -> str:
    """Создаёт каталог, записывает файл и возвращает путь относительно upload_root, например "tasks/3/specification/a1b2c3d4_tz.pdf"
    (хранится в БД, формирует URL /uploads/<rel_path>). dest_dir — абсолютный путь внутри upload_root.

    upload_root — явный параметр: поиск каталога "uploads" в dest_dir.parts давал неверный путь, если проект лежит под каталогом
    с таким именем, а тесты патчат attachments.UPLOAD_ROOT, а не константу этого модуля.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    dest_path.write_bytes(content)

    # as_posix(): путь идёт в БД и URL, где обратный слэш недопустим (в том числе на Windows).
    return dest_path.relative_to(upload_root).as_posix()


def parse_other_paths(raw: list[str] | None) -> list[str]:
    """JSONB-колонка → list[str]; [] при NULL."""
    return raw if raw is not None else []
