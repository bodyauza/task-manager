"""Общие форматтеры для sqladmin-представлений: ссылки на файлы и время."""

import datetime
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape
from sqladmin.formatters import BASE_FORMATTERS

from src.config import settings

_LINK = '<a href="/uploads/{path}" target="_blank" rel="noopener">открыть</a>'


def file_link_formatter(attribute: str):
    """column_formatters-совместимая фабрика для одиночного файлового слота
    (specification_path) — возвращает (model, attr) -> Markup."""

    def _format(model, _attr) -> Markup:
        value = getattr(model, attribute)
        if not value:
            return Markup("")
        return Markup(_LINK.format(path=escape(value)))

    return _format


def other_files_formatter(model, _attr) -> Markup:
    """other_file_paths — JSONB-список путей ("Иные документы"): ссылка на
    каждый файл через <br>."""
    values = model.other_file_paths or []
    if not values:
        return Markup("")
    return Markup("<br>".join(_LINK.format(path=escape(v)) for v in values))


def local_datetime_formatter(value: datetime.datetime) -> str:
    """Показывает TIMESTAMPTZ в ADMIN_TIMEZONE вместо UTC (asyncpg отдаёт
    aware-datetime в UTC, sqladmin по умолчанию печатает его как есть).
    Naive-значение считается UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(ZoneInfo(settings.ADMIN_TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")


# Общий словарь для column_type_formatters (действует и в списке, и в детальной
# странице — sqladmin использует один и тот же _default_formatter).
TYPE_FORMATTERS = {**BASE_FORMATTERS, datetime.datetime: local_datetime_formatter}
