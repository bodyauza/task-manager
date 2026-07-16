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

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
