from sqladmin import ModelView

from src.auth.user_models import Role


class RoleAdmin(ModelView, model=Role):
    name = "Роль"
    name_plural = "Роли"
    icon = "fa-solid fa-user-shield"

    column_list = [Role.id, Role.name]
    column_searchable_list = [Role.name]
    column_sortable_list = [Role.id, Role.name]
    # users — обратная сторона m2m: роли назначаются со стороны UserAdmin.roles.
    form_excluded_columns = [Role.users]
