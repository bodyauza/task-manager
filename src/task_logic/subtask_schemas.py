import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_whitespace(v: str) -> str:
    return re.sub(r'\s+', ' ', v).strip()
    # re.sub(r'\s+', ' '): заменяет любую последовательность пробелов/табов/переносов строки
    # на один пробел; .strip() убирает пробелы по краям строки
    # итог: "  foo   bar  " → "foo bar"


class SubtaskCreate(BaseModel):
    task_id: int                                    # ID родительской задачи; проверяется в роутере
    title: str = Field(..., min_length=1, max_length=100)
    # ...: поле обязательное; min_length=1 запрещает пустую строку (даже пустую после trim)
    description: str = Field("", max_length=2000)
    # default="" — поле необязательное; соответствует server_default ORM-модели

    @field_validator("title", mode="before")        # mode="before": до приведения типов Pydantic
    @classmethod
    def normalize_title(cls, v: object) -> object:
        if isinstance(v, str):
            return _normalize_whitespace(v)         # нормализуем только str; иначе Pydantic сам поднимет ValidationError
        return v


class SubtaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    # None = "не обновлять title"; роутер использует model_dump(exclude_unset=True)
    description: Optional[str] = Field(None, max_length=2000)
    completed: Optional[bool] = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, v: object) -> object:
        if v is None:
            return v                                # явный None ("не трогать поле") — пропускаем
        if isinstance(v, str):
            return _normalize_whitespace(v)
        return v


class SubtaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    # from_attributes=True: Pydantic читает атрибуты ORM-объекта напрямую (task.title),
    # а не ожидает dict; нужно для SubtaskResponse.model_validate(db_subtask)

    id: int
    title: str
    description: str
    completed: bool
    task_id: int
    crm_subtask_id: Optional[int] = None
    crm_synced: Optional[bool] = None
    # crm_synced отсутствует в ORM-модели; Pydantic подставит None по умолчанию;
    # роутер устанавливает вручную: result.crm_synced = crm_subtask_id is not None

    # Путь к файлу ТЗ подзадачи относительно src/uploads/.
    # Пример: "subtasks/7/specification/e5f6_spec.pdf". None — файл не загружен.
    specification_path: Optional[str] = None

    # Список путей к иным документам подзадачи.
    # ORM-колонка JSONB: asyncpg десериализует JSONB → list[str] при чтении автоматически.
    # None/[] — документов нет.
    other_file_paths: Optional[list[str]] = None
