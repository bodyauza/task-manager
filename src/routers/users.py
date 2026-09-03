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

router = APIRouter(prefix="/users", tags=["Users"])

# require_role("admin") вычисляется один раз при загрузке модуля.
# Все маршруты этого роутера требуют роли admin. Использование одного экземпляра
# вместо повторного вызова в каждом Depends исключает создание лишних
# closure-объектов при каждом запросе.
_admin_only = require_role("admin")


class UserAdminUpdate(BaseModel):
    # Все поля опциональны: PATCH передаёт только изменяемые атрибуты.
    username:   Optional[str]        = None
    firstname:  Optional[str]        = None
    lastname:   Optional[str]        = None
    patronymic: Optional[str]        = None
    role_ids:   Optional[list[int]]  = None
    is_active:  Optional[bool]       = None


@router.get("/", response_model=list[UserRead])
async def list_users(
    admin: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_async_session),
):
    # selectinload(User.roles): UserRead.role_ids читает user.roles синхронно при
    # сериализации ответа — без явной eager-загрузки здесь это упало бы MissingGreenlet
    # (см. комментарий у User.role_ids в auth/user_models.py).
    users = (
        await db.execute(select(User).options(selectinload(User.roles)))
    ).scalars().all()
    return users


@router.patch("/{user_id}", response_model=UserRead)
# PATCH — семантика частичного обновления: клиент передаёт только изменяемые поля,
# остальные остаются нетронутыми. response_model=UserRead ограничивает ответ:
# поля, отсутствующие в UserRead (например, hashed_password), в JSON не попадут.
async def update_user(
    user_id: int,
    payload: UserAdminUpdate,
    # _admin_only проверяет наличие роли admin у текущего пользователя.
    # Если пользователь не аутентифицирован или не имеет роли admin — 403 Forbidden
    # до входа в тело функции.
    admin: User = Depends(_admin_only),
    # get_async_session открывает транзакцию через async with и закрывает её после ответа.
    db: AsyncSession = Depends(get_async_session),
):
    # options=[selectinload(User.roles)]: нужно по двум причинам — (1) если payload
    # содержит role_ids, ниже делается bulk-replace user.roles = [...], а replace
    # незагруженной async-relationship падает MissingGreenlet (SQLAlchemy должна
    # прочитать текущий список, чтобы вычислить diff на удаление/добавление строк
    # в user_role); (2) response_model=UserRead читает user.role_ids при сериализации
    # ответа независимо от того, менялись роли в этом запросе или нет.
    user = await db.get(User, user_id, options=[selectinload(User.roles)])
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # exclude_unset=True — Pydantic отслеживает, какие поля были явно переданы в теле
    # запроса, а какие получили default=None. Если клиент послал {"is_active": false},
    # update_data = {"is_active": False}; поля username, role_ids и прочие в словарь
    # не попадают. Без exclude_unset=True PATCH перезаписывал бы все поля модели,
    # включая те, которые клиент не трогал.
    update_data = payload.model_dump(exclude_unset=True)

    # role_ids заменяет весь набор ролей пользователя целиком — та же PATCH-конвенция,
    # что и у остальных полей этого эндпоинта (замена значения, не merge). Обрабатывается
    # отдельно от generic setattr-цикла ниже: user.roles — relationship (список
    # ORM-объектов Role), присвоить ему список int напрямую нельзя.
    if "role_ids" in update_data:
        role_ids = update_data.pop("role_ids")
        roles = (
            await db.execute(select(Role).where(Role.id.in_(role_ids)))
        ).scalars().all()
        # len(roles) != len(set(role_ids)): db.get() для одного id давал IntegrityError
        # с менее понятным сообщением при FK-нарушении; здесь то же самое, но для списка —
        # select(...).in_(...) молча возвращает только существующие строки, поэтому
        # несуществующий id иначе прошёл бы незамеченным. set() — дубликаты в role_ids
        # не считаются ошибкой (тот же id дважды даёт одну и ту же строку в roles).
        if len(roles) != len(set(role_ids)):
            raise HTTPException(status_code=400, detail="Invalid role_ids")
        user.roles = roles

    # setattr обновляет атрибуты ORM-объекта через InstrumentedAttribute-дескрипторы.
    # SQLAlchemy перехватывает каждое присваивание и помечает объект как dirty,
    # добавляя его в unit of work. SELECT на этом этапе не выполняется.
    for field, value in update_data.items():
        setattr(user, field, value)

    # commit() запускает unit of work: SQLAlchemy формирует UPDATE person SET ... WHERE id=$1
    # (плюс INSERT/DELETE в user_role, если менялись role_ids) только для изменённых
    # столбцов/связей. username здесь не UNIQUE (см. auth/user_models.py) — IntegrityError
    # маловероятен, но возможен (например, одна из ролей удалена конкурентным запросом
    # ровно между валидацией выше и этим commit — FK-нарушение). try/except не даёт
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

    # options=[selectinload(User.roles)]: snapshot ниже читает user.role_ids —
    # без eager-загрузки это упало бы MissingGreenlet (см. update_user выше).
    user = await db.get(User, user_id, options=[selectinload(User.roles)])
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    snapshot = UserRead.model_validate(user)
    await db.delete(user)
    await db.commit()
    return snapshot
