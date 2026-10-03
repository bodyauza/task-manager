import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.email_service import send_confirmation_code
from src.auth.manager import UserManager, get_user_manager, password_helper_bc
from src.auth.registration_rate_limit import acquire_request_code_slot
from src.auth.user_models import RegistrationPending, User
from src.auth.user_schemas import (
    PASSWORD_ERROR,
    UserCreate,
    is_valid_email_format,
    is_valid_password_format,
)
from src.config import settings
from src.database import get_async_session
from src.openapi_responses import responses
from src.utils.log_utils import mask_email

logger = logging.getLogger(__name__)

registration_router = APIRouter(prefix="/auth", tags=["Registration"])

# Максимум неверных попыток ввода кода: достаточно для опечаток, мало для перебора 10^6 вариантов.
_MAX_ATTEMPTS = 3

# Время жизни кода подтверждения.
_CODE_TTL_MINUTES = 15

# Минимальный интервал между повторными запросами кода для одного email.
_RATE_LIMIT_SECONDS = 60


class _RequestCodeBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"title": "RequestCodeBody"})

    email: str


class _VerifyCodeBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"title": "VerifyCodeBody"})

    email: str
    # Паттерн проверяется на сервере: maxlength/inputmode в HTML — только подсказки UX.
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class _CompleteBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"title": "CompleteRegistrationBody"})

    firstname:  str           = Field(..., min_length=1, max_length=255)
    lastname:   str           = Field(..., min_length=1, max_length=255)
    patronymic: Optional[str] = Field(default=None, max_length=255)
    # max_length=72 — см. src/auth/user_schemas.py::UserCreate.password.
    password:   str = Field(..., min_length=5, max_length=72)


