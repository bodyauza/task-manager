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


crm_settings = CRMSettings()
