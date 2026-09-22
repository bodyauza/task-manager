"""ModelView для registration_pending (незавершённые регистрации, см.
src/auth/registration_endpoints.py).

code_hash не показывается нигде — ни в списке, ни на детальной странице (та по
умолчанию показывает ВСЕ колонки, поэтому нужен отдельный
column_details_exclude_list): просмотр хеша кода — материал для его подбора.

can_create/can_edit = False — одноразовый короткоживущий артефакт флоу.
can_delete = True — сценарий поддержки: пользователь исчерпал попытки ввода
кода или не хочет ждать RATE_LIMIT — админ удаляет запись, и код можно
запросить заново сразу.
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
