from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth_config import require_permission
from src.auth.user_models import Role, User
from src.auth.user_schemas import UserRead
from src.database import get_async_session

router = APIRouter(prefix="/users", tags=["Users"])

# require_permission("delete") вычисляется один раз при загрузке модуля.
# Все маршруты этого роутера требуют разрешения "delete" — оно есть только у роли admin (id=2).
# Использование одного экземпляра вместо повторного вызова в каждом Depends исключает
# создание лишних closure-объектов при каждом запросе.
_admin_only = require_permission("delete")


class UserAdminUpdate(BaseModel):
    # Все поля опциональны: PATCH передаёт только изменяемые атрибуты.
    username:   Optional[str]  = None
    firstname:  Optional[str]  = None
    lastname:   Optional[str]  = None
    patronymic: Optional[str]  = None
    role_id:    Optional[int]  = None
    is_active:  Optional[bool] = None


@router.get("/", response_model=list[UserRead])
async def list_users(
    admin: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_async_session),
):
    users = (await db.execute(select(User))).scalars().all()
    return users


@router.patch("/{user_id}", response_model=UserRead)
# PATCH — семантика частичного обновления: клиент передаёт только изменяемые поля,
# остальные остаются нетронутыми. response_model=UserRead ограничивает ответ:
# поля, отсутствующие в UserRead (например, hashed_password), в JSON не попадут.
async def update_user(
    user_id: int,
    payload: UserAdminUpdate,
    # _admin_only проверяет наличие разрешения "delete" в роли текущего пользователя.
    # Если пользователь не аутентифицирован или роль не содержит "delete" — 403 Forbidden
    # до входа в тело функции.
    admin: User = Depends(_admin_only),
    # get_async_session открывает транзакцию через async with и закрывает её после ответа.
    db: AsyncSession = Depends(get_async_session),
):
    # db.get() — lookup по первичному ключу. SQLAlchemy сначала проверяет identity map
    # (кеш сессии): если объект с таким id уже загружался в рамках этой транзакции —
    # возвращает его без SELECT. Иначе выполняет SELECT * FROM person WHERE id = $1.
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # exclude_unset=True — Pydantic отслеживает, какие поля были явно переданы в теле
    # запроса, а какие получили default=None. Если клиент послал {"is_active": false},
    # update_data = {"is_active": False}; поля username, role_id и прочие в словарь
    # не попадают. Без exclude_unset=True PATCH перезаписывал бы все поля модели,
    # включая те, которые клиент не трогал.
    update_data = payload.model_dump(exclude_unset=True)

    # Валидация role_id до setattr: db.get возвращает None для несуществующего id,
    # FK-нарушение на уровне БД дало бы IntegrityError с менее понятным сообщением.
    if "role_id" in update_data:
        role = await db.get(Role, update_data["role_id"])
        if role is None:
            raise HTTPException(status_code=400, detail="Invalid role_id")

    # setattr обновляет атрибуты ORM-объекта через InstrumentedAttribute-дескрипторы.
    # SQLAlchemy перехватывает каждое присваивание и помечает объект как dirty,
    # добавляя его в unit of work. SELECT на этом этапе не выполняется.
    for field, value in update_data.items():
        setattr(user, field, value)

    # commit() запускает unit of work: SQLAlchemy формирует UPDATE person SET ... WHERE id=$1
    # только для изменённых столбцов. username здесь не UNIQUE (см. auth/user_models.py) — IntegrityError
    # маловероятен, но возможен (например, role_id указывает на роль, удалённую конкурентным
    # запросом ровно между валидацией выше и этим commit — FK-нарушение). try/except не даёт
    # такому конфликту улететь наверх голым 500.
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Update conflicts with an existing user")

    # db.refresh() здесь не нужен: async_session_maker сконфигурирован с
    # expire_on_commit=False (src/database.py) — commit() НЕ переводит атрибуты
    # объекта в состояние expired (это поведение по умолчанию при expire_on_commit=True,
    # но не в этом проекте). Проверено эмпирически: обращение к атрибутам ORM-объекта
    # сразу после commit(), без промежуточного refresh(), не вызывает MissingGreenlet
    # и не требует дополнительного SELECT.
    #
    # FastAPI вызывает UserRead.model_validate(user) (from_attributes=True) и сериализует
    # ORM-объект в JSON согласно схеме UserRead.
    return user


@router.delete("/{user_id}", response_model=UserRead)
async def delete_user(
    user_id: int,
    admin: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_async_session),
):
    # Запрет самоудаления: если единственный admin удалит свою учётную запись,
    # доступ к управлению пользователями будет утрачен без возможности восстановления через UI.
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    snapshot = UserRead.model_validate(user)
    await db.delete(user)
    await db.commit()
    return snapshot
