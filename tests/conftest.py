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
    """Защита от запуска на чужой БД: drop_all и TRUNCATE необратимо стирают все таблицы того, на что указывает engine."""
    database = engine.url.database or ""
    assert database.endswith("_test"), (
        f"Тесты запущены не на тестовой БД (DB_NAME={database!r}): имя должно "
        f"оканчиваться на '_test'. Проверьте API_MODE и src/.tests.env."
    )


@pytest.fixture(scope="session", autouse=True)
def _test_schema():
    """Схема создаётся один раз за сессию (drop_all + create_all), между тестами данные чистит TRUNCATE.
    Отдельный NullPool-engine + asyncio.run: сессионная фикстура выполняется вне event loop тестов.
    """
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
    """Код подтверждения хешируется bcrypt; в тестах rounds=4 ускоряет registration-flow тесты. Пароли пользователей (argon2id) это не затрагивает."""
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
    """Повышает пользователя до admin напрямую в БД (replace-семантика: после вызова у него ровно одна роль, admin)."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(User).options(selectinload(User.roles)).where(User.email == email)
        )
        user = result.scalar_one()
        admin_role = (
            await session.execute(select(Role).where(Role.name == "admin"))
        ).scalar_one()
        # user.roles загружен через selectinload — иначе bulk-replace падает MissingGreenlet.
        user.roles = [admin_role]
        await session.commit()


@pytest.fixture(autouse=True)
def mock_outbox_dispatch():
    """Патчит dispatch_outbox_row во всех трёх продюсерах (services/tasks.py, subtasks.py, attachments.py — три отдельных
    имени), чтобы тесты не ставили настоящую Celery-задачу. Синхронизацию проверяют по содержимому строк CrmOutbox.
    """
    # AsyncMock, а не Mock: dispatch_outbox_row асинхронная, и вызывающий код делает await.
    mock = AsyncMock()
    with patch("src.services.tasks.dispatch_outbox_row", mock), \
         patch("src.services.subtasks.dispatch_outbox_row", mock), \
         patch("src.services.attachments.dispatch_outbox_row", mock):
        yield mock


@pytest.fixture(autouse=True)
def mock_realtime_redis():
    """connection_manager.broadcast() публикует событие в Redis Pub/Sub из любого HTTP-теста, трогающего задачи/подзадачи;
    без мока каждый такой вызов открывал бы настоящее соединение к REDIS_URL.
    """
    fake = AsyncMock()
    with patch("src.realtime.connection_manager._get_redis", return_value=fake):
        yield fake


@pytest.fixture(autouse=True)
def mock_chat_history_redis():
    """src.realtime.chat_history использует свой module-level _get_redis(). Мок нужен любому HTTP/WS-тесту, который пишет
    в историю или читает её. lrange/get по умолчанию — «истории нет» (переопределяются в тестах); incr — реальный счётчик,
    иначе json.dumps записи сломался бы на AsyncMock. Логика Redis List проверяется на FakeRedis (tests/test_chat_history.py).
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
def mock_registration_rate_limit_redis():
    """src.auth.registration_rate_limit (лимит по IP на POST /auth/register/request-code) — свой module-level _get_redis().
    incr — реальный счётчик по ключу (client_ip): сравнение с MAX_REQUESTS_PER_WINDOW иначе падало бы на AsyncMock.
    В ASGITransport-тестах IP всегда "127.0.0.1".
    """
    fake = AsyncMock()
    counters: dict[str, int] = {}

    async def _incr(key):
        counters[key] = counters.get(key, 0) + 1
        return counters[key]

    fake.incr.side_effect = _incr
    with patch("src.auth.registration_rate_limit._get_redis", return_value=fake):
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
    """Патчит magic.from_buffer: тип определяется по первым байтам без libmagic.

    PDF, PNG и JPEG распознаются по сигнатурам, PK (docx) — формат больше не разрешён и нужен тестам на отклонение,
    всё остальное — application/octet-stream.
    """
    def _detect(content, mime=True):
        if content[:4] == b'%PDF':
            return "application/pdf"
        if content[:4] == b'\x89PNG':
            return "image/png"
        if content[:3] == b'\xff\xd8\xff':
            return "image/jpeg"
        if content[:2] == b'PK':
            # docx и xlsx — ZIP-архивы; magic возвращает MIME openxmlformats
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return "application/octet-stream"  # неизвестная сигнатура

    with patch("src.utils.file_utils.magic.from_buffer", side_effect=_detect):
        yield


@pytest.fixture
def upload_root(tmp_path):
    """Временная директория uploads/ для изоляции файловых тестов.

    Патчит UPLOAD_ROOT в src.services.attachments (там строятся пути); save_file() принимает upload_root параметром.
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
    """Прогоняет трёхшаговый flow регистрации; статус каждого шага проверяется, чтобы тест падал сразу и с понятным сообщением."""
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
    """Регистрация → повышение до admin → перелогин (чистое состояние кук, как у настоящего администратора)."""
    await register_and_login(client, mock_smtp, email, password)
    await promote_to_admin(email)
    await client.post("/auth/logout")
    await login(client, email, password)


async def make_user(session, email: str = "alice@example.com") -> User:
    """Пользователь напрямую в БД (для сервисных тестов без HTTP). roles=[role] — kwarg конструктора, а не bulk-replace
    (риск MissingGreenlet).
    """
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
