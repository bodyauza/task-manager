"""
Поток инициализации тестового окружения (корневой conftest.py)
──────────────────────────────────────────────────────────────
Почему этот файл лежит в корне репозитория, а не только в tests/: pytest
автоматически подхватывает conftest.py на каждом уровне каталогов от корня
запуска вниз до тестового файла (без импорта — это часть механизма discovery
самого pytest, "rootdir"-конфигурация). Файл tests/conftest.py импортирует
`from src.main import app`, поэтому к моменту, когда pytest доходит до
tests/conftest.py, весь модуль src.main (а вместе с ним src.config) уже
должен быть готов к импорту с правильными переменными окружения — сделать
это позже, внутри tests/conftest.py, было бы поздно: Python кэширует модули
в sys.modules при первом импорте, повторный импорт с другими os.environ
ничего не изменит.

pytest выполняет conftest.py ДО импорта любого тестового модуля.
tests/conftest.py импортирует src.main → src.config → get_settings() кэшируется.
Код ниже должен отработать раньше этой цепочки.

[1] asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())
    asyncpg несовместим с ProactorEventLoop (умолчание Windows 3.8+).
    Заменяем на SelectorEventLoop до создания любого event loop.

[2] os.environ["API_MODE"] = "test"
    Устанавливается до первого src-импорта.
    get_settings() в src/config.py кэшируется при первом вызове через @lru_cache.
    Если API_MODE не выставлен здесь → get_settings() вернёт ProductionSettings
    и все тесты будут работать с продакшн-БД.

    Загружать .tests.env отдельно здесь больше не нужно: src/config.py сам
    выбирает файл текущего режима (_ENV_FILE_BY_MODE в src/config.py) и грузит
    именно его первым — API_MODE=test однозначно приводит к .tests.env и
    DB_NAME=task_manager_test независимо от того, что лежит в .dev.env. Раньше
    (до этого фикса) src/config.py грузил .dev.env первым для любого режима,
    и без принудительного override=True здесь тесты подключались бы к
    dev-БД task_manager вместо task_manager_test — см. докстринг в src/config.py.

[3] Состояние os.environ после выполнения conftest.py
    os.environ["API_MODE"] = "test"
    остальное (DB_NAME=task_manager_test и т.д.) заполнит сам src/config.py при импорте.

[4] Первый импорт src.config (через tests/conftest.py → src.main)
    get_settings() → "test" → TestingSettings() → подключение к task_manager_test
    @lru_cache фиксирует объект навсегда на время сессии pytest.
"""

import asyncio
import os
import sys

# asyncpg падает с ProactorEventLoop (умолчание Python 3.8+ на Windows):
# asyncpg использует низкоуровневые сокетные операции, несовместимые с Proactor.
# SelectorEventLoop поддерживает те же примитивы на всех ОС.
# sys.platform == "win32": проверка нужна только потому, что WindowsSelectorEventLoopPolicy
# существует исключительно в модуле asyncio на Windows — на Linux/macOS обращение к этому
# атрибуту упало бы AttributeError. На этих ОС SelectorEventLoop и так используется по
# умолчанию (Proactor — Windows-специфичный механизм ввода-вывода на базе IOCP), поэтому
# для них никакого дополнительного действия не требуется.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# API_MODE должен быть выставлен ДО первого импорта любого src.* модуля:
# src/config.py вызывает get_settings() на уровне модуля, а lru_cache кэширует
# результат навсегда. Поздняя установка API_MODE не изменит закэшированный объект.
os.environ["API_MODE"] = "test"
