import asyncio
import os
import sys
from dotenv import load_dotenv

# asyncpg падает с ProactorEventLoop (умолчание Python 3.8+ на Windows):
# asyncpg использует низкоуровневые сокетные операции, несовместимые с Proactor.
# SelectorEventLoop поддерживает те же примитивы на всех ОС.
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
