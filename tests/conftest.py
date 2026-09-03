import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from unittest.mock import AsyncMock, patch

from src.auth.user_models import Role, User
from src.crm.subtask_service import get_subtask_crm_sync
from src.crm.task_service import get_task_crm_sync
from src.crm.user_service import get_user_lookup, get_user_registrar
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
def mock_crm():
    # Подмена через app.dependency_overrides, а не patch() по пути импорта: роутеры,
    # UserManager и /auth/login получают CRM-абстракции через
    # Depends(get_task_crm_sync)/Depends(get_subtask_crm_sync)/Depends(get_user_registrar)/
    # Depends(get_user_lookup) (см. src/crm/*) — тест подставляет фейковую реализацию прямо
    # в граф зависимостей FastAPI, не завися от того, где и как именно вызывающий код
    # импортирует конкретный класс.
    # test_crm.py работает с TaskManager/CRMClient/CRMUserSelector напрямую (не через app),
    # поэтому эта фикстура на него не влияет.
    mock_crm_instance = AsyncMock()
    mock_crm_instance.register_user.return_value = {"status": "success", "data": {"id": "99"}}

    mock_selector = AsyncMock()
    # find_user_by_email возвращает dict с id: login-эндпоинт проверяет,
    # что пользователь существует в CRM перед выдачей токенов.
    mock_selector.find_user_by_email.return_value = {"id": "99"}

    mock_task_mgr = AsyncMock()
    mock_task_mgr.create_task.return_value = {"id": 99}
    mock_task_mgr.update_task.return_value = {}
    mock_task_mgr.delete_task.return_value = {}

    mock_subtask_mgr = AsyncMock()
    # id=55: роутер делает crm_subtask_id=55 → crm_synced=True в ответе по умолчанию
    mock_subtask_mgr.create_subtask.return_value = {"id": 55, "response": {"status": "success"}}
    mock_subtask_mgr.update_subtask.return_value = {"status": "success"}
    mock_subtask_mgr.delete_subtask.return_value = {"status": "success"}

    app.dependency_overrides[get_user_registrar] = lambda: mock_crm_instance
    app.dependency_overrides[get_user_lookup] = lambda: mock_selector
    app.dependency_overrides[get_task_crm_sync] = lambda: mock_task_mgr
    app.dependency_overrides[get_subtask_crm_sync] = lambda: mock_subtask_mgr

    yield {
        "crm": mock_crm_instance,
        "selector": mock_selector,
        "task_mgr": mock_task_mgr,
        "subtask_mgr": mock_subtask_mgr,
    }

    del app.dependency_overrides[get_user_registrar]
    del app.dependency_overrides[get_user_lookup]
    del app.dependency_overrides[get_task_crm_sync]
    del app.dependency_overrides[get_subtask_crm_sync]


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
