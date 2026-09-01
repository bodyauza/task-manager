import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.email_service import send_confirmation_code
from src.auth.manager import UserManager, get_user_manager, password_helper_bc
from src.auth.user_models import RegistrationPending, User
from src.auth.user_schemas import (
    PASSWORD_ERROR,
    UserCreate,
    is_valid_email_format,
    is_valid_password_format,
)
from src.config import settings
from src.crm.client import CRMUnavailableError
from src.database import get_async_session

logger = logging.getLogger(__name__)

registration_router = APIRouter(prefix="/auth", tags=["Registration"])

# Максимальное число неверных попыток ввода кода до удаления записи.
# 3 попытки — достаточно для опечаток, мало для перебора 6-значного кода (10^6 вариантов).
_MAX_ATTEMPTS = 3

# Время жизни кода подтверждения. После истечения запись удаляется, код недействителен.
_CODE_TTL_MINUTES = 15

# Минимальный интервал между повторными запросами кода для одного email.
# Ограничивает рассылку писем: без rate limit один адрес можно атаковать непрерывно.
_RATE_LIMIT_SECONDS = 60


# ─── Internal schemas ─────────────────────────────────────────────────────────

class _RequestCodeBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"title": "RequestCodeBody"})

    email: str


class _VerifyCodeBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"title": "VerifyCodeBody"})

    email: str
    # Паттерн проверяется на сервере: HTML-атрибуты maxlength=6, inputmode=numeric
    # — только UX-подсказки, браузер их не гарантирует.
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class _CompleteBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"title": "CompleteRegistrationBody"})

    firstname:  str           = Field(..., min_length=1, max_length=255)
    lastname:   str           = Field(..., min_length=1, max_length=255)
    # patronymic не является обязательным полем: отсутствие в теле запроса
    # не вызывает ошибку валидации — Pydantic подставляет None по умолчанию.
    patronymic: Optional[str] = Field(default=None, max_length=255)
    # max_length=72: bcrypt учитывает только первые 72 байта пароля и молча
    # обрезает остальное — см. пояснение в src/auth/user_schemas.py::UserCreate.password.
    password:   str = Field(..., min_length=5, max_length=72)


# ─── JWT helpers ──────────────────────────────────────────────────────────────

