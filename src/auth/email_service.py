import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from src.config import settings

logger = logging.getLogger(__name__)


async def send_confirmation_code(to_email: str, code: str) -> None:
    # MIMEMultipart("alternative"): обе части (plain + html) описывают одно сообщение,
    # почтовый клиент выбирает наиболее «богатый» вариант, который умеет отобразить.
    # Порядок вложения важен: по RFC 2046 клиент предпочитает последнее вложение —
    # html идёт вторым, plain первым (как fallback для текстовых клиентов).
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Код подтверждения — Task Manager"
    msg["From"] = settings.SMTP_USER
    msg["To"] = to_email

    plain = (
        f"Ваш код подтверждения регистрации в Task Manager: {code}\n"
        "Код действителен 15 минут.\n"
        "Если вы не запрашивали регистрацию — проигнорируйте это письмо."
    )
    html = (
        "<html><body style=\"font-family:'Segoe UI',Arial,sans-serif;"
        "color:#212529;max-width:480px;margin:0 auto;padding:24px\">"
        "<p style=\"font-size:13px;font-weight:700;color:#003f6b;"
        "letter-spacing:1.2px;text-transform:uppercase\">Task Manager</p>"
        "<hr style=\"border:none;border-top:2px solid #003f6b;margin:0 0 20px\">"
        "<p style=\"font-size:14px\">Ваш код подтверждения регистрации:</p>"
        f"<div style=\"font-family:monospace;font-size:32px;font-weight:700;"
        f"letter-spacing:12px;color:#003f6b;padding:16px 0\">{code}</div>"
        "<p style=\"font-size:13px;color:#6c757d\">Код действителен <b>15 минут</b>.</p>"
        "<p style=\"font-size:12px;color:#9e9e9e\">"
        "Если вы не запрашивали регистрацию — проигнорируйте это письмо.</p>"
        "</body></html>"
    )
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    # use_tls=True: порт 465 требует TLS с первого пакета (Implicit TLS / SSL wrap).
    # Порт 587 использует STARTTLS (upgrade внутри plain-соединения) — это другой механизм.
    # Yandex Mail слушает оба порта, но смешивать port=465 со STARTTLS нельзя.
    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASSWORD,
        use_tls=True,
    )
    logger.info("Confirmation code sent to %s", to_email)
