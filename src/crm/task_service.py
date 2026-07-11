import base64
import logging
from pathlib import Path
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

    ENTITY_ID   = 29    # числовой ID сущности «Задачи» в CRM Руководитель
    FIELD_TITLE = 311   # ID поля «Название»
    FIELD_DESCR = 312   # ID поля «Описание»
    FIELD_DONE  = 313   # ID поля «Статус» (чекбокс: "true" / "false")
    FIELD_SPEC  = 395   # ID поля «Техническое задание» (file upload, одиночный файл)
    FIELD_OTHER = 396   # ID поля «Иные документы» (вложения, множественные файлы)

    @staticmethod
    def _bool_to_crm(value: bool) -> str:
        """Преобразует bool в строковый формат поля-чекбокса CRM."""
        return "true" if value else "false"

    @staticmethod
    def _file_to_crm(abs_path: Path) -> dict:
        """Читает файл с диска и возвращает CRM-совместимый словарь.

        CRM ожидает файлы в виде {'name': 'filename.pdf', 'content': '<base64>'}.
        Метод вызывается только если abs_path существует — роутер создаёт файл
        до вызова update_task, поэтому read_bytes() не должен упасть.
        """
        return {
            "name":    abs_path.name,                                    # оригинальное имя (с UUID-префиксом)
            "content": base64.b64encode(abs_path.read_bytes()).decode(), # base64 без переносов строк
        }

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
        specification_abs_path: Optional[Path] = None,
        clear_specification: bool = False,
        other_file_abs_paths: Optional[list[Path]] = None,
    ) -> Dict[str, Any]:
        """Обновляет задачу по CRM-ID; передаёт только заполненные поля.

        clear_specification=True: field_395 = [] (CRM удаляет вложение ТЗ).
        other_file_abs_paths=[]: field_396 = [] (CRM очищает поле иных документов).
        other_file_abs_paths=[p1,p2]: field_396 = [file1, file2] (полная замена содержимого поля).
        """
        data: Dict[str, Any] = {}
        if title is not None:
            data[f"field_{self.FIELD_TITLE}"] = title
        if description is not None:
            data[f"field_{self.FIELD_DESCR}"] = description
        if completed is not None:
            data[f"field_{self.FIELD_DONE}"] = self._bool_to_crm(completed)

        if clear_specification:
            # [] — CRM-формат для очистки файлового поля: запись обновляется без вложений.
            data[f"field_{self.FIELD_SPEC}"] = []
        elif specification_abs_path is not None:
            # Одиночный файл ТЗ: CRM принимает список из одного элемента.
            data[f"field_{self.FIELD_SPEC}"] = [self._file_to_crm(specification_abs_path)]

        if other_file_abs_paths is not None:
            # None → поле не трогать; [] → очистить; [p1,…] → заменить всё содержимое.
            data[f"field_{self.FIELD_OTHER}"] = [self._file_to_crm(p) for p in other_file_abs_paths]

        if not data:
            return {"status": "skipped", "message": "No fields to update"}

        logger.info("CRM: update task crm_id=%s", task_id)
        return await self._call(
            action="update",
            entity_id=self.ENTITY_ID,
            data=data,
            update_by_field={"id": task_id},  # критерий обновления — CRM-ID задачи
        )

    async def delete_task(self, task_id: int) -> Dict[str, Any]:
        """Удаляет задачу по CRM-ID."""
        logger.info("CRM: delete task crm_id=%s", task_id)
        return await self._call(
            action="delete",
            entity_id=self.ENTITY_ID,
            delete_by_field={"id": task_id},
        )
