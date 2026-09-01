import logging
from typing import Any, Dict, Optional, Protocol

from src.crm.client import CRMClient
from src.crm.crm_config import crm_settings

logger = logging.getLogger(__name__)


class UserLookup(Protocol):
    """Абстракция поиска пользователя в CRM, на которую опирается /auth/login.

    Эндпоинт зависит от этого протокола, а не от конкретного CRMUserSelector (DIP).
    """

    async def find_user_by_email(self, email: str) -> Optional[Dict[str, Any]]: ...


class UserRegistrar(Protocol):
    """Абстракция регистрации пользователя в CRM, на которую опирается UserManager.

    UserManager зависит от этого протокола, а не от конкретного CRMUserRegistrar (DIP) —
    доменный слой (создание пользователя) не завязан на детали HTTP-транспорта к CRM.
    """

    async def register_user(
        self,
        group_id: int,
        firstname: str,
        lastname: str,
        username: str,
        email: str,
        password: str = "",
        notify: bool = True,
        login_url: Optional[str] = None,
    ) -> Dict[str, Any]: ...


class CRMUserSelector(CRMClient):
    """Поиск пользователей в сущности «Пользователи» (entity_id из crm_settings.USER_ENTITY_ID).

    Поля (номера — из crm_settings, читаются из CRM_USER_FIELD_* переменных окружения,
    т.к. генерируются внутри конкретной инсталляции CRM и отличаются между инстансами):
    FIELD_FIRSTNAME=Имя, FIELD_LASTNAME=Фамилия, FIELD_EMAIL=Email,
    FIELD_USERNAME=Логин, FIELD_GROUP=Группа.
    """

    USER_ENTITY_ID  = crm_settings.USER_ENTITY_ID
    FIELD_FIRSTNAME = crm_settings.USER_FIELD_FIRSTNAME
    FIELD_LASTNAME  = crm_settings.USER_FIELD_LASTNAME
    FIELD_EMAIL     = crm_settings.USER_FIELD_EMAIL
    FIELD_USERNAME  = crm_settings.USER_FIELD_USERNAME
    FIELD_GROUP     = crm_settings.USER_FIELD_GROUP

    async def find_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Ищет пользователя по точному совпадению email.

        :return: Словарь с данными первой найденной записи или None.
        """
        select_fields = ",".join(str(f) for f in (
            self.FIELD_EMAIL, self.FIELD_FIRSTNAME, self.FIELD_LASTNAME,
            self.FIELD_USERNAME, self.FIELD_GROUP,
        ))
        result = await self._call(
            action="select",
            entity_id=self.USER_ENTITY_ID,
            select_fields=select_fields,
            # condition='include' — точное совпадение, не LIKE
            filters={str(self.FIELD_EMAIL): {"value": email, "condition": "include"}},
        )
        data = result.get("data", [])
        if not data:
            return None
        # Email уникален — возвращаем первую (единственную) запись
        return data[0]


class CRMUserRegistrar(CRMClient):
    """Регистрация пользователя в сущности «Пользователи» (entity_id из crm_settings.USER_ENTITY_ID).

    Отдельный класс, а не метод на CRMClient (ISP): register_user — доменное
    действие «регистрация пользователя», не общий HTTP-транспорт. Раньше он
    жил прямо на CRMClient и наследовался TaskManager/SubtaskManager, которым
    никогда не нужен, — тот же _call()/_http() транспорт остаётся общим
    (наследование от CRMClient), а регистрация пользователей — только здесь.
    """

    USER_ENTITY_ID = crm_settings.USER_ENTITY_ID

    async def register_user(
        self,
        group_id: int,
        firstname: str,
        lastname: str,
        username: str,
        email: str,
        password: str = "",
        notify: bool = True,
        login_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Регистрирует пользователя в сущности «Пользователи».

        :param username: Логин в CRM = часть email до '@'
        """
        record: Dict[str, Any] = {
            "group_id":  group_id,
            "firstname": firstname,
            "lastname":  lastname,
            "username":  username,
            "email":     email,
        }
        if password:
            record["password"] = password
        if login_url is None:
            login_url = self.login_url

        return await self._call(
            action="insert",
            entity_id=self.USER_ENTITY_ID,
            items=[record],
            notify=notify,
            login_url=login_url,
        )


def get_user_lookup() -> UserLookup:
    """FastAPI-зависимость: единственная точка, знающая, что UserLookup
    реализует именно CRMUserSelector — вызывающий код работает только с протоколом.
    """
    return CRMUserSelector()


def get_user_registrar() -> UserRegistrar:
    """FastAPI-зависимость: единственная точка, знающая, что UserRegistrar
    реализует именно CRMUserRegistrar — вызывающий код (UserManager,
    get_user_manager) работает только с протоколом.
    """
    return CRMUserRegistrar()
