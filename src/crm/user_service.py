import logging
from typing import Any, Dict, Optional, Protocol

from src.crm.client import CRMClient
from src.crm.crm_config import crm_settings

logger = logging.getLogger(__name__)


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


def get_user_registrar() -> UserRegistrar:
    """FastAPI-зависимость: единственная точка, знающая, что UserRegistrar
    реализует именно CRMUserRegistrar — вызывающий код (UserManager,
    get_user_manager) работает только с протоколом.
    """
    return CRMUserRegistrar()
