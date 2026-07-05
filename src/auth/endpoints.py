import logging
from typing import Optional

from fastapi import Cookie, Depends, APIRouter, Response, status, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi_users import models

from src.auth.auth_config import (auth_backend, current_user,
                                  get_access_strategy, get_refresh_strategy,
                                  refresh_cookie_transport)
from src.auth.manager import UserManager, get_user_manager
from src.auth.user_schemas import is_valid_email_format, is_valid_password_format, PASSWORD_ERROR

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])


def _apply_transport_cookies(target_response: Response, transport_response: Response) -> None:
    # fastapi-users возвращает Response с Set-Cookie заголовками.
    # JSONResponse/RedirectResponse не наследуют их автоматически — переносим явно.
    # getlist() возвращает все Set-Cookie значения, если кук несколько.
    for value in transport_response.headers.getlist("set-cookie"):
        target_response.headers.append("set-cookie", value)


@auth_router.post("/login")
async def login(
        credentials: OAuth2PasswordRequestForm = Depends(),
        user_manager: UserManager = Depends(get_user_manager),
):
    # Предварительная валидация формата снижает нагрузку на БД при явно невалидных данных.
    if not is_valid_email_format(credentials.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email format",
        )
    if not is_valid_password_format(credentials.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=PASSWORD_ERROR,
        )

    user = await user_manager.authenticate(credentials)

    if user is None or not user.is_active:
        # Единый код ошибки для «нет пользователя» и «неверный пароль»:
        # раздельные коды позволяют атакующему перечислять зарегистрированные email.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LOGIN_BAD_CREDENTIALS",
        )

    # Проверка наличия записи в CRM при каждом входе: регистрация в CRM предшествует
    # INSERT в person, но в случае ручного добавления в БД или сбоя при регистрации
    # запись в CRM может отсутствовать — вход блокируется с кодом 403.
    from src.crm.user_service import CRMUserSelector

    try:
        selector = CRMUserSelector()
        crm_user = await selector.find_user_by_email(user.email)
    except Exception as exc:
        logger.error("CRM check failed for user %d (%s): %s", user.id, user.email, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CRM_UNAVAILABLE",
        )

    if crm_user is None:
        logger.warning(
            "Login blocked for user %d (%s): record not found in CRM",
            user.id, user.email,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not registered in CRM. Contact administrator.",
        )

    logger.info(
        "CRM check passed for user %d: crm_record_id=%s", user.id, crm_user.get("id")
    )

    json_response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Login successful"},
    )

    # access_token  (30 мин) — краткосрочный, используется в каждом запросе.
    # refresh_token (7 дней) — долгосрочный, хранится отдельно; подписан другим секретом,
    # поэтому кража access_token не позволяет продлить сессию через /auth/access-token.
    access_cookie_response = await auth_backend.login(strategy=get_access_strategy(), user=user)
    _apply_transport_cookies(json_response, access_cookie_response)

    refresh_strategy = get_refresh_strategy()
    refresh_token = await refresh_strategy.write_token(user)
    refresh_cookie_response = await refresh_cookie_transport.get_login_response(refresh_token)
    _apply_transport_cookies(json_response, refresh_cookie_response)

    return json_response


@auth_router.post("/access-token")
async def get_access_token(
        refresh_token: Optional[str] = Cookie(default=None),
        user_manager: UserManager = Depends(get_user_manager),
):
    # Вызывается клиентским JS при получении 401 на любом защищённом эндпоинте.
    # Читает refresh_token из HttpOnly-куки (JS не имеет к ней доступа напрямую).
    if refresh_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing refresh token",
        )

    refresh_strategy = get_refresh_strategy()
    user = await refresh_strategy.read_token(refresh_token, user_manager)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    json_response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Access token successfully updated!"},
    )

    access_cookie_response = await auth_backend.login(strategy=get_access_strategy(), user=user)
    _apply_transport_cookies(json_response, access_cookie_response)

    return json_response


@auth_router.post("/do-logout")
async def do_logout(user: models.UP = Depends(current_user)):
    # Форм-based вариант выхода: браузер POST-ом отправляет форму (не fetch),
    # поэтому ответ — 303 See Other, а не JSON 200.
    # 303 заставляет браузер перейти на GET "/" — это исключает повторную отправку
    # формы при нажатии «Назад» (в отличие от 302).
    redirect_response = RedirectResponse(url="/", status_code=303)

    access_logout_response = await auth_backend.transport.get_logout_response()
    _apply_transport_cookies(redirect_response, access_logout_response)

    refresh_logout_response = await refresh_cookie_transport.get_logout_response()
    _apply_transport_cookies(redirect_response, refresh_logout_response)

    return redirect_response


@auth_router.post("/logout")
async def logout(
        user: models.UP = Depends(current_user),
):
    # JS-вариант выхода: fetch-запрос из profile.js ждёт JSON 200,
    # после чего JS выполняет window.location.replace('/').
    json_response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Successfully logged out"},
    )

    access_logout_response = await auth_backend.transport.get_logout_response()
    _apply_transport_cookies(json_response, access_logout_response)

    refresh_logout_response = await refresh_cookie_transport.get_logout_response()
    _apply_transport_cookies(json_response, refresh_logout_response)

    return json_response