def _issue_reg_token(email: str) -> str:
    """Выдаёт короткоживущий JWT, привязанный к email.

    Подписан отдельным REG_TOKEN_SECRET; поле "purpose" не даёт использовать access-токен вместо него.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub":     email,
        "purpose": "registration",
        "iat":     int(now.timestamp()),
        "exp":     int((now + timedelta(seconds=settings.REG_TOKEN_EXP)).timestamp()),
    }
    return jwt.encode(payload, settings.REG_TOKEN_SECRET, algorithm=settings.algorithm)


def _decode_reg_token(token: str) -> str:
    """Проверяет подпись, срок и purpose JWT; возвращает email из sub. Любая ошибка → HTTP 401."""
    try:
        payload = jwt.decode(
            token,
            settings.REG_TOKEN_SECRET,
            algorithms=[settings.algorithm],
        )
        if payload.get("purpose") != "registration":
            raise ValueError("wrong purpose")
        return payload["sub"]
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="REG_TOKEN_INVALID",
        )


def _now_utc() -> datetime:
    # В БД хранится aware-datetime в UTC; возвращаем aware, чтобы сравнения не падали с TypeError.
    return datetime.now(timezone.utc)


@registration_router.post(
    "/register/request-code",
    status_code=200,
    summary="Шаг 1: отправить код подтверждения на email",
    responses=responses(400, 409, 429, 503, c400="INVALID_EMAIL", c409="EMAIL_ALREADY_REGISTERED", c429="RATE_LIMIT:<секунд> — повторный запрос раньше чем через 60 с (на этот email), либо RATE_LIMIT_IP — исчерпан общий лимит запросов с этого IP", c503="SMTP_ERROR — не удалось отправить письмо, либо RATE_LIMITER_UNAVAILABLE — недоступно хранилище счётчиков лимита по IP (Redis)"),
)
async def request_registration_code(
    request: Request,
    body: _RequestCodeBody,
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Шаг 1 из 3: генерирует 6-значный код и отправляет его на email.

    Код хранится как bcrypt-хеш.
    """
    # Лимит по IP — первая и самая дешёвая проверка: останавливает перебор разных адресов с одного клиента
    # (лимит на один email этого не решает). request.client может быть None — тогда общий ключ "unknown".
    client_ip = request.client.host if request.client is not None else "unknown"
    if not await acquire_request_code_slot(client_ip):
        raise HTTPException(status_code=429, detail="RATE_LIMIT_IP")

    # Нормализуем email до любых проверок.
    email = body.email.lower().strip()

    if not is_valid_email_format(email):
        raise HTTPException(status_code=400, detail="INVALID_EMAIL")

    # Проверка существующего пользователя идёт до rate limit: 409 информативнее, чем 429.
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="EMAIL_ALREADY_REGISTERED")

    now = _now_utc()
    pending = (
        await db.execute(
            select(RegistrationPending).where(RegistrationPending.email == email)
        )
    ).scalar_one_or_none()

    if pending is not None:
        elapsed = (now - pending.created_at).total_seconds()
        if elapsed < _RATE_LIMIT_SECONDS:
            wait = int(_RATE_LIMIT_SECONDS - elapsed)
            # Секунды ожидания передаём в detail — фронт запускает обратный отсчёт.
            raise HTTPException(status_code=429, detail=f"RATE_LIMIT:{wait}")
        # Интервал истёк — заменяем запись; flush() выполняет DELETE до INSERT, чтобы не нарушить UNIQUE по email.
        await db.delete(pending)
        await db.flush()

    # secrets.randbelow исключает modulo bias; :06d дополняет нулями слева.
    code = f"{secrets.randbelow(1_000_000):06d}"
    # bcrypt синхронный — выносим в поток, чтобы не блокировать event loop (даже при rounds=6).
    code_hash = await asyncio.to_thread(password_helper_bc.hash, code)

    db.add(RegistrationPending(
        email=email,
        code_hash=code_hash,
        attempts=0,
        expires_at=now + timedelta(minutes=_CODE_TTL_MINUTES),
        created_at=now,
    ))
    try:
        await db.commit()
    except IntegrityError:
        # Гонка первого запроса: два параллельных запроса оба не видят pending, и второй INSERT падает IntegrityError.
        # Отвечаем как на обычный кулдаун, время ожидания берём из created_at уже вставленной записи.
        await db.rollback()
        winner = (
            await db.execute(select(RegistrationPending).where(RegistrationPending.email == email))
        ).scalar_one_or_none()
        wait = _RATE_LIMIT_SECONDS
        if winner is not None:
            elapsed = (_now_utc() - winner.created_at).total_seconds()
            wait = max(int(_RATE_LIMIT_SECONDS - elapsed), 0)
        raise HTTPException(status_code=429, detail=f"RATE_LIMIT:{wait}")

    # Письмо — после commit: при сбое SMTP запись уже сохранена, пользователь повторит запрос после rate limit и получит 503.
    try:
        await send_confirmation_code(email, code)
    except Exception as exc:
        # Адрес маскируется (ПДн в логах), в том числе в тексте исключения — aiosmtplib возвращает адрес получателя.
        masked = mask_email(email)
        logger.error("SMTP error for %s: %s: %s", masked, type(exc).__name__, str(exc).replace(email, masked))
        raise HTTPException(status_code=503, detail="SMTP_ERROR")

    return {"message": "Code sent"}