def _issue_reg_token(email: str) -> str:
    """Выдаёт короткоживущий JWT, привязанный к адресу email.

    Токен подписывается отдельным секретом (REG_TOKEN_SECRET), не связанным
    с access/refresh-секретами: компрометация одного ключа не затрагивает другие.
    Поле "purpose" исключает переиспользование токена на других эндпоинтах —
    access-токен с тем же sub не пройдёт валидацию в _decode_reg_token.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub":     email,               # subject — кому выдан токен
        "purpose": "registration",      # защита от повторного использования в др. flow
        "iat":     int(now.timestamp()),
        "exp":     int((now + timedelta(seconds=settings.REG_TOKEN_EXP)).timestamp()),
    }
    return jwt.encode(payload, settings.REG_TOKEN_SECRET, algorithm=settings.algorithm)


def _decode_reg_token(token: str) -> str:
    """Проверяет подпись, срок действия и назначение JWT; возвращает email из sub.

    jwt.decode автоматически проверяет exp — просроченный токен бросает
    ExpiredSignatureError, которая перехватывается вместе с остальными
    InvalidTokenError и превращается в HTTP 401.
    Проверка purpose защищает от подстановки access-токена в reg_token-куку.
    """
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
    # TIMESTAMP(timezone=True) в БД хранит aware-datetime в UTC.
    # Возвращаем aware datetime, чтобы сравнения (now > pending.expires_at)
    # не бросали TypeError при вычитании naive и aware объектов.
    return datetime.now(timezone.utc)


# ─── Endpoints ────────────────────────────────────────────────────────────────

@registration_router.post("/register/request-code", status_code=200)
async def request_registration_code(
    body: _RequestCodeBody,
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Шаг 1 из 3. Генерирует 6-значный код и отправляет его на указанный email.

    Код хранится в registration_pending не в открытом виде, а в виде bcrypt-хеша:
    утечка таблицы не позволит восстановить код до истечения 15 минут.
    """
    # lower().strip(): нормализация перед любыми проверками — дубликат
    # «User@Example.COM » и «user@example.com» должны давать один и тот же результат.
    email = body.email.lower().strip()

    if not is_valid_email_format(email):
        raise HTTPException(status_code=400, detail="INVALID_EMAIL")

    # Проверка на существующего пользователя выполняется до rate-limit-проверки:
    # сообщение EMAIL_ALREADY_REGISTERED (409) информативнее, чем RATE_LIMIT (429).
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
            # Количество секунд ожидания передаётся в detail: фронтенд
            # читает его и запускает обратный отсчёт без дополнительного запроса.
            raise HTTPException(status_code=429, detail=f"RATE_LIMIT:{wait}")
        # Интервал истёк — старая запись заменяется новой.
        # flush() применяет DELETE до INSERT в той же транзакции,
        # чтобы не нарушить UNIQUE-ограничение на колонке email.
        await db.delete(pending)
        await db.flush()

    # secrets.randbelow исключает модульное смещение (modulo bias), которое
    # возникает при random.randint(): каждое из 10^6 значений равновероятно.
    # :06d — дополняет нулями слева (например, 42 → "000042").
    code = f"{secrets.randbelow(1_000_000):06d}"
    code_hash = password_helper_bc.hash(code)

    db.add(RegistrationPending(
        email=email,
        code_hash=code_hash,
        attempts=0,
        expires_at=now + timedelta(minutes=_CODE_TTL_MINUTES),
        created_at=now,
    ))
    await db.commit()

    # Отправка письма — после commit: если SMTP упадёт, запись в БД уже зафиксирована
    # и пользователь может повторить запрос после истечения rate-limit (60 сек).
    # До commit откатывать было бы нечего — но при сбое SMTP мы вернём 503,
    # а не оставим пользователя без объяснений.
    try:
        await send_confirmation_code(email, code)
    except Exception as exc:
        logger.error("SMTP error for %s: %s", email, exc)
        raise HTTPException(status_code=503, detail="SMTP_ERROR")

    return {"message": "Code sent"}


