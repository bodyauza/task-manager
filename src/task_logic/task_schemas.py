import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _normalize_whitespace(v: str) -> str:
    # Схлопывает пробельные последовательности в один пробел и обрезает края, чтобы «Задача №1» и «Задача\t№1» не давали разных записей.
    return re.sub(r'\s+', ' ', v).strip()


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., max_length=2000)
    # CRM-ID опции списка «Проект» (value из <option>, не метка); проверяется только длина, членство — в сервисе.
    project: Optional[str] = Field(None, max_length=20)

    # mode='before': нормализация должна идти до coercion, иначе строка из одного таба прошла бы min_length=1.
    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, v: object) -> object:
        if isinstance(v, str):
            return _normalize_whitespace(v)
        return v


# Все поля Optional: клиент передаёт только изменяемые поля.
class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=2000)
    completed: Optional[bool] = None
    # None — «не трогать» (exclude_unset=True); "" — явная очистка проекта.
    project: Optional[str] = Field(None, max_length=20)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, v: object) -> object:
        if v is None:
            return v
        if isinstance(v, str):
            return _normalize_whitespace(v)
        return v

    # title/description/completed — NOT NULL: явный null отклоняем (422), иначе IntegrityError превратился бы в ложный 409.
    # "project" сюда не входит: null для него — штатная очистка.
    @model_validator(mode="after")
    def _reject_explicit_null_for_not_null_fields(self) -> "TaskUpdate":
        for name in ("title", "description", "completed"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name}: null не допускается — поле NOT NULL; чтобы не менять его, не передавайте ключ вовсе")
        return self


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    completed: bool
    # crm_task_id/crm_synced в ответ не входят; CRM-id, шард и попытки видны только администратору.
    # sync_status ('unsynced' | 'pending' | 'synced' | 'failed') — бейдж на task-board/subtask-board.
    sync_status: str = "unsynced"
    # subtask_count вычисляется подзапросом в read_tasks; в остальных эндпоинтах None.
    subtask_count: Optional[int] = None

    # Путь к файлу ТЗ относительно src/uploads/; None — файла нет. URL: /uploads/<путь> (routers/uploads.py).
    specification_path: Optional[str] = None

    # Пути к иным документам; None/[] — документов нет.
    other_file_paths: Optional[list[str]] = None

    # Заполняется только при атомарном создании, если часть файлов не сохранилась на диск после создания задачи.
    # Ключ — оригинальное имя файла, значение — текст ошибки. Не персистентное поле.
    file_upload_errors: Optional[dict[str, str]] = None

    # Значение опции («Альфа»), не CRM-ID; из relationship task.project_ref.label. None — проект не выбран.
    project: Optional[str] = None
    # CRM-ID выбранного проекта — для предзаполнения <select> при редактировании; в БД не хранится.
    project_option_id: Optional[str] = None