@registration_router.post(
    "/register/verify-code",
    status_code=200,
    summary="Шаг 2: подтвердить код",
    description="До 3 попыток, TTL кода 15 минут. При успехе выставляет HttpOnly-куку `reg_token` (20 минут).",
    responses=responses(400, c400="NO_PENDING_REGISTRATION, CODE_EXPIRED, TOO_MANY_ATTEMPTS или INVALID_CODE:<осталось попыток>"),
)
async def verify_registration_code(
    body: _VerifyCodeBody,
    response: Response,
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Шаг 2 из 3: сверяет код, удаляет запись pending (код одноразовый), выдаёт reg_token в HttpOnly-куке."""
    email = body.email.lower().strip()

    pending = (
        await db.execute(
            select(RegistrationPending).where(RegistrationPending.email == email)
        )
    ).scalar_one_or_none()

    if pending is None:
        raise HTTPException(status_code=400, detail="NO_PENDING_REGISTRATION")

    now = _now_utc()

    # Срок проверяем до числа попыток: просроченный код попыткой не считается.
    if now > pending.expires_at:
        await db.delete(pending)
        await db.commit()
        raise HTTPException(status_code=400, detail="CODE_EXPIRED")

    # Лимит попыток и инкремент — одним атомарным UPDATE ... WHERE attempts < _MAX_ATTEMPTS RETURNING: PostgreSQL
    # сериализует конкурентные UPDATE, поэтому суммарно проходит ровно _MAX_ATTEMPTS попыток. Раньше проверка в
    # Python допускала гонку, и лимит снимался параллельными запросами.
    #
    # Инкремент делаем до bcrypt (не тратим время на заблокированный запрос); при верном коде запись всё равно удаляется.
    #
    # pending при исчерпании попыток не удаляем: created_at — точка отсчёта кулдауна, иначе можно обойти rate limit
    # (запросить код → N неверных → сразу новый код).
    new_attempts = (
        await db.execute(
            update(RegistrationPending)
            .where(
                RegistrationPending.id == pending.id,
                RegistrationPending.attempts < _MAX_ATTEMPTS,
            )
            .values(attempts=RegistrationPending.attempts + 1)
            .returning(RegistrationPending.attempts)
        )
    ).scalar_one_or_none()
    if new_attempts is None:
        # WHERE не сработал — лимит исчерпан (параллельным запросом); ничего не менялось, commit не нужен.
        raise HTTPException(status_code=400, detail="TOO_MANY_ATTEMPTS")
    await db.commit()

    # verify_and_update сверяет код с хешем (хеш не обновляем — код одноразовый); bcrypt выносим в поток.
    verified, _ = await asyncio.to_thread(
        password_helper_bc.verify_and_update, body.code, pending.code_hash
    )

    if not verified:
        remaining = _MAX_ATTEMPTS - new_attempts
        # remaining=0: следующая попытка попадёт в TOO_MANY_ATTEMPTS.
        raise HTTPException(status_code=400, detail=f"INVALID_CODE:{remaining}")

    # Код верный — запись удаляем: повторная верификация невозможна.
    await db.delete(pending)
    await db.commit()

    token = _issue_reg_token(email)
    _secure = settings.is_production
    # samesite="strict": кука не уходит при межсайтовых запросах; httponly=True: JS не читает токен.
    response.set_cookie(
        key="reg_token",
        value=token,
        max_age=settings.REG_TOKEN_EXP,
        httponly=True,
        secure=_secure,
        samesite="strict",
    )
    return {"message": "Email confirmed"}


@registration_router.post(
    "/register/complete",
    status_code=201,
    summary="Шаг 3: создать пользователя",
    description="Требует куку `reg_token` из шага 2.",
    responses=responses(401, 409, 422, c401="MISSING_REG_TOKEN — нет куки reg_token или токен недействителен", c409="EMAIL_ALREADY_REGISTERED", c422="Слабый пароль или ошибка валидации полей"),
)
async def complete_registration(
    body: _CompleteBody,
    response: Response,
    reg_token: Optional[str] = Cookie(default=None),
    user_manager: UserManager = Depends(get_user_manager),
) -> dict:
    """Шаг 3 из 3: создаёт пользователя в БД и удаляет reg_token-куку.

    reg_token — единственное доказательство подтверждённого email; подделать его без REG_TOKEN_SECRET нельзя.
    """
    if reg_token is None:
        raise HTTPException(status_code=401, detail="MISSING_REG_TOKEN")

    email = _decode_reg_token(reg_token)

    # Валидация пароля дублируется: UserCreate даёт 422 на английском, здесь — контролируемое сообщение на русском.
    if not is_valid_password_format(body.password):
        raise HTTPException(status_code=422, detail=PASSWORD_ERROR)

    user_create = UserCreate(
        email=email,
        password=body.password,
        firstname=body.firstname.strip(),
        lastname=body.lastname.strip(),
        patronymic=body.patronymic.strip() if body.patronymic else None,
        username=email.split("@")[0],
        is_active=True,
        is_verified=True,
    )

    try:
        await user_manager.create(user_create)
    except Exception as exc:
        from fastapi_users import exceptions as fu_exc
        if isinstance(exc, fu_exc.UserAlreadyExists):
            # Гонка: между verify-code и complete тот же email успели зарегистрировать — 409 вместо 500.
            raise HTTPException(status_code=409, detail="EMAIL_ALREADY_REGISTERED")
        raise

    _secure = settings.is_production
    # Параметры удаления куки (secure, httponly, samesite) должны совпадать с параметрами выдачи.
    response.delete_cookie("reg_token", secure=_secure, httponly=True, samesite="strict")
    return {"message": "Registration complete"}
