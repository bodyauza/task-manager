"""Read-only ModelView для таблицы project (зеркало глобального списка «Проект» CRM).

Таблица целиком управляется Celery-задачей sync_project_table
(src/tasks/global_lists_tasks.py, Celery Beat + POST /admin/crm-options/refresh):
label/sort_order/is_active перезаписываются при каждой синхронизации, поэтому
правка через форму молча откатилась бы — выглядит как баг. can_create/can_edit/
can_delete = False убирают форму целиком, включая обработчики маршрутов.
"""

from sqladmin import ModelView

from src.admin.formatters import TYPE_FORMATTERS
from src.task_logic.models import Project


class ProjectAdmin(ModelView, model=Project):
    name = "Проект"
    name_plural = "Проекты (справочник)"
    icon = "fa-solid fa-list"
    category = "Справочники CRM"

    can_create = False
    can_edit = False
    can_delete = False

    column_type_formatters = TYPE_FORMATTERS
    column_list = [
        Project.id, Project.crm_id, Project.label, Project.sort_order,
        Project.is_active, Project.synced_at,
    ]
    column_searchable_list = [Project.label, Project.crm_id]
    column_sortable_list = [Project.id, Project.sort_order, Project.is_active]
