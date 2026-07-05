# Покрывает трёхшаговый регистрационный flow:
#   POST /auth/register/request-code  — отправка кода на email
#   POST /auth/register/verify-code   — проверка кода, выдача reg_token cookie
#   POST /auth/register/complete      — создание записи в person
import pytest
from httpx import AsyncClient

from tests.conftest import promote_to_admin

VALID_EMAIL    = "new@example.com"
VALID_PASSWORD = "Password1!"


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _request_code(client: AsyncClient, email: str = VALID_EMAIL):
    return await client.post(
        "/auth/register/request-code", json={"email": email}
    )


async def _verify_code(client: AsyncClient, mock_smtp: dict, email: str = VALID_EMAIL):
    code = mock_smtp[email]
    return await client.post(
        "/auth/register/verify-code", json={"email": email, "code": code}
    )


async def _complete(
    client: AsyncClient,
    password: str = VALID_PASSWORD,
    patronymic: str | None = None,
):
    body = {"firstname": "Иван", "lastname": "Иванов", "password": password}
    if patronymic is not None:
        body["patronymic"] = patronymic
    return await client.post("/auth/register/complete", json=body)


# ─── request-code ─────────────────────────────────────────────────────────────

async def test_request_code_success(client: AsyncClient, mock_smtp: dict):
    r = await _request_code(client)
    assert r.status_code == 200
    assert VALID_EMAIL in mock_smtp


async def test_request_code_normalises_email(client: AsyncClient, mock_smtp: dict):
    r = await _request_code(client, email="  NEW@EXAMPLE.COM  ")
    assert r.status_code == 200
    assert "new@example.com" in mock_smtp


async def test_request_code_invalid_email(client: AsyncClient):
    r = await _request_code(client, email="not-an-email")
    assert r.status_code == 400
    assert r.json()["detail"] == "INVALID_EMAIL"


async def test_request_code_duplicate_email(client: AsyncClient, register_and_login: dict):
    # register_and_login регистрирует user@example.com
    r = await _request_code(client, email=register_and_login["email"])
    assert r.status_code == 409
    assert r.json()["detail"] == "EMAIL_ALREADY_REGISTERED"


async def test_request_code_rate_limit(client: AsyncClient, mock_smtp: dict):
    await _request_code(client)               # первый — OK
    r = await _request_code(client)           # второй в пределах 60 с — 429
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail.startswith("RATE_LIMIT:")
    assert int(detail.split(":")[1]) > 0


# ─── verify-code ──────────────────────────────────────────────────────────────

async def test_verify_code_success(client: AsyncClient, mock_smtp: dict):
    await _request_code(client)
    r = await _verify_code(client, mock_smtp)
    assert r.status_code == 200
    assert "reg_token" in r.cookies


