from typing import Optional

from fastapi import Cookie, Depends, APIRouter, Response, status, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from fastapi_users import models

from src.auth.auth_config import (auth_backend, current_user,
                                  get_access_strategy, get_refresh_strategy,
                                  refresh_cookie_transport)
from src.auth.manager import UserManager, get_user_manager
from src.auth.user_schemas import is_valid_email_format, is_valid_password_format

# Добавляем новые маршруты для работы с токенами
auth_router = APIRouter(prefix="/auth", tags=["Authentication"])


# В fastapi-users==14.0.1 CookieTransport.get_login_response(token)
# принимает ТОЛЬКО токен и возвращает СВОЙ СОБСТВЕННЫЙ новый Response (204 No Content)
# с уже выставленным заголовком Set-Cookie - он не принимает и не модифицирует
# переданный объект response. Аналогично get_logout_response() не принимает
# response и возвращает новый Response с куки, очищающей значение.
# Эта функция переносит Set-Cookie заголовки из такого "временного" Response
# в реальный объект Response, который будет отправлен клиенту.
def _apply_transport_cookies(target_response: Response, transport_response: Response) -> None:
    for value in transport_response.headers.getlist("set-cookie"):
        target_response.headers.append("set-cookie", value)


# ВАЖНО: раньше сюда передавался параметр `response: Response` (через DI), и куки
# писались в него через _apply_transport_cookies(response, ...), но функция при
# этом RETURN-ила отдельный JSONResponse(...). FastAPI в таком случае отправляет
# клиенту именно возвращённый объект, а инжектированный `response` со всеми
# выставленными куки полностью отбрасывается - поэтому Set-Cookie не доходил до
# браузера, хотя код выполнялся без ошибок (200 OK). Теперь куки применяются
# к тому же объекту JSONResponse, который реально возвращается.
@auth_router.post("/login")
async def login(
        credentials: OAuth2PasswordRequestForm = Depends(),
        user_manager: UserManager = Depends(get_user_manager),
):
    # Валидация формата email и пароля общепринятыми регулярными
    # выражениями применяется теперь не только при регистрации (UserCreate),
    # но и при каждой авторизации. OAuth2PasswordRequestForm не наследуется
    # от UserCreate и не запускает её field_validator-ы автоматически, поэтому
    # обе проверки переиспользуются здесь явно, до обращения к БД -
    # credentials.username в этой форме - это email пользователя.
    if not is_valid_email_format(credentials.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email format",
        )
    if not is_valid_password_format(credentials.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Password must be at least 8 characters long and contain "
                "at least one lowercase letter, one uppercase letter, and one digit"
            ),
        )

    user = await user_manager.authenticate(credentials)

    # authenticate() теперь возвращает None при неверных данных
    # (см. manager.py), здесь это превращается в стандартный ответ fastapi-users.
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LOGIN_BAD_CREDENTIALS",
        )

    json_response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Login successful"},
    )

    # Выдаём access токен (короткоживущий, используется для доступа к защищённым маршрутам).
    # auth_backend.login(strategy, user) в fastapi-users==14.0.1 уже
    # сам генерирует токен (strategy.write_token) и возвращает готовый Response
    # с выставленной кукой (await transport.get_login_response(token)).
    # Раньше код повторно вызывал transport.get_login_response(...), передавая
    # туда уже готовый Response объект как "token" - в результате в куку
    # access_token попадал repr() этого объекта
    # (access_token="<starlette.responses.Response object at 0x...>"),
    # а не сам JWT.
    access_cookie_response = await auth_backend.login(strategy=get_access_strategy(), user=user)
    _apply_transport_cookies(json_response, access_cookie_response)

    # Выдаём refresh токен (долгоживущий, используется только для обновления access токена)
    refresh_strategy = get_refresh_strategy()
    refresh_token = await refresh_strategy.write_token(user)
    refresh_cookie_response = await refresh_cookie_transport.get_login_response(refresh_token)
    _apply_transport_cookies(json_response, refresh_cookie_response)

    return json_response


# Обновление access-токена производится по refresh-токену из отдельной куки.
@auth_router.post("/access-token")
async def get_access_token(
        refresh_token: Optional[str] = Cookie(default=None),
        user_manager: UserManager = Depends(get_user_manager),
):
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

    # Генерируем новый access токен (см. комментарий в /auth/login про auth_backend.login)
    access_cookie_response = await auth_backend.login(strategy=get_access_strategy(), user=user)
    _apply_transport_cookies(json_response, access_cookie_response)

    return json_response


@auth_router.post("/logout")
async def logout(
        user: models.UP = Depends(current_user),
):
    json_response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Successfully logged out"},
    )

    # Удаляем куки с access токеном
    access_logout_response = await auth_backend.transport.get_logout_response()
    _apply_transport_cookies(json_response, access_logout_response)

    # Дополнительно удаляем куку с refresh токеном,
    # иначе после logout refresh_token оставался бы валидным.
    refresh_logout_response = await refresh_cookie_transport.get_logout_response()
    _apply_transport_cookies(json_response, refresh_logout_response)

    return json_response