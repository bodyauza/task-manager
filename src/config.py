import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = os.path.dirname(__file__)

# Файл текущего API_MODE — единственный источник для ключей, объявленных сразу в нескольких .env-файлах. Грузится первым
# с override=False: значения из os.environ (shell, docker-compose, CI) сохраняют приоритет.
_ENV_FILE_BY_MODE = {
    "test": ".tests.env", "testing": ".tests.env",
    "dev": ".dev.env", "development": ".dev.env",
    "prod": ".env", "production": ".env",
}
_explicit_mode = os.getenv("API_MODE")  # снимок ДО load_dotenv — то, что передал shell/systemd/docker-compose

# Явный API_MODE=prod: .dev.env/.tests.env не грузятся вовсе, единственный источник — .env, чтобы dev-файл не «застолбил» общий ключ.
if _explicit_mode in ("prod", "production"):
    load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)
else:
    # API_MODE явно dev/test или не задан: файл режима первым, затем безусловно все три файла. Это self-bootstrap локального запуска:
    # .dev.env сам объявляет API_MODE=dev. Ветку не менять — на ней держится локальный запуск.
    _mode_env_file = _ENV_FILE_BY_MODE.get(_explicit_mode)
    if _mode_env_file is not None:
        load_dotenv(os.path.join(BASE_DIR, _mode_env_file), override=False)
    load_dotenv(os.path.join(BASE_DIR, ".dev.env"),   override=False)
    load_dotenv(os.path.join(BASE_DIR, ".tests.env"), override=False)
    load_dotenv(os.path.join(BASE_DIR, ".env"),        override=False)


class Settings(BaseSettings):
    api_mode: str
    app_name: str
    admin_email: str
    access_secret: str
    algorithm: str
    access_exp: int

    # Refresh-токен подписывается отдельным секретом: компрометация access_secret не позволяет его подделать.
    refresh_secret: str
    refresh_exp: int

    DB_HOST: str
    DB_PORT: str
    DB_USER: str
    DB_PASS: str
    DB_NAME: str
    DB_DRIVER_SYNC: str
    DB_DRIVER_ASYNC: str

    # Дефолты равны встроенным значениям QueuePool (5 + 10 соединений на процесс). Нужное значение зависит от max_connections PostgreSQL:
    # (DB_POOL_SIZE + DB_MAX_OVERFLOW) × число воркеров uvicorn не должно к нему приближаться.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10

    # Без дефолта: тихий дефолт привязал бы проект к конкретному SMTP-провайдеру. SMTP_PORT=465 (implicit TLS) оставлен с дефолтом.
    SMTP_HOST: str
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""

    # Отдельный секрет reg_token; без дефолта — приложение не запустится без REG_TOKEN_SECRET.
    REG_TOKEN_SECRET: str
    REG_TOKEN_EXP: int = 1200

    # Секрет подписи сессионной куки sqladmin, независимый от access_secret; без дефолта.
    ADMIN_SESSION_SECRET: str

    # CORS_ORIGINS_CSV — строка через запятую (список в .env неудобен). Дефолт — только localhost; в production переопределить реальным доменом.
    CORS_ORIGINS_CSV: str = (
        "http://localhost,http://localhost:8080,http://127.0.0.1:8000,"
        "http://localhost:3000,http://127.0.0.1:3000"
    )

    # Брокер Celery и Pub/Sub: общий Redis. Дефолт — для запуска без Docker; docker-compose переопределяет на redis://redis:6379/0.
    REDIS_URL: str = "redis://localhost:6379/0"

    # Часовой пояс только для отображения дат в sqladmin; в БД остаётся UTC.
    ADMIN_TIMEZONE: str = "Europe/Moscow"

    # Сколько последних записей держать в Redis List "chat:history".
    CHAT_HISTORY_MAX_LEN: int = 500

    # Лимиты входящих сообщений WS-чата (на одно соединение).
    WS_MAX_MESSAGE_CHARS: int = 1000
    WS_RATE_LIMIT_MESSAGES: int = 10
    WS_RATE_LIMIT_WINDOW_SECONDS: float = 10.0

    # Документация API (/docs, /redoc, /openapi.json): None — везде, кроме production; True/False — явное переопределение через .env.
    DOCS_ENABLED: bool | None = None

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS_CSV.split(",") if origin.strip()]

    @property
    def ASYNC_DATABASE_URL(self):
        return f"postgresql+{self.DB_DRIVER_ASYNC}://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def is_production(self) -> bool:
        return self.api_mode in ("prod", "production")

    @property
    def docs_enabled(self) -> bool:
        if self.DOCS_ENABLED is not None:
            return self.DOCS_ENABLED
        return not self.is_production


class ProductionSettings(Settings):
    model_config = SettingsConfigDict(env_file=os.path.join(BASE_DIR, ".env"), extra="ignore")


class DevelopmentSettings(Settings):
    model_config = SettingsConfigDict(env_file=os.path.join(BASE_DIR, ".dev.env"), extra="ignore")


