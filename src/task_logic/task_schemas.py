import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_whitespace(v: str) -> str:
    # Сворачивает любые последовательности пробельных символов (пробелы, табуляции,
    # переносы строк) в один пробел и обрезает края.
    # Без нормализации «Задача №1» и «Задача\t№1» дадут разные записи в БД,
    # несмотря на идентичное визуальное представление в UI.
    return re.sub(r'\s+', ' ', v).strip()


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., max_length=2000)

    # mode='before': нормализация запускается до type-coercion Pydantic.
    # При mode='after' строка из одного таба прошла бы проверку min_length=1,
    # но нормализация ещё не выполнена — в БД попал бы таб вместо пустой строки.
    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, v: object) -> object:
        if isinstance(v, str):
            return _normalize_whitespace(v)
        return v


# Все поля Optional: клиент передаёт только изменяемые поля (частичное обновление).
class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=2000)
    completed: Optional[bool] = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, v: object) -> object:
        if v is None:
            return v
        if isinstance(v, str):
            return _normalize_whitespace(v)
        return v


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    completed: bool
    crm_task_id: Optional[int] = None
    # crm_synced не хранится в БД — вычисляется в роутере по результату CRM-запроса.
    # None  = операция не предполагала обращения к CRM (GET-запросы).
    # True  = последняя синхронизация прошла успешно.
    # False = последняя синхронизация завершилась ошибкой.
    crm_synced: Optional[bool] = None
    # subtask_count вычисляется через подзапрос в read_tasks; None в остальных эндпоинтах.
    subtask_count: Optional[int] = None

    # Путь к файлу ТЗ относительно src/static/uploads/.
    # None — файл не загружен. Пример: "tasks/3/specification/a1b2_tz.pdf".
    # URL доступа: /uploads/tasks/3/specification/a1b2_tz.pdf (через routers/uploads.py,
    # аутентифицированный роутер, а не StaticFiles mount — см. src/routers/uploads.py).
    specification_path: Optional[str] = None

    # Список путей к иным документам.
    # ORM-колонка JSONB: asyncpg десериализует JSONB → list[str] при чтении автоматически.
    # Pydantic получает готовый list[str] — ручной десериализации не требуется.
    # None/[] — документов нет.
    other_file_paths: Optional[list[str]] = None
