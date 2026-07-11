from typing import List, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class Task(Base):
    __tablename__ = "task"
    # UniqueConstraint на (title, owner_id): один пользователь не может иметь
    # две задачи с одинаковым названием, но разные пользователи могут.
    # Два одновременных запроса одного пользователя с одинаковым title пройдут
    # Pydantic-проверку до commit, но один получит IntegrityError → HTTP 409.
    __table_args__ = (UniqueConstraint("title", "owner_id", name="uq_task_title_owner"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str] = mapped_column(String(2000))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("person.id"))
    owner: Mapped["User"] = relationship("User", back_populates="tasks")
    subtasks: Mapped[List["Subtask"]] = relationship(
        "Subtask", back_populates="task", cascade="all, delete-orphan"
    )
    # NULL = задача не синхронизирована с CRM (CRM был недоступен при создании
    # или задача создана до интеграции). Тип int, а не UUID: CRM присваивает числовые ID.
    crm_task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)

    # Путь к файлу ТЗ относительно src/static/uploads/, например "tasks/3/specification/a1b2_tz.pdf".
    # NULL — файл не загружен. Хранится строка, не байты: файл лежит на диске.
    specification_path: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)

    # JSONB-список путей к «Иным документам».
    # PostgreSQL хранит как бинарный JSON (JSONB) — индексируется, не требует json.loads/dumps.
    # SQLAlchemy передаёт list[str] напрямую; asyncpg сериализует в JSONB при записи.
    # NULL — нет файлов. Максимум 10 элементов — проверяется в роутере, не в модели.
    # Пример значения в Python: ["tasks/3/other/a1b2_doc.pdf", "tasks/3/other/c3d4_img.jpg"].
    other_file_paths: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True, default=None)


class Subtask(Base):
    __tablename__ = "subtask"
    __table_args__ = (UniqueConstraint("title", "task_id", name="uq_subtask_title_task"),)
    # UniqueConstraint: пара (title, task_id) уникальна — нельзя создать две подзадачи
    # с одинаковым title в одной задаче; в разных задачах одноимённые подзадачи допустимы

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(2000), server_default="")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("task.id", ondelete="CASCADE"), index=True
    )
    task: Mapped["Task"] = relationship("Task", back_populates="subtasks")
    crm_subtask_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)

    # Путь к файлу ТЗ подзадачи относительно src/static/uploads/.
    # Пример: "subtasks/7/specification/e5f6_spec.pdf". NULL — файл не загружен.
    specification_path: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)

    # JSONB-список путей к иным документам подзадачи. Структура аналогична task.other_file_paths.
    other_file_paths: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True, default=None)
