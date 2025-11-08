from pydantic_settings import BaseSettings
from functools import lru_cache
import os


class Settings(BaseSettings):
    api_mode: str
    app_name: str
    admin_email: str
    access_secret: str
    algorithm: str
    access_exp: int

    # db parameters
    DB_HOST: str
    DB_PORT: str
    DB_USER: str
    DB_PASS: str
    DB_NAME: str
    DB_DRIVER_SYNC: str
    DB_DRIVER_ASYNC: str

    # DATABASE_URL = "postgresql+asyncpg://root:123@localhost:5432/clients"

    @property
    def ASYNC_DATABASE_URL(self):
        return f"postgresql+{self.DB_DRIVER_ASYNC}://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"


class ProductionSettings(Settings):
    class Config:
        env_file = os.path.join(os.path.dirname(__file__), ".env")


class DevelopmentSettings(Settings):
    class Config:
        env_file = os.path.join(os.path.dirname(__file__), ".dev.env")


class TestingSettings(Settings):
    class Config:
        env_file = os.path.join(os.path.dirname(__file__), ".tests.env")


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