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
    # Обратная сторона many-to-many к User через user_role — см. её определение ниже.
    users: Mapped[List["User"]] = relationship("User", secondary="user_role", back_populates="roles")


# Таблица-связка many-to-many между person и role. Составной первичный ключ
# (person_id, role_id) одновременно даёт уникальность пары — назначить одну и ту же
# роль пользователю дважды физически нельзя, отдельный UniqueConstraint не нужен.
# Обычная Table, а не ORM-класс (association object): под текущие требования
# (только сам факт принадлежности к роли, без доп. данных вроде assigned_at/
# assigned_by) полноценная модель избыточна — если такие поля понадобятся,
# Table меняется на ORM-класс без изменения DDL.
user_role = Table(
    "user_role",
    Base.metadata,
    Column("person_id", Integer, ForeignKey("person.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id",   Integer, ForeignKey("role.id",   ondelete="CASCADE"), primary_key=True),
)


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
    # many-to-many через user_role: один пользователь может иметь несколько ролей.
    # secondary=user_role — Python-объект уже определён выше (не строка), в отличие
    # от Role.users, который вынужден ссылаться на "user_role" по имени, т.к. в
    # момент определения класса Role сама таблица user_role ещё не создана.
    roles: Mapped[List["Role"]] = relationship("Role", secondary=user_role, back_populates="users")
    hashed_password: Mapped[str] = mapped_column(String(length=1024), nullable=False)
    tasks: Mapped[List["Task"]] = relationship(
        "Task", back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # is_superuser не переопределяется: в проекте права задаются через roles/require_role().
    # Поле остаётся в БД через SQLAlchemyBaseUserTable, скрыто из API через UserRead.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    @property
    def role_ids(self) -> List[int]:
        # Удобство для сериализации в UserRead (from_attributes читает role_ids как
        # обычный атрибут). ВАЖНО: требует, чтобы self.roles уже был загружен явно
        # (selectinload/options=) в вызывающем коде — обращение к self.roles здесь
        # НЕ делает await и не может лениво подгрузить связь в async-контексте;
        # необращение к этому правилу роняет запрос MissingGreenlet.
        return [role.id for role in self.roles]
