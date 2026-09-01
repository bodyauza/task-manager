import os

# src/config.py вызывает load_dotenv() при импорте settings, поэтому CRM-переменные
# уже находятся в os.environ — повторный вызов load_dotenv здесь не нужен.


class CRMSettings:
    """Настройки CRM-клиента — читаются из переменных окружения."""

    API_URL: str = os.getenv("CRM_API_URL", "")
    API_KEY: str = os.getenv("CRM_API_KEY", "")
    API_USER: str = os.getenv("CRM_API_USER", "")
    API_PASSWORD: str = os.getenv("CRM_API_PASSWORD", "")
    LOGIN_URL: str = os.getenv("CRM_LOGIN_URL", "")
    # Пустая строка — production-режим (без demo_id в URL)
    DEMO_ID: str = os.getenv("CRM_DEMO_ID", "")
    USER_GROUP_ID: int = int(os.getenv("CRM_USER_GROUP_ID", "6"))

    # entity_id сущностей/подсущностей CRM «Руководитель» и ID их полей (field_<ID>
    # в payload). Эти номера генерируются ВНУТРИ конкретной инсталляции CRM и могут
    # отличаться между demo/production/другими клиентами — поэтому не хардкодятся
    # в TaskManager/SubtaskManager/CRMUserSelector/CRMUserRegistrar, а читаются
    # отсюда. Дефолты ниже совпадают с demo-инстансом, на котором разрабатывался
    # проект (см. docs/crm_issue.md) — смена инстанса CRM требует только .env,
    # без изменений кода.
    TASK_ENTITY_ID:    int = int(os.getenv("CRM_TASK_ENTITY_ID", "29"))
    SUBTASK_ENTITY_ID: int = int(os.getenv("CRM_SUBTASK_ENTITY_ID", "30"))
    USER_ENTITY_ID:    int = int(os.getenv("CRM_USER_ENTITY_ID", "1"))

    # Поля сущности «Задачи»
    TASK_FIELD_TITLE:         int = int(os.getenv("CRM_TASK_FIELD_TITLE", "317"))
    TASK_FIELD_DESCRIPTION:   int = int(os.getenv("CRM_TASK_FIELD_DESCRIPTION", "318"))
    TASK_FIELD_COMPLETED:     int = int(os.getenv("CRM_TASK_FIELD_COMPLETED", "319"))
    TASK_FIELD_SPECIFICATION: int = int(os.getenv("CRM_TASK_FIELD_SPECIFICATION", "320"))
    TASK_FIELD_OTHER_FILES:   int = int(os.getenv("CRM_TASK_FIELD_OTHER_FILES", "321"))

    # Поля подсущности «Подзадачи»
    SUBTASK_FIELD_TITLE:         int = int(os.getenv("CRM_SUBTASK_FIELD_TITLE", "322"))
    SUBTASK_FIELD_DESCRIPTION:   int = int(os.getenv("CRM_SUBTASK_FIELD_DESCRIPTION", "323"))
    SUBTASK_FIELD_COMPLETED:     int = int(os.getenv("CRM_SUBTASK_FIELD_COMPLETED", "324"))
    SUBTASK_FIELD_SPECIFICATION: int = int(os.getenv("CRM_SUBTASK_FIELD_SPECIFICATION", "325"))
    SUBTASK_FIELD_OTHER_FILES:   int = int(os.getenv("CRM_SUBTASK_FIELD_OTHER_FILES", "326"))

    # Поля сущности «Пользователи», используемые при поиске по email (select_fields/filters
    # в CRMUserSelector.find_user_by_email)
    USER_FIELD_FIRSTNAME: int = int(os.getenv("CRM_USER_FIELD_FIRSTNAME", "7"))
    USER_FIELD_LASTNAME:  int = int(os.getenv("CRM_USER_FIELD_LASTNAME", "8"))
    USER_FIELD_EMAIL:     int = int(os.getenv("CRM_USER_FIELD_EMAIL", "9"))
    USER_FIELD_USERNAME:  int = int(os.getenv("CRM_USER_FIELD_USERNAME", "12"))
    USER_FIELD_GROUP:     int = int(os.getenv("CRM_USER_FIELD_GROUP", "6"))


crm_settings = CRMSettings()
