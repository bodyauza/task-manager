from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


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
