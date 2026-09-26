import asyncio

import pytest
import pytest_asyncio
from fastapi_users.password import PasswordHelper
from httpx import AsyncClient, ASGITransport
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import selectinload
from unittest.mock import AsyncMock, Mock, patch

from src.auth.user_models import Role, User
from src.config import settings
from src.database import async_session_maker, engine, Base
from src.main import app

_REG_EMAIL    = "user@example.com"
_REG_PASSWORD = "Password1!"

DEFAULT_PASSWORD = "Password1!"
# Email администратора для общей фикстуры admin_client (sqladmin-панель).
ADMIN_PANEL_EMAIL = "admin_panel@example.com"


def _assert_test_database() -> None:
    """Защита от запуска на чужой БД: и drop_all, и TRUNCATE ниже необратимо
    стирают ВСЕ таблицы того, на что указывает engine. Режим test выбирает
    .tests.env (DB_NAME=task_manager_test), но при ошибке конфигурации (нет файла,
    переопределённая переменная окружения) без этой проверки тесты уничтожили бы
    рабочую БД."""
    database = engine.url.database or ""
    assert database.endswith("_test"), (
        f"Тесты запущены не на тестовой БД (DB_NAME={database!r}): имя должно "
        f"оканчиваться на '_test'. Проверьте API_MODE и src/.tests.env."
    )


@pytest.fixture(scope="session", autouse=True)
def _test_schema():
    """Схема БД создаётся ОДИН РАЗ за сессию (drop_all + create_all), а не перед
    каждым тестом: drop/create десятков таблиц на каждый из сотен тестов занимал
    заметную часть прогона. Между тестами данные чистит TRUNCATE (setup_and_reset
    ниже). drop_all здесь гарантирует, что схема соответствует текущим ORM-моделям
    (create_all не добавляет колонки к уже существующим таблицам). Отдельный
    NullPool-engine + asyncio.run: сессионная фикстура выполняется вне event loop
    отдельных тестов (pytest-asyncio создаёт loop на каждый тест)."""
    _assert_test_database()

    async def _rebuild() -> None:
        eng = create_async_engine(settings.ASYNC_DATABASE_URL, poolclass=NullPool)
        try:
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await eng.dispose()

    asyncio.run(_rebuild())
    yield


@pytest.fixture(autouse=True)
def fast_registration_code_hash():
    """Код подтверждения регистрации хешируется bcrypt с rounds=14 (~0.5 с на
    хеширование и столько же на проверку) — в каждом тесте, где регистрируется
    пользователь, это секунды впустую. В тестах подставляется тот же bcrypt с
    rounds=4 (проверка/хеширование внутри теста идут одним и тем же помощником).
    Пароли пользователей хешируются другим помощником (argon2id, см.
    src/auth/manager.py) — их это не касается."""
    fast = PasswordHelper(PasswordHash((BcryptHasher(rounds=4),)))
    with patch("src.auth.registration_endpoints.password_helper_bc", fast):
        yield fast


@pytest_asyncio.fixture(autouse=True)
async def setup_and_reset(_test_schema):
    _assert_test_database()
    # TRUNCATE ... RESTART IDENTITY CASCADE: все таблицы разом, счётчики id снова
    # с 1 (часть тестов обращается к первому пользователю/записи по id=1).
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))

    async with async_session_maker() as session:
        # Роли не создаются через миграции (Alembic управляет схемой, не данными).
        # После TRUNCATE таблица role пуста — вставляем без проверки на существующие записи.
        session.add_all([
            Role(id=1, name="user"),
            Role(id=2, name="admin"),
        ])
        await session.commit()

    yield


@pytest_asyncio.fixture
async def client():
    # ASGITransport позволяет httpx отправлять запросы напрямую в ASGI-приложение,
    # минуя TCP-стек — тесты не требуют запущенного сервера.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def promote_to_admin(email: str) -> None:
    """Повышает зарегистрированного пользователя до роли admin напрямую в БД.

    Replace-семантика (как и PATCH /users/{id}/role_ids): набор ролей пользователя
    заменяется на [admin] целиком, а не дополняется — после вызова у пользователя
    ровно одна роль, admin. Тесту, которому нужен пользователь одновременно с
    ролями user И admin, следует не использовать этот хелпер, а присвоить
    user.roles явным списком самому.

    Используется в тестах, которым нужен admin без прохождения полного flow
    управления ролями через API.
    """
    async with async_session_maker() as session:
        result = await session.execute(
            select(User).options(selectinload(User.roles)).where(User.email == email)
        )
        user = result.scalar_one()
        admin_role = (
            await session.execute(select(Role).where(Role.name == "admin"))
        ).scalar_one()
        # user.roles уже загружен через selectinload выше — bulk-replace на
        # незагруженной async-relationship падает MissingGreenlet (SQLAlchemy
        # не может лениво прочитать текущий список синхронно, чтобы вычислить
        # diff на удаление/добавление строк в user_role).
        user.roles = [admin_role]
        await session.commit()


