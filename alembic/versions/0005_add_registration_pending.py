"""Add registration_pending table

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-03

Таблица для трёхшаговой регистрации с подтверждением email (шаг 1 из 3 —
POST /auth/register/request-code, см. src/auth/registration_endpoints.py):
запись живёт здесь только между «код отправлен» и «код подтверждён/истёк»,
основная таблица person не трогается, пока владение email не доказано.
Ровно одна незавершённая регистрация на email одновременно — отсюда
UniqueConstraint(email) ниже.

Примечание задним числом (см. 0012): эта миграция создаёт на колонке email
сразу два объекта — UniqueConstraint И отдельный обычный (неуникальный)
индекс через create_index. Это избыточно (уникальный constraint уже реализован
PostgreSQL как unique index и полностью покрывает те же запросы) и не совпадает
с тем, что ожидает ORM-модель (RegistrationPending.email с unique=True,
index=True — то есть один объект, а не два); подробный разбор и исправление —
в 0012.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "registration_pending",
        sa.Column("id", sa.Integer(), primary_key=True),
        # email: NOT NULL, уникальность обеспечивается отдельным UniqueConstraint
        # ниже (не через unique=True в самой колонке) — так исторически было
        # выражено в этой ревизии; итоговая схема после 0012 будет другой.
        sa.Column("email", sa.String(255), nullable=False),
        # code_hash — bcrypt-хеш 6-значного кода подтверждения, не сам код в
        # открытом виде: утечка таблицы не позволит восстановить код до истечения
        # expires_at. Длина 1024 — тот же запас, что и person.hashed_password.
        sa.Column("code_hash", sa.String(1024), nullable=False),
        # attempts — сколько раз пользователь ввёл неверный код.
        # При достижении лимита запись удаляется: повторная отправка кода обязательна.
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        # expires_at — момент, после которого код недействителен независимо от
        # attempts (TTL кода подтверждения, см. _CODE_TTL_MINUTES в коде роутера).
        # TIMESTAMP без часового пояса на этом этапе — исправляется в 0007
        # (переход на TIMESTAMPTZ).
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
        # UniqueConstraint, а не unique=True на колонке: PostgreSQL реализует оба
        # варианта одинаково (через уникальный индекс), это два синтаксиса одного
        # результата — здесь выбран явный именованный constraint.
        sa.UniqueConstraint("email", name="uq_registration_pending_email"),
    )
    # Второй, отдельный, НЕуникальный индекс на ту же колонку email — избыточен
    # поверх уникального constraint выше (см. docstring ревизии и разбор в 0012).
    # Сохранён здесь для точного соответствия исторической ревизии; не трогаем
    # прошлые миграции — только 0012 умеет чинить итоговое состояние правильно.
    op.create_index("ix_registration_pending_email", "registration_pending", ["email"])


def downgrade() -> None:
    op.drop_index("ix_registration_pending_email", table_name="registration_pending")
    op.drop_table("registration_pending")  # UniqueConstraint падает вместе с таблицей
