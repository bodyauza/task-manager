import logging
from typing import Any, Dict, Optional

from src.crm.client import CRMClient

logger = logging.getLogger(__name__)


class CRMUserSelector(CRMClient):
    """Поиск пользователей в сущности «Пользователи» (entity_id=1).

    Поля: 7=Имя, 8=Фамилия, 9=Email, 12=Логин, 6=Группа.
    """

    USER_ENTITY_ID = 1

    async def find_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Ищет пользователя по точному совпадению email (поле 9).

        :return: Словарь с данными первой найденной записи или None.
        """
        result = await self._call(
            action="select",
            entity_id=self.USER_ENTITY_ID,
            select_fields="9,7,8,12,6",
            # condition='include' — точное совпадение, не LIKE
            filters={"9": {"value": email, "condition": "include"}},
        )
        data = result.get("data", [])
        if not data:
            return None
        # Email уникален — возвращаем первую (единственную) запись
        return data[0]
