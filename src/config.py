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
Процесс запускается (API_MODE=dev в shell)
        ↓
import src.config
        ↓
load_dotenv(".dev.env", override=False)   → os.environ["CRM_API_URL"] = "..."
load_dotenv(".tests.env", override=False) → CRM_API_URL уже есть, пропускаем
load_dotenv(".env", override=False)       → пропускаем
        ↓
get_settings()
        ↓
os.getenv("API_MODE") → "dev"
        ↓
DevelopmentSettings()
  pydantic читает os.environ + ".dev.env"
  валидирует типы (str "465" → int 465)
  ValidationError если REG_TOKEN_SECRET отсутствует
        ↓
settings = <DevelopmentSettings object>
        ↓
@lru_cache сохраняет объект
        ↓
все модули импортируют тот же settings
"""
