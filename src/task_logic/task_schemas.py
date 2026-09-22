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
    # CRM-ID опции списка "Проект" (значение <option value="...">, не сама метка) —
    # формальная проверка длины, не членства в списке (это делает сервис через
    # локальную таблицу project, см. services/tasks.py::_resolve_project_id).
    project: Optional[str] = Field(None, max_length=20)

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
    # None здесь — «поле не передано, не трогать» (exclude_unset=True в update_task);
    # "" (пустая строка) — явная очистка выбранного проекта.
    project: Optional[str] = Field(None, max_length=20)

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
    # crm_task_id/crm_synced НЕ являются полями этого ответа — сам CRM-id и
    # внутренние детали (шард, outbox, попытки) клиенту не отдаются; они видны
    # только администратору (src/routers/admin.py, /admin/crm-sync).
    # sync_status — единственная деталь синхронизации, которую видит любой
    # пользователь: бейдж рядом со счётчиком подзадач на task-board/
    # subtask-board ('unsynced' | 'pending' | 'synced' | 'failed', см.
    # Task.sync_status в models.py). Читается напрямую из ORM-колонки.
    sync_status: str = "unsynced"
    # subtask_count вычисляется через подзапрос в read_tasks; None в остальных эндпоинтах.
    subtask_count: Optional[int] = None

    # Путь к файлу ТЗ относительно src/uploads/.
    # None — файл не загружен. Пример: "tasks/3/specification/a1b2_tz.pdf".
    # URL доступа: /uploads/tasks/3/specification/a1b2_tz.pdf (через routers/uploads.py,
    # аутентифицированный роутер, а не StaticFiles mount — см. src/routers/uploads.py).
    specification_path: Optional[str] = None

    # Список путей к иным документам.
    # ORM-колонка JSONB: asyncpg десериализует JSONB → list[str] при чтении автоматически.
    # Pydantic получает готовый list[str] — ручной десериализации не требуется.
    # None/[] — документов нет.
    other_file_paths: Optional[list[str]] = None

    # Заполняется только атомарным созданием (create_task с файлами), когда валидация
    # прошла, но сохранение на диск части файлов упало ПОСЛЕ гарантированного создания
    # задачи (см. attachments.py::save_files_for_create) — не персистентное поле,
    # как и crm_synced выше. Ключ — оригинальное имя файла, значение — текст ошибки.
    # None — либо не create-эндпоинт, либо все файлы (если были) сохранились успешно.
    file_upload_errors: Optional[dict[str, str]] = None

    # Значение опции ("Альфа"), не CRM-ID — читается через relationship task.project.label
    # (from_attributes=True само по себе не читает вложенный объект под другим именем,
    # см. services/tasks.py::_attach_project_option_id). None — проект не выбран.
    project: Optional[str] = None
    # Текущий CRM-ID выбранного проекта (task.project.crm_id) — только для
    # предзаполнения <select> в режиме редактирования. Вычисляемое поле,
    # как crm_synced — в БД не хранится.
    project_option_id: Optional[str] = None
