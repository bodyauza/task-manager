def mask_email(email: str) -> str:
    """Маскирует email для логов: "ivan@example.com" → "i***@example.com".

    Первая буква и домен остаются (нужны для диагностики SMTP), local-part скрыт. Без "@" или пустая строка — "***".
    """
    local, sep, domain = email.partition("@")
    if not sep or not local:
        return "***"
    return f"{local[0]}***@{domain}"
