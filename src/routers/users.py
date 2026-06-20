from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import require_permission
from src.auth.models import User
from src.auth.user_schemas import UserRead
from src.database import get_async_session

router = APIRouter(prefix="/users", tags=["Users"])

_admin_only = require_permission("delete")


class UserAdminUpdate(BaseModel):
    username: Optional[str] = None
    role_id: Optional[int] = None
    is_active: Optional[bool] = None


@router.get("/", response_model=list[UserRead])
async def list_users(
    admin: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_async_session),
):
    users = (await db.execute(select(User))).scalars().all()
    return users


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int,
    payload: UserAdminUpdate,
    admin: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_async_session),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", response_model=UserRead)
async def delete_user(
    user_id: int,
    admin: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_async_session),
):
    if user_id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete your own account")

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    snapshot = UserRead.model_validate(user)
    await db.delete(user)
    await db.commit()
    return snapshot
