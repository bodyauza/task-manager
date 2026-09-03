import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = os.path.dirname(__file__)

# Файл, соответствующий текущему API_MODE — единственный источник значений для
# ключей, которые ОДНОВРЕМЕННО объявлены в нескольких .env-файлах (DB_NAME,
# SMTP_HOST, ACCESS_SECRET и т.п.). Грузится ПЕРВЫМ и с override=False:
# значения, уже стоящие в os.environ (shell, docker-compose, CI — например,
# DB_HOST=db, который docker-compose подставляет для контейнера db), всё
# равно сохраняют приоритет над любым .env-файлом.
#
# Раньше все три файла грузились в фиксированном порядке .dev.env → .tests.env →
# .env независимо от API_MODE: тот файл, что успевал загрузиться первым,
# «застолбливал» совпадающие ключи в os.environ, а override=False не давал
# остальным файлам их переопределить — из-за этого API_MODE=test в обход
# pytest (который подстраховывался в conftest.py) резолвил DB_NAME=task_manager
# (из .dev.env), а не task_manager_test (из .tests.env).
_ENV_FILE_BY_MODE = {
    "test": ".tests.env", "testing": ".tests.env",
    "dev": ".dev.env", "development": ".dev.env",
    "prod": ".env", "production": ".env",
}
_mode_env_file = _ENV_FILE_BY_MODE.get(os.getenv("API_MODE"))
if _mode_env_file is not None:
    load_dotenv(os.path.join(BASE_DIR, _mode_env_file), override=False)

# Остальные файлы — как и раньше, но теперь только источник значений для ключей,
# которых нет в уже загруженном файле режима: например CRM_* (crm/crm_config.py
# читает их через os.getenv() напрямую, минуя pydantic-settings) объявлены
# только в .dev.env и ни в одном режиме не «конкурируют» за них с другим файлом.
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

    # Дефолты = встроенные дефолты SQLAlchemy QueuePool (5 + 10 = максимум 15 соединений
    # на процесс) — раньше эти значения были неявными (SQLAlchemy подставляла их сама,
    # если pool_size/max_overflow не переданы в create_async_engine). Здесь они не меняют
    # текущее поведение, а делают его видимым и настраиваемым per-deployment: правильное
    # значение зависит от max_connections на стороне PostgreSQL и от числа воркеров uvicorn
    # (UVICORN_WORKERS в src/Dockerfile) — каждый воркер держит свой собственный пул, поэтому
    # (DB_POOL_SIZE + DB_MAX_OVERFLOW) × число_воркеров не должно приближаться к
    # max_connections БД. Наугад увеличивать нельзя: каждое соединение пула — это реальный
    # процесс postgres на стороне БД.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10

    # Нет значения по умолчанию: дефолт вида "smtp.yandex.ru" молча привязал бы проект
    # к конкретному почтовому провайдеру — при разворачивании на другом инстансе без
    # явного SMTP_HOST письма подтверждения email тихо шли бы через чужой SMTP-сервер
    # (или падали бы с ошибкой аутентификации, которую трудно связать с причиной).
    # Та же логика, что и у REG_TOKEN_SECRET выше — конфигурация внешнего сервиса не
    # должна иметь скрытого дефолта. SMTP_PORT=465 оставлен с дефолтом: это стандартный
    # порт SMTPS (implicit TLS), не привязанный к конкретному провайдеру.
    SMTP_HOST: str
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""

    # Отдельный секрет для reg_token — компрометация access_secret не позволяет
    # подделать токен незавершённой регистрации.
    # Нет значения по умолчанию: приложение не запустится без явно заданной переменной
    # окружения REG_TOKEN_SECRET — случайный дефолт типа "change-me" был бы тихой уязвимостью.
    REG_TOKEN_SECRET: str
    REG_TOKEN_EXP: int = 1200  # 20 минут

    # Раньше был захардкожен как список Python-литералов прямо в классе, без связи с
    # переменными окружения — ProductionSettings его не переопределял, и смена origin'ов
    # для прода требовала правки этого файла, а не .env (см. историческую пометку об
    # этом в main.py у CORSMiddleware). CORS_ORIGINS_CSV — строка через запятую, а не
    # list[str]: pydantic-settings по умолчанию ожидает JSON-массив для env-значения
    # list[...], что неудобно писать в .env-файле; CSV проще. Дефолт ниже — только
    # localhost/127.0.0.1 для dev/test; для прода ОБЯЗАТЕЛЬНО переопределить через .env
    # реальным доменом приложения — иначе браузер будет блокировать запросы с фронтенда,
    # т.к. его Origin не попадёт в список.
    CORS_ORIGINS_CSV: str = (
        "http://localhost,http://localhost:8080,http://127.0.0.1:8000,"
        "http://localhost:3000,http://127.0.0.1:3000"
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS_CSV.split(",") if origin.strip()]

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
