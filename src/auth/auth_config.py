from fastapi import Depends, HTTPException, status
from fastapi_users import FastAPIUsers
from fastapi_users.authentication import (AuthenticationBackend,
                                          CookieTransport, JWTStrategy)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_async_session

# secure=True только в production: в dev нет TLS, браузер не отправит Secure-куку.
_cookie_secure = settings.is_production

# SameSite=lax: кука идёт при top-level navigation, но не при cross-site subresource-запросах — защита от CSRF.
cookie_transport = CookieTransport(
    cookie_name="access_token",
    cookie_max_age=settings.access_exp,
    cookie_secure=_cookie_secure,
    cookie_httponly=True,
    cookie_samesite="lax",
)

# Отдельная кука refresh_token (7 дней) с другим секретом: компрометация одной куки не даёт доступа к другой.
refresh_cookie_transport = CookieTransport(
    cookie_name="refresh_token",
    cookie_max_age=settings.refresh_exp,
    cookie_secure=_cookie_secure,
    cookie_httponly=True,
    cookie_samesite="lax",
)


# JWTStrategy создаётся callable: fastapi-users вызывает get_strategy() на каждый запрос.
def get_access_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.access_secret,
        lifetime_seconds=settings.access_exp,
        algorithm=settings.algorithm,
    )


def get_refresh_strategy() -> JWTStrategy:
    # Отдельный секрет: компрометация access_secret не позволяет подделать refresh-токен.
    return JWTStrategy(
        secret=settings.refresh_secret,
        lifetime_seconds=settings.refresh_exp,
        algorithm=settings.algorithm,
    )


auth_backend = AuthenticationBackend(
    name="access_jwt",
    transport=cookie_transport,
    get_strategy=get_access_strategy,
)


from src.auth.manager import get_user_manager
from src.auth.user_models import Role, User, user_role

fastapi_users = FastAPIUsers[User, int](
    get_user_manager,
    [auth_backend]
)

# active=True: неактивные пользователи получают 401, даже с действительным токеном.
current_user = fastapi_users.current_user(active=True)


def require_role(required_role: str):
    # Фабрика dependency; вызывается один раз на уровне модуля: _guard = require_role("admin").
    #
    # Роли many-to-many: доступ есть, если хотя бы одна роль называется required_role. Явный select().join(),
    # а не user.roles — иначе MissingGreenlet.
    async def _dependency(
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_async_session),
    ) -> User:
        roles = (await db.execute(
            select(Role).join(user_role).where(user_role.c.person_id == user.id)
        )).scalars().all()
        if not any(role.name == required_role for role in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для выполнения операции",
            )
        return user
    return _dependency
