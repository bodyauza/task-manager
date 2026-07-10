import logging
from typing import Any, Dict, Optional

from src.crm.client import CRMClient              # базовый клиент: _call(), _http, аутентификация

logger = logging.getLogger(__name__)              # логгер этого модуля для INFO/ERROR записей


class SubtaskManager(CRMClient):
    """CRUD-операции с подсущностью «Подзадачи» (entity_id=32).

    parent_item_id — CRM-ID задачи из сущности «Задачи» (entity_id=29),
    то есть crm_task_id из локальной БД.

    Поля:
        field_392 — Название (строка)
        field_393 — Описание (текст)
        field_394 — Статус   (чекбокс: "true" / "false")
    """

    ENTITY_ID   = 32    # ID сущности «Подзадачи» в CRM; «Задачи» — 29, «Пользователи» — 1
    FIELD_TITLE = 392   # числовой ID поля «Название»; в payload: f"field_{392}" = "field_392"
    FIELD_DESCR = 393   # числовой ID поля «Описание»
    FIELD_DONE  = 394   # числовой ID поля «Статус» (чекбокс CRM принимает строки "true"/"false")

    @staticmethod
    def _bool_to_crm(value: bool) -> str:
        return "true" if value else "false"     # CRM чекбокс — строка, не JSON boolean

    async def create_subtask(
        self,
        parent_item_id: int,                    # crm_task_id родительской задачи из локальной БД
        title: str,
        description: str,
        completed: bool = False,
    ) -> Dict[str, Any]:
        record = {
            f"field_{self.FIELD_TITLE}": title,             # "field_392": "Название"
            f"field_{self.FIELD_DESCR}": description,       # "field_393": "Описание"
            f"field_{self.FIELD_DONE}":  self._bool_to_crm(completed),  # "field_394": "false"
            "parent_item_id": parent_item_id,               # привязка к родительской задаче в CRM
        }
        logger.info("CRM: insert subtask parent_item_id=%s title='%s'", parent_item_id, title)
        result = await self._call(action="insert", entity_id=self.ENTITY_ID, items=[record])
        # _call() бросает Exception при: HTTP-ошибке, таймауте, невалидном JSON, ответе с "msg"

        subtask_id = None
        if result.get("status") == "success":               # не все версии CRM возвращают "success"
            data = result.get("data")
            if isinstance(data, dict):                      # большинство версий: {"id": "42"}
                subtask_id = data.get("id")
            elif isinstance(data, list) and data:           # отдельные версии: [{"id": "42"}]
                subtask_id = data[0].get("id")
        if subtask_id is not None:
            subtask_id = int(subtask_id)                    # CRM возвращает id как строку

        return {"id": subtask_id, "response": result}
        # subtask_id может быть None при нестандартном успешном ответе CRM

    async def update_subtask(
        self,
        subtask_id: int,                        # crm_subtask_id из локальной таблицы subtask
        title: Optional[str] = None,
        description: Optional[str] = None,
        completed: Optional[bool] = None,
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {}               # словарь только изменяемых полей
        if title is not None:
            data[f"field_{self.FIELD_TITLE}"] = title
        if description is not None:
            data[f"field_{self.FIELD_DESCR}"] = description
        if completed is not None:
            data[f"field_{self.FIELD_DONE}"] = self._bool_to_crm(completed)
        if not data:
            return {"status": "skipped", "message": "No fields to update"}
            # ранний возврат без HTTP-запроса: нет полей — нет смысла обращаться к CRM

        logger.info("CRM: update subtask crm_id=%s", subtask_id)
        return await self._call(
            action="update",
            entity_id=self.ENTITY_ID,
            data=data,                          # только переданные поля, остальные не тронуты
            update_by_field={"id": subtask_id}, # критерий: обновить запись с этим CRM-ID
        )

    async def delete_subtask(self, subtask_id: int) -> Dict[str, Any]:
        logger.info("CRM: delete subtask crm_id=%s", subtask_id)
        return await self._call(
            action="delete",
            entity_id=self.ENTITY_ID,
            delete_by_field={"id": subtask_id}, # CRM удалит запись по CRM-ID подзадачи
        )
