from datetime import datetime, timezone
from typing import List

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTable
from sqlalchemy import JSON, TIMESTAMP, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base

class Role(Base):
    __tablename__ = "role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    permissions: Mapped[list] = mapped_column(JSON, nullable=True)
    # permissions: Mapped[list[str]] = mapped_column(ARRAY(String(100)))


class Task(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), index=True, unique=True)
    description: Mapped[str] = mapped_column(String(2000))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("person.id"))
    owner: Mapped["User"] = relationship("User", back_populates="tasks")

class User(SQLAlchemyBaseUserTable[int], Base):
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    role_id: Mapped[int] = mapped_column(ForeignKey(Role.id))
    role: Mapped[Role] = relationship("Role")
    hashed_password: Mapped[str] = mapped_column(String(length=1024), nullable=False)
    tasks: Mapped[List["Task"]] = relationship("Task", back_populates="owner", cascade="all, delete-orphan")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
