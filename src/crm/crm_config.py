import os

# Явный импорт гарантирует, что load_dotenv() (src/config.py) отработает до чтения os.getenv() ниже,
# независимо от порядка импортов.
import src.config  # noqa: F401


def _required_env(name: str) -> str:
    """Без дефолта: значение привязано к инсталляции CRM, и тихий дефолт сломался бы позже при первом вызове."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Переменная окружения {name} обязательна, но не задана")
    return value


def _required_int_env(name: str) -> int:
    """Как _required_env, но для entity_id/field_id/list_id/group_id: номера различаются между инсталляциями,
    и дефолт тихо слал бы запросы с ID прежнего инстанса.
    """
    return int(_required_env(name))


class CRMSettings:
    """Настройки CRM-клиента из переменных окружения."""

    API_URL: str = _required_env("CRM_API_URL")
    API_KEY: str = _required_env("CRM_API_KEY")
    API_USER: str = _required_env("CRM_API_USER")
    API_PASSWORD: str = _required_env("CRM_API_PASSWORD")
    LOGIN_URL: str = _required_env("CRM_LOGIN_URL")
    # Пустая строка — осмысленный дефолт (production без demo_id).
    DEMO_ID: str = os.getenv("CRM_DEMO_ID", "")

    # entity_id и ID полей генерируются внутри инсталляции CRM, поэтому читаются из .env без дефолта.
    TASK_ENTITY_ID:    int = _required_int_env("CRM_TASK_ENTITY_ID")
    SUBTASK_ENTITY_ID: int = _required_int_env("CRM_SUBTASK_ENTITY_ID")

    # Поля сущности «Задачи»
    TASK_FIELD_TITLE:         int = _required_int_env("CRM_TASK_FIELD_TITLE")
    TASK_FIELD_DESCRIPTION:   int = _required_int_env("CRM_TASK_FIELD_DESCRIPTION")
    TASK_FIELD_COMPLETED:     int = _required_int_env("CRM_TASK_FIELD_COMPLETED")
    TASK_FIELD_SPECIFICATION: int = _required_int_env("CRM_TASK_FIELD_SPECIFICATION")
    TASK_FIELD_OTHER_FILES:   int = _required_int_env("CRM_TASK_FIELD_OTHER_FILES")
    TASK_FIELD_CREATOR_EMAIL: int = _required_int_env("CRM_TASK_FIELD_CREATOR_EMAIL")
    # «Local ID» — служебное поле с локальным Task.id: точный ключ для find_task/create_task.
    TASK_FIELD_LOCAL_ID: int = _required_int_env("CRM_TASK_FIELD_LOCAL_ID")

    # Поля подсущности «Подзадачи»
    SUBTASK_FIELD_TITLE:         int = _required_int_env("CRM_SUBTASK_FIELD_TITLE")
    SUBTASK_FIELD_DESCRIPTION:   int = _required_int_env("CRM_SUBTASK_FIELD_DESCRIPTION")
    SUBTASK_FIELD_COMPLETED:     int = _required_int_env("CRM_SUBTASK_FIELD_COMPLETED")
    SUBTASK_FIELD_SPECIFICATION: int = _required_int_env("CRM_SUBTASK_FIELD_SPECIFICATION")
    SUBTASK_FIELD_OTHER_FILES:   int = _required_int_env("CRM_SUBTASK_FIELD_OTHER_FILES")
    SUBTASK_FIELD_CREATOR_EMAIL: int = _required_int_env("CRM_SUBTASK_FIELD_CREATOR_EMAIL")
    # Local ID подзадачи — локальный Subtask.id.
    SUBTASK_FIELD_LOCAL_ID: int = _required_int_env("CRM_SUBTASK_FIELD_LOCAL_ID")

    # Глобальный список «Проект»: ID самого справочника (используется GlobalListsManager.get_choices()).
    LIST_PROJECT: int = _required_int_env("CRM_LIST_PROJECT")

    # Поле «Проект» (выпадающий список) есть только у Task.
    TASK_FIELD_PROJECT: int = _required_int_env("CRM_TASK_FIELD_PROJECT")

    # Операционные параметры с безопасным дефолтом (в отличие от идентификаторов выше).

    # Интервал Celery Beat для синхронизации таблицы project с CRM. Это не TTL кэша.
    PROJECT_SYNC_INTERVAL_SECONDS: int = int(os.getenv("CRM_PROJECT_SYNC_INTERVAL_SECONDS", "180"))

    # Число шардов outbox-очереди (shard_for_id: id % N; по одному celery-worker-shard-N на шард).
    # Смена требует дрейна шардов.
    OUTBOX_SHARD_COUNT: int = int(os.getenv("CRM_OUTBOX_SHARD_COUNT", "4"))

    # Ограничитель запросов к CRM (INCR+EXPIRE на ключ crm_rate_limit): защита от залпа при разборе backlog.
    RATE_LIMIT_PER_SECOND: int = int(os.getenv("CRM_RATE_LIMIT_PER_SECOND", "5"))

    # Сколько дней хранить 'done'-строки crm_outbox до cleanup_done_outbox (failed/blocked/pending не трогаются).
    # Читается на каждый вызов, а не кэшируется, — для тестов через monkeypatch.
    OUTBOX_RETENTION_DAYS: int = int(os.getenv("CRM_OUTBOX_RETENTION_DAYS", "30"))


crm_settings = CRMSettings()
