"""TaskAdmin/SubtaskAdmin — просмотр и правка «безопасных» полей.

Запись через форму sqladmin идёт мимо outbox, WebSocket, файлов и CRM, поэтому:
- не редактируются crm_*_id, crm_shard, sync_status, specification_path/other_file_paths (показываются ссылками)
  и коллекции subtasks (delete-orphan);
- can_create = False: созданная в форме сущность не получила бы create-события в outbox и не попала бы в CRM —
  задачи и подзадачи заводятся только через API (services);
- can_delete = False: удаление в обход services оставило бы сироту в CRM и файлы на диске;
- редактируются title/description/completed и связи owner, project_ref, task. Эти правки в CRM и по WebSocket не уйдут.
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

    can_create = False
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

    can_create = False
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
