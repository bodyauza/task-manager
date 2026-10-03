"""Юнит-тесты SubtaskManager (src/crm/subtask_service.py); HTTP-вызовы перехватываются через unittest.mock."""
import json

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.crm.subtask_service import SubtaskManager

# Ключи полей CRM берутся из констант SubtaskManager, а не хардкодятся.
_FIELD_TITLE    = f"field_{SubtaskManager.FIELD_TITLE}"
_FIELD_DESCR    = f"field_{SubtaskManager.FIELD_DESCR}"
_FIELD_DONE     = f"field_{SubtaskManager.FIELD_DONE}"
_FIELD_LOCAL_ID = f"field_{SubtaskManager.FIELD_LOCAL_ID}"


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
    # _shared_http_client — module-level singleton в src.crm.client (не атрибут
    # класса CRMClient), см. пояснение в tests/test_crm.py::_patch_httpx.
    mock_http = AsyncMock()
    if side_effect:
        mock_http.post = AsyncMock(side_effect=side_effect)
    else:
        mock_http.post = AsyncMock(return_value=return_value)
    patcher = patch("src.crm.client._shared_http_client", new=mock_http)
    patcher.start()
    return patcher, mock_http


@pytest.mark.asyncio
async def test_create_subtask_success_dict_data():
    """create_subtask извлекает CRM-ID из ответа формата data: {id: ...}."""
    patcher, mock_http = _patch_httpx(_resp({"id": "55"}))
    try:
        result = await SubtaskManager().create_subtask(
            parent_item_id=99, local_id=5, title="Write test", description="desc"
        )
        assert result["id"] == 55
        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["action"] == "insert"
        assert payload["entity_id"] == SubtaskManager.ENTITY_ID
        assert payload["items"][0][_FIELD_TITLE] == "Write test"
        assert payload["items"][0][_FIELD_DESCR] == "desc"
        assert payload["items"][0][_FIELD_DONE] == "false"   # completed=False по умолчанию
        assert payload["items"][0]["parent_item_id"] == 99
        assert payload["items"][0][_FIELD_LOCAL_ID] == "5"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_create_subtask_success_list_data():
    """create_subtask корректно извлекает CRM-ID из ответа формата data: [{id: ...}]."""
    patcher, _ = _patch_httpx(_resp([{"id": "77"}]))
    try:
        result = await SubtaskManager().create_subtask(
            parent_item_id=10, local_id=5, title="Sub", description=""
        )
        assert result["id"] == 77
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_create_subtask_completed_true():
    """create_subtask передаёт field_done="true" при completed=True."""
    patcher, mock_http = _patch_httpx(_resp({"id": "1"}))
    try:
        await SubtaskManager().create_subtask(
            parent_item_id=10, local_id=5, title="Done", description="", completed=True
        )
        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["items"][0][_FIELD_DONE] == "true"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_create_subtask_connection_error():
    patcher, _ = _patch_httpx(side_effect=httpx.ConnectError("refused"))
    try:
        with pytest.raises(Exception, match="Connection error"):
            await SubtaskManager().create_subtask(
                parent_item_id=1, local_id=1, title="X", description=""
            )
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_create_subtask_timeout():
    patcher, _ = _patch_httpx(side_effect=httpx.TimeoutException("timeout"))
    try:
        with pytest.raises(Exception, match="timed out"):
            await SubtaskManager().create_subtask(
                parent_item_id=1, local_id=1, title="X", description=""
            )
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_create_subtask_crm_api_error():
    patcher, _ = _patch_httpx(_err_resp("Duplicate subtask"))
    try:
        with pytest.raises(Exception, match="CRM API error"):
            await SubtaskManager().create_subtask(
                parent_item_id=1, local_id=1, title="X", description=""
            )
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_create_subtask_with_creator_email():
    """create_subtask передаёт email создателя, если он указан."""
    _FIELD_CREATOR_EMAIL = f"field_{SubtaskManager.FIELD_CREATOR_EMAIL}"
    patcher, mock_http = _patch_httpx(_resp({"id": "1"}))
    try:
        await SubtaskManager().create_subtask(
            parent_item_id=1, local_id=1, title="X", description="", creator_email="user@example.com"
        )
        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["items"][0][_FIELD_CREATOR_EMAIL] == "user@example.com"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_create_subtask_without_creator_email_omits_field():
    """creator_email=None (дефолт) — поле в CRM не отправляется вовсе."""
    _FIELD_CREATOR_EMAIL = f"field_{SubtaskManager.FIELD_CREATOR_EMAIL}"
    patcher, mock_http = _patch_httpx(_resp({"id": "1"}))
    try:
        await SubtaskManager().create_subtask(parent_item_id=1, local_id=1, title="X", description="")
        payload = mock_http.post.call_args.kwargs["json"]
        assert _FIELD_CREATOR_EMAIL not in payload["items"][0]
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_create_subtask_invalid_json():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = "not-json"
    mock_resp.json.side_effect = ValueError("invalid json")
    patcher, _ = _patch_httpx(mock_resp)
    try:
        with pytest.raises(Exception, match="invalid JSON"):
            await SubtaskManager().create_subtask(
                parent_item_id=1, local_id=1, title="X", description=""
            )
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_find_subtask_searches_by_local_id():
    """find_subtask ищет по точному Local ID (`field_{FIELD_LOCAL_ID}`), а не по title/description/parent_item_id."""
    patcher, mock_http = _patch_httpx(_resp([{"id": "901"}]))
    try:
        result = await SubtaskManager().find_subtask(5)
        assert result == {"id": "901"}
        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["action"] == "select"
        assert payload["select_fields"] == str(SubtaskManager.FIELD_LOCAL_ID)
        assert payload["filters"] == {str(SubtaskManager.FIELD_LOCAL_ID): {"value": "5", "condition": "include"}}
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_find_subtask_returns_none_when_not_found():
    patcher, _ = _patch_httpx(_resp([]))
    try:
        assert await SubtaskManager().find_subtask(999) is None
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_update_subtask_success():
    """update_subtask передаёт только заполненные поля в data."""
    patcher, mock_http = _patch_httpx(_resp({"id": "55"}))
    try:
        result = await SubtaskManager().update_subtask(
            subtask_id=55, title="New Title", completed=True
        )
        assert result["status"] == "success"
        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["action"] == "update"
        assert payload["entity_id"] == SubtaskManager.ENTITY_ID
        assert payload["data"][_FIELD_TITLE] == "New Title"
        assert payload["data"][_FIELD_DONE] == "true"
        assert _FIELD_DESCR not in payload["data"]   # description не передан → не попал в data
        assert payload["update_by_field"] == {"id": 55}
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_update_subtask_empty_id_raises():
    """Если подзадачу удалили в CRM напрямую, ответ «success» с пустым data.id должен стать Exception (expect_id), чтобы строка outbox осталась на повтор."""
    patcher, _ = _patch_httpx(_resp({"id": ""}))
    try:
        with pytest.raises(Exception, match="no valid id"):
            await SubtaskManager().update_subtask(subtask_id=55, title="New Title")
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_update_subtask_no_fields():
    """update_subtask возвращает skipped без HTTP-запроса, если нет полей."""
    patcher, mock_http = _patch_httpx(_resp({}))
    try:
        result = await SubtaskManager().update_subtask(subtask_id=55)
        assert result["status"] == "skipped"
        mock_http.post.assert_not_called()
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_update_subtask_connection_error():
    patcher, _ = _patch_httpx(side_effect=httpx.ConnectError("refused"))
    try:
        with pytest.raises(Exception, match="Connection error"):
            await SubtaskManager().update_subtask(subtask_id=55, title="X")
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_update_subtask_crm_api_error():
    patcher, _ = _patch_httpx(_err_resp("Record not found"))
    try:
        with pytest.raises(Exception, match="CRM API error"):
            await SubtaskManager().update_subtask(subtask_id=99, completed=False)
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_delete_subtask_success():
    """delete_subtask передаёт delete_by_field с CRM-ID."""
    patcher, mock_http = _patch_httpx(_resp({"id": "55"}))
    try:
        result = await SubtaskManager().delete_subtask(subtask_id=55)
        assert result["status"] == "success"
        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["action"] == "delete"
        assert payload["entity_id"] == SubtaskManager.ENTITY_ID
        assert payload["delete_by_field"] == {"id": 55}
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_delete_subtask_empty_id_raises():
    """Регрессия: та же дыра, что и в test_update_subtask_empty_id_raises,
    но для delete_subtask()."""
    patcher, _ = _patch_httpx(_resp({"id": ""}))
    try:
        with pytest.raises(Exception, match="no valid id"):
            await SubtaskManager().delete_subtask(subtask_id=55)
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_delete_subtask_connection_error():
    patcher, _ = _patch_httpx(side_effect=httpx.ConnectError("refused"))
    try:
        with pytest.raises(Exception, match="Connection error"):
            await SubtaskManager().delete_subtask(subtask_id=55)
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_delete_subtask_crm_api_error():
    patcher, _ = _patch_httpx(_err_resp("Not found"))
    try:
        with pytest.raises(Exception, match="CRM API error"):
            await SubtaskManager().delete_subtask(subtask_id=999)
    finally:
        patcher.stop()


# См. test_crm.py::test_backfill_local_id_success — то же для подзадач.

@pytest.mark.asyncio
async def test_backfill_local_id_success():
    patcher, mock_http = _patch_httpx(_resp({"id": "55"}))
    try:
        result = await SubtaskManager().backfill_local_id(subtask_id=55, local_id=7)
        assert result["status"] == "success"
        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["action"] == "update"
        assert payload["data"][_FIELD_LOCAL_ID] == "7"
        assert payload["update_by_field"] == {"id": 55}
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_backfill_local_id_empty_id_raises():
    patcher, _ = _patch_httpx(_resp({"id": ""}))
    try:
        with pytest.raises(Exception, match="no valid id"):
            await SubtaskManager().backfill_local_id(subtask_id=55, local_id=7)
    finally:
        patcher.stop()


def test_bool_to_crm_true():
    assert SubtaskManager._bool_to_crm(True) == "true"


def test_bool_to_crm_false():
    assert SubtaskManager._bool_to_crm(False) == "false"
