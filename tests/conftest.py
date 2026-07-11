import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from unittest.mock import AsyncMock, patch

from src.auth.models import Role, User
from src.database import async_session_maker, engine, Base
from src.main import app

_REG_EMAIL    = "user@example.com"
_REG_PASSWORD = "Password1!"


@pytest_asyncio.fixture(autouse=True)
async def setup_and_reset():
    # drop_all + create_all гарантирует, что схема БД всегда соответствует
    # текущим ORM-моделям. create_all идемпотентен и не добавляет колонки к
    # существующим таблицам, поэтому без drop_all изменения схемы (новые поля,
    # удалённые колонки) не применялись бы в тестовой БД.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        # Роли не создаются через миграции (Alembic управляет схемой, не данными).
        # После drop_all таблица role пуста — вставляем без проверки на существующие записи.
        session.add_all([
            Role(id=1, name="user",  permissions=["read", "write"]),
            Role(id=2, name="admin", permissions=["read", "write", "delete"]),
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
    """Повышает зарегистрированного пользователя до роли admin (role_id=2) напрямую в БД.

    Используется в тестах, которым нужен admin без прохождения полного flow
    управления ролями через API.
    """
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        user.role_id = 2
        await session.commit()


@pytest.fixture(autouse=True)
def mock_crm():
    # Патчим на уровне src.crm.*, а не в местах использования (manager.py, endpoints.py),
    # т.к. там применяются отложенные импорты внутри функций. Каждый вызов
    # `from src.crm.X import Y` читает атрибут из кэшированного модуля — патч
    # на уровне модуля-источника перехватывает все такие вызовы.
    # test_crm.py импортирует классы на уровне модуля до активации патча,
    # поэтому там используются реальные экземпляры с httpx-мок.
    with patch("src.crm.client.CRMClient") as mock_crm_cls, \
         patch("src.crm.user_service.CRMUserSelector") as mock_selector_cls, \
         patch("src.crm.task_service.TaskManager") as mock_task_mgr_cls, \
         patch("src.crm.subtask_service.SubtaskManager") as mock_subtask_mgr_cls:

        mock_crm_instance = AsyncMock()
        mock_crm_instance.register_user.return_value = {"status": "success", "data": {"id": "99"}}
        mock_crm_cls.return_value = mock_crm_instance

        mock_selector = AsyncMock()
        # find_user_by_email возвращает dict с id: login-эндпоинт проверяет,
        # что пользователь существует в CRM перед выдачей токенов.
        mock_selector.find_user_by_email.return_value = {"id": "99"}
        mock_selector_cls.return_value = mock_selector

        mock_task_mgr = AsyncMock()
        mock_task_mgr.create_task.return_value = {"id": 99}
        mock_task_mgr.update_task.return_value = {}
        mock_task_mgr.delete_task.return_value = {}
        mock_task_mgr_cls.return_value = mock_task_mgr

        mock_subtask_mgr = AsyncMock()
        # id=55: роутер делает crm_subtask_id=55 → crm_synced=True в ответе по умолчанию
        mock_subtask_mgr.create_subtask.return_value = {"id": 55, "response": {"status": "success"}}
        mock_subtask_mgr.update_subtask.return_value = {"status": "success"}
        mock_subtask_mgr.delete_subtask.return_value = {"status": "success"}
        mock_subtask_mgr_cls.return_value = mock_subtask_mgr

        yield {
            "crm": mock_crm_instance,
            "selector": mock_selector,
            "task_mgr": mock_task_mgr,
            "subtask_mgr": mock_subtask_mgr,
        }


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

    Патчит UPLOAD_ROOT в обоих файловых роутерах (task_files, subtask_files).
    Путь содержит 'uploads' в Path.parts — это обязательно для save_file(),
    которая вычисляет rel-путь через поиск 'uploads' в дереве директорий.
    """
    root = tmp_path / "uploads"
    root.mkdir()
    with patch("src.routers.task_files.UPLOAD_ROOT", root), \
         patch("src.routers.subtask_files.UPLOAD_ROOT", root):
        yield root


async def register_user(
    client: AsyncClient,
    mock_smtp: dict,
    email: str,
    password: str = "Password1!",
) -> None:
    """Вспомогательная функция: прогоняет полный трёхшаговый flow регистрации.

    Используется в фикстурах и тестах, которым нужен готовый пользователь,
    а не проверка самого flow регистрации.
    """
    await client.post("/auth/register/request-code", json={"email": email})
    code = mock_smtp[email]
    await client.post("/auth/register/verify-code", json={"email": email, "code": code})
    await client.post("/auth/register/complete", json={
        "firstname": "Test",
        "lastname":  "User",
        "password":  password,
    })


@pytest_asyncio.fixture
async def registered_user(client: AsyncClient, mock_smtp: dict) -> dict:
    """Регистрирует тестового пользователя через трёхшаговый flow."""
    await client.post(
        "/auth/register/request-code", json={"email": _REG_EMAIL}
    )
    code = mock_smtp[_REG_EMAIL]
    await client.post(
        "/auth/register/verify-code", json={"email": _REG_EMAIL, "code": code}
    )
    await client.post(
        "/auth/register/complete",
        json={"firstname": "Test", "lastname": "User", "password": _REG_PASSWORD},
    )
    return {"email": _REG_EMAIL, "password": _REG_PASSWORD}
