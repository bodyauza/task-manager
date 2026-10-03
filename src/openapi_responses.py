"""Описания ошибочных ответов для OpenAPI: FastAPI сам добавляет только успех и 422, остальные коды из HTTPException собирает responses(...).
На поведение эндпоинтов не влияет.
"""

from typing import Any

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """Тело ошибки HTTPException: {"detail": ...}."""

    detail: Any


_DESCRIPTIONS: dict[int, str] = {
    400: "Некорректный запрос (например, пустой параметр или несуществующий id роли)",
    401: "Нет действующего access_token (кука отсутствует или просрочена)",
    403: "Недостаточно прав (требуется роль admin)",
    404: "Объект не найден",
    409: "Конфликт: такое название уже существует или объект изменён параллельно",
    413: "Файл превышает допустимый размер (10 МБ)",
    422: "Ошибка валидации (поля запроса, расширение или MIME-тип файла)",
    429: "Слишком частые запросы (повтор допустим через указанное число секунд)",
    503: "Внешняя служба недоступна (например, SMTP)",
}


def responses(*codes: int, **overrides: str) -> dict[int | str, dict[str, Any]]:
    """responses(401, 404, 409) → {401: {...}, 404: {...}, 409: {...}}; overrides — уточнить текст кода: c404="..."."""
    return {
        code: {
            "model": ErrorDetail,
            "description": overrides.get(f"c{code}", _DESCRIPTIONS[code]),
        }
        for code in codes
    }
