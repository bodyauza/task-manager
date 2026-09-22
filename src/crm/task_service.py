import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from src.crm.client import CRMClient
from src.crm.crm_config import crm_settings

logger = logging.getLogger(__name__)


class TaskManager(CRMClient):
    """CRUD-операции с сущностью «Задачи» (entity_id из crm_settings.TASK_ENTITY_ID).

    Поля:
        FIELD_TITLE — Название    (строка, уникальное)
        FIELD_DESCR — Описание    (текст)
        FIELD_DONE  — Статус      (чекбокс: "true" / "false")

    Номера entity_id/field_* генерируются внутри конкретной инсталляции CRM и
    отличаются между инстансами — не хардкодятся, читаются из crm_settings
    (src/crm/crm_config.py), настраиваются через CRM_TASK_* переменные окружения.
    """

    ENTITY_ID   = crm_settings.TASK_ENTITY_ID           # ID сущности «Задачи» в CRM Руководитель
    FIELD_TITLE = crm_settings.TASK_FIELD_TITLE         # ID поля «Название»
    FIELD_DESCR = crm_settings.TASK_FIELD_DESCRIPTION   # ID поля «Описание»
    FIELD_DONE  = crm_settings.TASK_FIELD_COMPLETED     # ID поля «Статус» (чекбокс: "true" / "false")
    FIELD_SPEC    = crm_settings.TASK_FIELD_SPECIFICATION  # ID поля «Техническое задание» (одиночный файл)
    FIELD_OTHER   = crm_settings.TASK_FIELD_OTHER_FILES    # ID поля «Иные документы» (множественные файлы)
    FIELD_PROJECT = crm_settings.TASK_FIELD_PROJECT        # ID поля «Проект» (выпадающий список, см. docs/project_field_crm_implementation_guide.md)

    async def create_task(
        self,
        title: str,
        description: str,
        completed: bool = False,
        project: Optional[str] = None,  # CRM-ID опции списка "Проект"; None — поле не выбрано
    ) -> Dict[str, Any]:
        """Создаёт задачу в CRM; возвращает {'id': int|None, 'response': dict}."""
        record = {
            f"field_{self.FIELD_TITLE}": title,
            f"field_{self.FIELD_DESCR}": description,
            f"field_{self.FIELD_DONE}":  self._bool_to_crm(completed),
        }
        if project is not None:
            record[f"field_{self.FIELD_PROJECT}"] = project
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

    async def find_task(self, title: str, description: str) -> Optional[Dict[str, Any]]:
        """Ищет задачу по совпадению title И description — используется ТОЛЬКО
        для идемпотентности retry события 'create' в durable outbox
        (src/tasks/crm_outbox_tasks.py::_do_create): если процесс упал между
        успешной вставкой в CRM и записью crm_task_id в локальную БД, повторная
        попытка сначала ищет уже созданную запись, а не вставляет дубликат.

        Эвристика, не гарантия: у сущности «Задачи» в CRM «Руководитель»
        (entity_id=29) нет поля-владельца — совпадение title+description между
        разными пользователями (shared board допускает разные owner_id с
        похожими названиями) сузит, но не исключит коллизию полностью.
        Принятый риск — см. docs/task-manager-documentation.md, п. 14,
        «Идемпотентность: чего не хватает в текущем коде CRM-клиентов».

        :return: Словарь первой найденной записи или None.
        """
        select_fields = ",".join(str(f) for f in (self.FIELD_TITLE, self.FIELD_DESCR))
        result = await self._call(
            action="select",
            entity_id=self.ENTITY_ID,
            select_fields=select_fields,
            filters={
                str(self.FIELD_TITLE): {"value": title, "condition": "include"},
                str(self.FIELD_DESCR): {"value": description, "condition": "include"},
            },
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

        clear_specification=True: field_320 = [] (CRM удаляет вложение ТЗ).
        other_file_abs_paths=[]: field_321 = [] (CRM очищает поле иных документов).
        other_file_abs_paths=[p1,p2]: field_321 = [file1, file2] (полная замена содержимого поля).
        project="" (пустая строка): field_327 = "" (CRM очищает поле "Проект");
        project=None: поле не трогать.
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
            # [] — CRM-формат для очистки файлового поля: запись обновляется без вложений.
            data[f"field_{self.FIELD_SPEC}"] = []
        elif specification_abs_path is not None:
            # Одиночный файл ТЗ: CRM принимает список из одного элемента.
            data[f"field_{self.FIELD_SPEC}"] = [await self._file_to_crm(specification_abs_path)]

        if other_file_abs_paths is not None:
            # None → поле не трогать; [] → очистить; [p1,…] → заменить всё содержимое.
            # asyncio.gather запускает все _file_to_crm(...) не дожидаясь друг друга;
            # каждый вызов сам выносит read_bytes() в отдельный поток через asyncio.to_thread
            # (см. client.py) — поэтому сами чтения с диска идут одновременно в пуле потоков,
            # а не по очереди. gather() уже возвращает list — оборачивать в list() не нужно.
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
            update_by_field={"id": task_id},  # критерий обновления — CRM-ID задачи
            # expect_id: если задачу удалили в CRM напрямую (не через это приложение),
            # CRM отвечает "success" с пустым data.id вместо ошибки — expect_id превращает
            # это в Exception, чтобы вызывающий код (services/tasks.py::update_task) выставил
            # crm_synced=False, а не ошибочный True. См. docs/crm_issue.md.
            expect_id=True,
        )

    async def delete_task(self, task_id: int) -> Dict[str, Any]:
        """Удаляет задачу по CRM-ID."""
        logger.info("CRM: delete task crm_id=%s", task_id)
        return await self._call(
            action="delete",
            entity_id=self.ENTITY_ID,
            delete_by_field={"id": task_id},
            expect_id=True,  # см. update_task выше — та же проверка для уже отсутствующей в CRM записи
        )
