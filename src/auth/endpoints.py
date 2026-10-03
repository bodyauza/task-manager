from typing import Optional

from fastapi import Cookie, Depends, APIRouter, Response, status, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse, RedirectResponse

from src.auth.auth_config import (auth_backend, get_access_strategy,
                                  get_refresh_strategy,
                                  refresh_cookie_transport)
from src.auth.manager import UserManager, get_user_manager
from src.auth.user_schemas import is_valid_email_format
from src.openapi_responses import responses

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])


def _apply_transport_cookies(target_response: Response, transport_response: Response) -> None:
    # fastapi-users возвращает Response с Set-Cookie; JSONResponse/RedirectResponse их не наследуют — переносим явно.
    for value in transport_response.headers.getlist("set-cookie"):
        target_response.headers.append("set-cookie", value)


@auth_router.post(
    "/login",
    summary="Вход",
    description="`application/x-www-form-urlencoded`: `username` (email) и `password`. Выставляет куки `access_token` и `refresh_token`.",
    responses=responses(400, c400="Invalid email format или LOGIN_BAD_CREDENTIALS"),
)
async def login(
        credentials: OAuth2PasswordRequestForm = Depends(),
        user_manager: UserManager = Depends(get_user_manager),
):
    # Формат email проверяем заранее, чтобы не нагружать БД. Формат пароля намеренно не проверяем: логин сверяет
    # существующий секрет с хешем, а правила пароля при смене PASSWORD_REGEX не должны блокировать вход.
    if not is_valid_email_format(credentials.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email format",
        )

    user = await user_manager.authenticate(credentials)

    if user is None or not user.is_active:
        # Единый код ошибки для «нет пользователя» и «неверный пароль» — иначе можно перечислять email.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LOGIN_BAD_CREDENTIALS",
        )

    json_response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Login successful"},
    )

    # access_token (30 мин) — на каждый запрос; refresh_token (7 дней) подписан другим секретом.
    access_cookie_response = await auth_backend.login(strategy=get_access_strategy(), user=user)
    _apply_transport_cookies(json_response, access_cookie_response)

    refresh_strategy = get_refresh_strategy()
    refresh_token = await refresh_strategy.write_token(user)
    refresh_cookie_response = await refresh_cookie_transport.get_login_response(refresh_token)
    _apply_transport_cookies(json_response, refresh_cookie_response)

    return json_response


@auth_router.post(
    "/access-token",
    summary="Обновить access_token по refresh_token",
    responses=responses(401, c401="Missing refresh token или Invalid or expired refresh token"),
)
async def get_access_token(
        refresh_token: Optional[str] = Cookie(default=None),
        user_manager: UserManager = Depends(get_user_manager),
):
    # Вызывается клиентским JS при 401; читает refresh_token из HttpOnly-куки.
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


@auth_router.post(
    "/do-logout",
    summary="Выход (форм-вариант)",
    description="Удаляет обе куки и отвечает `303` на `/`.",
)
async def do_logout():
    # Без Depends(current_user): логаут обязан очищать куки, даже если access_token (30 мин) уже просрочен, иначе
    # 401 перехватил бы глобальный обработчик, а refresh_token остался бы рабочим. Операция идемпотентна.
    #
    # Форм-вариант: ответ 303 See Other — браузер переходит на GET "/", повторной отправки формы при «Назад» нет.
    redirect_response = RedirectResponse(url="/", status_code=303)

    access_logout_response = await auth_backend.transport.get_logout_response()
    _apply_transport_cookies(redirect_response, access_logout_response)

    refresh_logout_response = await refresh_cookie_transport.get_logout_response()
    _apply_transport_cookies(redirect_response, refresh_logout_response)

    return redirect_response


@auth_router.post(
    "/logout",
    summary="Выход (JS-вариант)",
    description="Удаляет обе куки, возвращает JSON `200`.",
)
async def logout():
    # Без Depends(current_user) — по той же причине, что в do_logout().
    #
    # JS-вариант: fetch из profile.js ждёт JSON 200 и сам делает redirect.
    json_response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Successfully logged out"},
    )

    access_logout_response = await auth_backend.transport.get_logout_response()
    _apply_transport_cookies(json_response, access_logout_response)

    refresh_logout_response = await refresh_cookie_transport.get_logout_response()
    _apply_transport_cookies(json_response, refresh_logout_response)

    return json_response
