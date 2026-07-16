"""Limit task.title to 100 chars; drop ix_task_description if exists

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-03

CRM «Руководитель» ограничивает поле «Название задачи» 100 символами
(field_317, entity_id=29) — задача с более длинным title не проходила бы
синхронизацию. Раз CRM — жёсткое ограничение внешней системы, локальная
схема (и Pydantic-валидация TaskCreate.title) сужена до того же предела,
чтобы несовпадение обнаруживалось на этапе валидации запроса (422), а не
как непрозрачная ошибка CRM-синхронизации постфактум.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ix_task_description создан в 0001 и должен быть удалён в 0002 — то есть
    # к этой ревизии его в нормальной истории миграций уже не должно быть.
    # DROP INDEX IF EXISTS — не op.drop_index(): последний упал бы с ошибкой,
    # если индекса нет, а IF EXISTS делает операцию безопасной независимо от
    # фактического состояния БД (например, если 0002 применялась к БД, где
    # индекса и так не было — тот же класс несоответствий, что нашла 0012).
    op.execute("DROP INDEX IF EXISTS ix_task_description")

    # Сужение VARCHAR(255) → VARCHAR(100): значения длиннее 100 символов в БД отсутствуют
    # (Pydantic-валидация с предыдущей версией допускала до 255, но CRM принимает до 100).
    # ALTER COLUMN TYPE на непустой таблице с более узким VARCHAR безопасен только
    # если фактические данные укладываются в новый предел — иначе PostgreSQL
    # откажет с ошибкой "value too long for type character varying(100)". Явной
    # проверки/трансформации данных здесь нет, потому что на момент миграции их
    # длина уже была проверена вручную.
    op.alter_column(
        "task", "title",
        type_=sa.String(100),
        existing_type=sa.String(255),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Расширение обратно до 255 безопасно всегда (более широкий VARCHAR принимает
    # любые значения, укладывающиеся в узкий) — в отличие от upgrade, здесь нет
    # риска потери данных.
    op.alter_column(
        "task", "title",
        type_=sa.String(255),
        existing_type=sa.String(100),
        existing_nullable=False,
    )
    # Восстанавливаем индекс, который upgrade() удалил (даже если исходно, до этой
    # миграции, его уже не было — downgrade должен быть симметричен объявленному
    # действию upgrade, а не фактическому состоянию конкретной БД).
    op.create_index("ix_task_description", "task", ["description"])
