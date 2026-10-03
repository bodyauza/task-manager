"""CRM-справочники (глобальные списки). Единственный потребитель — src/tasks/global_lists_tasks.py;
веб-процесс читает только локальную таблицу project.
"""

import logging
from typing import Any, Dict, List

from src.crm.client import CRMClient

logger = logging.getLogger(__name__)


class GlobalListsManager(CRMClient):
    """Читает значения глобального списка CRM по его ID."""

    async def get_choices(self, list_id: int) -> Dict[str, str]:
        """Возвращает {CRM-ID опции: метка} для списка list_id, например {"3": "Альфа"}."""
        result = await self._call(action="get_global_list_choices", list_id=list_id)
        data: List[Dict[str, Any]] = result.get("data", [])
        return {str(item["id"]): item["name"] for item in data if item.get("id")}
