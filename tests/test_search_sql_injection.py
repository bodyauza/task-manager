"""SQL-инъекции и LIKE-инъекции в GET /tasks/search?title=...

Запрос строится через ORM (Task.title.ilike(pattern, escape="\\")) — значение
передаётся bind-параметром, а спецсимволы LIKE (\\, %, _) экранируются в
services/tasks.py::search_tasks. Тесты фиксируют оба свойства: payload не
меняет структуру запроса (нет 500, таблица цела, «лишние» строки не возвращаются)
и wildcard-символы ищутся буквально.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from src.database import async_session_maker
from src.task_logic.models import Task

from tests.conftest import register_and_login

EMAIL = "alice@example.com"

# Классические payload'ы: закрытие литерала, tautology, UNION, stacked queries,
# комментарии, time-based, обратный слэш перед кавычкой.
SQLI_PAYLOADS = [
    "' OR '1'='1",
    "' OR 1=1--",
    "\" OR \"1\"=\"1",
    "'; DROP TABLE task;--",
    "'; DELETE FROM task WHERE '1'='1",
    "' UNION SELECT id, email, hashed_password FROM person--",
    "' UNION SELECT NULL,NULL,NULL,NULL--",
    "1; SELECT pg_sleep(5)--",
    "' OR pg_sleep(5)--",
    "\\' OR 1=1--",
    "x') OR ('1'='1",
    "/*",
    "*/ OR 1=1 /*",
]


async def _create(client: AsyncClient, title: str):
    r = await client.post(
        "/create-task/", data={"data": json.dumps({"title": title, "description": "d"})}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _task_count() -> int:
    async with async_session_maker() as s:
        return (await s.execute(select(func.count()).select_from(Task))).scalar_one()


@pytest.fixture
async def seeded(client: AsyncClient, mock_smtp: dict):
    await register_and_login(client, mock_smtp, EMAIL)
    for title in ("Python Tips", "100% done", "a_b", "Plain"):
        await _create(client, title)
    return client


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
async def test_search_sqli_payload_is_treated_as_literal(seeded: AsyncClient, payload: str):
    before = await _task_count()

    r = await seeded.get("/tasks/search", params={"title": payload})

    # Ни 500 (синтаксическая ошибка SQL), ни утечки: совпадений по буквальной
    # строке нет, tautology не превращается в «вернуть всё».
    assert r.status_code == 404 or (r.status_code == 200 and r.json() == []), r.text
    assert await _task_count() == before          # DROP/DELETE не выполнились


async def test_search_sqli_payload_matches_only_literal_title(seeded: AsyncClient):
    payload = "'; DROP TABLE task;--"
    created = await _create(seeded, payload)

    r = await seeded.get("/tasks/search", params={"title": payload})

    assert r.status_code == 200
    assert [t["id"] for t in r.json()] == [created["id"]]
    assert await _task_count() == 5


async def test_search_tautology_does_not_return_all_rows(seeded: AsyncClient):
    r = await seeded.get("/tasks/search", params={"title": "' OR '1'='1"})
    assert not (r.status_code == 200 and r.json())   # «Python Tips» и др. не утекли


@pytest.mark.parametrize("wildcard,expected", [
    ("%", ["100% done"]),        # % — буквальный, а не «любая строка»
    ("_", ["a_b"]),              # _ — буквальный, а не «любой символ»
    ("100%", ["100% done"]),
    ("a_b", ["a_b"]),
])
async def test_search_like_wildcards_are_literal(seeded: AsyncClient, wildcard: str, expected: list):
    r = await seeded.get("/tasks/search", params={"title": wildcard})
    assert r.status_code == 200
    assert sorted(t["title"] for t in r.json()) == sorted(expected)


async def test_search_backslash_is_literal(seeded: AsyncClient):
    created = await _create(seeded, "path\\to")

    r = await seeded.get("/tasks/search", params={"title": "\\"})

    assert r.status_code == 200
    assert [t["id"] for t in r.json()] == [created["id"]]


async def test_search_escape_sequences_do_not_break_pattern(seeded: AsyncClient):
    # «\%» после экранирования — буквальные «\» и «%», а не экранированный %.
    r = await seeded.get("/tasks/search", params={"title": "\\%"})
    assert r.status_code in (200, 404)
    assert not (r.status_code == 200 and r.json())


@pytest.mark.parametrize("value", ["", "   "])
async def test_search_empty_title_rejected(seeded: AsyncClient, value: str):
    r = await seeded.get("/tasks/search", params={"title": value})
    assert r.status_code in (400, 422)


@pytest.mark.parametrize("param,value", [("skip", "0; DROP TABLE task"), ("limit", "1 OR 1=1"),
                                         ("skip", "-1"), ("limit", "0")])
async def test_search_pagination_params_reject_non_integers(seeded: AsyncClient, param: str, value: str):
    r = await seeded.get("/tasks/search", params={"title": "Python", param: value})
    assert r.status_code == 422
    assert await _task_count() == 4
