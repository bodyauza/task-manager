import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from src.crm.client import CRMClient
from src.crm.crm_config import crm_settings

logger = logging.getLogger(__name__)


class TaskManager(CRMClient):
    """CRUD-операции с сущностью «Задачи». Номера entity_id/field_* читаются из crm_settings (CRM_TASK_*),
    потому что различаются между инсталляциями CRM.
    """

    ENTITY_ID   = crm_settings.TASK_ENTITY_ID
    FIELD_TITLE = crm_settings.TASK_FIELD_TITLE
    FIELD_DESCR = crm_settings.TASK_FIELD_DESCRIPTION
    FIELD_DONE  = crm_settings.TASK_FIELD_COMPLETED
    FIELD_SPEC    = crm_settings.TASK_FIELD_SPECIFICATION
    FIELD_OTHER   = crm_settings.TASK_FIELD_OTHER_FILES
    FIELD_PROJECT = crm_settings.TASK_FIELD_PROJECT
    FIELD_CREATOR_EMAIL = crm_settings.TASK_FIELD_CREATOR_EMAIL
    FIELD_LOCAL_ID      = crm_settings.TASK_FIELD_LOCAL_ID

    async def create_task(
        self,
        local_id: int,
        title: str,
        description: str,
        completed: bool = False,
        project: Optional[str] = None,
        creator_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Создаёт задачу в CRM; возвращает {'id': int|None, 'response': dict}."""
        record = {
            f"field_{self.FIELD_TITLE}": title,
            f"field_{self.FIELD_DESCR}": description,
            f"field_{self.FIELD_DONE}":  self._bool_to_crm(completed),
            f"field_{self.FIELD_LOCAL_ID}": str(local_id),
        }
        if project is not None:
            record[f"field_{self.FIELD_PROJECT}"] = project
        if creator_email is not None:
            record[f"field_{self.FIELD_CREATOR_EMAIL}"] = creator_email
        logger.info("CRM: insert task title='%s'", title)
        result = await self._call(action="insert", entity_id=self.ENTITY_ID, items=[record])

        task_id = None
        if result.get("status") == "success":
            data = result.get("data")
            # Поле "data" в ответе на insert бывает словарём {"id": "42"} или списком [{"id": "42"}].
            if isinstance(data, dict):
                task_id = data.get("id")
            elif isinstance(data, list) and data:
                task_id = data[0].get("id")
        if task_id is not None:
            task_id = int(task_id)

        return {"id": task_id, "response": result}

    async def find_task(self, local_id: int) -> Optional[Dict[str, Any]]:
        """Ищет задачу по точному Local ID (`field_{FIELD_LOCAL_ID}` хранит Task.id).

        Нужен для идемпотентного retry 'create' (_do_create_task): после сбоя между вставкой в CRM и записью
        crm_task_id повтор находит созданную запись, а не вставляет дубликат. Local ID — точный ключ, поэтому
        поиск не может вернуть запись другой локальной задачи.

        :return: словарь найденной записи или None.
        """
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

    async def update_task(
        self,
        task_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        completed: Optional[bool] = None,
        specification_abs_path: Optional[Path] = None,
        clear_specification: bool = False,
        other_file_abs_paths: Optional[list[Path]] = None,
        project: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Обновляет задачу по CRM-ID; передаёт только заполненные поля.

        clear_specification=True — очищает поле ТЗ. other_file_abs_paths: None — не трогать, [] — очистить,
        [p1, p2] — заменить содержимое. project: "" — очистить поле «Проект», None — не трогать.
        """
        data: Dict[str, Any] = {}
        if title is not None:
            data[f"field_{self.FIELD_TITLE}"] = title
        if description is not None:
            data[f"field_{self.FIELD_DESCR}"] = description
        if completed is not None:
            data[f"field_{self.FIELD_DONE}"] = self._bool_to_crm(completed)
        if project is not None:
            data[f"field_{self.FIELD_PROJECT}"] = project

        if clear_specification:
            data[f"field_{self.FIELD_SPEC}"] = []
        elif specification_abs_path is not None:
            # ТЗ — одиночный файл, CRM принимает список из одного элемента.
            data[f"field_{self.FIELD_SPEC}"] = [await self._file_to_crm(specification_abs_path)]

        if other_file_abs_paths is not None:
            # None — не трогать; [] — очистить; [p1, …] — заменить. gather читает файлы параллельно.
            data[f"field_{self.FIELD_OTHER}"] = await asyncio.gather(
                *[self._file_to_crm(p) for p in other_file_abs_paths]
            )

        if not data:
            return {"status": "skipped", "message": "No fields to update"}

        logger.info("CRM: update task crm_id=%s", task_id)
        return await self._call(
            action="update",
            entity_id=self.ENTITY_ID,
            data=data,
            update_by_field={"id": task_id},
            # expect_id: ответ «success» с пустым data.id (запись удалена в CRM) становится исключением.
            expect_id=True,
        )

    async def backfill_local_id(self, task_id: int, local_id: int) -> Dict[str, Any]:
        """Дописывает field_{FIELD_LOCAL_ID} в существующую CRM-запись (для scripts/backfill_crm_local_id.py).

        Нужен для записей, созданных до появления Local ID: без него find_task не находит их при retry
        'create'. Повторная запись того же значения безопасна.
        """
        logger.info("CRM: backfill local_id=%s on task crm_id=%s", local_id, task_id)
        return await self._call(
            action="update",
            entity_id=self.ENTITY_ID,
            data={f"field_{self.FIELD_LOCAL_ID}": str(local_id)},
            update_by_field={"id": task_id},
            expect_id=True,
        )

    async def delete_task(self, task_id: int) -> Dict[str, Any]:
        """Удаляет задачу по CRM-ID."""
        logger.info("CRM: delete task crm_id=%s", task_id)
        return await self._call(
            action="delete",
            entity_id=self.ENTITY_ID,
            delete_by_field={"id": task_id},
            expect_id=True,
        )
