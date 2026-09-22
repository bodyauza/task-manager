"""TaskAdmin/SubtaskAdmin — просмотр и точечная правка «безопасных» полей.

Не замена продуктовых роутов (services/tasks.py, services/subtasks.py — outbox,
WebSocket-события, файлы, CRM): запись через форму sqladmin идёт мимо них.
Поэтому:

- НЕ редактируются и исключены из формы: crm_task_id/crm_subtask_id/crm_shard/
  sync_status (источник истины — CRM/outbox), specification_path/other_file_paths
  (запись файла — только через services/attachments.py: MIME-проверка, rel-путь;
  здесь показываются кликабельными ссылками на /uploads/...), коллекции
  subtasks (delete-orphan: снятие галочки физически удалило бы подзадачу).
- can_delete = False: удаление в обход services/*::delete_* оставило бы
  задачу-сироту в CRM и файлы на диске.
- Редактируются title/description/completed и связи: owner (ajax по email),
  project_ref/task (ajax) — «гибкая правка сырых полей включая владельца».
  Изменение этих полей в CRM и по WebSocket не уйдёт — это осознанная цена
  админской правки; продуктовый флоу остаётся путём для синхронной правки.
"""

from sqladmin import ModelView

from src.admin.formatters import TYPE_FORMATTERS, file_link_formatter, other_files_formatter
from src.task_logic.models import Subtask, Task

_FILE_FORMATTERS = {
    "specification_path": file_link_formatter("specification_path"),
    "other_file_paths": other_files_formatter,
}


class TaskAdmin(ModelView, model=Task):
    name = "Задача"
    name_plural = "Задачи"
    icon = "fa-solid fa-list-check"

    can_delete = False

    column_type_formatters = TYPE_FORMATTERS
    column_list = [
        Task.id, Task.title, Task.completed, Task.owner, Task.project_ref,
        Task.sync_status, Task.crm_task_id,
    ]
    column_searchable_list = [Task.title]
    column_sortable_list = [Task.id, Task.title, Task.completed, Task.sync_status]
    column_default_sort = [(Task.id, True)]
    column_labels = {Task.owner: "Владелец", Task.project_ref: "Проект"}

    column_formatters_detail = _FILE_FORMATTERS
    column_details_exclude_list = [Task.subtasks]

    form_columns = [Task.title, Task.description, Task.completed, Task.owner, Task.project_ref]
    form_ajax_refs = {
        "owner": {"fields": ("email", "username")},
        "project_ref": {"fields": ("label",)},
    }


class SubtaskAdmin(ModelView, model=Subtask):
    name = "Подзадача"
    name_plural = "Подзадачи"
    icon = "fa-solid fa-diagram-successor"

    can_delete = False

    column_type_formatters = TYPE_FORMATTERS
    column_list = [
        Subtask.id, Subtask.title, Subtask.completed, Subtask.task,
        Subtask.sync_status, Subtask.crm_subtask_id,
    ]
    column_searchable_list = [Subtask.title]
    column_sortable_list = [Subtask.id, Subtask.title, Subtask.completed, Subtask.sync_status]
    column_default_sort = [(Subtask.id, True)]
    column_labels = {Subtask.task: "Задача"}

    column_formatters_detail = _FILE_FORMATTERS

    form_columns = [Subtask.title, Subtask.description, Subtask.completed, Subtask.task]
    form_ajax_refs = {
        "task": {"fields": ("title",)},
    }
