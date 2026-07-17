"""Initial schema: role, person, task tables

Revision ID: 0001
Revises:
Create Date: 2026-06-18

Первая ревизия проекта — создаёт три таблицы с нуля (`alembic_version` в БД
после этого содержит "0001", схема ещё не знает про подзадачи, файлы, CRM
или трёхшаговую регистрацию — все они появляются позже, миграциями 0003-0012).
Начальные данные (роли "user"/"admin") сюда не входят: Alembic управляет
схемой, а не данными — они вставляются идемпотентно в `create_initial_roles()`
при старте приложения (`src/main.py::lifespan`), см. `docs/task-manager-documentation.md`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision/down_revision — идентификаторы ревизии в служебной таблице alembic_version.
# down_revision=None означает, что это самая первая ревизия в цепочке (родителя нет);
# `alembic upgrade head` начинает применение отсюда на пустой БД.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # role — справочник ролей. Строки ("user"/"admin") вставляются не здесь
    # (см. docstring выше), эта миграция только создаёт таблицу и её структуру.
    op.create_table(
        "role",
        sa.Column("id", sa.Integer(), primary_key=True),
        # name — не UNIQUE и не индексирован: таблица маленькая (справочник из
        # нескольких строк), поиск по имени роли в коде проекта не выполняется.
        sa.Column("name", sa.String(), nullable=False),
        # permissions — JSON-массив строк, например ["read", "write", "delete"].
        # nullable=True: на момент этой ревизии предполагалось, что права могут
        # отсутствовать; на практике create_initial_roles() всегда их задаёт.
        sa.Column("permissions", sa.JSON(), nullable=True),
    )

    # person — таблица пользователей. Названа "person", а не "user": "user" —
    # зарезервированное слово в PostgreSQL, использовать его как имя таблицы
    # без кавычек нельзя. FastAPI Users ожидает эту таблицу под именем модели User
    # (SQLAlchemyBaseUserTable[int]), реальное имя задаётся через __tablename__.
    op.create_table(
        "person",
        sa.Column("id", sa.Integer(), primary_key=True),
        # email: NOT NULL, но без UNIQUE на этом этапе — уникальность на уровне
        # БД добавляется только в 0002 (uq_person_email). До 0002 (и, как выяснилось
        # значительно позже — фактически до 0012, см. её docstring) уникальность
        # обеспечивало только приложение через SELECT-проверку перед INSERT.
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        # registered_at: nullable=True на этом этапе — впоследствии в 0007 колонка
        # переводится с TIMESTAMP (без часового пояса) на TIMESTAMPTZ; серверный
        # default появляется на уровне ORM-модели (Python datetime.now(timezone.utc)),
        # не на уровне БД.
        sa.Column("registered_at", sa.TIMESTAMP(), nullable=True),
        # role_id: nullable=True — на момент создания пользователя роль может быть
        # ещё не назначена; в текущем коде UserManager.create() всегда проставляет
        # role_id=1 явно, так что на практике колонка всегда заполнена.
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("role.id"), nullable=True),
        # hashed_password: длина 1024 — с большим запасом под bcrypt-хеш (реальная
        # длина бы уложилась в ~60 символов), запас на случай смены алгоритма хеширования.
        sa.Column("hashed_password", sa.String(length=1024), nullable=False),
        # is_active/is_superuser/is_verified — обязательные поля FastAPI Users
        # (SQLAlchemyBaseUserTable). server_default гарантирует значение по умолчанию
        # и на уровне БД (не только на уровне Python), в том числе для прямых INSERT
        # в обход ORM.
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        # is_superuser не используется в бизнес-логике проекта (права заданы через
        # role_id/role.permissions), но обязателен как часть контракта FastAPI Users.
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # ix_person_id — явный индекс на первичный ключ. PostgreSQL и так создаёт
    # уникальный индекс под PRIMARY KEY автоматически; эта строка — по сути дубль,
    # возникающий из-за index=True на mapped_column(primary_key=True) в ORM-модели.
    op.create_index("ix_person_id", "person", ["id"])

    # task — таблица задач. owner_id связывает задачу с её создателем (person),
    # обратной связи "person видит все свои задачи" на уровне БД нет — фильтрация
    # по владельцу в API не реализована (см. «Векторы развития» в документации проекта).
    op.create_table(
        "task",
        sa.Column("id", sa.Integer(), primary_key=True),
        # title: VARCHAR(255) на этом этапе; сужается до VARCHAR(100) в 0004,
        # когда выяснилось, что CRM «Руководитель» принимает не более 100 символов.
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        # owner_id: NOT NULL с самого начала — задача без владельца не имеет смысла;
        # ON DELETE не задан явно → по умолчанию RESTRICT (удаление person с задачами
        # запрещено на уровне БД, если бы такое удаление вообще выполнялось напрямую).
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("person.id"), nullable=False),
    )
    op.create_index("ix_task_id", "task", ["id"])
    # ix_task_title — ускоряет точное совпадение по title; в 0002 сюда же добавляется
    # UNIQUE-ограничение (позже переезжает на составной ключ в 0009).
    op.create_index("ix_task_title", "task", ["title"])
    # ix_task_description — индекс на VARCHAR(2000), избыточен (описание не участвует
    # ни в одном WHERE/ORDER BY проекта) и занимает место без пользы для запросов;
    # удаляется уже в следующей миграции (0002) как явная находка code review.
    op.create_index("ix_task_description", "task", ["description"])
    # uq_task_title — глобальная уникальность title по всем пользователям.
    # Слишком строго для реального использования (два разных пользователя не могут
    # назвать задачи одинаково) — заменяется на составной UNIQUE(title, owner_id)
    # в миграции 0009.
    op.create_unique_constraint("uq_task_title", "task", ["title"])


def downgrade() -> None:
    # Порядок обратный upgrade(): сначала таблицы, ссылающиеся через FK на другие
    # (task → person → role), иначе PostgreSQL откажется удалять "role"/"person"
    # с сообщением "cannot drop table because other objects depend on it".
    op.drop_table("task")
    op.drop_table("person")
    op.drop_table("role")
