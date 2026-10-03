"""Форматтеры для sqladmin: ссылки на файлы и время."""

import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape
from sqladmin.formatters import BASE_FORMATTERS

from src.config import settings

_LINK = '<a href="/uploads/{path}" target="_blank" rel="noopener">открыть</a>'


def _encode_upload_path(rel_path: str) -> str:
    """Кодирует rel-путь для /uploads/<rel_path> по сегментам (как encodeUploadPath() в common.js): quote(segment, safe="")
    кодировал бы и разделитель "/". Без кодирования имя вроде «отчёт #1.pdf» оборвало бы ссылку на «#».
    """
    return "/".join(quote(segment, safe="") for segment in rel_path.split("/"))


def file_link_formatter(attribute: str):
    """Формат-фабрика для одиночного файлового слота (specification_path): (model, attr) -> Markup."""

    def _format(model, _attr) -> Markup:
        value = getattr(model, attribute)
        if not value:
            return Markup("")
        # escape() после URL-кодирования: защита href от HTML-инъекции (два независимых слоя — URL и HTML).
        return Markup(_LINK.format(path=escape(_encode_upload_path(value))))

    return _format


def other_files_formatter(model, _attr) -> Markup:
    """other_file_paths — JSONB-список путей: ссылка на каждый файл через <br>."""
    values = model.other_file_paths or []
    if not values:
        return Markup("")
    return Markup("<br>".join(_LINK.format(path=escape(_encode_upload_path(v))) for v in values))


def local_datetime_formatter(value: datetime.datetime) -> str:
    """Показывает TIMESTAMPTZ в ADMIN_TIMEZONE вместо UTC; naive-значение считается UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(ZoneInfo(settings.ADMIN_TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")


# Общий словарь column_type_formatters для списка и детальной страницы.
TYPE_FORMATTERS = {**BASE_FORMATTERS, datetime.datetime: local_datetime_formatter}
