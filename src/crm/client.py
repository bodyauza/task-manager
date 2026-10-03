import asyncio
import base64
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from src.crm.crm_config import crm_settings

logger = logging.getLogger(__name__)

# Один httpx.AsyncClient на модуль (а не на класс): иначе каждый подкласс CRMClient заводил бы свой TCP-пул.
_shared_http_client: httpx.AsyncClient | None = None


def _get_shared_http_client() -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None:
        # Единый timeout=30 httpx разворачивает в четыре бюджета (connect/write/read/pool). Файловые операции
        # шлют до ~133 МБ base64 одним телом, поэтому write/read увеличены; connect/pool остаются 30 с.
        # При смене значений пересчитать src/tasks/crm_shard_lock.py::_LOCK_TIMEOUT_SECONDS.
        _shared_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, write=120.0, read=120.0, pool=30.0)
        )
    return _shared_http_client


async def aclose_http_client() -> None:
    """Закрывает разделяемый CRM-клиент (из lifespan при shutdown)."""
    global _shared_http_client
    if _shared_http_client is not None:
        await _shared_http_client.aclose()
        _shared_http_client = None


class CRMRecordNotFoundError(Exception):
    """update/delete с expect_id=True над записью, которой в CRM уже нет: CRM отвечает «success» с пустым
    data.id. Отдельный класс позволяет _do_delete отличить «уже удалено» от реального сбоя.
    """


class CRMClient:
    """Асинхронный HTTP-клиент для REST API CRM «Руководитель».

    Все запросы — POST на /api/rest.php с JSON-телом (для demo-инстанса к URL добавляется ?demo_id=<N>).
    Каждое тело содержит key, username, password и action (insert | select | update | delete),
    entity_id — ID сущности (1 — пользователи, 29 — задачи).

    - insert: items — список словарей полей; ответ {"status": "success", "data": {"id": "42"}}, id строкой.
      Чекбокс-поля — строки "true"/"false".
    - select: select_fields — ID полей через запятую; filters — {"<field_id>": {"value": ..., "condition": "include"}}
      (include — точное совпадение).
    - update: data — только изменяемые поля; update_by_field — {"id": <CRM-ID>}.
    - delete: delete_by_field — {"id": <CRM-ID>}.

    Формат ответа между версиями CRM нестабилен. Успех: {"success": true}, {"status": "ok"},
    {"status": "success", "data": ...} или ответ без ключей error/error_message. Ошибка: ключ "msg",
    "error_message" или "error". _call() проверяет все варианты и поднимает Exception, если признак
    успеха не найден.
    """

    @staticmethod
    def _bool_to_crm(value: bool) -> str:
        """Преобразует bool в строку поля-чекбокса CRM («true»/«false»)."""
        return "true" if value else "false"

    @staticmethod
    async def _file_to_crm(abs_path: Path) -> dict:
        """Читает файл и возвращает {'name': ..., 'content': '<base64>'}.

        Чтение вынесено в asyncio.to_thread, чтобы блокирующий I/O (до 10 МБ) не держал event loop;
        метод асинхронный, чтобы список файлов читался параллельно через asyncio.gather.
        """
        content = await asyncio.to_thread(abs_path.read_bytes)
        return {
            "name":    abs_path.name,
            "content": base64.b64encode(content).decode(),
        }

    def __init__(self):
        self.base_url: str = crm_settings.API_URL
        self.api_key: str = crm_settings.API_KEY
        self.username: str = crm_settings.API_USER
        self.password: str = crm_settings.API_PASSWORD
        self.login_url: str = crm_settings.LOGIN_URL
        self.demo_id: str = crm_settings.DEMO_ID

    async def _call(
        self,
        action: str,
        entity_id: Optional[int] = None,
        items: Optional[List[Dict[str, Any]]] = None,
        notify: bool = False,
        login_url: Optional[str] = None,
        expect_id: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Выполняет POST к REST API CRM и возвращает распакованный JSON.

        :param action:     'insert' | 'select' | 'update' | 'delete'
        :param entity_id:  ID сущности CRM
        :param items:      записи для action='insert'
        :param notify:     True — CRM отправляет email-уведомление новому пользователю
        :param login_url:  URL входа для письма-уведомления
        :param expect_id:  True — проверить, что в "data" есть непустой "id" (для update/delete над
                           существующей записью, не для insert)
        :param kwargs:     filters/select_fields (select), data/update_by_field (update), delete_by_field (delete)
        :raises Exception: HTTP-ошибка, таймаут, невалидный JSON, ошибка в ответе CRM или пустой id при expect_id
        """
        full_url = self.base_url
        if self.demo_id:
            sep = "&" if "?" in full_url else "?"
            full_url += f"{sep}demo_id={self.demo_id}"

        payload: Dict[str, Any] = {
            "key":      self.api_key,
            "username": self.username,
            "password": self.password,
            "action":   action,
        }
        if entity_id is not None:
            payload["entity_id"] = entity_id
        if notify:
            payload["notify"] = True
        if login_url:
            payload["login_url"] = login_url
        if items is not None:
            payload["items"] = items
        # None-значения kwargs в payload не попадают.
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value

        # api_key и password не логируем. DEBUG, а не INFO: в data файловых операций лежит base64 (до ~133 МБ).
        safe_payload = {k: v for k, v in payload.items() if k not in ("key", "password")}
        logger.debug("CRM → %s | %s", full_url, safe_payload)

        client = _get_shared_http_client()
        try:
            response = await client.post(full_url, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise Exception(f"HTTP {e.response.status_code}: {e.response.text}")
        except httpx.ConnectError:
            raise Exception(f"Connection error: cannot reach {full_url}")
        except httpx.TimeoutException:
            raise Exception("CRM request timed out")

        logger.debug("CRM ← %s", response.text)
        try:
            result = response.json()
        except Exception:
            raise Exception(f"CRM returned invalid JSON: {response.text[:200]}")

        # Формат признака успеха не стандартизирован между версиями CRM — проверяем все известные варианты.
        is_success = (
            result.get("success") is True
            or result.get("status") in ("ok", "success")
            or (
                ("result" in result or "data" in result)
                and "error" not in result
                and "error_message" not in result
            )
        )
        if not is_success:
            error_msg = (
                result.get("msg") or result.get("error_message") or "Unknown CRM error"
            )
            raise Exception(f"CRM API error: {error_msg}")

        # expect_id: на update/delete несуществующей записи CRM отвечает {"status": "success", "data": {"id": ""}},
        # и без этой проверки такой ответ считался бы успехом. На insert id пустым не бывает.
        if expect_id:
            data = result.get("data")
            if not isinstance(data, dict) or not data.get("id"):
                logger.warning("CRM: expected non-empty id in response data, got: %r", data)
                raise CRMRecordNotFoundError("CRM returned success but no valid id in data")

        return result
