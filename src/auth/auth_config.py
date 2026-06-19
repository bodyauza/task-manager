from fastapi_users import FastAPIUsers
from fastapi_users.authentication import (AuthenticationBackend,
                                          CookieTransport, JWTStrategy)
from src.config import settings

# secure включается только в production.
_cookie_secure = settings.api_mode in ("prod", "production")

# Транспорт для access токена
cookie_transport = CookieTransport(
    cookie_name="access_token",
    cookie_max_age=settings.access_exp,
    cookie_secure=_cookie_secure,  # Только для HTTPS в production
    cookie_httponly=True  # Защита от XSS
)

# Используется отдельной кукой "refresh_token" с более долгим сроком жизни,
# чтобы POST /auth/access-token мог выдать новый access токен даже после
# истечения старого access токена.
refresh_cookie_transport = CookieTransport(
    cookie_name="refresh_token",
    cookie_max_age=settings.refresh_exp,
    cookie_secure=_cookie_secure,
    cookie_httponly=True
)


# Стратегия аутентификации для access токена (короткоживущий)
def get_access_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.access_secret,
        lifetime_seconds=settings.access_exp,
        algorithm=settings.algorithm
    )


# Стратегия аутентификации для refresh токена (долгоживущий).
# Подписывается отдельным секретом (REFRESH_SECRET), чтобы компрометация
# access-токена не позволяла подделать refresh-токен и наоборот.
def get_refresh_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.refresh_secret,
        lifetime_seconds=settings.refresh_exp,
        algorithm=settings.algorithm
    )

auth_backend = AuthenticationBackend(
    name="access_jwt",
    transport=cookie_transport,
    get_strategy=get_access_strategy,
)


from src.auth.manager import get_user_manager
from src.auth.models import User

fastapi_users = FastAPIUsers[User, int](
    get_user_manager,
    [auth_backend]
)

# Создание dependency для получения текущего пользователя
# Используется как dependency в защищенных маршрутах, например:
# @router.get("/protected-route")
# async def protected_route(user: User = Depends(current_user)):

current_user = fastapi_users.current_user(active=True)
