from sqladmin import ModelView

from src.auth.user_models import Role


class RoleAdmin(ModelView, model=Role):
    name = "Роль"
    name_plural = "Роли"
    icon = "fa-solid fa-user-shield"

    column_list = [Role.id, Role.name]
    column_searchable_list = [Role.name]
    column_sortable_list = [Role.id, Role.name]
    # users — обратная сторона m2m: назначение ролей делается со стороны
    # UserAdmin.roles, а не вложенным списком всех пользователей на форме роли.
    form_excluded_columns = [Role.users]
