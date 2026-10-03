"""ModelView для registration_pending (незавершённые регистрации).

code_hash не показывается нигде (в том числе на детальной странице — column_details_exclude_list): хеш — материал для подбора кода.
can_delete = True: админ может удалить запись, и код можно запросить заново без ожидания RATE_LIMIT.
"""

from sqladmin import ModelView

from src.admin.formatters import TYPE_FORMATTERS
from src.auth.user_models import RegistrationPending


class RegistrationPendingAdmin(ModelView, model=RegistrationPending):
    name = "Заявка на регистрацию"
    name_plural = "Заявки на регистрацию"
    icon = "fa-solid fa-envelope-open-text"

    column_type_formatters = TYPE_FORMATTERS

    can_create = False
    can_edit = False
    can_delete = True

    column_list = [
        RegistrationPending.id,
        RegistrationPending.email,
        RegistrationPending.attempts,
        RegistrationPending.expires_at,
        RegistrationPending.created_at,
    ]
    column_details_exclude_list = [RegistrationPending.code_hash]
    column_searchable_list = [RegistrationPending.email]
    column_sortable_list = [
        RegistrationPending.id,
        RegistrationPending.email,
        RegistrationPending.expires_at,
        RegistrationPending.created_at,
    ]