@pytest.fixture(autouse=True)
def mock_outbox_dispatch():
    """Патчит dispatch_outbox_row (src/tasks/crm_outbox_tasks.py) во всех трёх
    продюсерах (services/tasks.py, services/subtasks.py, services/attachments.py
    импортируют его через `from ... import dispatch_outbox_row` — три отдельных
    локальных имени, патчить нужно каждое). Без этого create/update/delete
    задачи/подзадачи в любом HTTP- или сервисном тесте пытались бы поставить
    настоящую Celery-задачу (process_outbox_row.apply_async) в очередь —
    реального Redis/Celery-воркера в тестах нет (тот же принцип, что и для CRM/
    Redis Pub/Sub — мокается прямая зависимость, а не внешняя система).

    Сама CRM-синхронизация тестируется отдельно и напрямую — через содержимое
    вставленных строк CrmOutbox (см. tests/test_crm_outbox.py и обновлённые
    test_task_service.py/test_subtask_service.py) — не через факт диспатча.
    """
    # Mock, не AsyncMock: dispatch_outbox_row — обычная синхронная функция
    # (apply_async сам не блокирует и не требует await).
    mock = Mock()
    with patch("src.services.tasks.dispatch_outbox_row", mock), \
         patch("src.services.subtasks.dispatch_outbox_row", mock), \
         patch("src.services.attachments.dispatch_outbox_row", mock):
        yield mock


@pytest.fixture(autouse=True)
def mock_realtime_redis():
    """connection_manager.broadcast() (src/realtime/connection_manager.py)
    публикует каждое доменное событие (task_created/updated/..., п. 7 «Векторы
    развития проекта») в Redis Pub/Sub — вызывается из ЛЮБОГО HTTP-теста,
    трогающего create/update/delete задач или подзадач, не только из
    tests/test_realtime.py. Публикация уже best-effort (try/except в самом
    broadcast()), но без мока тесты всё равно пытались бы открыть настоящее
    TCP-соединение к REDIS_URL на каждый такой вызов — подменяем прямую
    зависимость, а не полагаемся на то, что внешний сервис недоступен и
    упадёт достаточно быстро.
    """
    fake = AsyncMock()
    with patch("src.realtime.connection_manager._get_redis", return_value=fake):
        yield fake


@pytest.fixture(autouse=True)
def mock_chat_history_redis():
    """src.realtime.chat_history (append_event/get_history_page — история
    панели WS: чат + CRUD-события задач/подзадач) использует отдельный
    module-level singleton _get_redis(), как и connection_manager.py (см. mock_realtime_redis
    выше) — свой клиент на каждый модуль. Без мока любой HTTP/WS-тест,
    трогающий GET /chat/history, WS-чат или create/update/delete задачи/
    подзадачи (broadcast_task_event теперь тоже вызывает append_event для
    CRUD- и файловых типов, см. src/realtime/events.py), попытался бы открыть настоящее
    соединение к REDIS_URL.

    lrange/get дефолтно сконфигурированы на «истории нет» — тестам, которым
    нужны конкретные сообщения, следует переопределить return_value/side_effect
    явно (см. tests/test_chat_endpoint.py). incr — на реальный счётчик (не
    голый AsyncMock): append_event кладёт результат incr прямо в id записи и
    сериализует её через json.dumps — неконфигурированный AsyncMock вместо
    int сломал бы json.dumps в КАЖДОМ тесте, который создаёт/меняет/удаляет
    задачу или подзадачу, не только в чат-специфичных тестах. Сама логика
    Redis List/пагинации тестируется отдельно, на самодельном FakeRedis —
    tests/test_chat_history.py.
    """
    fake = AsyncMock()
    fake.lrange.return_value = []
    fake.get.return_value = None
    _counter = {"n": 0}

    async def _incr(_key):
        _counter["n"] += 1
        return _counter["n"]

    fake.incr.side_effect = _incr
    with patch("src.realtime.chat_history._get_redis", return_value=fake):
        yield fake


@pytest.fixture(autouse=True)
def mock_smtp():
    # Перехватывает отправку письма и сохраняет код в словаре {email: code}.
    # Тесты читают код из словаря вместо реального ящика: mock_smtp[email].
    captured: dict = {}

    async def _fake_send(to_email: str, code: str) -> None:
        captured[to_email] = code

    with patch(
        "src.auth.registration_endpoints.send_confirmation_code",
        side_effect=_fake_send,
    ):
        yield captured