async def test_verify_code_no_pending(client: AsyncClient):
    r = await client.post(
        "/auth/register/verify-code",
        json={"email": VALID_EMAIL, "code": "123456"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "NO_PENDING_REGISTRATION"


async def test_verify_code_wrong_code(client: AsyncClient, mock_smtp: dict):
    await _request_code(client)
    r = await client.post(
        "/auth/register/verify-code",
        json={"email": VALID_EMAIL, "code": "000000"},
    )
    assert r.status_code == 400
    assert r.json()["detail"].startswith("INVALID_CODE:")


async def test_verify_code_attempts_count_down(client: AsyncClient, mock_smtp: dict):
    await _request_code(client)
    for expected_remaining in (2, 1, 0):
        r = await client.post(
            "/auth/register/verify-code",
            json={"email": VALID_EMAIL, "code": "000000"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == f"INVALID_CODE:{expected_remaining}"


async def test_verify_code_max_attempts_blocks(client: AsyncClient, mock_smtp: dict):
    await _request_code(client)
    # исчерпать 3 попытки
    for _ in range(3):
        await client.post(
            "/auth/register/verify-code",
            json={"email": VALID_EMAIL, "code": "000000"},
        )
    # 4-й вызов: запись уже удалена после 3-й попытки — TOO_MANY_ATTEMPTS
    r = await client.post(
        "/auth/register/verify-code",
        json={"email": VALID_EMAIL, "code": "000000"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "TOO_MANY_ATTEMPTS"


async def test_verify_code_invalid_format(client: AsyncClient, mock_smtp: dict):
    await _request_code(client)
    r = await client.post(
        "/auth/register/verify-code",
        json={"email": VALID_EMAIL, "code": "abc"},
    )
    assert r.status_code == 422  # Pydantic: pattern не совпадает


async def test_verify_code_too_short(client: AsyncClient, mock_smtp: dict):
    await _request_code(client)
    r = await client.post(
        "/auth/register/verify-code",
        json={"email": VALID_EMAIL, "code": "12345"},
    )
    assert r.status_code == 422


# ─── complete ─────────────────────────────────────────────────────────────────

async def test_complete_success(client: AsyncClient, mock_smtp: dict):
    await _request_code(client)
    await _verify_code(client, mock_smtp)
    r = await _complete(client)
    assert r.status_code == 201


async def test_complete_no_token(client: AsyncClient):
    r = await _complete(client)
    assert r.status_code == 401
    assert r.json()["detail"] == "MISSING_REG_TOKEN"


async def test_complete_weak_password(client: AsyncClient, mock_smtp: dict):
    await _request_code(client)
    await _verify_code(client, mock_smtp)
    r = await _complete(client, password="weak")
    assert r.status_code == 422


async def test_complete_missing_firstname(client: AsyncClient, mock_smtp: dict):
    await _request_code(client)
    await _verify_code(client, mock_smtp)
    r = await client.post(
        "/auth/register/complete",
        json={"firstname": "", "lastname": "Иванов", "password": VALID_PASSWORD},
    )
    assert r.status_code == 422


# ─── full flow + login ────────────────────────────────────────────────────────

async def test_full_registration_then_login(client: AsyncClient, mock_smtp: dict):
    r1 = await _request_code(client)
    assert r1.status_code == 200

    r2 = await _verify_code(client, mock_smtp)
    assert r2.status_code == 200
    assert "reg_token" in r2.cookies

    r3 = await _complete(client)
    assert r3.status_code == 201

    r4 = await client.post(
        "/auth/login", data={"username": VALID_EMAIL, "password": VALID_PASSWORD}
    )
    assert r4.status_code == 200
    assert "access_token" in r4.cookies


async def test_reg_token_deleted_after_complete(client: AsyncClient, mock_smtp: dict):
    await _request_code(client)
    await _verify_code(client, mock_smtp)
    r = await _complete(client)
    assert r.status_code == 201
    # reg_token должен быть удалён или иметь пустое значение
    cookie = r.cookies.get("reg_token", "")
    assert cookie == "" or "reg_token" not in r.cookies


async def test_code_consumed_after_verify(client: AsyncClient, mock_smtp: dict):
    await _request_code(client)
    code = mock_smtp[VALID_EMAIL]
    await client.post(
        "/auth/register/verify-code", json={"email": VALID_EMAIL, "code": code}
    )
    # Повторная попытка верификации — запись уже удалена
    r = await client.post(
        "/auth/register/verify-code", json={"email": VALID_EMAIL, "code": code}
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "NO_PENDING_REGISTRATION"


# ─── patronymic ───────────────────────────────────────────────────────────────

async def test_complete_with_patronymic(client: AsyncClient, mock_smtp: dict):
    # Отчество передано — регистрация должна завершиться с кодом 201.
    await _request_code(client)
    await _verify_code(client, mock_smtp)
    r = await _complete(client, patronymic="Сергеевич")
    assert r.status_code == 201


async def test_complete_without_patronymic(client: AsyncClient, mock_smtp: dict):
    # Поле необязательное: отсутствие patronymic в теле запроса не должно
    # приводить к ошибке валидации — Pydantic подставляет None по умолчанию.
    await _request_code(client)
    await _verify_code(client, mock_smtp)
    r = await _complete(client)
    assert r.status_code == 201


async def test_patronymic_stored_and_returned(client: AsyncClient, mock_smtp: dict):
    # После регистрации с отчеством значение должно возвращаться в GET /users/
    # (поле patronymic включено в UserRead).
    # Для доступа к /users/ пользователь повышается до admin.
    email = "patronymic@example.com"
    await _request_code(client, email=email)
    await _verify_code(client, mock_smtp, email=email)
    await client.post(
        "/auth/register/complete",
        json={
            "firstname":  "Иван",
            "lastname":   "Иванов",
            "patronymic": "Сергеевич",
            "password":   VALID_PASSWORD,
        },
    )

    await client.post("/auth/login", data={"username": email, "password": VALID_PASSWORD})
    await promote_to_admin(email)
    await client.post("/auth/logout")
    await client.post("/auth/login", data={"username": email, "password": VALID_PASSWORD})

    users = (await client.get("/users/")).json()
    record = next((u for u in users if u["email"] == email), None)
    assert record is not None
    assert record["patronymic"] == "Сергеевич"


async def test_patronymic_null_when_omitted(client: AsyncClient, mock_smtp: dict):
    # Если отчество не передано, в БД записывается NULL;
    # API возвращает null, а не пустую строку.
    email = "nopatr@example.com"
    await _request_code(client, email=email)
    await _verify_code(client, mock_smtp, email=email)
    await client.post(
        "/auth/register/complete",
        json={"firstname": "Анна", "lastname": "Петрова", "password": VALID_PASSWORD},
    )

    await client.post("/auth/login", data={"username": email, "password": VALID_PASSWORD})
    await promote_to_admin(email)
    await client.post("/auth/logout")
    await client.post("/auth/login", data={"username": email, "password": VALID_PASSWORD})

    users = (await client.get("/users/")).json()
    record = next((u for u in users if u["email"] == email), None)
    assert record is not None
    assert record["patronymic"] is None
