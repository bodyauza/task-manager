from datetime import datetime, timezone
from typing import List, Optional

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTable
from sqlalchemy import JSON, TIMESTAMP, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base


class RegistrationPending(Base):
    __tablename__ = "registration_pending"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    code_hash: Mapped[str] = mapped_column(String(1024), nullable=False)
    # attempts: количество неверных попыток. При достижении лимита запись удаляется.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Role(Base):
    __tablename__ = "role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # JSON-массив строк: ["read", "write", "delete"].
    # Проверяется в require_permission() через оператор in.
    permissions: Mapped[list] = mapped_column(JSON, nullable=True)


class Task(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # unique=True задаёт ограничение на уровне БД, а не только в Pydantic-валидаторе.
    # Два одновременных запроса с одинаковым title пройдут Pydantic-проверку до commit,
    # но один из них получит IntegrityError — перехватывается в роутере (HTTP 409).
    title: Mapped[str] = mapped_column(String(100), index=True, unique=True)
    description: Mapped[str] = mapped_column(String(2000))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("person.id"))
    owner: Mapped["User"] = relationship("User", back_populates="tasks")
    # NULL = задача не синхронизирована с CRM (CRM был недоступен при создании
    # или задача создана до интеграции). Тип int, а не UUID: CRM присваивает числовые ID.
    crm_task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)


class User(SQLAlchemyBaseUserTable[int], Base):
    # SQLAlchemyBaseUserTable[int]: параметр — тип первичного ключа.
    # Базовый класс добавляет колонки hashed_password, is_active, is_verified, is_superuser.
    # Ниже переопределяем только те, у которых нужно изменить поведение.
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # username = email.split("@")[0], вычисляется в UserManager.create().
    # Хранится в БД отдельно: CRM требует явное поле логина при регистрации.
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    firstname: Mapped[str] = mapped_column(String(255), nullable=False)
    lastname: Mapped[str] = mapped_column(String(255), nullable=False)
    # nullable=True: отчество необязательно — не все пользователи его имеют.
    # Хранится как NULL, а не как пустая строка, чтобы отличать «не указано» от «пусто».
    patronymic: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    registered_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    role_id: Mapped[int] = mapped_column(ForeignKey(Role.id))
    role: Mapped[Role] = relationship("Role")
    hashed_password: Mapped[str] = mapped_column(String(length=1024), nullable=False)
    tasks: Mapped[List["Task"]] = relationship(
        "Task", back_populates="owner", cascade="all, delete-orphan"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # is_superuser не переопределяется: в проекте права задаются через role_id.
    # Поле остаётся в БД через SQLAlchemyBaseUserTable, скрыто из API через UserRead.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
