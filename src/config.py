import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = os.path.dirname(__file__)

load_dotenv(os.path.join(BASE_DIR, ".dev.env"), override=False)
load_dotenv(os.path.join(BASE_DIR, ".tests.env"), override=False)
load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)


class Settings(BaseSettings):
    api_mode: str
    app_name: str
    admin_email: str
    access_secret: str
    algorithm: str
    access_exp: int

    # Настройки refresh-токена (используется в /auth/access-token)
    refresh_secret: str
    refresh_exp: int

    # db parameters
    DB_HOST: str
    DB_PORT: str
    DB_USER: str
    DB_PASS: str
    DB_NAME: str
    DB_DRIVER_SYNC: str
    DB_DRIVER_ASYNC: str

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


class ProductionSettings(Settings):
    model_config = SettingsConfigDict(env_file=os.path.join(BASE_DIR, ".env"))


class DevelopmentSettings(Settings):
    model_config = SettingsConfigDict(env_file=os.path.join(BASE_DIR, ".dev.env"))


class TestingSettings(Settings):
    model_config = SettingsConfigDict(env_file=os.path.join(BASE_DIR, ".tests.env"))


@lru_cache
def get_settings():
    mode = os.getenv("API_MODE")
    if mode in ("test", "testing"):
        return TestingSettings()
    if mode in ("dev", "development"):
        return DevelopmentSettings()
    if mode in ("prod", "production"):
        return ProductionSettings()
    return ProductionSettings()


settings = get_settings()
