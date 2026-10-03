"""Маскировка email в логах: сам хелпер и места, где адрес раньше писался открытым текстом
(manager.on_after_register, email_service.send_confirmation_code; SMTP-сбой request-code — в test_registration_flow.py).
"""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.auth.email_service import send_confirmation_code
from src.auth.manager import UserManager
from src.utils.log_utils import mask_email

EMAIL = "ivan.petrov@example.com"


@pytest.mark.parametrize("raw, expected", [
    ("ivan@example.com", "i***@example.com"),
    ("a@example.com", "a***@example.com"),
    ("first.last+tag@sub.example.org", "f***@sub.example.org"),
    ("no-at-sign", "***"),
    ("@example.com", "***"),
    ("", "***"),
])
def test_mask_email(raw, expected):
    assert mask_email(raw) == expected


async def test_send_confirmation_code_logs_masked_email(caplog):
    with patch("src.auth.email_service.aiosmtplib.send", new=AsyncMock()), \
            caplog.at_level(logging.INFO, logger="src.auth.email_service"):
        await send_confirmation_code(EMAIL, "123456")

    assert EMAIL not in caplog.text
    assert "i***@example.com" in caplog.text


async def test_on_after_register_logs_user_id_without_email(caplog):
    manager = UserManager(None)   # user_db в on_after_register не используется
    user = SimpleNamespace(id=42, email=EMAIL)

    with caplog.at_level(logging.INFO, logger="src.auth.manager"):
        await manager.on_after_register(user)

    assert EMAIL not in caplog.text
    assert "User 42 registered" in caplog.text
