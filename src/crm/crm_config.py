import os

# Явный импорт (а не расчёт на то, что кто-то ДРУГОЙ уже импортировал src.config
# раньше) — гарантирует, что load_dotenv() (src/config.py) выполнится ДО того,
# как код ниже начнёт читать os.getenv() при определении класса CRMSettings,
# независимо от того, в каком порядке модули импортируются вызывающим кодом.
#
# Раньше здесь был комментарий вида «src/config.py вызывает load_dotenv() при
# импорте settings, поэтому CRM-переменные уже находятся в os.environ» — это
# верно только если ЧТО-ТО успело импортировать src.config раньше этого модуля.
# При запуске через docker-compose это не проявлялось (Docker сам подставляет
# .dev.env как реальные переменные окружения контейнера, python-dotenv тут не
# требуется), но при прямом запуске src/main.py (например, из PyCharm) порядок
# импортов не гарантирован: например, если что-то импортирует src.admin раньше
# src.config, а src.admin тянет цепочку admin/auth.py → auth/manager.py →
# crm/client.py → crm/crm_config.py — этот модуль импортировался бы ДО того,
# как load_dotenv() успел бы отработать, и все os.getenv() ниже читали бы
# пустой os.environ. Явный импорт устраняет зависимость от порядка импортов
# в чужих модулях.
import src.config  # noqa: F401


def _required_env(name: str) -> str:
    """Без дефолта: значение привязано к конкретной инсталляции CRM (URL/ключ/
    логин конкретного demo- или production-инстанса) — тихий дефолт вида ""
    не упал бы сразу при старте, а сломался бы позже, при первом реальном
    HTTP-вызове к CRM, с куда менее понятной ошибкой. Тот же принцип, что и у
    SMTP_HOST/REG_TOKEN_SECRET в src/config.py: конфигурация внешнего сервиса
    не должна иметь скрытого дефолта.
    """
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Переменная окружения {name} обязательна, но не задана")
    return value


def _required_int_env(name: str) -> int:
    """Как _required_env, но для entity_id/field_id/list_id/group_id — номеров,
    которые генерируются ВНУТРИ конкретной инсталляции CRM «Руководитель» (см.
    докстринг класса ниже) и МОГУТ ОТЛИЧАТЬСЯ между demo/production/другими
    клиентами. Хардкоженный дефолт здесь был бы даже опаснее, чем для строковых
    секретов выше: при смене инстанса CRM без явной правки .env приложение не
    упало бы при старте, а тихо продолжило слать запросы с ID полей/сущностей/
    списков ПРЕЖНЕГО инстанса — CRM восприняла бы их как ссылку на случайное
    (или несуществующее) поле/список/группу в новой инсталляции, а не как ошибку
    конфигурации.
    """
    return int(_required_env(name))