@registration_router.post("/register/verify-code", status_code=200)
async def verify_registration_code(
    body: _VerifyCodeBody,
    response: Response,
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Шаг 2 из 3. Сверяет код, удаляет запись pending, выдаёт reg_token в HttpOnly-куке.

    После успешной верификации запись удаляется — код одноразовый.
    reg_token используется на шаге 3 как доказательство того, что email подтверждён.
    """
    email = body.email.lower().strip()

    pending = (
        await db.execute(
            select(RegistrationPending).where(RegistrationPending.email == email)
        )
    ).scalar_one_or_none()

    if pending is None:
        raise HTTPException(status_code=400, detail="NO_PENDING_REGISTRATION")

    now = _now_utc()

    # Проверка срока — до проверки числа попыток: просроченный код не считается попыткой.
    if now > pending.expires_at:
        await db.delete(pending)
        await db.commit()
        raise HTTPException(status_code=400, detail="CODE_EXPIRED")

    # Лимит проверяется до verify_and_update: bcrypt-хеширование занимает ~0.5 с,
    # эту задержку нельзя тратить на заведомо заблокированный запрос.
    #
    # НЕ удаляем pending здесь (в отличие от ветки CODE_EXPIRED выше): created_at
    # этой записи — точка отсчёта для 60-секундного кулдауна в
    # request_registration_code(). Если удалить запись сразу при исчерпании
    # попыток, следующий request-code для того же email не найдёт pending и
    # пропустит проверку кулдауна целиком — рабочий обход rate-limit'а
    # (запросить код → 4×неверный код → мгновенный новый код без ожидания).
    # Запись останется заблокированной (эта же проверка сработает повторно на
    # любой следующий verify-code) и будет корректно заменена свежей только
    # когда пройдут все 60 секунд — тем же путём, что и обычная замена
    # просроченного кулдауна в request_registration_code().
    if pending.attempts >= _MAX_ATTEMPTS:
        raise HTTPException(status_code=400, detail="TOO_MANY_ATTEMPTS")

    # verify_and_update: сверяет код с хешем и при необходимости возвращает
    # обновлённый хеш (если параметры хеширования устарели — rehash на лету).
    # Второй элемент кортежа (updated_hash) здесь не нужен: код одноразовый.
    verified, _ = password_helper_bc.verify_and_update(body.code, pending.code_hash)

    if not verified:
        pending.attempts += 1
        remaining = _MAX_ATTEMPTS - pending.attempts
        await db.commit()
        # remaining=0: следующая (4-я) попытка попадёт в ветку TOO_MANY_ATTEMPTS выше.
        raise HTTPException(status_code=400, detail=f"INVALID_CODE:{remaining}")

    # Код верный — запись сразу удаляется: повторная верификация того же кода невозможна.
    await db.delete(pending)
    await db.commit()

    token = _issue_reg_token(email)
    _secure = settings.is_production
    # samesite="strict": кука не отправляется при межсайтовых запросах —
    # злоумышленник не может выманить браузер завершить чужую регистрацию.
    # httponly=True: JS не может прочитать токен через document.cookie.
    response.set_cookie(
        key="reg_token",
        value=token,
        max_age=settings.REG_TOKEN_EXP,
        httponly=True,
        secure=_secure,
        samesite="strict",
    )
    return {"message": "Email confirmed"}


@registration_router.post("/register/complete", status_code=201)
async def complete_registration(
    body: _CompleteBody,
    response: Response,
    reg_token: Optional[str] = Cookie(default=None),
    user_manager: UserManager = Depends(get_user_manager),
) -> dict:
    """Шаг 3 из 3. Создаёт пользователя в БД и CRM; удаляет reg_token-куку.

    reg_token из HttpOnly-куки — единственное доказательство подтверждённого email.
    Подделать его без знания REG_TOKEN_SECRET невозможно: JWT подписан HMAC-SHA256.
    Даже если злоумышленник подставит произвольную строку в куку reg_token,
    _decode_reg_token выбросит исключение при проверке подписи → HTTP 401.
    """
    if reg_token is None:
        raise HTTPException(status_code=401, detail="MISSING_REG_TOKEN")

    # _decode_reg_token проверяет подпись, срок действия и поле purpose.
    # При любом нарушении бросает HTTP 401 — до создания пользователя дело не доходит.
    email = _decode_reg_token(reg_token)

    # Валидация пароля дублируется здесь (помимо UserCreate-валидатора):
    # UserCreate бросает ValidationError, который fastapi-users преобразует в 422
    # с деталями на английском. Явная проверка даёт контролируемое сообщение на русском.
    if not is_valid_password_format(body.password):
        raise HTTPException(status_code=422, detail=PASSWORD_ERROR)

    user_create = UserCreate(
        email=email,
        password=body.password,
        firstname=body.firstname.strip(),
        lastname=body.lastname.strip(),
        # patronymic.strip() — убирает пробелы по краям; None если поле не передано.
        patronymic=body.patronymic.strip() if body.patronymic else None,
        username=email.split("@")[0],
        is_active=True,
        is_verified=True,
    )

    try:
        # user_manager.create: регистрирует в CRM → затем INSERT в person.
        await user_manager.create(user_create)
    except CRMUnavailableError:
        # Перевод доменного исключения в HTTP-ответ — граница ответственности
        # эндпоинта, а не UserManager (см. auth/manager.py::create).
        raise HTTPException(status_code=503, detail="CRM_UNAVAILABLE")
    except Exception as exc:
        from fastapi_users import exceptions as fu_exc
        if isinstance(exc, fu_exc.UserAlreadyExists):
            # Гонка: между verify-code и complete другой поток успел зарегистрировать
            # тот же email. Возвращаем 409 вместо 500.
            raise HTTPException(status_code=409, detail="EMAIL_ALREADY_REGISTERED")
        raise

    _secure = settings.is_production
    # Удаление куки: браузер получает Set-Cookie с max_age=0 и немедленно её удаляет.
    # Параметры (secure, httponly, samesite) должны совпадать с теми, что были при выдаче —
    # без них некоторые браузеры игнорируют директиву удаления.
    response.delete_cookie("reg_token", secure=_secure, httponly=True, samesite="strict")
    return {"message": "Registration complete"}
