import logging
from typing import Any, Dict, Optional

from src.crm.client import CRMClient

logger = logging.getLogger(__name__)


class TaskManager(CRMClient):
    """CRUD-операции с сущностью «Задачи» (entity_id=29).

    Поля:
        field_311 — Название    (строка, уникальное)
        field_312 — Описание    (текст)
        field_313 — Статус      (чекбокс: "true" / "false")
    """

    ENTITY_ID   = 29
    FIELD_TITLE = 311
    FIELD_DESCR = 312
    FIELD_DONE  = 313

    @staticmethod
    def _bool_to_crm(value: bool) -> str:
        """Преобразует bool в строковый формат поля-чекбокса CRM."""
        return "true" if value else "false"

    async def create_task(
        self,
        title: str,
        description: str,
        completed: bool = False,
    ) -> Dict[str, Any]:
        """Создаёт задачу в CRM; возвращает {'id': int|None, 'response': dict}."""
        record = {
            f"field_{self.FIELD_TITLE}": title,
            f"field_{self.FIELD_DESCR}": description,
            f"field_{self.FIELD_DONE}":  self._bool_to_crm(completed),
        }
        logger.info("CRM: insert task title='%s'", title)
        result = await self._call(action="insert", entity_id=self.ENTITY_ID, items=[record])

        task_id = None
        if result.get("status") == "success":
            data = result.get("data")
            # Формат поля "data" в ответе на insert нестабилен между версиями CRM:
            # - большинство версий возвращают словарь: {"id": "42"}
            # - отдельные версии возвращают список:   [{"id": "42"}]
            # Оба варианта обрабатываются явно, чтобы не зависеть от конкретной версии.
            if isinstance(data, dict):
                task_id = data.get("id")
            elif isinstance(data, list) and data:
                task_id = data[0].get("id")
        if task_id is not None:
            task_id = int(task_id)

        return {"id": task_id, "response": result}

    async def update_task(
        self,
        task_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        completed: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Обновляет задачу по CRM-ID; передаёт только заполненные поля."""
        data: Dict[str, Any] = {}
        if title is not None:
            data[f"field_{self.FIELD_TITLE}"] = title
        if description is not None:
            data[f"field_{self.FIELD_DESCR}"] = description
        if completed is not None:
            data[f"field_{self.FIELD_DONE}"] = self._bool_to_crm(completed)

        if not data:
            return {"status": "skipped", "message": "No fields to update"}

        logger.info("CRM: update task crm_id=%s", task_id)
        return await self._call(
            action="update",
            entity_id=self.ENTITY_ID,
            data=data,
            update_by_field={"id": task_id},
        )

    async def delete_task(self, task_id: int) -> Dict[str, Any]:
        """Удаляет задачу по CRM-ID."""
        logger.info("CRM: delete task crm_id=%s", task_id)
        return await self._call(
            action="delete",
            entity_id=self.ENTITY_ID,
            delete_by_field={"id": task_id},
        )
