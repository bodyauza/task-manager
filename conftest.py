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
Весь код ниже должен отработать раньше этой цепочки.

[1] asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())
    asyncpg несовместим с ProactorEventLoop (умолчание Windows 3.8+).
    Заменяем на SelectorEventLoop до создания любого event loop.

[2] os.environ["API_MODE"] = "test"
    Устанавливается до первого src-импорта.
    get_settings() в src/config.py кэшируется при первом вызове через @lru_cache.
    Если API_MODE не выставлен здесь → get_settings() вернёт ProductionSettings
    и все тесты будут работать с продакшн-БД.

[3] load_dotenv(".tests.env", override=True)
    Проблема: src/config.py при импорте вызывает load_dotenv(".dev.env", override=False).
    .dev.env записывает DB_NAME=clients в os.environ первым.
    pydantic-settings читает os.environ с приоритетом над env_file →
    TestingSettings взяла бы DB_NAME=clients (dev-БД) вместо clients_test.
    Решение: загрузить .tests.env с override=True до любого src-импорта,
    чтобы перезаписать dev-значения тестовыми.

[4] Сохранение и восстановление DB_HOST
    override=True затирает ВСЕ переменные, включая DB_HOST.
    В Docker docker-compose задаёт DB_HOST=db (имя сервиса контейнера БД).
    .tests.env содержит DB_HOST=localhost — неверный адрес внутри Docker.
    Решение: сохранить DB_HOST → load_dotenv(override=True) → восстановить.

[5] Состояние os.environ после выполнения conftest.py
    os.environ["API_MODE"] = "test"
    os.environ["DB_NAME"]  = "clients_test"     (из .tests.env)
    os.environ["DB_HOST"]  = исходное значение  (shell/docker сохранено на шаге [4])

[6] Первый импорт src.config (через tests/conftest.py → src.main)
    get_settings() → "test" → TestingSettings() → подключение к clients_test
    @lru_cache фиксирует объект навсегда на время сессии pytest.
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

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

# src/config.py загружает .dev.env с override=False — эта операция записывает
# DB_NAME=clients и прочие dev-значения в os.environ первыми.
# pydantic-settings читает os.environ до env_file, поэтому без явного override=True
# здесь TestingSettings брала бы dev-значения из окружения вместо .tests.env.
# Итог: .tests.env загружается с override=True до любого src-импорта → тест-БД
# гарантированно clients_test, а не clients.
_tests_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", ".tests.env")

# load_dotenv с override=True перезаписывает ВСЕ переменные в os.environ, включая
# DB_HOST. Внутри Docker-контейнера docker-compose выставляет DB_HOST=db (имя сервиса);
# без сохранения override сбросил бы его в localhost из .tests.env — соединение бы падало.
_saved_db_host = os.environ.get("DB_HOST")
load_dotenv(_tests_env, override=True)
if _saved_db_host is not None:
    os.environ["DB_HOST"] = _saved_db_host
