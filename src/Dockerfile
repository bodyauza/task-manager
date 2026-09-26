FROM python:3.13-slim

WORKDIR /app

# libpq5 — runtime-библиотека PostgreSQL (нужна asyncpg).
# gcc и python3-dev не нужны: asyncpg поставляется с pre-built wheels для Python 3.13.
# libmagic1 — системная библиотека определения MIME-типа по сигнатуре байтов; нужна
# пакету python-magic (Linux-вариант python-magic-bin, см. requirements.txt). Без неё
# `import magic` в src/utils/file_utils.py падает при старте приложения OSError'ом
# ("failed to find libmagic") ещё до обработки первого запроса — эта библиотека
# используется на каждой загрузке файла (task_files.py/subtask_files.py) для проверки
# MIME-сигнатуры, а не только в тестах.
RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries \
    && apt-get update \
    && apt-get install -y --no-install-recommends libpq5 libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# UVICORN_WORKERS=2 — раньше было зафиксировано в 1, пока ConnectionManager
# (src/realtime/connection_manager.py) рассылал WebSocket-события только в
# памяти одного процесса: при --workers>1 события из воркера A не доходили
# бы до клиентов, чьё WS-соединение ОС направила в воркер B. Это снято:
# broadcast() теперь публикует каждое событие в Redis Pub/Sub, и каждый
# воркер подписан на тот же канал (см. docstring connection_manager.py) —
# рассылка больше не зависит от того, в каком процессе она была инициирована.
# Несколько процессов uvicorn также снижают blast radius блокировки event
# loop одним CPU-bound запросом (например, bcrypt при логине/регистрации —
# см. src/auth/manager.py) до 1/N пользователей вместо всех сразу. Число
# подобрано как разумный дефолт для demo-инстанса — не привязано к
# CRM_OUTBOX_SHARD_COUNT/числу celery-worker-shard-* сервисов, это независимые
# параметры (веб-процессы против Celery-воркеров outbox-очереди).
ENV UVICORN_WORKERS=2

# Shell-форма CMD (не JSON-массив): нужна подстановка переменной окружения
# ${UVICORN_WORKERS} на старте контейнера — JSON-форма exec её не раскрывает.
CMD uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}
