import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from src.crm.client import CRMClient
from src.crm.crm_config import crm_settings

logger = logging.getLogger(__name__)


class SubtaskManager(CRMClient):
    """CRUD-операции с подсущностью «Подзадачи». parent_item_id — CRM-ID задачи (crm_task_id).
    Номера entity_id/field_* читаются из crm_settings (CRM_SUBTASK_*).
    """

    ENTITY_ID   = crm_settings.SUBTASK_ENTITY_ID
    FIELD_TITLE = crm_settings.SUBTASK_FIELD_TITLE
    FIELD_DESCR = crm_settings.SUBTASK_FIELD_DESCRIPTION
    FIELD_DONE  = crm_settings.SUBTASK_FIELD_COMPLETED
    FIELD_SPEC  = crm_settings.SUBTASK_FIELD_SPECIFICATION
    FIELD_OTHER = crm_settings.SUBTASK_FIELD_OTHER_FILES
    FIELD_CREATOR_EMAIL = crm_settings.SUBTASK_FIELD_CREATOR_EMAIL
    FIELD_LOCAL_ID      = crm_settings.SUBTASK_FIELD_LOCAL_ID

    async def create_subtask(
        self,
        parent_item_id: int,
        local_id: int,
        title: str,
        description: str,
        completed: bool = False,
        creator_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = {
            f"field_{self.FIELD_TITLE}": title,
            f"field_{self.FIELD_DESCR}": description,
            f"field_{self.FIELD_DONE}":  self._bool_to_crm(completed),
            f"field_{self.FIELD_LOCAL_ID}": str(local_id),
            "parent_item_id": parent_item_id,
        }
        if creator_email is not None:
            record[f"field_{self.FIELD_CREATOR_EMAIL}"] = creator_email
        logger.info("CRM: insert subtask parent_item_id=%s title='%s'", parent_item_id, title)
        result = await self._call(action="insert", entity_id=self.ENTITY_ID, items=[record])

        subtask_id = None
        if result.get("status") == "success":
            data = result.get("data")
            if isinstance(data, dict):
                subtask_id = data.get("id")
            elif isinstance(data, list) and data:
                subtask_id = data[0].get("id")
        if subtask_id is not None:
            subtask_id = int(subtask_id)

        return {"id": subtask_id, "response": result}
        # subtask_id может быть None при нестандартном успешном ответе

    async def find_subtask(self, local_id: int) -> Optional[Dict[str, Any]]:
        """См. TaskManager.find_task: поиск по точному Local ID (Subtask.id), parent_item_id не нужен."""
        result = await self._call(
            action="select",
            entity_id=self.ENTITY_ID,
            select_fields=str(self.FIELD_LOCAL_ID),
            filters={str(self.FIELD_LOCAL_ID): {"value": str(local_id), "condition": "include"}},
        )
        data = result.get("data", [])
        if not data:
            return None
        return data[0]

    async def update_subtask(
        self,
        subtask_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        completed: Optional[bool] = None,
        specification_abs_path: Optional[Path] = None,
        clear_specification: bool = False,
        other_file_abs_paths: Optional[list[Path]] = None,
    ) -> Dict[str, Any]:
        """Обновляет подзадачу по CRM-ID; передаёт только заполненные поля (семантика файлов — как в TaskManager.update_task)."""
        data: Dict[str, Any] = {}
        if title is not None:
            data[f"field_{self.FIELD_TITLE}"] = title
        if description is not None:
            data[f"field_{self.FIELD_DESCR}"] = description
        if completed is not None:
            data[f"field_{self.FIELD_DONE}"] = self._bool_to_crm(completed)
        if clear_specification:
            data[f"field_{self.FIELD_SPEC}"] = []
        elif specification_abs_path is not None:
            data[f"field_{self.FIELD_SPEC}"] = [await self._file_to_crm(specification_abs_path)]
        if other_file_abs_paths is not None:
            # None — не трогать; [] — очистить; [p1, …] — заменить.
            data[f"field_{self.FIELD_OTHER}"] = await asyncio.gather(
                *[self._file_to_crm(p) for p in other_file_abs_paths]
            )
        if not data:
            return {"status": "skipped", "message": "No fields to update"}

        logger.info("CRM: update subtask crm_id=%s", subtask_id)
        return await self._call(
            action="update",
            entity_id=self.ENTITY_ID,
            data=data,
            update_by_field={"id": subtask_id},
            # expect_id: ответ «success» с пустым data.id (запись удалена в CRM) становится исключением.
            expect_id=True,
        )

    async def backfill_local_id(self, subtask_id: int, local_id: int) -> Dict[str, Any]:
        """См. TaskManager.backfill_local_id."""
        logger.info("CRM: backfill local_id=%s on subtask crm_id=%s", local_id, subtask_id)
        return await self._call(
            action="update",
            entity_id=self.ENTITY_ID,
            data={f"field_{self.FIELD_LOCAL_ID}": str(local_id)},
            update_by_field={"id": subtask_id},
            expect_id=True,
        )

    async def delete_subtask(self, subtask_id: int) -> Dict[str, Any]:
        logger.info("CRM: delete subtask crm_id=%s", subtask_id)
        return await self._call(
            action="delete",
            entity_id=self.ENTITY_ID,
            delete_by_field={"id": subtask_id},
            expect_id=True,
        )
