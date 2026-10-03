import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _normalize_whitespace(v: str) -> str:
    return re.sub(r'\s+', ' ', v).strip()
    # Схлопывает пробельные последовательности в один пробел и обрезает края: "  foo   bar  " → "foo bar".


class SubtaskCreate(BaseModel):
    task_id: int
    title: str = Field(..., min_length=1, max_length=100)
    # min_length=1 запрещает пустую строку (и пустую после trim).
    description: str = Field("", max_length=2000)
    # default="" — как server_default ORM-модели.

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, v: object) -> object:
        if isinstance(v, str):
            return _normalize_whitespace(v)
        return v


class SubtaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    # None — «не обновлять title» (роутер использует exclude_unset=True).
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

    # Явный null для NOT NULL-полей отклоняем (422): иначе IntegrityError превратился бы в ложный 409 (как в TaskUpdate).
    @model_validator(mode="after")
    def _reject_explicit_null_for_not_null_fields(self) -> "SubtaskUpdate":
        for name in ("title", "description", "completed"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name}: null не допускается — поле NOT NULL; чтобы не менять его, не передавайте ключ вовсе")
        return self


class SubtaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    # from_attributes=True: нужно для SubtaskResponse.model_validate(db_subtask).

    id: int
    title: str
    description: str
    completed: bool
    task_id: int
    # crm_subtask_id/crm_synced в ответ не входят; sync_status — единственная отдаваемая деталь (бейдж на subtask-board).
    sync_status: str = "unsynced"

    # Путь к файлу ТЗ подзадачи относительно src/uploads/; None — файла нет.
    specification_path: Optional[str] = None

    # Пути к иным документам подзадачи; None/[] — документов нет.
    other_file_paths: Optional[list[str]] = None

    # См. TaskResponse.file_upload_errors.
    file_upload_errors: Optional[dict[str, str]] = None
