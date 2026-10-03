from datetime import datetime, timezone
from typing import List, Optional

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTable
from sqlalchemy import TIMESTAMP, Boolean, Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base


class RegistrationPending(Base):
    __tablename__ = "registration_pending"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    code_hash: Mapped[str] = mapped_column(String(1024), nullable=False)
    # attempts: число неверных попыток; при достижении лимита запись блокируется.
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
    # Обратная сторона many-to-many к User через user_role.
    users: Mapped[List["User"]] = relationship("User", secondary="user_role", back_populates="roles")

    def __str__(self) -> str:
        # Подпись в ajax-полях sqladmin (str(model)).
        return self.name


# Связка many-to-many person ↔ role: составной PK (person_id, role_id) уже гарантирует уникальность пары.
# Обычная Table, а не ORM-класс — доп. данных о назначении нет.
user_role = Table(
    "user_role",
    Base.metadata,
    Column("person_id", Integer, ForeignKey("person.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id",   Integer, ForeignKey("role.id",   ondelete="CASCADE"), primary_key=True),
)


class User(SQLAlchemyBaseUserTable[int], Base):
    # SQLAlchemyBaseUserTable[int] добавляет hashed_password, is_active, is_verified, is_superuser; ниже переопределяем только нужное.
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # username = email.split("@")[0] (UserManager.create()); хранится отдельно.
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    firstname: Mapped[str] = mapped_column(String(255), nullable=False)
    lastname: Mapped[str] = mapped_column(String(255), nullable=False)
    # nullable: отчество необязательно; NULL отличает «не указано» от пустой строки.
    patronymic: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    registered_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    # many-to-many через user_role: у пользователя может быть несколько ролей.
    roles: Mapped[List["Role"]] = relationship("Role", secondary=user_role, back_populates="users")
    hashed_password: Mapped[str] = mapped_column(String(length=1024), nullable=False)
    tasks: Mapped[List["Task"]] = relationship(
        "Task", back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # is_superuser не переопределяется: права задаются через roles/require_role(); поле скрыто из API через UserRead.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __str__(self) -> str:
        # Подпись владельца в ajax-полях sqladmin (str(model)).
        return self.email

    @property
    def role_ids(self) -> List[int]:
        # Для сериализации UserRead. self.roles должен быть уже загружен (selectinload/options=): здесь нет await,
        # иначе MissingGreenlet.
        return [role.id for role in self.roles]
