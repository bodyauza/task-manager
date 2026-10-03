import datetime
from typing import Any, List, Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, TIMESTAMP, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.database import Base


class Project(Base):
    """Локальное зеркало глобального списка «Проект» CRM (источник истины — CRM).

    Пишет сюда только Celery-задача sync_project_table; веб-процесс таблицу только читает.
    """
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ID опции в CRM — натуральный ключ upsert в sync_project_table (id выше — локальный PK).
    crm_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Пропавшая из ответа CRM опция не удаляется (на неё ссылаются Task.project_id), а помечается неактивной.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    synced_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    def __str__(self) -> str:
        # Подпись в ajax-полях sqladmin (str(model)).
        return self.label


class CrmOutbox(Base):
    """Durable outbox для CRM-операций после основного commit (create/update/delete/sync_files для Task и Subtask).

    Строка вставляется в той же транзакции, что и изменение; при падении процесса после commit она остаётся
    'pending' и её подберёт reconcile_pending_outbox. Шардирование, Redlock и rate limit — src/tasks/sharding.py,
    crm_shard_lock.py, crm_rate_limit.py.
    """
    __tablename__ = "crm_outbox"
    __table_args__ = (
        # Partial-индекс по depends_on_event_id (WHERE IS NOT NULL): без него каждый DELETE строки проверяет self-FK полным сканированием,
        # а cleanup_done_outbox использует NOT EXISTS по той же колонке. Объявлен и здесь, и в миграции 0021: тесты создают схему через create_all.
        Index(
            "ix_crm_outbox_depends_on_event_id", "depends_on_event_id",
            postgresql_where=text("depends_on_event_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 'task' | 'subtask' — какая сущность стоит за aggregate_id; определяет менеджер CRM и обработчик.
    aggregate_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="task")
    # Не FK: к обработке 'delete' сущности в БД уже нет, payload несёт весь снимок для CRM-вызова.
    aggregate_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # 'create' | 'sync_files' | 'update' | 'delete' — обработчики в crm_outbox_tasks.py.
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    # Снимок Task.crm_shard на момент вставки (не lookup): строка 'delete' переживает удаление Task, а reconcile должен знать очередь.
    shard: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Всё для CRM-вызова без обращения к Task/Subtask, например {"crm_task_id": 42, ...}.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # 'pending' → 'done' | 'failed' (попытки исчерпаны) | 'blocked' (ждёт depends_on_event_id).
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Когда reconcile последний раз реально поставил строку в очередь. Только для attempts == 0 — событие, не дошедшее до
    # попытки (лимит CRM, более старое событие, зависимость): без поля reconcile каждую минуту переставлял бы те же строки.
    # attempts > 0 использует updated_at + _retry_delay_seconds.
    dispatched_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    # Причина последнего сбоя «ТипИсключения: текст», обрезанная до LAST_ERROR_MAX_LEN; очищается при успехе.
    # Видна только администратору: ответ CRM может содержать данные задачи, в API поле не попадает.
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Событие-зависимость: межагрегатное (Subtask.create ждёт Task.create) или внутриагрегатное (update/sync_files ждут create).
    # NULL — зависимости нет.
    depends_on_event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("crm_outbox.id"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Task(Base):
    __tablename__ = "task"
    # UniqueConstraint (title, owner_id): у пользователя не может быть двух задач с одним названием; гонка даст IntegrityError → 409.
    __table_args__ = (
        UniqueConstraint("title", "owner_id", name="uq_task_title_owner"),
        # Частичный уникальный индекс по crm_task_id (миграция 0019); объявлен и здесь, потому что тесты создают схему через create_all.
        Index(
            "ix_task_crm_task_id_unique", "crm_task_id",
            unique=True, postgresql_where=text("crm_task_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str] = mapped_column(String(2000))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("person.id", ondelete="CASCADE"))
    owner: Mapped["User"] = relationship("User", back_populates="tasks")
    subtasks: Mapped[List["Subtask"]] = relationship(
        "Subtask", back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )
    # NULL — задача не синхронизирована с CRM. Частичный уникальный индекс ix_task_crm_task_id_unique — вторая линия защиты
    # от «усыновления» одной CRM-записи двумя задачами: попытка занять чужой id даёт IntegrityError.
    crm_task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)

    # Путь к файлу ТЗ относительно src/uploads/, например "tasks/3/specification/a1b2_tz.pdf"; NULL — файла нет.
    specification_path: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)

    # JSONB-список путей к «Иным документам» (максимум 10 — проверяется в роутере); NULL — файлов нет.
    other_file_paths: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True, default=None)

    # Проект из справочника CRM (класс Project). Есть только у Task.
    project_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("project.id"), nullable=True
    )
    # Названа project_ref, а не project: TaskResponse.project — строка (label), и одноимённая relationship провалила бы
    # model_validate(from_attributes=True). lazy="selectin": иначе MissingGreenlet в async-коде.
    project_ref: Mapped[Optional["Project"]] = relationship("Project", lazy="selectin")

    # Шард очереди CRM-синхронизации: назначается один раз на первом outbox-событии задачи (shard_for_id) и не пересчитывается;
    # тот же шард используют все её Subtask. NULL — шард ещё не назначен (присваивается лениво).
    crm_shard: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # 'unsynced' (операций не было) | 'pending' (outbox-строка ждёт/повторяется) | 'synced' | 'failed' (попытки исчерпаны).
    # От него выводится TaskResponse.crm_synced.
    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="unsynced")

    def __str__(self) -> str:
        # Подпись в ajax-полях sqladmin (str(model)).
        return self.title


class Subtask(Base):
    __tablename__ = "subtask"
    __table_args__ = (
        UniqueConstraint("title", "task_id", name="uq_subtask_title_task"),
        # См. Task.__table_args__ и миграцию 0019.
        Index(
            "ix_subtask_crm_subtask_id_unique", "crm_subtask_id",
            unique=True, postgresql_where=text("crm_subtask_id IS NOT NULL"),
        ),
    )
    # UniqueConstraint (title, task_id): в одной задаче нельзя две подзадачи с одним названием.

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(2000), server_default="")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("task.id", ondelete="CASCADE"), index=True
    )
    task: Mapped["Task"] = relationship("Task", back_populates="subtasks")
    # Частичный уникальный индекс ix_subtask_crm_subtask_id_unique (миграция 0019), как у Task.crm_task_id.
    crm_subtask_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)

    # Путь к файлу ТЗ подзадачи относительно src/uploads/; NULL — файла нет.
    specification_path: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)

    # JSONB-список путей к иным документам подзадачи (как у Task).
    other_file_paths: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True, default=None)

    # Как Task.sync_status. Своего crm_shard у Subtask нет — шард берётся у родительской задачи.
    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="unsynced")

    def __str__(self) -> str:
        return self.title
