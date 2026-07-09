import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = os.path.dirname(__file__)

# override=False: переменные, уже установленные в os.environ (например, через shell),
# имеют приоритет над значениями из файлов .env.
# Порядок важен: .dev.env загружается первым и «захватывает» переменные, не заданные
# в os.environ. Это нужно для crm/config.py, который читает CRM_* через os.getenv()
# напрямую, минуя pydantic-settings.
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

    # Refresh-токен подписывается отдельным секретом.
    # Компрометация access_secret не позволяет подделать refresh-токен.
    refresh_secret: str
    refresh_exp: int

    DB_HOST: str
    DB_PORT: str
    DB_USER: str
    DB_PASS: str
    DB_NAME: str
    DB_DRIVER_SYNC: str
    DB_DRIVER_ASYNC: str

    SMTP_HOST: str = "smtp.yandex.ru"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""

    # Отдельный секрет для reg_token — компрометация access_secret не позволяет
    # подделать токен незавершённой регистрации.
    # Нет значения по умолчанию: приложение не запустится без явно заданной переменной
    # окружения REG_TOKEN_SECRET — случайный дефолт типа "change-me" был бы тихой уязвимостью.
    REG_TOKEN_SECRET: str
    REG_TOKEN_EXP: int = 1200  # 20 минут

    cors_origins: list[str] = [
        "http://localhost",
        "http://localhost:8080",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    @property
    def ASYNC_DATABASE_URL(self):
        return f"postgresql+{self.DB_DRIVER_ASYNC}://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def is_production(self) -> bool:
        return self.api_mode in ("prod", "production")


class ProductionSettings(Settings):
    model_config = SettingsConfigDict(env_file=os.path.join(BASE_DIR, ".env"), extra="ignore")


class DevelopmentSettings(Settings):
    model_config = SettingsConfigDict(env_file=os.path.join(BASE_DIR, ".dev.env"), extra="ignore")


class TestingSettings(Settings):
    model_config = SettingsConfigDict(env_file=os.path.join(BASE_DIR, ".tests.env"), extra="ignore")


@lru_cache
def get_settings():
    # lru_cache: pydantic-settings читает и парсит .env-файл при каждом вызове Settings().
    # Кэш сводит это к одному разбору на жизненный цикл процесса.
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
    os.environ содержит только то, что передал родительский shell или docker-compose.
    В локальной разработке: пусто (API_MODE не установлен заранее).
    В Docker: os.environ["API_MODE"] = "prod"  (из docker-compose environment:).

[2] load_dotenv(".dev.env", override=False)
    Читает файл построчно, для каждой строки KEY=VALUE:
      если KEY отсутствует в os.environ → os.environ[KEY] = VALUE
      если KEY уже есть в os.environ    → пропускает (override=False)
    Результат в локальной разработке:
      os.environ["API_MODE"]    = "dev"
      os.environ["CRM_API_URL"] = "https://..."   ← нужен crm/config.py через os.getenv()
      os.environ["DB_HOST"]     = "localhost"
      os.environ["DB_NAME"]     = "clients"
      ...все остальные переменные из .dev.env

[3] load_dotenv(".tests.env", override=False)
    Все переменные (API_MODE, DB_HOST, ...) уже есть в os.environ после шага [2].
    override=False: пропускает все совпадающие ключи.
    Эффект: шаг ничего не меняет при локальной разработке.

[4] load_dotenv(".env", override=False)
    В локальной разработке файл .env обычно отсутствует — вызов игнорируется.
    В production (если .env есть): все переменные уже в os.environ от shell/docker → пропускает.

[5] get_settings()  →  выбор подкласса Settings
    mode = os.getenv("API_MODE")  →  "dev"
    mode in ("dev", "development")  →  return DevelopmentSettings()

    Ветви выбора:
      "test" / "testing"       → TestingSettings   (env_file=".tests.env")
      "dev"  / "development"   → DevelopmentSettings (env_file=".dev.env")
      "prod" / "production"    → ProductionSettings  (env_file=".env")
      любое другое / None      → ProductionSettings  (безопасный fallback)

[6] DevelopmentSettings()  →  инициализация pydantic-settings
    Источники значений в порядке убывания приоритета:
      1. os.environ           (заполнен load_dotenv на шаге [2])
      2. env_file=".dev.env"  (повторно читается как резервный источник)
      3. default в классе     (SMTP_HOST="smtp.yandex.ru", SMTP_PORT=465, ...)

    Для каждого объявленного поля:
      api_mode: str      → os.environ["API_MODE"]    = "dev"           → "dev"
      smtp_port: int     → os.environ["SMTP_PORT"]   = "465" (строка)
                           lax-валидатор: int("465") → 465
      REG_TOKEN_SECRET   → не найдено ни в os.environ, ни в .dev.env
                           → ValidationError: приложение не запускается

    extra="ignore": переменные из .dev.env, не объявленные в Settings
    (CRM_API_URL, CRM_API_KEY, ...), молча отбрасываются — они нужны
    только crm/config.py через os.getenv(), не через pydantic.

[7] settings = <DevelopmentSettings object>
    Объект создан и привязан к имени settings на уровне модуля.
    @lru_cache сохраняет его внутри get_settings.

[8] Кэш lru_cache — необратимость после первого вызова
    Все последующие вызовы get_settings() возвращают тот же объект.
    Смена API_MODE после этой точки не имеет эффекта:
      os.environ["API_MODE"] = "prod"   →  get_settings()  →  тот же DevelopmentSettings
    Сбросить кэш можно только явно: get_settings.cache_clear()

[9] from src.config import settings  (в любом другом модуле)
    Python возвращает кэшированный модуль из sys.modules.
    settings — уже созданный объект из шага [7].
    Файлы .env не перечитываются, get_settings() не вызывается повторно.
    Все модули разделяют один и тот же экземпляр Settings.
"""
