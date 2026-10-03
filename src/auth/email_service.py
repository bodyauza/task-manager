import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.config import settings
from src.utils.log_utils import mask_email

logger = logging.getLogger(__name__)

# Отдельный от routers/pages.py Environment: письмо уходит из сервисного слоя без Request, а auth/ не должен зависеть от routers/.
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
)


async def send_confirmation_code(to_email: str, code: str) -> None:
    # MIMEMultipart("alternative"): клиент выбирает последнюю часть, поэтому plain идёт первым (fallback), html — вторым.
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Код подтверждения — Task Manager"
    msg["From"] = settings.SMTP_USER
    msg["To"] = to_email

    plain = (
        f"Ваш код подтверждения регистрации в Task Manager: {code}\n"
        "Код действителен 15 минут.\n"
        "Если вы не запрашивали регистрацию — проигнорируйте это письмо."
    )
    # HTML письма лежит в src/templates/email/confirmation-code.html.
    html = _env.get_template("confirmation-code.html").render(code=code)

    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    # use_tls=True: порт 465 требует TLS с первого пакета; порт 587 использует STARTTLS — это другой механизм.
    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASSWORD,
        use_tls=True,
    )
    # Адрес маскируется (ПДн в логах): пользователя ещё нет в БД, заменить на user.id нечем.
    logger.info("Confirmation code sent to %s", mask_email(to_email))
