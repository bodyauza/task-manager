"""
Юнит-тесты для каждой функции пакета src/crm.

Все HTTP-вызовы перехватываются через unittest.mock: реальных запросов нет.
Фикстура autouse mock_crm из conftest.py здесь переопределяется — каждый
тест настраивает собственный mock для полного контроля сценария.
"""
import json

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.crm.task_service import TaskManager
from src.crm.user_service import CRMUserRegistrar, CRMUserSelector

# Ключи полей CRM выводятся из констант TaskManager, а не хардкодятся строками:
# сторонние разработчики меняют FIELD_TITLE/FIELD_DESCR/FIELD_DONE в task_service.py
# под свой demo-инстанс CRM, и эти тесты продолжают проходить без правок.
_FIELD_TITLE = f"field_{TaskManager.FIELD_TITLE}"
_FIELD_DESCR = f"field_{TaskManager.FIELD_DESCR}"
_FIELD_DONE  = f"field_{TaskManager.FIELD_DONE}"


# ── helpers ───────────────────────────────────────────────────────────────────

def _resp(data, status_code: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.raise_for_status = MagicMock()
    payload = {"status": "success", "data": data}
    mock.text = json.dumps(payload)
    mock.json.return_value = payload
    return mock


def _err_resp(msg: str) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.raise_for_status = MagicMock()
    payload = {"msg": msg}
    mock.text = json.dumps(payload)
    mock.json.return_value = payload
    return mock


def _patch_httpx(return_value=None, side_effect=None):
    """Патчит module-level singleton _shared_http_client напрямую — _get_shared_http_client()
    возвращает его без вызова конструктора httpx.AsyncClient.

    Клиент — не атрибут класса CRMClient (см. src/crm/client.py: cls._http = ... в
    classmethod создавал бы отдельный атрибут в каждом подклассе, а не мутировал
    бы базовый — отсюда и перенос на module-level переменную), а module-level
    переменная в src.crm.client. patch() подменяет её значение и восстанавливает
    исходное (None) после patcher.stop().
    """
    mock_http = AsyncMock()
    if side_effect:
        mock_http.post = AsyncMock(side_effect=side_effect)
    else:
        mock_http.post = AsyncMock(return_value=return_value)

    patcher = patch("src.crm.client._shared_http_client", new=mock_http)
    patcher.start()
    return patcher, mock_http


# ── CRMClient.register_user ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_user_success():
    """register_user возвращает ответ CRM при успешном запросе."""
    patcher, mock_http = _patch_httpx(_resp({"id": "42"}))
    try:
        result = await CRMUserRegistrar().register_user(
            group_id=6, firstname="Ivan", lastname="Petrov",
            username="ivan", email="ivan@example.com",
        )
        assert result["status"] == "success"
        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["action"] == "insert"
        assert payload["entity_id"] == 1
        assert payload["items"][0]["email"] == "ivan@example.com"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_register_user_connection_error():
    """register_user бросает Exception при недоступности CRM."""
    patcher, _ = _patch_httpx(side_effect=httpx.ConnectError("refused"))
    try:
        with pytest.raises(Exception, match="Connection error"):
            await CRMUserRegistrar().register_user(
                group_id=6, firstname="Ivan", lastname="Petrov",
                username="ivan", email="ivan@example.com",
            )
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_register_user_timeout():
    """register_user бросает Exception при превышении таймаута."""
    patcher, _ = _patch_httpx(side_effect=httpx.TimeoutException("timeout"))
    try:
        with pytest.raises(Exception, match="timed out"):
            await CRMUserRegistrar().register_user(
                group_id=6, firstname="Ivan", lastname="Petrov",
                username="ivan", email="ivan@example.com",
            )
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_register_user_crm_api_error():
    """register_user бросает Exception, если CRM вернула ошибку в теле ответа."""
    patcher, _ = _patch_httpx(_err_resp("Email already exists"))
    try:
        with pytest.raises(Exception, match="CRM API error"):
            await CRMUserRegistrar().register_user(
                group_id=6, firstname="Ivan", lastname="Petrov",
                username="ivan", email="ivan@example.com",
            )
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_register_user_invalid_json():
    """register_user бросает Exception при невалидном JSON."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = "not-json"
    mock_resp.json.side_effect = ValueError("invalid json")

    patcher, _ = _patch_httpx(mock_resp)
    try:
        with pytest.raises(Exception, match="invalid JSON"):
            await CRMUserRegistrar().register_user(
                group_id=6, firstname="Ivan", lastname="Petrov",
                username="ivan", email="ivan@example.com",
            )
    finally:
        patcher.stop()


# ── CRMUserSelector.find_user_by_email ───────────────────────────────────────

@pytest.mark.asyncio
async def test_find_user_by_email_found():
    """find_user_by_email возвращает первую запись, если пользователь найден."""
    user_data = {"id": "30", "9": "ivan@example.com", "7": "Ivan", "8": "Petrov"}
    patcher, mock_http = _patch_httpx(_resp([user_data]))
    try:
        result = await CRMUserSelector().find_user_by_email("ivan@example.com")
        assert result is not None
        assert result["id"] == "30"
        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["action"] == "select"
        assert payload["filters"]["9"]["condition"] == "include"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_find_user_by_email_not_found():
    """find_user_by_email возвращает None при пустом data."""
    patcher, _ = _patch_httpx(_resp([]))
    try:
        assert await CRMUserSelector().find_user_by_email("nobody@example.com") is None
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_find_user_by_email_connection_error():
    patcher, _ = _patch_httpx(side_effect=httpx.ConnectError("refused"))
    try:
        with pytest.raises(Exception, match="Connection error"):
            await CRMUserSelector().find_user_by_email("ivan@example.com")
    finally:
        patcher.stop()


# ── TaskManager.create_task ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_task_success_dict_data():
    """create_task возвращает CRM-ID из ответа формата data: {id: ...}."""
    patcher, mock_http = _patch_httpx(_resp({"id": "17"}))
    try:
        result = await TaskManager().create_task(title="Task A", description="Desc A")
        assert result["id"] == 17
        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["action"] == "insert"
        assert payload["entity_id"] == TaskManager.ENTITY_ID
        assert payload["items"][0][_FIELD_TITLE] == "Task A"
        assert payload["items"][0][_FIELD_DONE] == "false"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_create_task_success_list_data():
    """create_task корректно извлекает CRM-ID из ответа формата data: [{id: ...}]."""
    patcher, _ = _patch_httpx(_resp([{"id": "99"}]))
    try:
        result = await TaskManager().create_task(title="Task B", description="Desc B", completed=True)
        assert result["id"] == 99
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_create_task_connection_error():
    patcher, _ = _patch_httpx(side_effect=httpx.ConnectError("refused"))
    try:
        with pytest.raises(Exception, match="Connection error"):
            await TaskManager().create_task(title="T", description="D")
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_create_task_crm_api_error():
    patcher, _ = _patch_httpx(_err_resp("Duplicate title"))
    try:
        with pytest.raises(Exception, match="CRM API error"):
            await TaskManager().create_task(title="Existing", description="D")
    finally:
        patcher.stop()


# ── TaskManager.update_task ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_task_success():
    """update_task передаёт только заполненные поля."""
    patcher, mock_http = _patch_httpx(_resp({"id": "17"}))
    try:
        result = await TaskManager().update_task(task_id=17, title="New Title", completed=True)
        assert result["status"] == "success"
        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["action"] == "update"
        assert payload["data"][_FIELD_TITLE] == "New Title"
        assert payload["data"][_FIELD_DONE] == "true"
        assert _FIELD_DESCR not in payload["data"]
        assert payload["update_by_field"] == {"id": 17}
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_update_task_empty_id_raises():
    """Регрессия (docs/crm_issue.md): если задачу удалили в CRM напрямую, CRM отвечает
    "success" с пустым data.id вместо ошибки — expect_id должен превратить это в Exception,
    чтобы update_task() в services/tasks.py выставил crm_synced=False, а не True."""
    patcher, _ = _patch_httpx(_resp({"id": ""}))
    try:
        with pytest.raises(Exception, match="no valid id"):
            await TaskManager().update_task(task_id=17, title="New Title")
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_update_task_no_fields():
    """update_task возвращает skipped без HTTP-запроса, если нет полей."""
    patcher, mock_http = _patch_httpx(_resp({}))
    try:
        result = await TaskManager().update_task(task_id=17)
        assert result["status"] == "skipped"
        mock_http.post.assert_not_called()
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_update_task_connection_error():
    patcher, _ = _patch_httpx(side_effect=httpx.ConnectError("refused"))
    try:
        with pytest.raises(Exception, match="Connection error"):
            await TaskManager().update_task(task_id=17, title="X")
    finally:
        patcher.stop()


# ── TaskManager.delete_task ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_task_success():
    """delete_task передаёт delete_by_field с CRM-ID."""
    patcher, mock_http = _patch_httpx(_resp({"id": "17"}))
    try:
        result = await TaskManager().delete_task(task_id=17)
        assert result["status"] == "success"
        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["action"] == "delete"
        assert payload["entity_id"] == TaskManager.ENTITY_ID
        assert payload["delete_by_field"] == {"id": 17}
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_delete_task_empty_id_raises():
    """Регрессия (docs/crm_issue.md): та же дыра, что и в test_update_task_empty_id_raises,
    но для delete_task() — повторное/запоздалое удаление уже отсутствующей в CRM записи."""
    patcher, _ = _patch_httpx(_resp({"id": ""}))
    try:
        with pytest.raises(Exception, match="no valid id"):
            await TaskManager().delete_task(task_id=17)
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_delete_task_connection_error():
    patcher, _ = _patch_httpx(side_effect=httpx.ConnectError("refused"))
    try:
        with pytest.raises(Exception, match="Connection error"):
            await TaskManager().delete_task(task_id=17)
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_delete_task_crm_api_error():
    patcher, _ = _patch_httpx(_err_resp("Record not found"))
    try:
        with pytest.raises(Exception, match="CRM API error"):
            await TaskManager().delete_task(task_id=999)
    finally:
        patcher.stop()


# ── TaskManager._bool_to_crm ─────────────────────────────────────────────────

def test_bool_to_crm_true():
    assert TaskManager._bool_to_crm(True) == "true"


def test_bool_to_crm_false():
    assert TaskManager._bool_to_crm(False) == "false"
