"""CRM-справочники (глобальные списки CRM «Руководитель»).

Источник истины для допустимых значений поля Task.project — сама CRM.
Единственный потребитель — src/tasks/global_lists_tasks.py (Celery-задача
периодической синхронизации) — не FastAPI-роутеры и не сервисы: веб-процесс
читает и пишет только
локальную таблицу project (src/task_logic/models.py::Project), никогда не
обращается к CRM за этим списком напрямую.
"""

import logging
from typing import Any, Dict, List

from src.crm.client import CRMClient

logger = logging.getLogger(__name__)


class GlobalListsManager(CRMClient):
    """Читает значения одного глобального списка CRM по его ID."""

    async def get_choices(self, list_id: int) -> Dict[str, str]:
        """Возвращает {CRM-ID опции (строка): текстовая метка} для списка list_id.

        Пример: {"3": "Альфа", "7": "Бета"} для list_id=11 ("Проект").
        """
        result = await self._call(action="get_global_list_choices", list_id=list_id)
        data: List[Dict[str, Any]] = result.get("data", [])
        return {str(item["id"]): item["name"] for item in data if item.get("id")}
