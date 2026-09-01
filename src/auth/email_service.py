import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.config import settings

logger = logging.getLogger(__name__)

# Отдельный от src/routers/pages.py Environment: тот заточен под Starlette
# TemplateResponse(request, ...) и требует объект Request, которого здесь нет
# (письмо отправляется из сервисного слоя, не из HTTP-обработчика). Плюс auth/
# не должен зависеть от routers/ — стрелка зависимостей в проекте идёт только
# в обратную сторону (routers → services/auth, не наоборот).
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
)


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
    # HTML — в src/templates/email/confirmation-code.html, а не строкой в Python:
    # редактировать вёрстку письма теперь можно как обычный .html-файл, не трогая
    # логику отправки и не экранируя кавычки внутри f-string.
    html = _env.get_template("confirmation-code.html").render(code=code)

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