class TestingSettings(Settings):
    model_config = SettingsConfigDict(env_file=os.path.join(BASE_DIR, ".tests.env"), extra="ignore")


@lru_cache
def get_settings():
    # lru_cache: Settings() иначе перечитывает .env при каждом вызове.
    mode = os.getenv("API_MODE")
    if mode in ("test", "testing"):
        return TestingSettings()
    if mode in ("dev", "development"):
        return DevelopmentSettings()
    if mode in ("prod", "production"):
        return ProductionSettings()
    return ProductionSettings()


settings = get_settings()

"""
Поток инициализации конфигурации при первом импорте src.config
──────────────────────────────────────────────────────────────

[1] Старт процесса
    os.environ содержит только то, что передал родительский shell/docker-compose/CI.
    Пример ниже — тестовый прогон: conftest.py (корневой) выставляет
    os.environ["API_MODE"] = "test" до первого импорта src.*, до этого os.environ пуст.

[2] _ENV_FILE_BY_MODE.get(os.getenv("API_MODE"))  →  ".tests.env"
    Файл текущего режима определяется ДО загрузки чего-либо.

[3] load_dotenv(".tests.env", override=False)   ← файл режима, грузится первым
    Читает файл построчно, для каждой строки KEY=VALUE:
      если KEY отсутствует в os.environ → os.environ[KEY] = VALUE
      если KEY уже есть в os.environ    → пропускает (override=False, значения
                                           из shell/docker-compose/CI не трогаются)
    Результат:
      os.environ["DB_NAME"]     = "task_manager_test"   ← застолблено файлом режима
      os.environ["SMTP_HOST"]   = "smtp.test.invalid"
      ...остальные переменные .tests.env

    Раньше на этом месте грузился .dev.env первым независимо от режима — тогда
    DB_NAME=task_manager застолбливался ИМ, и .tests.env ниже уже не мог его переопределить
    (override=False). Явный выбор файла режима на шаге [2] убирает эту зависимость
    от фиксированного порядка: для DB_NAME/SMTP_HOST/ACCESS_SECRET и т.п. побеждает
    файл активного API_MODE, а не тот, что физически стоит в коде первым.

[4] load_dotenv(".dev.env", override=False) → load_dotenv(".tests.env", override=False)
    → load_dotenv(".env", override=False)
    Прежний fallback-проход по всем трём файлам — без изменений. Ключи, уже занятые
    на шаге [3] (DB_NAME и другие поля .tests.env), пропускаются. Ключи, которых
    в .tests.env нет — например CRM_API_URL, объявленный только в .dev.env, — берутся
    отсюда: os.environ["CRM_API_URL"] = "https://..." (нужен crm/crm_config.py через
    os.getenv() напрямую, минуя pydantic-settings).

[5] get_settings()  →  выбор подкласса Settings
    mode = os.getenv("API_MODE")  →  "test"
    mode in ("test", "testing")  →  return TestingSettings()

    Ветви выбора:
      "test" / "testing"       → TestingSettings   (env_file=".tests.env")
      "dev"  / "development"   → DevelopmentSettings (env_file=".dev.env")
      "prod" / "production"    → ProductionSettings  (env_file=".env")
      любое другое / None      → ProductionSettings  (безопасный fallback)

[6] TestingSettings()  →  инициализация pydantic-settings
    Источники значений в порядке убывания приоритета:
      1. os.environ           (заполнен load_dotenv на шагах [3]-[4])
      2. env_file=".tests.env" (повторно читается как резервный источник)
      3. default в классе      (SMTP_PORT=465, CORS_ORIGINS_CSV="http://localhost,...", ...)

    Для каждого объявленного поля:
      db_name: str        → os.environ["DB_NAME"]    = "task_manager_test"    → "task_manager_test"
      smtp_port: int       → os.environ["SMTP_PORT"]  = "465" (строка)
                             lax-валидатор: int("465") → 465
      REG_TOKEN_SECRET     → не найдено ни в os.environ, ни в .tests.env
                             → ValidationError: приложение не запускается

    extra="ignore": переменные из .dev.env/.tests.env, не объявленные в Settings
    (CRM_API_URL, CRM_API_KEY, ...), молча отбрасываются — они нужны
    только crm/crm_config.py через os.getenv(), не через pydantic.

[7] settings = <TestingSettings object>
    Объект создан и привязан к имени settings на уровне модуля.
    @lru_cache сохраняет его внутри get_settings.

[8] Кэш lru_cache — необратимость после первого вызова
    Все последующие вызовы get_settings() возвращают тот же объект.
    Смена API_MODE после этой точки не имеет эффекта:
      os.environ["API_MODE"] = "prod"   →  get_settings()  →  тот же TestingSettings
    Сбросить кэш можно только явно: get_settings.cache_clear()

[9] from src.config import settings  (в любом другом модуле)
    Python возвращает кэшированный модуль из sys.modules.
    settings — уже созданный объект из шага [7].
    Файлы .env не перечитываются, get_settings() не вызывается повторно.
    Все модули разделяют один и тот же экземпляр Settings.
"""
