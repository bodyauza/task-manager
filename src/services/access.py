"""Проверка ролей вне FastAPI-dependency (require_role/_is_admin в роутерах).

Нужна коду, который не может использовать Depends: AdminAuth для sqladmin
(src/admin/auth.py) работает со своей сессией и своим Request.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.user_models import Role, user_role


async def has_role(db: AsyncSession, user_id: int, role_name: str) -> bool:
    """Явный select() по user_role, а не user.roles: связь не lazy="selectin" —
    обращение к ней в async-коде роняет MissingGreenlet (та же конвенция, что в
    require_role() и pages.py::_is_admin)."""
    found = (
        await db.execute(
            select(Role.id).join(user_role).where(
                user_role.c.person_id == user_id, Role.name == role_name,
            )
        )
    ).scalar_one_or_none()
    return found is not None


async def is_admin(db: AsyncSession, user_id: int) -> bool:
    return await has_role(db, user_id, "admin")
