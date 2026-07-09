import src.auth.models  # noqa: F401
import src.task_logic.models  # noqa: F401

"""
Эти строки не используют модули напрямую. Их цель — запустить выполнение тел обоих модулей,
чтобы SQLAlchemy зарегистрировал все ORM-классы в Base.metadata до того, как Alembic или тесты к нему обратятся.
"""