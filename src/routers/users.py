from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.auth.auth_config import require_role
from src.auth.user_models import Role, User
from src.auth.user_schemas import UserRead
from src.database import get_async_session
from src.openapi_responses import responses
from src.services import tasks as task_service

router = APIRouter(prefix="/users", tags=["Users"])

# require_role("admin") вычисляется один раз при загрузке модуля для всех маршрутов роутера.
_admin_only = require_role("admin")


class UserAdminUpdate(BaseModel):
    # Все поля опциональны: PATCH передаёт только изменяемые атрибуты.
    username:   Optional[str]        = None
    firstname:  Optional[str]        = None
    lastname:   Optional[str]        = None
    patronymic: Optional[str]        = None
    role_ids:   Optional[list[int]]  = None
    is_active:  Optional[bool]       = None


@router.get(
    "/",
    response_model=list[UserRead],
    summary="Список пользователей",
    responses=responses(401, 403),
)
async def list_users(
    admin: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_async_session),
):
    # selectinload(User.roles): UserRead.role_ids читает user.roles синхронно, без eager-загрузки будет MissingGreenlet.
    users = (
        await db.execute(select(User).options(selectinload(User.roles)))
    ).scalars().all()
    return users


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    summary="Изменить пользователя",
    description="Частичное обновление. `role_ids` заменяет весь набор ролей целиком.",
    responses=responses(400, 401, 403, 404, 409, c400="Несуществующий id в `role_ids`"),
)
async def update_user(
    user_id: int,
    payload: UserAdminUpdate,
    admin: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_async_session),
):
    # selectinload(User.roles): нужен для замены user.roles при role_ids и для сериализации UserRead.role_ids.
    user = await db.get(User, user_id, options=[selectinload(User.roles)])
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # exclude_unset: только явно переданные поля (PATCH).
    update_data = payload.model_dump(exclude_unset=True)

    # role_ids заменяет весь набор ролей; user.roles — relationship, присваивать список int нельзя.
    if "role_ids" in update_data:
        role_ids = update_data.pop("role_ids")
        roles = (
            await db.execute(select(Role).where(Role.id.in_(role_ids)))
        ).scalars().all()
        # select().in_() молча отбрасывает несуществующие id, поэтому сравниваем число найденных ролей с len(set(role_ids)).
        if len(roles) != len(set(role_ids)):
            raise HTTPException(status_code=400, detail="Invalid role_ids")
        user.roles = roles

    for field, value in update_data.items():
        setattr(user, field, value)

    # IntegrityError возможен, если роль удалена конкурентно между валидацией и commit (FK) — вместо 500 отвечаем явно.
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Update conflicts with an existing user")

    # db.refresh() не нужен: expire_on_commit=False.
    return user


@router.delete(
    "/{user_id}",
    response_model=UserRead,
    summary="Удалить пользователя",
    responses=responses(400, 401, 403, 404, c400="Попытка удалить собственную учётную запись"),
)
async def delete_user(
    user_id: int,
    admin: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_async_session),
):
    # Запрет самоудаления: единственный admin иначе потерял бы управление пользователями.
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    # selectinload(User.roles): снимок читает user.role_ids (иначе MissingGreenlet).
    # with_for_update конфликтует с FOR KEY SHARE от INSERT задачи: параллельно созданная задача не проскочит
    # между снимком и DELETE (CASCADE удалил бы её, минуя очистку файлов и CRM).
    user = await db.get(User, user_id, options=[selectinload(User.roles)], with_for_update=True)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    snapshot = UserRead.model_validate(user)
    # ON DELETE CASCADE не чистит файлы и CRM, поэтому каждая задача удаляется тем же путём, что и DELETE /delete-task/{id}.
    deletions = await task_service.prepare_owner_tasks_deletion(db, user_id)
    await db.delete(user)
    await db.commit()
    await task_service.finish_tasks_deletion(deletions, actor_email=admin.email, actor_id=admin.id)
    return snapshot
