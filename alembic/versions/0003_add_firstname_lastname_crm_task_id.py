"""Add firstname, lastname to person; add crm_task_id to task

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-01

Первая ревизия, связанная с интеграцией CRM «Руководитель»: CRM требует
имя/фамилию при регистрации пользователя (entity_id=1, поля firstname/lastname),
а задачи начинают синхронизироваться с CRM-сущностью «Задачи» (entity_id=29) —
для этого нужен способ хранить CRM-ID локально.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # firstname/lastname — новые обязательные (NOT NULL) колонки на таблице,
    # в которой уже могут быть строки (пользователи, зарегистрированные до этой
    # миграции). ALTER TABLE ADD COLUMN NOT NULL без DEFAULT на непустой таблице
    # упал бы ошибкой — PostgreSQL не может заполнить NOT NULL-колонку для уже
    # существующих строк значением "из ниоткуда".
    # server_default="" — временное значение для существующих строк ИМЕННО на
    # уровне БД (а не Python-default, который сработал бы только для новых INSERT
    # через ORM). После миграции NOT NULL соблюдён формально, но данные для старых
    # пользователей содержательно пустые — их заполняет администратор вручную или
    # разовым скриптом; сама миграция это не делает (не её ответственность).
    op.add_column(
        "person",
        sa.Column("firstname", sa.String(255), nullable=False, server_default=""),
    )
    op.add_column(
        "person",
        sa.Column("lastname", sa.String(255), nullable=False, server_default=""),
    )
    # crm_task_id — nullable=True, в отличие от firstname/lastname выше: здесь NULL
    # не «временное значение до ручного заполнения», а полноценное постоянное
    # состояние — "задача не синхронизирована с CRM" (CRM был недоступен при
    # создании задачи, или задача создана до появления этой колонки). Поэтому
    # server_default не нужен: NULL по умолчанию для ADD COLUMN nullable=True
    # PostgreSQL проставляет сам, без явного DEFAULT.
    op.add_column(
        "task",
        sa.Column("crm_task_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    # Порядок обратный upgrade(): удаляем последнюю добавленную колонку первой
    # (здесь порядок между task/person не принципиален — они в разных таблицах
    # и не связаны FK друг с другом через эти три колонки, но соблюдён для
    # единообразия с остальными миграциями).
    op.drop_column("task", "crm_task_id")
    op.drop_column("person", "lastname")
    op.drop_column("person", "firstname")
