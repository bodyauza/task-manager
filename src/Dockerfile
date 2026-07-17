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

# UVICORN_WORKERS=1 — ВРЕМЕННО, пока нет Redis. Несколько процессов uvicorn снизили бы
# blast radius блокировки event loop одним CPU-bound запросом (например, bcrypt при
# логине/регистрации — см. src/auth/manager.py) до 1/N пользователей вместо всех сразу.
# Но ConnectionManager (src/realtime/manager.py) хранит подключения WebSocket в памяти
# ОДНОГО процесса ("Единственный экземпляр на процесс" — см. комментарий там же); при
# --workers > 1 это два (и более) независимых процесса с двумя независимыми наборами
# соединений, не знающих друг о друге. HTTP-запрос, обработанный в воркере A, рассылает
# WS-событие только тем, кто подключён именно к воркеру A — пользователи, чьё WS-соединение
# ОС направила в воркер B, ничего не получат, хотя их сессия полностью рабочая. Ни sticky-
# sessions, ни что-либо ещё в проекте сейчас не гарантирует, что HTTP- и WS-трафик одного
# пользователя попадут в один и тот же воркер.
# Поднимать это значение можно только после того, как broadcast() в
# src/realtime/manager.py будет переведён на Redis Pub/Sub (или аналог) — тогда рассылка
# перестанет зависеть от того, в каком именно процессе она была инициирована.
ENV UVICORN_WORKERS=1

# Shell-форма CMD (не JSON-массив): нужна подстановка переменной окружения
# ${UVICORN_WORKERS} на старте контейнера — JSON-форма exec её не раскрывает.
CMD uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}
