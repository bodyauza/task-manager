import logging
from typing import Any, Dict, List, Optional

import httpx

from src.crm.config import crm_settings

logger = logging.getLogger(__name__)


class CRMClient:
    """Асинхронный HTTP-клиент для REST API CRM «Руководитель».

    Формат запросов к API
    ---------------------
    Все запросы — HTTP POST на endpoint /api/rest.php.
    Content-Type: application/json (тело — JSON-объект).
    При работе с demo-инстансом к URL добавляется параметр ?demo_id=<N>.

    Аутентификация
    --------------
    Каждый запрос содержит три обязательных поля аутентификации в теле:

        {
            "key":      "<API-ключ из Settings → API>",
            "username": "<логин пользователя с ролью API>",
            "password": "<пароль этого пользователя>"
        }

    Поле action
    -----------
    Определяет тип операции (аналог SQL DML):

        "action": "insert"   — создание записи
        "action": "select"   — выборка записей
        "action": "update"   — обновление записей
        "action": "delete"   — удаление записей

    Поле entity_id
    --------------
    Идентификатор сущности (таблицы) в CRM:

        entity_id: 1   → сущность «Пользователи»
        entity_id: 29  → сущность «Задачи»

    action = "insert"
    -----------------
    Параметр "items" — массив (list) словарей. Каждый словарь — одна
    создаваемая запись. Все поля сущности передаются внутри элемента массива.

    Ключи — str:  "field_311", "field_312", "group_id", "email" ...
    Значения — Any: "false" (str), 6 (int), "Иван" (str) ...
    {"field_311": "Название", "field_313": "false", "group_id": 6}

    Для сущности entity_id=29 (Задачи) поля именуются как "field_<ID>",
    где ID — числовой идентификатор поля в CRM:

        {
            "key": "...", "username": "...", "password": "...",
            "action": "insert",
            "entity_id": 29,
            "items": [
                {
                    "field_311": "Название задачи",
                    "field_312": "Описание задачи",
                    "field_313": "false"
                }
            ]
        }

    Поле field_313 (статус) — чекбокс; значения строковые: "true" / "false".
    items может содержать несколько словарей (batch-создание), но в данном
    приложении всегда передаётся ровно один элемент.

    Для сущности entity_id=1 (Пользователи) поля используют встроенные
    имена (не field_<N>), плюс дополнительные параметры уведомления:

        {
            "key": "...", "username": "...", "password": "...",
            "action": "insert",
            "entity_id": 1,
            "items": [
                {
                    "group_id":  6,
                    "firstname": "Иван",
                    "lastname":  "Иванов",
                    "username":  "ivan.ivanov",
                    "email":     "ivan@example.com",
                    "password":  ""
                }
            ],
            "notify":    true,
            "login_url": "https://crm.example.com/index.php?module=users/login"
        }

    notify=true → CRM отправляет пользователю email со ссылкой из login_url.

    Ответ на insert:
        {"status": "success", "data": {"id": "42"}}
    id возвращается строкой; преобразование в int выполняется на стороне
    приложения. Поле status нестабильно между версиями CRM — см. ниже.

    action = "select"
    -----------------
    Параметр "select_fields" — строка из идентификаторов полей через запятую.
    Параметр "filters" — словарь, где ключ = ID поля, значение = условие.
    Условие "include" означает точное совпадение (не LIKE).

        {
            "key": "...", "username": "...", "password": "...",
            "action": "select",
            "entity_id": 1,
            "select_fields": "9,7,8,12,6",
            "filters": {
                "9": {
                    "value":     "ivan@example.com",
                    "condition": "include"
                }
            }
        }

    Поле 9 — email пользователя в сущности «Пользователи».
    Ответ: {"status": "success", "data": [{...}, {...}]}

    action = "update"
    -----------------
    Параметр "data" — словарь обновляемых полей (только изменяемые поля).
    Параметр "update_by_field" — словарь критерия поиска обновляемой записи.

        {
            "key": "...", "username": "...", "password": "...",
            "action": "update",
            "entity_id": 29,
            "data": {
                "field_311": "Новое название задачи",
                "field_313": "true"
            },
            "update_by_field": {"id": 42}
        }

    update_by_field.id — это CRM-ID записи (crm_task_id в локальной БД).
    Поля, отсутствующие в "data", не изменяются.

    action = "delete"
    -----------------
    Параметр "delete_by_field" — словарь критерия поиска удаляемой записи.

        {
            "key": "...", "username": "...", "password": "...",
            "action": "delete",
            "entity_id": 29,
            "delete_by_field": {"id": 42}
        }

    Формат ответа
    -------------
    CRM «Руководитель» не придерживается единого формата ответа:
    разные версии и разные операции возвращают разные признаки успеха.

    Известные варианты успешного ответа:
        {"success": true, ...}
        {"status": "ok", ...}
        {"status": "success", "data": {...}}
        {"result": [...], "data": {...}}   ← нет ключей "error" / "error_message"

    Признаки ошибки:
        {"msg": "Error description"}
        {"error_message": "..."}
        любой ответ с ключом "error"

    _call() проверяет все перечисленные варианты и поднимает Exception,
    если ни один признак успеха не найден.
    """

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
        **kwargs,
    ) -> Dict[str, Any]:
        """Выполняет HTTP POST к REST API CRM и возвращает распакованный JSON.

        :param action:     'insert' | 'select' | 'update' | 'delete'
        :param entity_id:  ID сущности (1=Пользователи, 29=Задачи)
        :param items:      Массив записей для action='insert' (список словарей)
        :param notify:     True → CRM отправляет email-уведомление новому пользователю
        :param login_url:  URL входа в CRM, вставляемый в тело письма-уведомления
        :param kwargs:     Дополнительные поля payload:
                           - filters          (dict)  — для action='select'
                           - select_fields    (str)   — для action='select', через запятую
                           - data             (dict)  — для action='update'
                           - update_by_field  (dict)  — для action='update', критерий поиска
                           - delete_by_field  (dict)  — для action='delete', критерий поиска
        :raises Exception: При HTTP-ошибке, таймауте, невалидном JSON или ответе CRM с ошибкой
        """
        # demo_id — GET-параметр, идентифицирующий конкретный demo-инстанс CRM.
        # Production-инстансы его не требуют; в .dev.env CRM_DEMO_ID оставляется пустым.
        full_url = self.base_url
        if self.demo_id:
            sep = "&" if "?" in full_url else "?"
            full_url += f"{sep}demo_id={self.demo_id}"

        # Базовый payload присутствует в каждом запросе к API:
        # key + username + password — аутентификация; action — тип операции.
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
        # items — массив словарей для action='insert'.
        # Каждый элемент — одна создаваемая запись с полями сущности.
        if items is not None:
            payload["items"] = items
        # kwargs передают поля, специфичные для конкретной операции:
        # filters/select_fields (select), data/update_by_field (update),
        # delete_by_field (delete). None-значения исключаются из payload.
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value

        # Не логируем api_key и password во избежание утечки секретов
        safe_payload = {k: v for k, v in payload.items() if k not in ("key", "password")}
        logger.info("CRM → %s | %s", full_url, safe_payload)

        # timeout=30.0: CRM demo-инстанс иногда отвечает с задержкой до 20+ с.
        # Без явного таймаута зависший запрос заблокирует worker до закрытия соединения.
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(full_url, json=payload)
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise Exception(f"HTTP {e.response.status_code}: {e.response.text}")
            except httpx.ConnectError:
                raise Exception(f"Connection error: cannot reach {full_url}")
            except httpx.TimeoutException:
                raise Exception("CRM request timed out")

            logger.info("CRM ← %s", response.text)
            try:
                result = response.json()
            except Exception:
                raise Exception(f"CRM returned invalid JSON: {response.text[:200]}")

        # Формат признака успеха не стандартизирован между версиями CRM и типами операций.
        # Проверяем все известные варианты — подробнее в docstring класса CRMClient.
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

        return result

    async def register_user(
        self,
        group_id: int,
        firstname: str,
        lastname: str,
        username: str,
        email: str,
        password: str = "",
        notify: bool = True,
        login_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Регистрирует пользователя в сущности «Пользователи» (entity_id=1).

        :param username: Логин в CRM = часть email до '@'
        """
        record: Dict[str, Any] = {
            "group_id":  group_id,
            "firstname": firstname,
            "lastname":  lastname,
            "username":  username,
            "email":     email,
        }
        if password:
            record["password"] = password
        if login_url is None:
            login_url = self.login_url

        return await self._call(
            action="insert",
            entity_id=1,
            items=[record],
            notify=notify,
            login_url=login_url,
        )
