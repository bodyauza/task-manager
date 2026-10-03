"""Юнит-тесты save_file (src/utils/file_utils.py) без HTTP: вычисление относительного пути."""

from src.utils.file_utils import _MAX_SAFE_FILENAME_BYTES, safe_filename, save_file


def test_save_file_rel_path_ignores_ancestor_dir_named_uploads(tmp_path):
    """Регрессия: rel-путь вычислялся по первому каталогу "uploads" в dest_dir.parts, и проект под /srv/uploads/... давал путь
    относительно чужого "uploads". Он подставлен намеренно до настоящего upload_root.
    """
    fake_ancestor_uploads = tmp_path / "srv" / "uploads" / "task-manager"
    upload_root = fake_ancestor_uploads / "src" / "uploads"
    dest_dir = upload_root / "tasks" / "3" / "specification"

    rel = save_file(dest_dir, "a1b2c3d4_tz.pdf", b"%PDF-1.4 content", upload_root)

    assert rel == "tasks/3/specification/a1b2c3d4_tz.pdf"
    # Файл физически лежит там, откуда rel-путь его найдёт через upload_root.
    assert (upload_root / rel).read_bytes() == b"%PDF-1.4 content"


def test_save_file_rel_path_uses_forward_slashes(tmp_path):
    """as_posix(): путь для БД/URL всегда с прямыми слэшами, даже если тест
    гоняется на Windows (обратный слэш недопустим в /uploads/<rel_path>)."""
    upload_root = tmp_path / "uploads"
    dest_dir = upload_root / "tasks" / "5" / "other"

    rel = save_file(dest_dir, "x.pdf", b"content", upload_root)

    assert "\\" not in rel
    assert rel == "tasks/5/other/x.pdf"


def test_save_file_creates_missing_directories(tmp_path):
    upload_root = tmp_path / "uploads"
    dest_dir = upload_root / "tasks" / "1" / "specification"
    assert not dest_dir.exists()

    save_file(dest_dir, "f.pdf", b"data", upload_root)

    assert (dest_dir / "f.pdf").exists()


def test_safe_filename_short_name_unchanged_besides_prefix():
    result = safe_filename("report.pdf")
    assert result.endswith("_report.pdf")
    prefix, _, rest = result.partition("_")
    assert len(prefix) == 8
    assert rest == "report.pdf"


def test_safe_filename_truncates_long_name_keeping_extension():
    """Регрессия: имя от ~120 кириллических символов (2 байта на символ) давало OSError('File name too long') на записи."""
    long_name = "Техническое задание на разработку " * 10 + ".pdf"  # заметно длиннее лимита
    result = safe_filename(long_name)

    assert len(result.encode("utf-8")) <= _MAX_SAFE_FILENAME_BYTES
    assert result.endswith(".pdf")  # расширение сохранено, обрезана только основа имени
    prefix, _, _ = result.partition("_")
    assert len(prefix) == 8


def test_safe_filename_truncation_does_not_split_multibyte_char():
    """Обрезка не должна оставлять "хвост" многобайтового UTF-8 символа —
    результат обязан оставаться валидной строкой без mojibake."""
    long_name = "ё" * 300 + ".pdf"  # "ё" — 2 байта в UTF-8, 300*2 = 600 байт основы
    result = safe_filename(long_name)

    assert len(result.encode("utf-8")) <= _MAX_SAFE_FILENAME_BYTES
    assert result.endswith(".pdf")
    # Строка валидна и не содержит символа замены — decode(errors="ignore")
    # отбросил бы неполный хвост, а не заменил его на replacement character.
    assert "�" not in result


def test_safe_filename_preserves_short_ascii_name_length_unaffected():
    """Обычные короткие имена, как и раньше, не обрезаются вовсе."""
    assert safe_filename("a.pdf").endswith("_a.pdf")


def test_save_file_default_upload_root_matches_module_constant():
    """upload_root по умолчанию — модульная константа UPLOAD_ROOT (реальный
    src/uploads/), а не что-то захардкоженное отдельно."""
    import inspect

    default = inspect.signature(save_file).parameters["upload_root"].default
    from src.utils.file_utils import UPLOAD_ROOT

    assert default == UPLOAD_ROOT