@pytest.fixture
def mock_magic():
    """Патчит magic.from_buffer для имитации MIME-детектирования по magic bytes.

    Логика определения типа по первым байтам (без зависимости от libmagic):
      b'%PDF...'  → "application/pdf"
      b'\\x89PNG'  → "image/png"
      b'PK...'    → docx/xlsx (ZIP-based OpenXML)
      иначе       → "application/octet-stream"  (неизвестный формат)

    Это позволяет тестам проверять MIME-валидацию без установки libmagic в CI.
    """
    def _detect(content, mime=True):
        if content[:4] == b'%PDF':
            return "application/pdf"
        if content[:4] == b'\x89PNG':
            return "image/png"
        if content[:2] == b'PK':
            # docx и xlsx — ZIP-архивы; magic возвращает MIME openxmlformats
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return "application/octet-stream"  # неизвестная сигнатура

    with patch("src.utils.file_utils.magic.from_buffer", side_effect=_detect):
        yield


@pytest.fixture
def upload_root(tmp_path):
    """Временная директория uploads/ для изоляции файловых тестов от диска.

    Патчит UPLOAD_ROOT в src.services.attachments — единственном месте, где
    роутеры task_files/subtask_files теперь строят пути (см. AttachmentConfig).
    Путь содержит 'uploads' в Path.parts — это обязательно для save_file(),
    которая вычисляет rel-путь через поиск 'uploads' в дереве директорий.
    """
    root = tmp_path / "uploads"
    root.mkdir()
    with patch("src.services.attachments.UPLOAD_ROOT", root):
        yield root


async def register_user(
    client: AsyncClient,
    mock_smtp: dict,
    email: str,
    password: str = DEFAULT_PASSWORD,
) -> None:
    """Вспомогательная функция: прогоняет полный трёхшаговый flow регистрации.

    Статус каждого шага проверяется: при сбое регистрации тест падает СРАЗУ и с
    понятным сообщением (какой шаг, что ответил сервер), а не позже с KeyError в
    mock_smtp или 401 на первом же запросе.
    """
    r = await client.post("/auth/register/request-code", json={"email": email})
    assert r.status_code == 200, f"request-code {email}: {r.status_code} {r.text}"
    code = mock_smtp[email]
    r = await client.post("/auth/register/verify-code", json={"email": email, "code": code})
    assert r.status_code == 200, f"verify-code {email}: {r.status_code} {r.text}"
    r = await client.post("/auth/register/complete", json={
        "firstname": "Test",
        "lastname":  "User",
        "password":  password,
    })
    assert r.status_code == 201, f"complete {email}: {r.status_code} {r.text}"


async def login(client: AsyncClient, email: str, password: str = DEFAULT_PASSWORD) -> None:
    """POST /auth/login с проверкой успеха (куки access_token выставлены)."""
    r = await client.post("/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"


async def register_and_login(
    client: AsyncClient, mock_smtp: dict, email: str, password: str = DEFAULT_PASSWORD,
) -> None:
    """Регистрация + вход — общий хелпер вместо копий _register_login/_auth."""
    await register_user(client, mock_smtp, email, password)
    await login(client, email, password)


async def login_as_admin(
    client: AsyncClient, mock_smtp: dict, email: str, password: str = DEFAULT_PASSWORD,
) -> None:
    """Регистрация → повышение до admin → перелогин (роли читаются при каждом
    запросе из БД, но перелогин даёт чистое состояние кук, как у настоящего
    администратора)."""
    await register_and_login(client, mock_smtp, email, password)
    await promote_to_admin(email)
    await client.post("/auth/logout")
    await login(client, email, password)


async def make_user(session, email: str = "alice@example.com") -> User:
    """Пользователь напрямую в БД (для сервисных тестов без HTTP).

    roles=[role] при конструировании нового User — не bulk-replace на уже
    загруженной связи (риск MissingGreenlet, см. auth/manager.py::UserManager.
    create()), а обычный kwarg конструктора transient-объекта."""
    role = (await session.execute(select(Role).where(Role.id == 1))).scalar_one()
    user = User(
        email=email, username=email.split("@")[0], firstname="A", lastname="B",
        hashed_password="x", roles=[role], is_active=True, is_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_client(client: AsyncClient, mock_smtp: dict) -> AsyncClient:
    """Клиент, залогиненный в sqladmin-панель (/admin) как admin."""
    await register_user(client, mock_smtp, ADMIN_PANEL_EMAIL)
    await promote_to_admin(ADMIN_PANEL_EMAIL)
    r = await client.post(
        "/admin/login",
        data={"username": ADMIN_PANEL_EMAIL, "password": DEFAULT_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 302, f"admin login: {r.status_code} {r.text}"
    return client


@pytest_asyncio.fixture
async def registered_user(client: AsyncClient, mock_smtp: dict) -> dict:
    """Регистрирует тестового пользователя через трёхшаговый flow."""
    await register_user(client, mock_smtp, _REG_EMAIL, _REG_PASSWORD)
    return {"email": _REG_EMAIL, "password": _REG_PASSWORD}