class CRMSettings:
    """Настройки CRM-клиента — читаются из переменных окружения."""

    API_URL: str = _required_env("CRM_API_URL")
    API_KEY: str = _required_env("CRM_API_KEY")
    API_USER: str = _required_env("CRM_API_USER")
    API_PASSWORD: str = _required_env("CRM_API_PASSWORD")
    LOGIN_URL: str = _required_env("CRM_LOGIN_URL")
    # Пустая строка — легитимный, осмысленный дефолт (production-режим, без
    # demo_id в URL), а не сентинел «забыли настроить» — в отличие от полей
    # выше, оставлен как есть.
    DEMO_ID: str = os.getenv("CRM_DEMO_ID", "")
    USER_GROUP_ID: int = _required_int_env("CRM_USER_GROUP_ID")

    # entity_id сущностей/подсущностей CRM «Руководитель» и ID их полей (field_<ID>
    # в payload). Эти номера генерируются ВНУТРИ конкретной инсталляции CRM и могут
    # отличаться между demo/production/другими клиентами — поэтому не хардкодятся
    # в TaskManager/SubtaskManager/CRMUserRegistrar, а читаются отсюда, без
    # дефолта (см. _required_int_env выше) — смена инстанса CRM требует правки
    # .env и НЕМЕДЛЕННО проявится как ошибка при старте, если её забыли сделать,
    # а не тихой отправкой запросов со значениями прежнего инстанса.
    TASK_ENTITY_ID:    int = _required_int_env("CRM_TASK_ENTITY_ID")
    SUBTASK_ENTITY_ID: int = _required_int_env("CRM_SUBTASK_ENTITY_ID")
    USER_ENTITY_ID:    int = _required_int_env("CRM_USER_ENTITY_ID")

    # Поля сущности «Задачи»
    TASK_FIELD_TITLE:         int = _required_int_env("CRM_TASK_FIELD_TITLE")
    TASK_FIELD_DESCRIPTION:   int = _required_int_env("CRM_TASK_FIELD_DESCRIPTION")
    TASK_FIELD_COMPLETED:     int = _required_int_env("CRM_TASK_FIELD_COMPLETED")
    TASK_FIELD_SPECIFICATION: int = _required_int_env("CRM_TASK_FIELD_SPECIFICATION")
    TASK_FIELD_OTHER_FILES:   int = _required_int_env("CRM_TASK_FIELD_OTHER_FILES")

    # Поля подсущности «Подзадачи»
    SUBTASK_FIELD_TITLE:         int = _required_int_env("CRM_SUBTASK_FIELD_TITLE")
    SUBTASK_FIELD_DESCRIPTION:   int = _required_int_env("CRM_SUBTASK_FIELD_DESCRIPTION")
    SUBTASK_FIELD_COMPLETED:     int = _required_int_env("CRM_SUBTASK_FIELD_COMPLETED")
    SUBTASK_FIELD_SPECIFICATION: int = _required_int_env("CRM_SUBTASK_FIELD_SPECIFICATION")
    SUBTASK_FIELD_OTHER_FILES:   int = _required_int_env("CRM_SUBTASK_FIELD_OTHER_FILES")

    # ── Глобальный список «Проект» ──
    # ID самого справочника (не ID опций внутри него) — используется
    # GlobalListsManager.get_choices() (src/crm/global_lists_service.py).
    LIST_PROJECT: int = _required_int_env("CRM_LIST_PROJECT")

    # Поле сущности «Задачи» (entity_id=29), ссылающееся на список выше — тип
    # «Выпадающий список», название «Проект», уже создано в CRM. Только Task —
    # у Subtask (entity_id=30) такого поля нет и не будет.
    TASK_FIELD_PROJECT: int = _required_int_env("CRM_TASK_FIELD_PROJECT")

    # ── Ниже — операционные параметры тюнинга, НЕ идентификаторы конкретной
    # инсталляции CRM: в отличие от entity_id/field_id/list_id/group_id выше,
    # у них есть безопасный, осмысленный дефолт, который просто можно уточнить
    # под конкретный деплой — как DB_POOL_SIZE/REG_TOKEN_EXP в src/config.py.
    # Дефолт тут не маскирует ошибку конфигурации (не «случайное число из
    # прошлого инстанса»), поэтому os.getenv(..., дефолт) оставлен как есть.

    # Интервал расписания Celery Beat (src/celery_app.py) для периодической
    # синхронизации локальной таблицы project с этим списком CRM
    # (src/tasks/global_lists_tasks.py::sync_project_table) — НЕ TTL кэша: кэша
    # для этого справочника в веб-процессе нет, он читает таблицу project напрямую.
    PROJECT_SYNC_INTERVAL_SECONDS: int = int(os.getenv("CRM_PROJECT_SYNC_INTERVAL_SECONDS", "180"))

    # ── Шардирование outbox-очереди ──
    # Число шардов M шины crm_sync.shard_0..shard_{M-1} — читается
    # src/tasks/sharding.py (shard_for_id: id % N) и src/docker-compose.yml
    # (по одному сервису celery-worker-shard-N на шард, --pool=solo -Q
    # crm_sync.shard_N — см. комментарий там же про concurrency=1 на шард).
    # Смена этого числа требует дрейна шардов (см. основную документацию,
    # «Что это даёт при добавлении шарда — и чего НЕ даёт при удалении») —
    # не значение, которое меняют часто.
    OUTBOX_SHARD_COUNT: int = int(os.getenv("CRM_OUTBOX_SHARD_COUNT", "4"))

    # Ограничитель скорости запросов к CRM (token-bucket, INCR+EXPIRE на ключ
    # crm_rate_limit в Redis, см. src/tasks/crm_rate_limit.py) — защита от
    # одновременного «залпа» запросов при разборе большого backlog после
    # простоя CRM (reconcile_pending_outbox может разом вернуть в очередь
    # сотни накопившихся pending-строк).
    RATE_LIMIT_PER_SECOND: int = int(os.getenv("CRM_RATE_LIMIT_PER_SECOND", "5"))

    # Сколько дней хранить обработанные ('done') строки crm_outbox, прежде чем
    # cleanup_done_outbox (src/tasks/crm_outbox_tasks.py, Celery Beat, раз в
    # сутки) их удалит. Таблица иначе растёт бесконечно — каждое изменение
    # задачи/подзадачи добавляет строку, и 'done'-строки никогда не удаляются
    # сами по себе. failed/blocked/pending не затрагиваются никогда — только
    # успешно обработанные и старше этого срока. Читается на каждый вызов
    # cleanup-задачи через crm_settings (не кэшируется в отдельную module-level
    # константу) — тот же приём, что и OUTBOX_SHARD_COUNT в src/tasks/sharding.py,
    # для тестируемости через monkeypatch без перезапуска процесса.
    OUTBOX_RETENTION_DAYS: int = int(os.getenv("CRM_OUTBOX_RETENTION_DAYS", "30"))


crm_settings = CRMSettings()
