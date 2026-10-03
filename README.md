# Task Manager

Веб-приложение для совместной работы с задачами и подзадачами: общая доска, файлы (ТЗ и «иные документы»), WebSocket-чат и фоновая синхронизация данных с CRM «Руководитель» через Celery + Redis (transactional outbox). Стек: FastAPI, SQLAlchemy (async), PostgreSQL, Celery, Redis, sqladmin.

## Содержание

- [Screenshots](#screenshots)
  - [Task board page](#task-board-page)
  - [List of tasks in CRM](#list-of-tasks-in-crm)
  - [Loading global list values from CRM](#loading-global-list-values-from-crm)
  - [Board of subtasks linked to a parent task](#board-of-subtasks-linked-to-a-parent-task)
  - [Subtask page](#subtask-page)
  - [List of subtasks in CRM](#list-of-subtasks-in-crm)
  - [Protected user profile page](#protected-user-profile-page)
  - [Login page](#login-page)
  - [Registration Pages](#registration-pages)
    - [Protected registration page](#protected-registration-page)
  - [CRM synchronization status visibility — administrator only](#crm-synchronization-status-visibility-administrator-only)
  - [SQLAdmin pages](#sqladmin-pages)
  - [Flower Monitoring Pages](#flower-monitoring-pages)
- [Technological Stack](#technological-stack)
  - [Backend](#backend)
  - [ASGI web server](#asgi-web-server)
  - [File uploads](#file-uploads)
  - [Database](#database)
  - [Testing](#testing)
  - [Frontend](#frontend)
  - [Task queue & cache](#task-queue-cache)
  - [Admin panel & monitoring](#admin-panel-monitoring)
- [Диаграммы архитектуры](#диаграммы-архитектуры)
  - [1. Контекстная диаграмма](#1-контекстная-диаграмма)
  - [2. Диаграмма контейнеров](#2-диаграмма-контейнеров)
  - [3. Диаграмма последовательностей](#3-диаграмма-последовательностей)
  - [4. Диаграмма развёртывания](#4-диаграмма-развёртывания)
  - [5. Диаграмма вариантов использования](#5-диаграмма-вариантов-использования)
- [Registration Flow](#registration-flow)
  - [Коды ошибок регистрации](#коды-ошибок-регистрации)
- [Authentication Flow](#authentication-flow)
  - [Токены](#токены)
  - [Коды ошибок аутентификации](#коды-ошибок-аутентификации)
- [Endpoints](#endpoints)
  - [Swagger UI / OpenAPI](#swagger-ui-openapi)
  - [Страницы (HTML)](#страницы-html)
  - [Регистрация](#регистрация)
  - [Аутентификация](#аутентификация)
  - [Задачи (требуют действующий `access_token`)](#задачи-требуют-действующий-access_token)
  - [Подзадачи (требуют действующий `access_token`)](#подзадачи-требуют-действующий-access_token)
  - [Файлы задач (требуют действующий `access_token`)](#файлы-задач-требуют-действующий-access_token)
    - [Ограничения на загрузку файлов](#ограничения-на-загрузку-файлов)
  - [Файлы подзадач (требуют действующий `access_token`)](#файлы-подзадач-требуют-действующий-access_token)
  - [Управление пользователями (требуют роль `admin`)](#управление-пользователями-требуют-роль-admin)
  - [WebSocket](#websocket)
- [Архитектура приложения](#архитектура-приложения)
- [Ценность Celery + Redis в проекте](#ценность-celery--redis-в-проекте)
  - [Что дал бы проект без них](#что-дал-бы-проект-без-них)
  - [Ценность Celery](#ценность-celery)
  - [Ценность Redis](#ценность-redis)
  - [Что будет при сбоях](#что-будет-при-сбоях)
  - [Где ценность ограничена](#где-ценность-ограничена)
- [Database Schema](#database-schema)
  - [Миграции Alembic](#миграции-alembic)
  - [Блокировки строк: FOR UPDATE и FOR NO KEY UPDATE](#блокировки-строк-for-update-и-for-no-key-update)
- [CRM «Руководитель»](#crm-руководитель)
  - [Синхронизация задач (полностью асинхронно, через Celery/Redis)](#синхронизация-задач-полностью-асинхронно-через-celeryredis)
    - [Назначение каждого Celery-воркера и Beat](#назначение-каждого-celery-воркера-и-beat)
    - [Каждая функция, которую выполняют Celery-воркеры](#каждая-функция-которую-выполняют-celery-воркеры)
  - [Таймауты HTTP-запросов к CRM и большие файлы](#таймауты-http-запросов-к-crm-и-большие-файлы)
  - [Поле «Проект»](#поле-проект)
  - [Валидация PATCH: null в NOT NULL-полях](#валидация-patch-null-в-not-null-полях)
  - [Email создателя](#email-создателя)
  - [Local ID](#local-id)
  - [Сущности CRM](#сущности-crm)
  - [Конфигурация](#конфигурация)
  - [Структура модуля](#структура-модуля)
  - [HTTP-клиент](#http-клиент)
  - [Формат запросов к API](#формат-запросов-к-api)
- [Deployment on a server running Ubuntu OS](#deployment-on-a-server-running-ubuntu-os)
  - [1. Обновление системы и системные пакеты](#1-обновление-системы-и-системные-пакеты)
  - [2. PostgreSQL 18](#2-postgresql-18)
  - [3. Системный пользователь для приложения](#3-системный-пользователь-для-приложения)
  - [4. Код и виртуальное окружение](#4-код-и-виртуальное-окружение)
  - [5. `src/.env` — переменные окружения для `API_MODE=prod`](#5-srcenv-переменные-окружения-для-api_modeprod)
  - [6. Миграции Alembic и первый администратор](#6-миграции-alembic-и-первый-администратор)
  - [7. Каталог загруженных файлов](#7-каталог-загруженных-файлов)
  - [8. Ручная проверка перед systemd](#8-ручная-проверка-перед-systemd)
  - [9. systemd — веб-приложение](#9-systemd-веб-приложение)
  - [10. systemd — Celery: worker, шарды, beat, Flower](#10-systemd-celery-worker-шарды-beat-flower)
  - [11. Nginx — reverse proxy, статика, WebSocket](#11-nginx-reverse-proxy-статика-websocket)
  - [12. SSL — Let's Encrypt через certbot](#12-ssl-lets-encrypt-через-certbot)
  - [13. Firewall (ufw)](#13-firewall-ufw)
  - [14. Итоговая проверка всего стека](#14-итоговая-проверка-всего-стека)
  - [15. Обновление приложения (redeploy)](#15-обновление-приложения-redeploy)
  - [16. Логи и типичные проблемы](#16-логи-и-типичные-проблемы)
- [Docker Deployment](#docker-deployment)
  - [Структура файлов](#структура-файлов)
  - [Число воркеров uvicorn (`UVICORN_WORKERS`)](#число-воркеров-uvicorn-uvicorn_workers)
  - [Конфигурация](#конфигурация-1)
  - [Команды](#команды)
  - [Сохранение данных Redis (том + AOF)](#сохранение-данных-redis-том-aof)
  - [Celery result backend — Redis хранит ответ каждой задачи, но его никто не читает](#celery-result-backend-redis-хранит-ответ-каждой-задачи-но-его-никто-не-читает)
    - [Шаг 1. Изменить `src/docker-compose.yml`](#шаг-1-изменить-srcdocker-composeyml)
    - [Шаг 2. Применить](#шаг-2-применить)
    - [Шаг 3. Проверить](#шаг-3-проверить)
    - [Эксплуатация](#эксплуатация)
    - [Redis без Docker (варианты B и C выше)](#redis-без-docker-варианты-b-и-c-выше)
  - [Применение миграций](#применение-миграций)
  - [Доступ](#доступ)
- [Testing](#testing-1)
  - [Локальный запуск](#локальный-запуск)
  - [Запуск тестов внутри Docker-контейнера](#запуск-тестов-внутри-docker-контейнера)
  - [Фикстуры](#фикстуры)
  - [Покрытие тестами](#покрытие-тестами)

## Screenshots

### Task board page

![Task Board](src/screenshots/task-board_1.png)

### List of tasks in CRM

![List of tasks in CRM](src/screenshots/crm_task_1.png)

![List of tasks in CRM](src/screenshots/crm_task_2.png)

### Loading global list values from CRM

![Loading global list values from CRM](src/screenshots/Loading_global_list_values_from_CRM.png)

### Board of subtasks linked to a parent task

![Board of subtasks linked to a parent task](src/screenshots/subtask-board.png)

### Subtask page

![Subtask page](src/screenshots/subtask-detail.png)

### List of subtasks in CRM

![List of subtasks in CRM](src/screenshots/crm_subtask.png)

### Protected user profile page

![User profile](src/screenshots/user_profile.png)

### Login page

![Login page](src/screenshots/login_page.png)

### Registration Pages

![Registration first step](src/screenshots/registration_first_step.png)

![Registration second step](src/screenshots/registration_second_step.png)

#### Protected registration page

![Registration step three](src/screenshots/registration_step_three.png)

### CRM synchronization status visibility — administrator only

![CRM synchronization status visibility — administrator only](src/screenshots/CRM_synchronization_status_visibility.png)

### SQLAdmin pages

![SQLAdmin page](src/screenshots/SQLAdmin_page_1.png)

![SQLAdmin page](src/screenshots/SQLAdmin_page_2.png)

![SQLAdmin page](src/screenshots/SQLAdmin_page_3.png)

![SQLAdmin page](src/screenshots/SQLAdmin_page_4.png)

![SQLAdmin page](src/screenshots/SQLAdmin_page_5.png)

![SQLAdmin page](src/screenshots/SQLAdmin_page_6.png)

### Flower Monitoring Pages

![Flower Monitoring Page](src/screenshots/Flower_1.png)

![Flower Monitoring Page](src/screenshots/Flower_2.png)

![Flower Monitoring Page](src/screenshots/Flower_3.png)

![Flower Monitoring Page](src/screenshots/Flower_4.png)

![Flower Monitoring Page](src/screenshots/Flower_5.png)

### Swagger UI / ReDoc (OpenAPI)

![](src/screenshots/Swagger_1.png)

![](src/screenshots/Swagger_2.png)

![](src/screenshots/Swagger_3.png)

![](src/screenshots/Swagger_4.png)

![](src/screenshots/Swagger_chat_history.png)

## Technological Stack

### Backend
- **Python**: 3.13.3
- **FastAPI**: 0.115.14
- **FastAPI Users**: 15.0.2
- **Swagger UI / ReDoc** (OpenAPI): интерактивное описание API; JS/CSS, favicon и шрифты лежат в `src/static`, внешние CDN не используются

### ASGI web server
- **uvicorn**: 0.35.0 (с `websockets`)

### File uploads
- **python-magic-bin**: 0.4.14 — определение MIME-типа по сигнатуре байтов (Windows-сборка libmagic; на Linux/macOS — `python-magic` + системный `libmagic1`)

### Database
- **PostgreSQL**: 18.0
- **SQLAlchemy**: 2.0.41
- **Alembic**: 1.14.0

### Testing
- **pytest**: 8.3.5
- **pytest-asyncio**: 0.24.0
- **httpx**: 0.27.2

### Frontend
- **HTML5**, **CSS3**, **JavaScript**
- **Jinja2**: 3.1.6

### Task queue & cache
- **Celery**: 5.4.0 — фоновые задачи: durable-retry очередь CRM-синхронизации (`crm_outbox`), периодическая загрузка справочника «Проект»
- **Redis**: 5.0.8 (клиент; сервер — образ `redis:7-alpine` в Docker) — брокер Celery и result backend (результаты задач нигде в проекте не читаются — см. «Celery result backend» ниже), Pub/Sub-канал для WebSocket между несколькими uvicorn-воркерами, история WS-чата (`chat:history`), Redlock, token-bucket ограничитель запросов к CRM, лимит `request-code` по IP (подробно — «Ценность Celery + Redis в проекте»)

### Admin panel & monitoring
- **sqladmin**: 0.20.1 (+ `wtforms` 3.1.2, `itsdangerous` 2.2.0) — админ-панель `/admin`: пользователи (создание, сброс пароля), роли, задачи, подзадачи, справочник «Проект», очередь `crm_outbox` (просмотр и действие «Повторить»)
- **Flower**: 2.0.1 — веб-мониторинг Celery (порт 5555, только loopback)

---

## Диаграммы архитектуры

### 1. Контекстная диаграмма

Границы системы: кто с ней работает и от каких внешних систем она зависит.

```mermaid
flowchart LR
    user(["👤 Пользователь<br/>сотрудник"])
    admin(["🛡️ Администратор"])
    tm["<b>Task Manager</b><br/>веб-приложение: задачи, подзадачи, файлы,<br/>WebSocket-чат, фоновая синхронизация с CRM"]
    crm["🏢 CRM «Руководитель»<br/>REST API (внешняя система):<br/>задачи, подзадачи, справочник «Проект»"]
    smtp["📧 SMTP-сервер<br/>(внешняя система)"]

    user -->|"браузер: задачи, файлы, чат<br/>HTTP / WebSocket"| tm
    admin -->|"браузер: /users, /admin (sqladmin)<br/>Flower :5555"| tm
    tm -->|"задачи, подзадачи, файлы, справочники — из Celery-воркеров"| crm
    tm -->|"коды подтверждения email<br/>SMTP SSL"| smtp
```

Пользователь и администратор работают через браузер; CRM — единственная
система, куда уходят данные задач, и вызывается она **не из HTTP-запроса**,
а исключительно фоновыми Celery-воркерами. Регистрация и вход в приложение
CRM не затрагивают вообще.

### 2. Диаграмма контейнеров

Из каких развёртываемых единиц состоит система и как они связаны.

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 40, "rankSpacing": 110, "curve": "basis", "htmlLabels": true, "padding": 15}}}%%
flowchart LR
    browser["<b>Браузер</b><br/>Jinja2-страницы + JS<br/>(fetch, WebSocket)"]

    subgraph tm["Task Manager"]
        direction LR
        web["<b>web</b><br/>FastAPI + uvicorn<br/>(2 воркера)<br/>REST API, страницы,<br/>/ws/tasks, /admin, /uploads"]
        pg[("<b>PostgreSQL</b><br/>person, role, task, subtask,<br/>project, crm_outbox,<br/>registration_pending")]
        redis[("<b>Redis</b><br/>брокер Celery, Pub/Sub,<br/>chat:history, Redlock,<br/>rate-limit")]
        fs[("<b>Файлы</b><br/>src/uploads/")]

        subgraph celery["Celery"]
            beat["<b>celery-beat</b><br/>расписание"]
            wdef["<b>celery-worker</b><br/>очередь celery"]
            wshard["<b>celery-worker-shard-0..3</b><br/>очереди crm_sync.shard_N<br/>--pool=solo"]
            flower["<b>flower</b> :5555<br/>мониторинг"]
        end
    end

    smtp["<b>SMTP</b>"]
    crm["<b>CRM «Руководитель»</b>"]

    browser -->|"HTTP / WS"| web
    web -->|"SQL"| pg
    web -->|"очередь, Pub/Sub"| redis
    web -->|"файлы"| fs
    web -->|"письма"| smtp
    redis <-->|"задачи"| celery
    pg <-->|"SQL"| celery
    fs -.->|"чтение"| wshard
    wshard -.->|"insert / update / delete"| crm
```

Ключевая связка: `web` вставляет строку `crm_outbox` в ту же транзакцию PostgreSQL,
что и изменение задачи, и кладёт её в очередь `crm_sync.shard_N` Redis; на каждый шард
приходится ровно один воркер (`--pool=solo`), поэтому события одной задачи
обрабатываются строго по порядку. Redis выполняет несколько ролей (полный список и
ценность каждой — в разделе «Ценность Celery + Redis в проекте»): брокер Celery
(очереди задач) и result backend Celery (см. «Celery result backend» ниже —
результаты задач туда пишутся, но нигде не читаются), канал Pub/Sub для
рассылки WebSocket-событий между воркерами `web`, история чата и служебные
ключи (Redlock шарда, ограничитель запросов к CRM, лимит `request-code` по IP).

### 3. Диаграмма последовательностей

Сценарий: пользователь создаёт задачу с файлом — от HTTP-запроса до появления
задачи в CRM и галочки `✓` в интерфейсе.

```mermaid
%%{init: {'theme': 'default', 'themeVariables': {'noteTextColor': '#0f172a', 'noteBkgColor': '#fffbeb', 'noteBorderColor': '#b45309', 'signalTextColor': '#0f172a', 'actorTextColor': '#0f172a', 'actorBkg': '#f0f9ff', 'actorBorderColor': '#0284c7', 'labelTextColor': '#0f172a', 'sequenceNumberColor': '#0f172a'}}}%%
sequenceDiagram
    autonumber
    participant B as 🌐 Браузер
    participant W as ⚙️ web
    participant D as 🗄️ PostgreSQL
    participant R as ⚡ Redis
    participant K as 🧵 celery-worker-shard-N
    participant C as 🏢 CRM

    rect rgb(219, 234, 254)
        Note over B,R: Запрос пользователя — CRM не участвует
        B->>W: POST /create-task/ (multipart: JSON + файл ТЗ)
        Note right of W: валидация полей, расширения, MIME и размера файла —<br/>при ошибке 422, ничего не записано
        W->>D: INSERT task (crm_shard = id % N, sync_status = pending)
        W->>W: запись файла в src/uploads
        W->>D: INSERT crm_outbox: create<br/>INSERT crm_outbox: sync_files (depends_on_event_id = create)
        W->>D: COMMIT — задача и события фиксируются вместе
        W->>R: apply_async(process_outbox_row, queue=crm_sync.shard_N)
        Note right of W: сбой брокера не ломает ответ —<br/>строку подберёт reconcile_pending_outbox
        W->>R: PUBLISH task_events + RPUSH chat:history
        W-->>B: 201 Created (sync_status = pending)
        R-->>B: WebSocket: task_created — «You: …» у автора, «email: …» у остальных
        Note over B: список перечитывается раз в 3 с, пока есть pending — бейдж «В обработке»
    end

    rect rgb(254, 243, 199)
        Note over R,C: Фон — воркер шарда
        R->>K: process_outbox_row(create)
        K->>D: SELECT crm_outbox — зависимостей нет
        K->>R: token-bucket (лимит запросов к CRM) и Redlock на шард
        K->>C: find_task (защита от дубля), затем insert
        C-->>K: id = 27
        K->>D: SELECT task FOR NO KEY UPDATE → crm_task_id = 27, sync_status = synced
        Note right of K: задачу удалили, пока шёл вызов CRM →<br/>запись в CRM удаляется (компенсация)
        K->>D: crm_outbox.status = done, COMMIT
        R->>K: process_outbox_row(sync_files)
        K->>D: зависимость done → crm_task_id читается из БД
        K->>C: update: файл ТЗ (base64)
        C-->>K: success
        K->>D: status = done
    end

    rect rgb(209, 250, 229)
        Note over B,D: Результат
        B->>W: GET /tasks/ (очередной опрос)
        W->>D: SELECT task
        W-->>B: sync_status = synced → зелёная галочка ✓
    end

    Note over K,D: сбой CRM: до 5 попыток с экспоненциальной паузой (reconcile раз в минуту),<br/>затем failed — администратор видит last_error в /admin и нажимает «Повторить»
```

### 4. Диаграмма развёртывания

Где что запускается, как масштабируется и что переживает сбои.

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 50, "rankSpacing": 120, "curve": "basis", "htmlLabels": true, "padding": 15}}}%%
flowchart LR
    smtp["<b>SMTP-сервер</b><br/>внешний хост"]

    subgraph host["Хост (сервер или ПК разработчика)"]
        direction LR

        subgraph compose["Docker Compose, проект task-manager (9 контейнеров)"]
            direction LR
            web["<b>web</b> :8000<br/>uvicorn --workers<br/>UVICORN_WORKERS (по умолч. 2)<br/>bind mount ..:/app"]
            redis[("<b>redis</b> :6379<br/>redis:7-alpine")]

            subgraph workers["Celery-контейнеры (bind mount ..:/app)"]
                wdef["<b>celery-worker</b><br/>очередь celery"]
                wsh["<b>celery-worker-shard-0..3</b><br/>по контейнеру на шард<br/>(CRM_OUTBOX_SHARD_COUNT)"]
                beat["<b>celery-beat</b><br/>один экземпляр"]
                flower["<b>flower</b><br/>127.0.0.1:5555"]
            end
        end

        pg[("<b>PostgreSQL</b><br/>нативная служба,<br/>порт 5432, вне контейнеров")]
    end

    crm["<b>CRM «Руководитель»</b><br/>внешний хост"]

    web -->|"SMTP SSL"| smtp
    web -->|"Redis"| redis
    redis <-->|"Redis"| workers
    web -->|"host.docker.internal:5432"| pg
    workers <-->|"host.docker.internal:5432"| pg
    workers -->|"HTTPS"| crm
```

| Что | Как масштабируется / что происходит при сбое |
|---|---|
| `web` | Число воркеров uvicorn — `UVICORN_WORKERS`; WebSocket-события доходят между воркерами через Redis Pub/Sub, sticky-сессий не нужно. Перезапуск: `restart: unless-stopped` |
| Шарды CRM-очереди | Пропускная способность растёт числом шардов: новый шард = новый сервис `celery-worker-shard-N` + `CRM_OUTBOX_SHARD_COUNT`. Уменьшать число нельзя, пока в `task.crm_shard` есть значения снятых шардов. На шард — ровно один процесс (`concurrency=1`), иначе теряется порядок событий |
| `celery-beat` | Единственный экземпляр (иначе периодические задачи ставились бы дважды) |
| Падение воркера или Redis | События не теряются: строки лежат в PostgreSQL (`crm_outbox`), `reconcile_pending_outbox` каждую минуту возвращает зависшие в очередь |
| Redis без тома | История WS-чата и очереди не переживают пересоздание контейнера — включение AOF описано в разделе «Сохранение данных Redis (том + AOF)» ниже |
| PostgreSQL | Нативная служба хоста, в compose её нет; размер пула — `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` на каждый процесс |

### 5. Диаграмма вариантов использования

Что доступно каждой роли. Администратор — тот же пользователь с дополнительной
ролью `admin` (роли many-to-many): ему доступно всё, что и обычному пользователю.

```mermaid
flowchart LR
    guest(["👤 Гость<br/>не вошёл"])
    user(["👤 Пользователь<br/>роль user"])
    admin(["🛡️ Администратор<br/>роль admin"])
    smtp(["📧 SMTP-сервер"])
    crm(["🏢 CRM «Руководитель»"])

    subgraph sys["Task Manager"]
        direction TB
        uc1(["Зарегистрироваться<br/>email → код → пароль"])
        uc2(["Войти / выйти"])
        uc3(["Создавать, искать, менять<br/>и удалять задачи"])
        uc4(["Вести подзадачи задачи"])
        uc5(["Прикреплять файлы:<br/>ТЗ и иные документы"])
        uc6(["Общаться в чате и видеть<br/>действия других: You / email"])
        uc7(["Видеть статус синхронизации<br/>с CRM: ✓ · в обработке · ошибка"])
        uc8(["Смотреть профиль"])
        uc9(["Управлять пользователями и ролями<br/>/users, /admin"])
        uc10(["Создавать пользователей<br/>и задавать пароль"])
        uc11(["Разбирать очередь CRM (crm_outbox):<br/>last_error, «Повторить»"])
        uc12(["Обновить справочник «Проект»<br/>вне расписания"])
        uc13(["Мониторить Celery — Flower"])
    end

    guest --> uc1
    guest --> uc2
    user --> uc2
    user --> uc3
    user --> uc4
    user --> uc5
    user --> uc6
    user --> uc7
    user --> uc8
    admin -->|"может всё, что и пользователь"| user
    admin --> uc9
    admin --> uc10
    admin --> uc11
    admin --> uc12
    admin --> uc13

    uc1 -.->|"код подтверждения"| smtp
    uc3 -.->|"фоновая синхронизация"| crm
    uc4 -.->|"фоновая синхронизация"| crm
    uc5 -.->|"фоновая синхронизация"| crm
    uc12 -.-> crm
```

---

## Registration Flow

Регистрация разбита на три шага для подтверждения владения email-адресом.
Между шагами 2 и 3 сервер выдаёт короткоживущий JWT (`reg_token`) в HttpOnly-куке —
он служит доказательством того, что email подтверждён, и связывает шаги без серверного состояния.

```mermaid
%%{init: {'theme': 'default', 'themeVariables': {'noteTextColor': '#0f172a', 'noteBkgColor': '#fffbeb', 'noteBorderColor': '#b45309', 'signalTextColor': '#0f172a', 'actorTextColor': '#0f172a', 'actorBkg': '#f0f9ff', 'actorBorderColor': '#0284c7', 'labelTextColor': '#0f172a', 'sequenceNumberColor': '#0f172a'}}}%%
sequenceDiagram
    autonumber
    participant C as 🌐 Клиент
    participant S as ⚙️ Сервер
    participant D as 🗄️ PostgreSQL
    participant M as 📧 SMTP

    rect rgb(219, 234, 254)
        Note over C,M: Шаг 1 — Запрос кода подтверждения
        C->>S: POST /auth/register/request-code
        Note left of C: body: email
        S->>D: SELECT person WHERE email=?
        Note right of S: дубль → 409 EMAIL_ALREADY_REGISTERED<br/>повторный запрос < 60с → 429 RATE_LIMIT
        S->>D: SELECT registration_pending WHERE email=?
        Note right of S: secrets.randbelow(1_000_000) → код<br/>bcrypt.hash(code) → code_hash
        S->>D: INSERT registration_pending (code_hash, expires_at=now+15мин)
        S->>M: send_confirmation_code(email, code)
        M-->>C: Письмо с кодом подтверждения
        S-->>C: 200 OK
        Note left of C: message: Code sent
    end

    rect rgb(254, 243, 199)
        Note over C,M: Шаг 2 — Верификация кода
        C->>S: POST /auth/register/verify-code
        Note left of C: body: email, code
        S->>D: SELECT registration_pending WHERE email=?
        Note right of S: expires_at прошёл → DELETE + 400 CODE_EXPIRED<br/>attempts >= 3 → 400 TOO_MANY_ATTEMPTS (pending НЕ удаляется — держит created_at для rate-limit шага 1)<br/>bcrypt.verify fail → 400 INVALID_CODE
        S->>D: DELETE registration_pending
        Note right of S: jwt.encode(sub=email, purpose=registration,<br/>exp=now+20мин, secret=REG_TOKEN_SECRET)
        S-->>C: 200 OK
        Note left of C: Set-Cookie: reg_token=eyJ...<br/>HttpOnly, SameSite=Strict, Max-Age=1200
    end

    rect rgb(209, 250, 229)
        Note over C,M: Шаг 3 — Создание пользователя
        C->>S: POST /auth/register/complete
        Note left of C: Cookie: reg_token=eyJ...<br/>body: firstname, lastname, patronymic?, password
        Note right of S: jwt.decode(reg_token) → email<br/>purpose != registration → 401<br/>password regex fail → 422
        S->>D: INSERT INTO person
        S-->>C: 201 Created
        Note left of C: message: Registration complete<br/>reg_token cookie удалена (Max-Age=0)
    end
```

### Коды ошибок регистрации

| Эндпоинт | Код | Detail | Причина |
|---|---|---|---|
| `request-code` | 400 | `INVALID_EMAIL` | Формат email не совпадает с regex |
| `request-code` | 409 | `EMAIL_ALREADY_REGISTERED` | Email уже есть в `person` |
| `request-code` | 429 | `RATE_LIMIT:<sec>` | Повторный запрос до истечения 60 с |
| `request-code` | 503 | `SMTP_ERROR` | SMTP-сервер недоступен |
| `verify-code` | 400 | `NO_PENDING_REGISTRATION` | Нет записи в `registration_pending` |
| `verify-code` | 400 | `CODE_EXPIRED` | `expires_at` истёк (15 мин) |
| `verify-code` | 400 | `TOO_MANY_ATTEMPTS` | 3 неверные попытки исчерпаны; запись `registration_pending` **не удаляется** (иначе следующий `request-code` обходил бы 60-секундный rate-limit — см. таблицу `registration_pending` ниже) |
| `verify-code` | 400 | `INVALID_CODE:<rem>` | Неверный код, `rem` — оставшихся попыток |
| `complete` | 401 | `MISSING_REG_TOKEN` | Кука `reg_token` отсутствует |
| `complete` | 401 | `REG_TOKEN_INVALID` | JWT не прошёл проверку подписи/срока |
| `complete` | 409 | `EMAIL_ALREADY_REGISTERED` | Гонка: email зарегистрирован параллельным запросом |
| `complete` | 422 | текст требований к паролю / ошибка валидации | Пароль не соответствует требованиям (заглавная буква, цифра, спецсимвол, 5–72 символа) или не заполнены имя/фамилия |

`complete` в CRM не обращается вообще, поэтому 503 `CRM_UNAVAILABLE` эндпоинт не возвращает даже при недоступной CRM.

---

## Authentication Flow

Аутентификация построена на двух JWT-токенах с раздельными секретами и TTL.
`access_token` используется при каждом запросе, `refresh_token` — только для его обновления.

```mermaid
%%{init: {'theme': 'default', 'themeVariables': {'noteTextColor': '#0f172a', 'noteBkgColor': '#fffbeb', 'noteBorderColor': '#b45309', 'signalTextColor': '#0f172a', 'actorTextColor': '#0f172a', 'actorBkg': '#f0f9ff', 'actorBorderColor': '#0284c7', 'labelTextColor': '#0f172a', 'sequenceNumberColor': '#0f172a'}}}%%
sequenceDiagram
    autonumber
    participant C as 🌐 Клиент
    participant S as ⚙️ Сервер
    participant D as 🗄️ PostgreSQL

    rect rgb(219, 234, 254)
        Note over C,D: Вход в систему — POST /auth/login
        C->>S: POST /auth/login
        Note left of C: Content-Type: x-www-form-urlencoded<br/>body: username=email, password=pwd
        S->>D: SELECT person WHERE email=?
        Note right of S: password_helper.verify_and_update(pwd, hash)<br/>ошибка → 400 LOGIN_BAD_CREDENTIALS
        Note right of S: jwt.encode(access_token, exp=30мин, secret=ACCESS_SECRET)<br/>jwt.encode(refresh_token, exp=7д, secret=REFRESH_SECRET)
        S-->>C: 200 OK
        Note left of C: Set-Cookie: access_token=eyJ... (HttpOnly, SameSite=Lax, Max-Age=1800)<br/>Set-Cookie: refresh_token=eyJ... (HttpOnly, SameSite=Lax, Max-Age=604800)
    end

    Note over C,D: ⏱ 30 минут — access_token истёк, fetchWithAuth() инициирует обновление

    rect rgb(254, 243, 199)
        Note over C,S: Обновление токена — POST /auth/access-token
        C->>S: POST /auth/access-token
        Note left of C: Cookie: refresh_token=eyJ...
        S->>D: SELECT person WHERE id=sub AND is_active=true
        Note right of S: jwt.decode(refresh_token, secret=REFRESH_SECRET)<br/>проверка is_active<br/>jwt.encode(access_token, exp=30мин)
        S-->>C: 200 OK
        Note left of C: Set-Cookie: access_token=eyJ... (HttpOnly, SameSite=Lax, Max-Age=1800)
    end
```

Вход не обращается к CRM вообще. Единственная проверка на вход — совпадение
пароля с хешем в PostgreSQL. Регистрация (`POST /auth/register/complete`) тоже не
обращается к CRM (см. «CRM «Руководитель»» ниже).

### Токены

| Параметр | access_token | refresh_token |
|---|---|---|
| TTL | 30 мин (`ACCESS_EXP`) | 7 дней (`REFRESH_EXP`) |
| Подписывается | `ACCESS_SECRET` | `REFRESH_SECRET` |
| Кука | `access_token` | `refresh_token` |
| HttpOnly | да | да |
| SameSite | Lax | Lax |
| Используется | все защищённые маршруты | только `POST /auth/access-token` |

`SameSite=Lax` — кука отправляется при top-level navigation (переход по ссылке), но не при cross-site subresource-запросах. Защита от CSRF без ограничений OAuth-редиректов.

Раздельные секреты изолируют компрометацию: утечка `ACCESS_SECRET` не позволяет подделать `refresh_token` и получить долгосрочный доступ.

### Коды ошибок аутентификации

| Эндпоинт | Код | Detail | Причина |
|---|---|---|---|
| `login` | 400 | `LOGIN_BAD_CREDENTIALS` | Неверный пароль или пользователь не найден |
| `access-token` | 401 | — | `refresh_token` отсутствует, просрочен или недействителен |

---

## Endpoints

### Swagger UI / OpenAPI

Интерактивное описание API строится автоматически из кода (FastAPI) и доступно после запуска сервера:

| Адрес | Что это |
|---|---|
| `/docs` | Swagger UI — просмотр эндпоинтов и вызов через «Try it out» |
| `/redoc` | ReDoc — то же описание в виде читаемой документации |
| `/openapi.json` | Схема OpenAPI 3 (для генераторов клиентов и импорта в Postman) |

- **Что описано.** Все REST-эндпоинты (задачи, подзадачи, файлы, пользователи, регистрация, вход, `/admin/crm-*`,
  `/chat/history`) с параметрами, схемами ответов и реальными кодами ошибок (`400`/`401`/`403`/`404`/`409`/`413`/`422`/`429`/`503`).
  Не входят: WebSocket (`/ws/tasks/{client_id}` — OpenAPI его не описывает, см. раздел «WebSocket»), раздача файлов
  `/uploads/*` и sqladmin (`/admin`).
- **Авторизация.** Эндпоинты защищены кукой `access_token`. Сначала выполните `POST /auth/login` в Swagger UI (или войдите
  на странице `/` в этом же браузере) — кука подставится в последующие запросы «Try it out».
- **Создание задачи и подзадачи** (`POST /create-task/`, `POST /create-subtask/`) принимают `multipart/form-data`: JSON —
  строкой в поле `data`, файлы — в `specification` и `other_files`. Формат и пример JSON указаны в описании поля `data`.
- **Без внешних ресурсов.** Бандлы Swagger UI и ReDoc, favicon и стили раздаются из `/static`; страницы работают без интернета.
- **Отключение.** Переменная `DOCS_ENABLED` (см. «Переменные окружения»): не задана — документация включена везде, кроме
  `API_MODE=prod`; `true`/`false` — явное значение. Если документация выключена, `/docs`, `/redoc` и `/openapi.json`
  отвечают `404`. Значение читается при старте — после изменения перезапустите сервер.

### Страницы (HTML)

- `/` — вход, `/register` — регистрация, шаг 1; `/confirm-email` — шаг 2; `/complete-registration` — шаг 3.
- `/task-board` — общая доска задач (со списком, поиском и WebSocket-чатом); `/task/{task_id}` — страница задачи; `/subtask-board/{task_id}` — подзадачи задачи; `/subtask/{subtask_id}` — страница подзадачи; `/profile` — профиль.
- `/admin/crm-sync` — страница статуса CRM-синхронизации (только `admin`); `/admin` — sqladmin-панель.

Страницы, требующие входа, при отсутствии сессии перенаправляют браузер на `/`; запросы `fetch` получают JSON `401`.

### Регистрация

- `POST /auth/register/request-code` — шаг 1: отправить 6-значный код на email. Тело (JSON): `{ email }`.
- `POST /auth/register/verify-code` — шаг 2: подтвердить код (≤ 3 попытки, TTL 15 мин). Тело (JSON): `{ email, code }`. Выдаёт куку `reg_token` (HttpOnly, SameSite=Strict, TTL 20 мин).
- `POST /auth/register/complete` — шаг 3: создать пользователя. Требует куку `reg_token`. Тело (JSON): `{ firstname, lastname, patronymic?, password }`.

### Аутентификация

- `POST /auth/login` — выдать `access_token` и `refresh_token`. Тело: `application/x-www-form-urlencoded`, поля `username` (email) и `password`.
- `POST /auth/access-token` — обновить `access_token` по `refresh_token`-куке.
- `POST /auth/logout` — JS-вариант выхода: удаляет обе куки, возвращает JSON 200.
- `POST /auth/do-logout` — форм-вариант выхода: удаляет обе куки, возвращает 303 See Other на `/`.

### Задачи (требуют действующий `access_token`)

> **Shared board:** все аутентифицированные пользователи видят один общий список и могут создавать, редактировать и удалять любую задачу. Инициатор каждого изменения передаётся остальным через WebSocket (`sender: "user@example.com"`).

- `GET /tasks/` — список всех задач. Параметры: `skip` (≥ 0, по умолчанию 0), `limit` (1–100, по умолчанию 5). Общее число задач — в заголовке `X-Total-Count`. Порядок — по возрастанию `id` (стабильная пагинация: обновление задачи не перемещает её между страницами).
- `GET /tasks/search?title=...` — поиск по части названия (регистронезависимый ILIKE с экранированием спецсимволов). Поддерживает те же параметры пагинации.
- `GET /tasks/{task_id}` — получить задачу по ID; включает поле `subtask_count`. `404` если задача не найдена.
- `POST /create-task/` — создать задачу (`201 Created`). Тело — `multipart/form-data`: JSON задачи в form-поле `data` (`{ title, description, project? }`) + необязательные файлы `specification` (ТЗ) и `other_files` (до 10). Задача и файлы создаются одним запросом: все файлы валидируются до записи в БД (недопустимый файл → `422`, ничего не создаётся); сбой сохранения отдельного файла на диск не отменяет создание — причины возвращаются в поле ответа `file_upload_errors`. Опциональное поле `project` — CRM-ID опции справочника «Проект» (см. раздел CRM ниже); неизвестный/неактивный CRM-ID → `422`. `409 Conflict` при дублировании названия у того же владельца.
- `PATCH /tasks/{task_id}` — частичное обновление задачи (только переданные поля). `project: ""` очищает поле, отсутствие ключа — не трогает. `409 Conflict` при переименовании в существующее название.
- `DELETE /delete-task/{task_id}` — удалить задачу; каскадно удаляет все её подзадачи.

### Подзадачи (требуют действующий `access_token`)

> Все аутентифицированные пользователи могут создавать, редактировать и удалять подзадачи любой задачи.

- `POST /create-subtask/` — создать подзадачу (`201 Created`). Тело — `multipart/form-data`: JSON `{ task_id, title, description }` в form-поле `data` + необязательные файлы `specification` и `other_files` (семантика та же, что у `POST /create-task/`, включая `file_upload_errors`). `404` если родительская задача не найдена. `409 Conflict` при дублировании названия в рамках той же задачи.
- `GET /subtasks/` — список подзадач задачи. Параметры: `task_id` (обязательный), `skip` (≥ 0), `limit` (1–100, по умолчанию 20). Общее число — в заголовке `X-Total-Count`.
- `GET /subtasks/{subtask_id}` — получить подзадачу по ID. `404` если не найдена.
- `PATCH /subtasks/{subtask_id}` — частичное обновление подзадачи. `409 Conflict` при дублировании названия.
- `DELETE /delete-subtask/{subtask_id}` — удалить подзадачу.

### Файлы задач (требуют действующий `access_token`)

Файлы хранятся на диске в `src/uploads/tasks/{task_id}/...`; в БД — только относительные пути (`specification_path`, `other_file_paths`). Раздача — не `StaticFiles`-mount, а отдельный маршрут `GET /uploads/{path}` с `Depends(current_user)`: файлы недоступны без авторизации. Поддерживаемые форматы: PDF, JPEG, PNG (расширение и MIME-сигнатура по magic bytes проверяются отдельно). Максимальный размер одного файла — 10 МБ. Подробности — в таблице «Ограничения на загрузку файлов» ниже. Загрузка/удаление ставит durable-outbox-строку `sync_files` (см. «Синхронизация задач» ниже) и рассылает WebSocket-событие `task_files_updated`.

- `POST /tasks/{task_id}/specification` — загрузить (или заменить) файл технического задания. `multipart/form-data`, поле `file`. `404` если задача не найдена, `413` при превышении размера, `422` при недопустимом расширении/MIME.
- `DELETE /tasks/{task_id}/specification` — удалить файл ТЗ. `404` если файл не загружен.
- `POST /tasks/{task_id}/files` — добавить файлы в «Иные документы» (до 10 файлов суммарно на задачу). `422` при превышении лимита.
- `DELETE /tasks/{task_id}/files/{filename}` — удалить один файл из «Иных документов» по имени. `404` если файл не найден.

#### Ограничения на загрузку файлов

| Ограничение | Значение | Ответ при нарушении |
|---|---|---|
| Поддерживаемые форматы | PDF, JPEG, PNG — расширения `.pdf`, `.jpg`, `.jpeg`, `.png` | `422` «Расширение '…' не разрешено» |
| Соответствие содержимого | MIME по сигнатуре байтов (libmagic) должен совпадать с расширением: `application/pdf`, `image/jpeg`, `image/png` | `422` «MIME-тип файла … не соответствует расширению» |
| Запрещённые символы в имени | `\ / : * ? " < > \|` (набор, который Windows Explorer запрещает при сохранении файла) | `422` «Имя файла содержит недопустимые символы: …» |
| Максимальный размер одного файла | 10 МБ | `413` «Размер файла превышает лимит 10 МБ» |
| «Иные документы» | не более 10 файлов на задачу/подзадачу | `422` «Превышен лимит файлов» |

Word, Excel и TXT (`.doc`, `.docx`, `.xls`, `.xlsx`, `.txt`) не поддерживаются. Ограничения совпадают с ais-assignment (кроме запрещённых символов в имени — отдельное усиление task-manager, не из ais-assignment). Источник истины — сервер (`MAX_FILE_SIZE`, `ALLOWED`, `MAX_OTHER_FILES`, `FORBIDDEN_FILENAME_CHARS` в `src/utils/file_utils.py`). Клиент дублирует их для мгновенной обратной связи (`OTHER_FILES_MAX_SIZE`, `OTHER_FILES_ALLOWED_EXT`, `MAX_OTHER_FILES`, `FORBIDDEN_FILENAME_CHARS`/`findForbiddenFilenameChars()` в `src/static/js/common.js` и `accept=".pdf,.jpg,.jpeg,.png"` у полей выбора файла): неподходящий файл не попадает в очередь «Иных документов», а пользователь видит тост вида ««отчёт.docx»: расширение «.docx» не поддерживается» или ««скан.pdf»: размер превышает лимит 10 МБ». При изменении лимита нужно поменять обе стороны.

Запрещённые символы в имени и кодирование URL — два независимых механизма. Скачивание файлов с `#`/`%`/`?` в имени обеспечивает кодирование URL (`encodeUploadPath`/`_encode_upload_path`, см. «Файлы задач» выше): оно действует для ВСЕХ символов, не только из списка `\ / : * ? " < > |`. Список запрещённых символов пересекается с `#`/`%`/`?` только по `?`; `#` и `%` допустимы в имени и полагаются на кодирование URL, а не на запрет при загрузке. Цель проверки запрещённых символов — не пустить на диск символы, проблемные для файловой системы Windows (при прямом доступе к `uploads/` в обход приложения).

### Файлы подзадач (требуют действующий `access_token`)

Аналогичные маршруты для подзадач, файлы — в `src/uploads/subtasks/{subtask_id}/...`, событие — `subtask_files_updated`:

- `POST /subtasks/{subtask_id}/specification`, `DELETE /subtasks/{subtask_id}/specification`
- `POST /subtasks/{subtask_id}/files`, `DELETE /subtasks/{subtask_id}/files/{filename}`

### Управление пользователями (требуют роль `admin`)

> Доступ проверяется через `require_role("admin")`: пользователь, среди ролей которого нет `admin`, получает `403 Forbidden`. Роли — many-to-many (`user_role`): пользователь может одновременно иметь несколько ролей, доступ разрешён, если хотя бы одна из них называется `admin`.

- `GET /users/` — список всех пользователей.
- `PATCH /users/{user_id}` — изменить данные пользователя (`username`, `firstname`, `lastname`, `patronymic`, `role_ids`, `is_active`). `role_ids` заменяет весь набор ролей пользователя целиком (не добавляет к существующим). `400` при несуществующем id в `role_ids`. `404` если пользователь не найден.
- `DELETE /users/{user_id}` — удалить пользователя. `400` при попытке удалить собственную учётную запись. Задачи пользователя удаляются так же, как через `DELETE /delete-task/{id}`: вместе с файлами на диске (свои и подзадач), outbox-строкой `delete` для CRM и WebSocket-событием `task_deleted`.
- `POST /admin/crm-options/refresh` — поставить в очередь Celery немедленную синхронизацию справочника «Проект» с CRM, не дожидаясь расписания (раз в `CRM_PROJECT_SYNC_INTERVAL_SECONDS`). `202 Accepted` — работа принята, но не гарантированно завершена к моменту ответа.
- `GET /admin/crm-sync` — HTML-страница статуса CRM-синхронизации; `GET /admin/crm-sync-status/tasks` и `/subtasks` — те же данные в JSON (CRM-id, `sync_status`, последняя попытка).
- `/admin` — sqladmin-панель (создание пользователей и сброс/смена пароля, просмотр и точечная правка пользователей, ролей, задач, подзадач; read-only: справочник «Проект» и `crm_outbox`); в `crm_outbox` — действие «Повторить» для `failed`-событий. Вход отдельной формой email/пароль, только роль `admin`. Отображение времени — `ADMIN_TIMEZONE` (по умолчанию `Europe/Moscow`). Правки задач/подзадач через панель не синхронизируются с CRM.

### WebSocket

`ws://<host>/ws/tasks/{client_id}` — подключение требует куку `access_token`. Без неё сервер закрывает соединение с кодом `1008 Policy Violation`. Один и тот же маршрут используют `task-board.html` (полный клиент с чатом), `subtask-board.html` и страницы деталей `task-detail.html`/`subtask-detail.html` (реакция на события своей задачи/подзадачи — без чата).

Реестр соединений (`src/realtime/connection_manager.py`) хранит **набор** WebSocket-соединений на каждого пользователя (`dict[user_id, set[WebSocket]]`), а не одно — несколько одновременно открытых вкладок/устройств получают события независимо, новое подключение не вытесняет предыдущие.

Реестр — в памяти процесса, но рассылка работает и при нескольких uvicorn-воркерах (в Docker `web` запускается с 2 воркерами): `broadcast()` публикует каждое событие в Redis Pub/Sub-канал, и каждый воркер подписан на тот же канал — событие, обработанное в воркере A, доходит до клиентов, чьё WS-соединение принял воркер B.

Типы событий и формат сообщения в чате. **Все события рассылаются всем подключённым клиентам, включая
инициатора и все его вкладки**, а клиент рисует единый формат: **`You: …`** — для собственных сообщений и
действий, **`user@mail.ru: …`** — для чужих (константа `SELF_LABEL` в `task-board.js`):

| `type` | Что происходит | Формат в чате |
|---|---|---|
| `chat` | Текст из панели чата. Сервер сам выставляет каждому получателю признак `is_own` (внутренний id отправителя клиенту не отдаётся), поэтому собственное сообщение появляется по эху от сервера — одинаково во всех вкладках автора | `You: text` / `email: text` |
| `task_created` / `task_updated` / `task_deleted` | Действие над задачей; событие несёт `actor_id` инициатора | `You: Создана задача: X` / `email: Обновлена задача: X` / `…: Удалена задача: X` |
| `subtask_created` / `subtask_updated` / `subtask_deleted` | Действие над подзадачей | `You: Создана подзадача «Y» [X]` / `email: Обновлена подзадача «Y» [X]` / `…: Удалена подзадача «Y» [X]` |
| `task_files_updated` / `subtask_files_updated` | Загрузка или удаление файлов (`action: "uploaded"` \| `"deleted"`) | `You: Добавлены файлы к задаче «X»` / `email: Удалены файлы у подзадачи «Y» [X]` |

Как реагируют страницы (по `actor_id` клиент отличает своё действие от чужого): доски и страницы деталей
перечитывают данные своей сущности — кроме самого инициатора, который уже видит результат в HTTP-ответе;
если сущность удалил другой пользователь, страница `task-detail`/`subtask-detail` показывает алерт и
переходит на список — иначе следующее действие упёрлось бы в `404 Task/Subtask not found`. Рассылка
работает через `broadcast()` без исключения кого-либо, поэтому `exclude_user_id` для действий не используется.

**История чата** хранится на сервере в Redis-списке `chat:history` (не в `localStorage`): последние
`CHAT_HISTORY_MAX_LEN` (по умолчанию 500) записей — сообщения чата и события действий (задачи, подзадачи и
файлы). Панель загружает её при открытии `task-board` через `GET /chat/history?limit=50` и подгружает более
старые записи при прокрутке вверх (`before_id` — курсор; `limit` ≤ 200; требует входа). Формат записей после
перезагрузки страницы тот же, что и вживую: `You: …` / `email: …`. При разрыве соединения клиент
переподключается через 3 секунды.

**Надёжность и лимиты.** Сбой Redis при записи истории или рассылке не превращает уже выполненную операцию в `500`: ошибка логируется, событие уходит только локальным соединениям. Фоновая подписка на Pub/Sub переподключается сама (backoff 0,5 → 30 с). На одно WS-соединение действуют лимиты: сообщение не длиннее `WS_MAX_MESSAGE_CHARS` (1000 символов), не более `WS_RATE_LIMIT_MESSAGES` (10) сообщений за `WS_RATE_LIMIT_WINDOW_SECONDS` (10 с). Превышение отбрасывает сообщение и возвращает отправителю `{"type": "error", "detail": …}` без разрыва соединения; размер фрейма дополнительно ограничен `--ws-max-size 65536` у uvicorn.

---

## Архитектура приложения

`src/main.py` собран по паттерну Application Factory: `create_app()` создаёт `FastAPI`-инстанс
(обработчики ошибок, `/static`-mount, middleware, роутеры, sqladmin-панель) и возвращает его;
`app = create_app()` на уровне модуля — единственная точка входа для `uvicorn src.main:app`
(см. `src/Dockerfile`) и для `from src.main import app` в `tests/conftest.py`. Само наполнение
вынесено из `main.py` в отдельные модули:

- **`src/middlewares.py`** (`register_middlewares(app)`) — CORS (`CORS_ORIGINS_CSV`), GZip, кеш-заголовок `Cache-Control` для `/static/*`, Content-Security-Policy.
- **`src/errors_handlers.py`** (`register_errors_handlers(app)`) — 401 с `Accept: text/html` (браузерная навигация) редиректит на `/`; иначе — JSON-ответ для `fetch`.
- **`src/static/js/common.js`** — общие frontend-константы (лимиты размера/числа файлов, размер страницы пагинации, задержка переподключения WebSocket), подключается тегом `<script>` до основного скрипта страницы на всех досках и страницах деталей — исключает дублирование этих значений в page-скриптах.

Остальные части приложения:

- **`src/auth/`** — fastapi-users: `UserManager` (хеш пароля argon2id, роль по умолчанию), JWT-стратегии, трёхшаговая регистрация с кодом на email.
- **`src/routers/`** — REST-роутеры задач, подзадач, файлов и пользователей; HTML-страницы (`pages.py`); раздача `/uploads/*`; маршруты `/admin/crm-*`.
- **`src/services/`** — бизнес-логика (`tasks.py`, `subtasks.py`, `attachments.py`, `access.py`, `admin_sync.py`): изменение и строка `crm_outbox` в одной транзакции, блокировки строк, события WebSocket.
- **`src/task_logic/`** — ORM-модели (`Task`, `Subtask`, `Project`, `CrmOutbox`) и Pydantic-схемы.
- **`src/crm/`** — клиенты CRM (см. «Структура модуля» ниже).
- **`src/celery_app.py`, `src/tasks/`** — Celery: `process_outbox_row`, `reconcile_*`, `sync_project_table`, шардирование `id % N`, Redlock шарда, token-bucket.
- **`src/realtime/`** — WebSocket-эндпоинт, `ConnectionManager` (рассылка между воркерами через Redis Pub/Sub), формирование событий, история чата.
- **`src/admin/`** — sqladmin-панель `/admin`.
- **`src/models/`, `src/utils/`, `src/templates/`, `src/static/`** — реестр ORM-моделей для Alembic, утилиты файлов, Jinja2-шаблоны, статика.

---

## Ценность Celery + Redis в проекте

Celery и Redis — не «украшение стека», а то, что делает возможными две вещи, без которых приложение
вело бы себя заметно хуже: **синхронизацию с внешней CRM, не зависящую от того, жива ли CRM прямо сейчас**,
и **работу нескольких процессов `web` как одного целого** (WebSocket-рассылка, общий лимит запросов).
Один и тот же Redis-инстанс (`REDIS_URL`) и один `celery_app` (`src/celery_app.py`) обслуживают всё сразу —
второго брокера или второго Celery-приложения в проекте нет.

### Что дал бы проект без них

| Возможность | Без Celery + Redis | С Celery + Redis |
|---|---|---|
| Создание/правка/удаление задачи, пока CRM медленная или недоступна | Либо HTTP-запрос ждёт CRM (до ~270 с на большой файл — таймауты `connect 30 + write 120 + read 120`), либо ошибка пользователю из-за чужого сервиса | Ответ уходит сразу после `commit` в PostgreSQL; CRM-вызов выполняется в фоне |
| Падение сервера между `db.commit()` и вызовом CRM | CRM-изменение теряется навсегда: в БД задача есть, в CRM — нет, и узнать об этом нечем | Строка `crm_outbox` уже закоммичена в PostgreSQL — её найдёт и повторит `reconcile_pending_outbox` |
| Недоступная CRM | Нужен собственный механизм повторов в коде веб-процесса | Повторы с нарастающей паузой (60 → 120 → 240 → 480 с, потолок 900 с), 5 попыток, затем `failed` с причиной в админке |
| Периодические задачи (справочник «Проект», очистка `crm_outbox`) при `UVICORN_WORKERS > 1` | Каждый воркер `web` запускал бы свой таймер — задачи дублировались бы | Один `celery-beat` — одно расписание на весь деплой, независимо от числа воркеров `web` |
| WebSocket при нескольких процессах `web` | Событие, обработанное воркером A, не дошло бы до клиентов, подключённых к воркеру B | Redis Pub/Sub: каждый воркер подписан на один канал и пересылает событие своим клиентам |
| Лимит запросов к CRM и к `request-code` | Лимит в памяти процесса — у каждого воркера свой счётчик, суммарный лимит не соблюдается | Один счётчик в Redis на весь проект |

### Ценность Celery

1. **CRM убрана из пути HTTP-запроса.** `web` никогда не вызывает CRM — только Celery-воркер (см. «CRM
   «Руководитель» → Синхронизация задач»). Латентность и сбои CRM не доходят до пользователя; файлы до
   ~133 МБ (base64 от 10×10 МБ) уходят в CRM из воркера, а не из запроса.
2. **Надёжность доставки поверх транзакционного outbox.** `crm_outbox` защищает от потери самого *факта*
   изменения (строка в той же транзакции PostgreSQL), а Celery добавляет исполнение и повторы. Флаги
   `task_acks_late` и `task_reject_on_worker_lost` (`src/celery_app.py`) защищают от потери задачи уже *после*
   постановки в очередь — при `kill -9`/OOM воркера сообщение не считается подтверждённым.
3. **Порядок и ограниченная скорость.** Один воркер на шард (`--pool=solo`) и структурный запрет на обгон
   событий одной сущности гарантируют, что `update` не обгонит `create`; `acquire_slot()` не даёт всем шардам
   вместе превысить `CRM_RATE_LIMIT_PER_SECOND` (по умолчанию 5/с) при разборе накопившегося backlog'а.
4. **Периодика без дублей.** `celery-beat`: `reconcile_pending_outbox` (60 с), `reconcile_blocked_outbox`
   (300 с), `sync_project_table` (180 с), `cleanup_done_outbox` (03:00 UTC).
5. **Наблюдаемость.** Flower (`:5555`, только loopback) показывает воркеры, очереди шардов, статусы и
   аргументы задач; необработанные исключения пишет единый обработчик сигнала `task_failure` под именем
   логгера `src.celery_app`.

### Ценность Redis

| Роль | Ключи / механизм | Ценность |
|---|---|---|
| **Брокер Celery** | очередь `celery`, очереди шардов `crm_sync.shard_N` | Доставка задач воркерам. Сам Redis — не источник истины: события лежат в PostgreSQL |
| **Pub/Sub для WebSocket** | канал рассылки событий | Событие доходит до клиентов на любом из процессов `web` (в Docker их 2); sticky-сессии не нужны |
| **История WS-чата** | список `chat:history`, счётчик `chat:history:next_id` | Единственные данные, которых нет больше нигде; ради них включён AOF (см. «Сохранение данных Redis»). Размер ограничен `CHAT_HISTORY_MAX_LEN` |
| **Лимит запросов к CRM** | `crm_rate_limit` (INCR + `EXPIRE NX`, TTL 1 с) | Один общий лимит на все шарды: ограничение накладывает сама CRM, а не отдельный шард |
| **Лимит `request-code` по IP** | `reg_code_rate_limit:<ip>` (5 запросов / 600 с) | Защита от email-бомбинга: один анонимный клиент не может засыпать письмами произвольные чужие адреса |
| **Redlock шарда** | `crm_shard_lock:<shard>` (TTL 300 с) | Страховка от ошибки деплоя «два воркера на один шард»; порядок обеспечивает топология, лок — только защита от её нарушения |
| Result backend Celery | `celery-task-meta-*` | См. «Где ценность ограничена» — результаты никто не читает |

### Что будет при сбоях

- **Падает Celery-воркер или Redis.** Данные не теряются: строки лежат в PostgreSQL, `reconcile_pending_outbox`
  возвращает зависшие в очередь. Пользователь продолжает работать; растёт только задержка синхронизации
  с CRM.
- **CRM недоступна.** Задачи копятся как `pending`, повторяются с паузой, после 5 неудач становятся `failed`;
  администратор видит причину (`last_error`) и возвращает событие в очередь действием «Повторить».
- **Redis пересоздан без тома.** Теряется история WS-чата и «событие в полёте» (например, разовый запуск
  `sync_project_table`); CRM-синхронизация не страдает. Как сохранить чат — «Сохранение данных Redis (том + AOF)».

### Где ценность ограничена

- **Result backend не используется:** Celery пишет результат каждой задачи в Redis, но ни одна строка проекта его
  не читает — это паразитная запись с TTL 24 ч (подробности — «Celery result backend»).
- **Redis — не гарантия доставки.** Надёжность обеспечивает PostgreSQL (`crm_outbox`) и `reconcile`; потеря
  сообщения в Redis задерживает синхронизацию не более чем на минуту (тик `reconcile`), но не теряет её.
- **Строгий порядок стоит параллелизма.** Один процесс на шард (`--pool=solo`) — осознанная плата за порядок
  событий одной задачи; параллелизм достигается только между шардами.

---

## Database Schema

```
role
├── id   INTEGER PK
└── name VARCHAR      ("user" | "admin")

user_role                                  (таблица-связка many-to-many person ↔ role)
├── person_id INTEGER FK → person.id (ondelete CASCADE)  ┐
└── role_id   INTEGER FK → role.id   (ondelete CASCADE)  ┴─ составной PK (person_id, role_id)

person
├── id              INTEGER PK
├── email           VARCHAR(255) UNIQUE
├── username        VARCHAR(255)        (= email до @)
├── firstname       VARCHAR(255)
├── lastname        VARCHAR(255)
├── patronymic      VARCHAR(255) NULL
├── hashed_password VARCHAR(1024)       (argon2id; при входе проверяются и bcrypt-хеши)
├── registered_at   TIMESTAMP WITH TIME ZONE
├── is_active       BOOLEAN
├── is_superuser    BOOLEAN
└── is_verified     BOOLEAN

task
├── id                 INTEGER PK
├── title              VARCHAR(100)      (B-tree index + GIN pg_trgm index для ILIKE-поиска)
├── description        VARCHAR(2000)
├── completed          BOOLEAN
├── owner_id           INTEGER FK → person.id (ondelete CASCADE)
├── crm_task_id        INTEGER NULL      (NULL = не синхронизировано с CRM)
├── specification_path VARCHAR NULL      (rel-путь к файлу ТЗ в uploads/)
├── other_file_paths   JSONB NULL        (список rel-путей «иных документов», до 10)
├── project_id         INTEGER NULL FK → project.id (опция глобального справочника CRM «Проект»)
├── crm_shard          VARCHAR(20) NULL  (шард CRM-outbox-очереди, sticky-присвоение через `id % N` — см. «Celery + Redis» ниже)
├── sync_status        VARCHAR(20)       ("unsynced"|"pending"|"synced"|"failed" — бейдж на досках, см. «CRM → Синхронизация задач»)
└── UNIQUE(title, owner_id)              (название уникально в рамках владельца)

subtask
├── id                 INTEGER PK
├── title              VARCHAR(100)
├── description        VARCHAR(2000)
├── completed          BOOLEAN
├── task_id            INTEGER FK → task.id (ondelete CASCADE)
├── crm_subtask_id     INTEGER NULL      (NULL = не синхронизировано с CRM)
├── specification_path VARCHAR NULL      (rel-путь к файлу ТЗ в uploads/)
├── other_file_paths   JSONB NULL        (список rel-путей «иных документов», до 10)
├── sync_status        VARCHAR(20)       (та же семантика, что и task.sync_status; своего crm_shard нет — берётся из родительской task)
└── UNIQUE(title, task_id)               (название уникально в рамках задачи)

project                                   (локальное зеркало списка «Проект» CRM, list_id=11 — см. CRM-раздел ниже)
├── id          INTEGER PK
├── crm_id      VARCHAR(20) UNIQUE       (ID опции в CRM — натуральный ключ синхронизации)
├── label       VARCHAR(255)             (отображаемое название опции)
├── sort_order  INTEGER
├── is_active   BOOLEAN                  (опция пропала из CRM → false, не удаляется — не ломать FK у task.project_id)
└── synced_at   TIMESTAMP WITH TIME ZONE

crm_outbox                                 (durable-retry очередь CRM-синхронизации — см. «Celery + Redis» ниже)
├── id                   INTEGER PK
├── aggregate_type       VARCHAR(20)      ("task" | "subtask")
├── aggregate_id         INTEGER, INDEX   (НЕ FK — строка переживает каскадное удаление сущности)
├── operation            VARCHAR(20)      ("create" | "sync_files" | "update" | "delete")
├── shard                VARCHAR(20) NULL (снимок task.crm_shard на момент вставки строки)
├── payload              JSONB            (снимок данных, нужных для CRM-вызова)
├── status               VARCHAR(20)      ("pending" → "done" | "failed" | "blocked")
├── attempts             INTEGER
├── dispatched_at        TIMESTAMP WITH TIME ZONE NULL (последняя реальная постановка в очередь reconcile'ом; только для attempts == 0 — см. п. 15 ниже)
├── last_error           TEXT NULL        (причина последнего сбоя, до 1000 символов; очищается при успехе)
├── depends_on_event_id  INTEGER NULL FK → crm_outbox.id (self-FK; порядок между зависимыми событиями)
├── created_at           TIMESTAMP WITH TIME ZONE
└── updated_at           TIMESTAMP WITH TIME ZONE

registration_pending
├── id          INTEGER PK
├── email       VARCHAR(255) UNIQUE
├── code_hash   VARCHAR(1024)           (bcrypt-хеш 6-значного кода)
├── attempts    INTEGER                 (счётчик неверных попыток, лимит = 3)
├── expires_at  TIMESTAMP WITH TIME ZONE (now + 15 мин)
└── created_at  TIMESTAMP WITH TIME ZONE (используется для rate-limit: 60 с)
```

### Миграции Alembic

| Ревизия | Изменение |
|---|---|
| `0001` | Создание таблиц `role`, `person`, `task` |
| `0002` | UNIQUE на `person.email`; удаление индекса `ix_task_description` |
| `0003` | Добавление `firstname`, `lastname` в `person`; `crm_task_id` в `task` |
| `0004` | Сужение `task.title` до `VARCHAR(100)` |
| `0005` | Создание таблицы `registration_pending` |
| `0006` | Добавление `patronymic` (nullable) в `person` |
| `0007` | `TIMESTAMP WITH TIME ZONE` для `registered_at`, `expires_at`, `created_at`; `person.username` → `VARCHAR(255)` |
| `0008` | Создание таблицы `subtask`; FK → `task.id` с `ondelete CASCADE`; `UNIQUE(title, task_id)` |
| `0009` | Замена глобального `UNIQUE(task.title)` на `UNIQUE(task.title, task.owner_id)` |
| `0010` | Добавление `specification_path` (VARCHAR) и `other_file_paths` (Text) в `task` и `subtask` |
| `0011` | Конвертация `other_file_paths` из `Text` в `JSONB` (`ALTER COLUMN ... TYPE JSONB USING ...::jsonb`) |
| `0012` | Добавление недостающего `uq_person_email` (обнаружено `alembic check` — 0002 задумывался, но constraint не был применён); объединение `registration_pending.email` в один unique index вместо `constraint + дублирующий обычный индекс` |
| `0013` | GIN-индекс `pg_trgm` (`ix_task_title_trgm`) на `task.title` — обычный B-tree не ускоряет `ILIKE("%...%")` с ведущим `%`, поиск по названию задачи (`search_tasks()`) до этой миграции всегда делал Seq Scan |
| `0014` | `ON DELETE CASCADE` на `task.owner_id → person.id` — каскад работает на уровне БД: прямой SQL `DELETE FROM person` тоже удаляет задачи пользователя, а не только `session.delete(user)` через ORM |
| `0015` | `person.role_id` (one-to-many) → таблица-связка `user_role` (many-to-many, составной PK `(person_id, role_id)`) с backfill существующих назначений; `role.permissions` удалена (не используется — `require_permission()` заменён на `require_role()`, проверяющий `role.name`) |
| `0016` | Таблица `project` (локальное зеркало списка «Проект» CRM, `uq_project_crm_id`); `task.project_id` FK → `project.id` (nullable); таблица `crm_outbox` (durable-retry очередь CRM-синхронизации задач) |
| `0017` | `crm_outbox`: `task_id` → `aggregate_id` + новая `aggregate_type` (`"task"`/`"subtask"`), `shard`, `depends_on_event_id` (self-FK), `idempotency_key` (удалена миграцией 0022 — не использовалась, см. ниже); `task.crm_shard`, `task.sync_status`, `subtask.sync_status` — шардирование CRM-outbox через consistent hashing, распространение durable-retry на `Subtask` |
| `0018` | `crm_outbox.last_error` (`Text`, nullable) — причина последнего сбоя CRM-события, видна администратору в `/admin` |
| `0019` | Частичные уникальные индексы `ix_task_crm_task_id_unique`/`ix_subtask_crm_subtask_id_unique` (`WHERE crm_task_id/crm_subtask_id IS NOT NULL`) — защита от «усыновления» двумя локальными сущностями одной CRM-записи |
| `0020` | `crm_outbox.dispatched_at` (`TIMESTAMP WITH TIME ZONE`, nullable) — момент последней реальной постановки «застрявшей» (`attempts == 0`) строки в очередь `reconcile_pending_outbox`, не даёт переставлять её в очередь на каждом тике без прогресса |
| `0021` | Частичный индекс `ix_crm_outbox_depends_on_event_id` (`WHERE depends_on_event_id IS NOT NULL`) — self-FK без индекса заставлял `DELETE`/`NOT EXISTS`-проверку в `cleanup_done_outbox` делать полное сканирование таблицы |
| `0022` | Удаление `crm_outbox.idempotency_key` — колонка ни разу не читалась нигде в коде с момента появления в 0017; заявленная цель (дедупликация повторного выполнения одной Celery-задачи при redelivery после сбоя воркера) уже закрыта проверкой `status == 'done'` + атомарностью транзакции в начале `_process_outbox_row_async`, а не этим полем |

### Блокировки строк: FOR UPDATE и FOR NO KEY UPDATE

Обычная транзакция (`BEGIN` / `COMMIT`) без явной блокировки не защищает от двух классов гонок, которые в
этом проекте реальны при параллельных запросах к одной и той же `Task`/`Subtask`/`User`: **потерянное
обновление** (два конкурентных запроса читают строку до commit'а друг друга, и один UPDATE молча
перезатирает результат другого) и **осиротевшая вставка** (подзадача/файл успевает вставиться в узкое
окно между снимком состояния родителя и его удалением, после чего каскадно удаляется, минуя очистку
файлов/CRM). PostgreSQL решает оба класса явной блокировкой строки — `SELECT ... FOR UPDATE` в
SQLAlchemy — но в проекте сознательно используются ДВЕ разные степени этой блокировки, не одна.

**Ключевой факт PostgreSQL, на котором строится весь выбор ниже:** `INSERT` строки с `FOREIGN KEY` на
другую таблицу (например, `Subtask` с FK на `Task.id`) автоматически берёт на строку-родителя лёгкую
блокировку `FOR KEY SHARE` — даже если сам `INSERT` не оборачивается в `with_for_update()` явно, этого
требует сама ссылочная целостность. `FOR KEY SHARE` конфликтует с `FOR UPDATE`, но **не конфликтует** с
`FOR NO KEY UPDATE`. Отсюда — два разных инструмента с разным эффектом на конкурентную вставку
подзадачи:

| Что берёт код | SQLAlchemy | Что получает в SQL | Блокирует параллельный `INSERT` подзадачи (FK → эта строка)? |
|---|---|---|---|
| Защита поля, не связанного со вставкой детей | `with_for_update(key_share=True)` | `FOR NO KEY UPDATE` | **Нет** — можно одновременно обновлять задачу и создавать её подзадачи |
| Защита строки, которую собираются удалить, или снимка, который не должна испортить гонка со вставкой | `with_for_update()` (без аргументов) | `FOR UPDATE` | **Да** — конкурентный `INSERT` ждёт commit/rollback этой транзакции |

Полный список мест, где это применяется:

| Файл · функция | Строка(и) под блокировкой | Тип | Почему именно этот тип |
|---|---|---|---|
| `src/services/tasks.py::update_task` | `Task` (обновляемая задача) | `FOR NO KEY UPDATE` | Не должна блокировать параллельный `create_subtask` той же задачи — обновление `title`/`description`/`completed`/`project` не связано со вставкой детей. Конфликтует с `FOR UPDATE` из `delete_task` (ниже) — `UPDATE` ждёт удаления/отката вместо падения в `sqlalchemy.orm.exc.StaleDataError` (0 затронутых строк после конкурентного `DELETE`) |
| `src/services/tasks.py::delete_task`, `prepare_owner_tasks_deletion` | `Task` (удаляемая задача/все задачи владельца, `ORDER BY Task.id`) | `FOR UPDATE` | Здесь блокировка нужна НАМЕРЕННО сильнее: задача удаляется, значит и вставка новой подзадачи к ней должна быть исключена — иначе подзадача успела бы вставиться в окне между снимком `subtask_rows`/`crm_subtask_ids` и самим `DELETE`, после чего её каскадно удалил бы `ON DELETE CASCADE`, минуя очистку файлов на диске и `delete`-событие в CRM. Конкурентный `create_subtask` после разблокировки получает `IntegrityError` (нарушение FK — родителя уже нет) |
| `src/services/tasks.py::_prepare_task_deletion` | Все `Subtask` этой задачи (`subtask_rows`, `ORDER BY Subtask.id`) | `FOR UPDATE` | Симметрично блокировкам в `update_subtask`/`delete_subtask` ниже — без неё здесь можно прочитать `crm_subtask_id` подзадачи, которую в этот момент конкурентно удаляет/обновляет прямой `PATCH`/`DELETE /subtasks/{id}`. `ORDER BY` — дешёвая защита от дедлока на случай будущих изменений: сегодня дедлок структурно невозможен (эта блокировка берётся уже ПОСЛЕ `FOR UPDATE` на саму задачу, которая и так сериализует доступ к её подзадачам) |
| `src/services/subtasks.py::update_subtask` | `Subtask` (обновляемая подзадача) | `FOR UPDATE` | У `Subtask` нет собственных детей с FK на неё — различие NO KEY/обычный тут не играет роли для вставок, важно только заблокировать конкурентный `delete_task` родителя: без этого `UPDATE` мог бы попасть в уже каскадно удалённую строку (та же `StaleDataError`, что и в `update_task` выше) |
| `src/services/subtasks.py::delete_subtask` | `Subtask` (удаляемая подзадача) | `FOR UPDATE` | Сериализует с конкурентным `delete_task` родителя, который читает ту же подзадачу в свой снимок `subtask_rows`: без блокировки оба пути могли бы вызвать `crm.delete_subtask()` для одного и того же `crm_subtask_id` дважды. С блокировкой только одна транзакция реально видит строку и удаляет её в CRM, вторая получает `404` |
| `src/services/attachments.py` — `upload_specification`/`delete_specification`/`upload_other_files`/`delete_other_file` (4 места, параметризовано `AttachmentConfig.model`) | `Task` или `Subtask` — сущность, которой принадлежит файловый слот | `FOR NO KEY UPDATE` | Защищает `specification_path`/`other_file_paths` от потерянного обновления при двух параллельных файловых операциях — но, как и `update_task`, намеренно НЕ блокирует параллельный `create_subtask`: загрузка файла к задаче не должна мешать созданию её подзадач |
| `src/tasks/crm_outbox_tasks.py::_lock_entity` (вызывается из `_do_create_task`/`_do_create_subtask`, ПОСЛЕ CRM-вызова) | `Task` или `Subtask` (параметризовано моделью) | `FOR NO KEY UPDATE` + `populate_existing=True` | Записывает `crm_task_id`/`crm_subtask_id` по результату CRM-вызова. Конфликтует с `FOR UPDATE` из `delete_task`/`delete_subtask` веб-стороны — запись либо успевает раньше удаления, либо, если сущность удалена первой, `_lock_entity` возвращает `None`, и воркер запускает компенсирующее удаление только что созданной «сироты» в CRM (`_compensate_orphan`). `populate_existing=True` — объект мог быть загружен в сессию РАНЬШЕ, до CRM-вызова; без флага вернулась бы устаревшая версия из identity map, а не актуальная строка |
| `src/routers/users.py::delete_user` | `User` (удаляемый пользователь) | `FOR UPDATE` | Та же логика, что у `delete_task`, но на уровень выше: `INSERT` в `task` с FK на `person` берёт `FOR KEY SHARE` на строку пользователя — обычная блокировка здесь обязательно должна быть `FOR UPDATE`, иначе задача, которую пользователь создаёт параллельно со своим удалением администратором, успела бы вставиться в окне между снимком его задач (`prepare_owner_tasks_deletion`) и `DELETE FROM person` — и была бы каскадно удалена, минуя очистку файлов и CRM |

**Общее правило при добавлении новой блокировки в этот проект:** если строка, которую блокируете, —
родитель по FK для сущности, создаваемой в другом запросе (`Task` для `Subtask`, `User` для `Task`), и
вы НЕ хотите блокировать эту вставку — используйте `with_for_update(key_share=True)` (`FOR NO KEY
UPDATE`). Если строка удаляется, или вставка во время операции — это именно то, от чего нужно защититься
(задача удаляется → новая подзадача не должна успеть вставиться), — используйте `with_for_update()` без
аргументов (`FOR UPDATE`). Голый `FOR SHARE`/`FOR KEY SHARE` на стороне приложения в проекте не
применяется — оба нужны только на уровне автоматического поведения `INSERT`-а по FK, вручную их брать не
требуется.

---

## CRM «Руководитель»

Task Manager интегрирован с CRM-системой [«Руководитель»](https://rukovoditel.net/) (open-source PHP/MySQL).
Интеграция работает через REST API CRM и затрагивает управление задачами: создание, изменение, удаление
задач и подзадач, а также синхронизацию справочника «Проект».

Ни регистрация (`POST /auth/register/complete`), ни вход (`POST /auth/login`) к CRM не обращаются вообще
(см. [«Authentication Flow»](#authentication-flow) выше): единственная проверка на вход — совпадение
пароля с хешем в PostgreSQL.

### Синхронизация задач (полностью асинхронно, через Celery/Redis)

Веб-процесс **никогда** не вызывает CRM напрямую — только Celery-воркер. Каждая операция в одной транзакции с основным изменением вставляет строку `crm_outbox` (`status='pending'`), затем — уже после `db.commit()` — сразу ставит её в очередь Celery (`dispatch_outbox_row`, немедленный `apply_async`, не дожидаясь расписания). В типовом случае воркер простаивает и подхватывает задачу практически мгновенно, но HTTP-ответ пользователю не ждёт ни этого, ни тем более самого CRM-запроса — латентность CRM полностью вынесена из запроса. Если диспатч не успел выполниться (падение процесса) или Celery/Redis временно недоступны — строка остаётся `pending`, и её всё равно найдёт и повторно поставит в очередь фоновая Celery-задача `reconcile_pending_outbox`. Это устраняет риск, при котором CRM-запрос терялся бы навсегда при падении сервера между `db.commit()` и вызовом CRM.

`dispatch_outbox_row` сама по себе выносит `apply_async` в `asyncio.to_thread` (см. ниже), но без дополнительных настроек Celery поток, в который вынесен вызов, завис бы на дефолтном таймауте kombu (~4 с) плюс встроенные повторы публикации, если Redis временно недоступен, — пользователь ждал бы ответа, хотя задача уже сохранена и `commit` прошёл. `celery_app.conf` (`src/celery_app.py`) задаёт `broker_connection_timeout=2` (вместо дефолтных ~4 с — Redis обычно в той же сети) и `task_publish_retry_policy` (2 повтора публикации, backoff 0.2–0.5 с) — одна глобальная настройка, покрывающая и `dispatch_outbox_row`, и `sync_project_table.delay()` (`routers/admin.py`).

#### Статусы строки `crm_outbox`

Колонка `crm_outbox.status` (`VARCHAR(20)`) принимает четыре значения. Не путать со статусом самой сущности
`Task.sync_status`/`Subtask.sync_status` (`unsynced`/`pending`/`synced`/`failed`, см. таблицу ниже): `crm_outbox.status` —
состояние ОДНОГО события, `sync_status` — свёртка состояний всех событий сущности, видимая пользователю.

| Статус | Что означает | Кто и когда выставляет | Что происходит со строкой дальше | Влияние на порядок и `sync_status` сущности |
|---|---|---|---|---|
| `pending` | Событие ещё не доставлено в CRM. Это единственное начальное состояние (`default="pending"` модели `CrmOutbox`) и состояние «в работе»: статус меняется только в конце обработки, поэтому строка остаётся `pending` и пока воркер выполняет CRM-вызов. Бывает в четырёх ситуациях: (1) только что вставлена продюсером; (2) после неудачной попытки при `attempts < MAX_ATTEMPTS` (5) — ждёт паузу `_retry_delay_seconds`: 60 → 120 → 240 → 480 с, потолок 900 с, `last_error` заполнен; (3) ждёт, не тратя попытку: есть более старое незавершённое событие сущности (`_has_older_unfinished`), зависимость `depends_on_event_id` ещё не `done`, либо исчерпан лимит `acquire_slot`; (4) возвращена из `failed` действием «Повторить» (`attempts = 0`) или из `blocked` задачей `reconcile_blocked_outbox` | Продюсеры (`services/tasks.py`, `subtasks.py`, `attachments.py`) вставляют строку в одной транзакции с изменением; `_compensate_orphan` — компенсирующую `delete`; обработчик оставляет `pending` при сбое; `outbox_admin.retry` и `_reconcile_blocked_outbox_async` возвращают в `pending` | Ставится в очередь шарда немедленно (`dispatch_outbox_row`) или повторно `reconcile_pending_outbox` (раз в 60 с, строки старше 45 с, с учётом паузы после сбоя и `dispatched_at`). Из `pending` строка уходит в `done`, `failed` или `blocked` | Считается «незавершённой»: младшие события той же сущности не начинают попытку (`_has_older_unfinished`), а `_refresh_sync_status` не выставит `synced`, пока есть другая `pending`/`blocked` строка. `sync_status` сущности — `pending` (его при вставке выставляет продюсер) |
| `done` | Событие успешно применено в CRM — либо достигнута цель без реальной работы: сущности уже нет локально (`update`, `sync_files`, пропущенный `create` подзадачи, `create` задачи с компенсацией сироты), запись уже отсутствует в CRM (`CRMRecordNotFoundError` при `delete`). Терминальный статус | `_process_outbox_row_async` после успешного `handler`, одновременно `last_error = NULL` | Повторно не обрабатывается: `process_outbox_row` сразу выходит (`row.status == "done"`). Служит выполненной зависимостью для зависимых строк (`_dependency_status`, `reconcile_blocked_outbox`) и критерием «устаревшее» для `has_newer_done_sibling`. Через `CRM_OUTBOX_RETENTION_DAYS` (30) удаляется `cleanup_done_outbox`, если на строку не ссылается `depends_on_event_id` другой строки | Перестаёт блокировать младшие события. Запускает `_refresh_sync_status(failed=False)`: сущность — `synced`, если не осталось других `pending`/`blocked` событий, иначе `pending`. Это же восстанавливает статус после более раннего `failed` |
| `failed` | Попытки исчерпаны или повтор бессмыслен — нужно ручное вмешательство. Три причины: (1) `attempts >= MAX_ATTEMPTS` (5) после исключения обработчика; (2) `IntegrityError` при финальном commit (гонка на уникальном индексе `crm_task_id`/`crm_subtask_id`): `failed` сразу, т.к. повтор упал бы так же; (3) неизвестная пара `(aggregate_type, operation)` — попытка не тратится. Терминальный статус до ручного действия | `_process_outbox_row_async` (все три случая) | В `/admin` → «CRM outbox» виден `last_error`. Админ устраняет причину и нажимает «Повторить» (`outbox_admin.retry`): `pending`, `attempts = 0`, немедленный диспатч; для не-`delete` строк `sync_status` сущности `failed` → `pending`. Строка пропускается, если у сущности уже есть более новое `done` (`has_newer_done_sibling`). `cleanup_done_outbox` её никогда не удаляет | НЕ блокирует младшие события — `_has_older_unfinished` учитывает только `pending`/`blocked`, поэтому следующее событие сущности выполняется. Для причин (1) и (2) `_refresh_sync_status(failed=True)` ставит `sync_status = failed` безусловно; для причины (3) `sync_status` не пересчитывается. Зависимые строки при следующей попытке станут `blocked` |
| `blocked` | Событие не может выполниться, потому что событие-зависимость (`depends_on_event_id`) окончательно провалилось (`failed`). Попытки не тратились. Пока зависимость просто ожидает (`pending`), строка остаётся `pending`, а не `blocked` | `_process_outbox_row_async`: при обработке строки, у которой `dep_status == "failed"` (CRM не вызывается, `attempts` не растёт) | Единственный выход — `reconcile_blocked_outbox` (раз в 300 с): когда зависимость стала `done` (обычно после «Повторить» для неё), строка возвращается в `pending`, а в очередь её ставит `reconcile_pending_outbox`. «Повторить» на самой `blocked` строке не работает (берёт только `failed`). `cleanup_done_outbox` не удаляет | Считается «незавершённой»: удерживает младшие события сущности и не даёт выставить `synced`. `sync_status` при переходе в `blocked` НЕ пересчитывается — остаётся таким, каким его оставил провал зависимости (`failed`) или продюсер (`pending`) |

Диаграмма переходов (кто двигает строку):

| Переход | Условие | Кто |
|---|---|---|
| (вставка) → `pending` | изменение сущности + событие в одной транзакции | продюсеры в `services/*`, `_compensate_orphan` |
| `pending` → `pending` | сбой попытки, `attempts < 5`; ожидание старшего события/зависимости/лимита `acquire_slot` (без траты попытки) | `_process_outbox_row_async` |
| `pending` → `done` | `handler` завершился без исключения | `_process_outbox_row_async` |
| `pending` → `failed` | `attempts` достиг 5; `IntegrityError` на commit; неизвестная пара операции | `_process_outbox_row_async` |
| `pending` → `blocked` | зависимость в статусе `failed` | `_process_outbox_row_async` |
| `blocked` → `pending` | зависимость стала `done` | `reconcile_blocked_outbox` |
| `failed` → `pending` | ручное «Повторить» после устранения причины (если нет более нового `done`) | `outbox_admin.retry` |
| `done` → (удаление) | старше `CRM_OUTBOX_RETENTION_DAYS`, нет зависимых строк | `cleanup_done_outbox` |

Правило, общее для всех статусов: в `done` и `failed` строка переходит только вместе с пересчётом `sync_status` сущности в
той же транзакции (`_refresh_sync_status`), а `pending`/`blocked` — «незавершённые» состояния, по которым работают
`_has_older_unfinished` и `_other_unfinished_events_exist`. Статусы `failed` и `blocked` ожидают человека или другое событие,
а не таймер: автоматически из них строка не выйдет, кроме `blocked` → `pending` по готовности зависимости.

#### Назначение каждого Celery-воркера и Beat

«Celery-воркер» — не единая сущность: в проекте пять процессов-исполнителей (`celery-worker` + `celery-worker-shard-0..3`) с разными очередями, и один планировщик (`celery-beat`), который сам ничего не исполняет. Разделение задано именно `-Q`/`--pool` в `command` каждого сервиса ([src/docker-compose.yml](src/docker-compose.yml)), а не кодом самих задач — одна и та же функция `process_outbox_row` может быть поставлена в любую из очередей `crm_sync.shard_N`, куда именно — решает продюсер в рантайме (`apply_async(..., queue=...)`).

| Сервис | `command` | Что слушает | Что выполняет |
|---|---|---|---|
| `celery-worker` | `celery -A src.celery_app worker --loglevel=info` | Дефолтная очередь Celery `celery` (без `-Q` — используется встроенный дефолт), обычный пул (`prefork`, конкурентность по числу ядер CPU) | Всё, что НЕ `process_outbox_row`: `sync_project_table`, `reconcile_pending_outbox`, `reconcile_blocked_outbox`, `cleanup_done_outbox` — периодические задачи, которые ставит `celery-beat` |
| `celery-worker-shard-0`…`celery-worker-shard-3` | `celery -A src.celery_app worker --loglevel=info --pool=solo -Q crm_sync.shard_N` | Ровно одна очередь `crm_sync.shard_N`, `--pool=solo` (один процесс, без подпроцессов/потоков) | Только `process_outbox_row` — реальные CRM-вызовы (`create`/`update`/`delete`/`sync_files`) для задач и подзадач, закреплённых за этим шардом (`task.crm_shard`, sticky-присвоение через `id % N`) |
| `celery-beat` | `celery -A src.celery_app beat --loglevel=info` | Ничего — не воркер, а планировщик | Сам не вызывает CRM и не трогает БД напрямую: по расписанию (`beat_schedule` в `src/celery_app.py`) кладёт задачи в очередь `celery`, откуда их забирает `celery-worker` |

**Почему шардовые воркеры отделены от дефолтного и друг от друга.** `process_outbox_row` — единственная задача, для которой важен строгий порядок обработки: события одной и той же задачи/подзадачи должны выполняться в CRM в порядке создания (иначе `update` мог бы применить данные раньше `create`, или устаревший повтор — позже свежего события, см. «Порядок событий одной сущности — структурный запрет на обгон» ниже). Гарантия строгого порядка обеспечивается именно топологией — «один процесс на одну очередь шарда, `--pool=solo` (без параллелизма внутри самого процесса)», а не кодом задачи. Если бы `process_outbox_row` делил очередь и пул с `sync_project_table`/`reconcile_*` (как `celery-worker`, где `--pool=solo` не выставлен намеренно — эти задачи короткие, независимые друг от друга и не требуют строгого порядка, параллелизм тут только на пользу), два события одной задачи потенциально могли бы выполниться не по порядку.

**Почему `celery-beat` — не воркер.** У Beat нет ни `-Q`, ни `--pool` — он вообще не «worker», а отдельная роль `celery ... beat`: единственная задача процесса — читать `beat_schedule` и по таймеру публиковать сообщения в очередь `celery` (через тот же Redis-брокер). Поэтому в манифесте он ровно один экземпляр — второй `celery-beat` дублировал бы каждую периодическую задачу.

**Расписание `celery-beat`** (`src/celery_app.py::beat_schedule`):

| Задача | Периодичность | Куда попадает | Что делает |
|---|---|---|---|
| `sync_project_table` | `CRM_PROJECT_SYNC_INTERVAL_SECONDS` (по умолчанию 180 сек) | `celery-worker` | Обновляет локальную таблицу `project` из справочника «Проект» CRM |
| `reconcile_pending_outbox` | 60 сек | `celery-worker` (но найденные `pending`-строки `crm_outbox` она сама ставит обратно в `crm_sync.shard_N` — то есть их дальше подхватит уже нужный шардовый воркер) | Сеть безопасности outbox — находит зависшие `pending`-строки старше грейс-периода и переставляет их в очередь повторно |
| `reconcile_blocked_outbox` | 300 сек | `celery-worker` (аналогично — сама разблокированная строка идёт в `crm_sync.shard_N`) | Переводит `blocked`-строки обратно в `pending`, если событие-зависимость (`depends_on_event_id`) с тех пор стало `done` |
| `cleanup_done_outbox` | раз в сутки, 03:00 UTC (`crontab`) | `celery-worker` | Удаляет старые `done`-строки `crm_outbox` (подробности — абзац «Таблица `crm_outbox` не растёт бесконечно» ниже) |

То есть `dispatch_outbox_row` (мгновенный путь сразу после `db.commit()`) и `reconcile_pending_outbox`/`reconcile_blocked_outbox` (путь безопасности через Beat) — это ДВА разных способа поставить одну и ту же задачу `process_outbox_row` в один и тот же шардовый воркер: быстрый путь просто оптимизирует задержку в типовом случае, а Beat гарантирует, что событие не потеряется, даже если быстрый путь по какой-то причине не сработал.

#### Каждая функция, которую выполняют Celery-воркеры

В проекте ровно 5 задач, зарегистрированных в Celery (`@celery_app.task(...)`) — больше никаких `.delay()`/`.apply_async()` в коде нет. Первые четыре объявлены в `src/tasks/crm_outbox_tasks.py`, пятая — в `src/tasks/global_lists_tasks.py`.

| Функция | Очередь | Кто ставит в очередь и когда | Что делает |
|---|---|---|---|
| `process_outbox_row(outbox_id: int) -> None` | `crm_sync.shard_N` (динамически, по `row.shard`) | `dispatch_outbox_row()` — сразу после `db.commit()` в `create_task`/`update_task`/`delete_task`/`create_subtask`/`update_subtask`/`delete_subtask` и файловых функциях `attachments.py`; повторно — `reconcile_pending_outbox`/`reconcile_blocked_outbox`; вручную — действие «Повторить» в `/admin` → «CRM outbox» | Обрабатывает ровно одну строку `crm_outbox`: структурный запрет на обгон (`_has_older_unfinished`), ожидание зависимости (`depends_on_event_id`), token-bucket (`acquire_slot`), Redlock шарда, сам CRM-вызов (`create`/`update`/`delete`/`sync_files` через `TaskManager`/`SubtaskManager` — для `create` идентичность через точный поиск по «Local ID», см. «Retry шардирован...» ниже), пересчёт `sync_status` (`_refresh_sync_status`) |
| `reconcile_pending_outbox() -> None` | `celery` (дефолтная) | `celery-beat`, раз в 60 сек | Сеть безопасности outbox: находит (не более 500 строк за тик — `_RECONCILE_BATCH_LIMIT`, чтобы не читать в память весь backlog разом) `pending`-строки `crm_outbox` старше грейс-периода (45 сек) и переставляет их в очередь `process_outbox_row` (в очередь их собственного шарда). Строка с `attempts > 0` берётся по истечении экспоненциальной паузы после последней неудачной попытки; строка без попыток (`attempts == 0` — застряла на исчерпанном token-bucket, более старом незавершённом событии сущности или ещё не готовой зависимости, реальной попытки CRM-вызова ещё не было) переставляется в очередь не чаще раза в 5 минут (`dispatched_at`) — иначе такая строка ставилась бы в очередь Celery/Redis заново на КАЖДОМ тике без какого-либо прогресса |
| `reconcile_blocked_outbox() -> None` | `celery` | `celery-beat`, раз в 300 сек | Переводит `blocked`-строки обратно в `pending`, если событие-зависимость (`depends_on_event_id`) с тех пор стало `done` — дальше её подхватит `reconcile_pending_outbox` |
| `cleanup_done_outbox() -> int` | `celery` | `celery-beat`, раз в сутки (`crontab(hour=3, minute=0)`, 03:00 UTC) | Удаляет старые `done`-строки `crm_outbox` (старше `CRM_OUTBOX_RETENTION_DAYS`, по умолчанию 30 дней), батчами по 1000, с защитой по self-FK `depends_on_event_id` (строка, на которую ещё ссылается другая неудалённая, не трогается); `failed`/`blocked`/`pending` не трогает никогда. Возвращает число удалённых строк — правда, как и у остальных четырёх задач, этот результат никто не читает (см. «Celery result backend» ниже) |
| `sync_project_table() -> None` | `celery` | `celery-beat`, раз в `CRM_PROJECT_SYNC_INTERVAL_SECONDS` (по умолчанию 180 сек); вручную — `POST /admin/crm-options/refresh` (роль `admin`) | Забирает список «Проект» (`list_id=11`) из CRM, делает upsert в локальную таблицу `project` (`INSERT ... ON CONFLICT DO UPDATE` по `crm_id` — подхватывает переименование опции в CRM для всех уже ссылающихся на неё задач); опции, пропавшие из CRM, помечает `is_active=false`, а не удаляет — удаление сломало бы FK у `task.project_id` |

**Пример, почему `dispatched_at` нужен именно для `attempts == 0`-строк.** CRM недоступна 20 минут. Строка `outbox_id=777` создана в 10:00:00, застряла ДО первой попытки CRM-вызова (`attempts=0` — например, ждёт `_has_older_unfinished`). Без `dispatched_at` каждый тик `reconcile` (10:01, 10:02, …, 10:20) видел бы её как «`pending`, старше грейс-периода» и переставлял в очередь заново — **~20 копий** одной и той же строки за 20 минут (без вреда для корректности — `process_outbox_row` идемпотентен, — но с лишней нагрузкой на Redis/БД). С `dispatched_at` + cooldown 300 с строка переставляется в 10:01, затем не раньше 10:06, 10:11, 10:16 — **4 копии** вместо ~20.

Необработанное исключение в любой из этих пяти задач логируется одним общим обработчиком, подписанным на
сигнал Celery `task_failure` (`src/celery_app.py::_log_task_failure`) — имя задачи, `task_id` и трейсбек
через `logger.error(...)` под именем логгера `src.celery_app`, не зависящим от служебного шума самой
библиотеки Celery. Обработчик покрывает сбои, которые иначе код проекта не логировал бы вообще, — например,
недоступность CRM внутри `sync_project_table` или сбой `acquire_slot()`/`shard_lock()`/поиска зависимости
внутри `process_outbox_row` ДО входа в его собственный защищённый `try/except` (без обработчика они попали бы
только в общий лог самой Celery — `celery.app.trace`, — зависящий от флага `--loglevel` в манифесте деплоя).
Единственное исключение — сбой самого CRM-вызова внутри `process_outbox_row`: он перехватывается отдельным
`try/except` и пишется в `CrmOutbox.last_error`/`logger.warning` (см. выше), поэтому наружу не
пробрасывается и сигнал `task_failure` на него не срабатывает — двойного логирования нет.

##### Внутренние функции (`src/tasks/crm_outbox_tasks.py`)

Ни одна из них не зарегистрирована в Celery отдельно (`@celery_app.task` есть только у пяти функций из
таблицы выше) — все физически выполняются ВНУТРИ `process_outbox_row`/`reconcile_pending_outbox`/
`reconcile_blocked_outbox`/`cleanup_done_outbox`, тем же процессом-воркером, просто без собственной
очереди и расписания.

**Диспетчеризация и блокировки:**

| Функция | Вызывается из | Что делает |
|---|---|---|
| `dispatch_outbox_row(row)` | `create_task`/`update_task`/`delete_task`/`create_subtask`/`update_subtask`/`delete_subtask` (`services/tasks.py`/`subtasks.py`), файловые функции `attachments.py` — сразу после `db.commit()` | Ставит только что закоммиченную строку в очередь её шарда немедленно (`apply_async` в `asyncio.to_thread` — синхронный сетевой вызов kombu/redis-py иначе блокирует единственный event loop веб-процесса на всё время TCP-таймаута к недоступному Redis). Чисто оптимизация задержки, не механизм надёжности: исключение здесь перехватывается и проглатывается (уже залогировано `logger.warning`) — строка всё равно `pending` в БД и её найдёт `reconcile_pending_outbox` на следующем тике, а необработанное исключение здесь ложно превратило бы уже состоявшееся сохранение задачи в HTTP 500 для пользователя. |
| `_lock_entity(db, model, entity_id)` | `_do_create_task`/`_do_create_subtask`, ПОСЛЕ CRM-вызова | `SELECT ... FOR NO KEY UPDATE` строки `Task`/`Subtask` (`key_share=True` — совместимо с `FOR KEY SHARE`, которым веб-сторона блокирует строку при удалении: конфликтует ровно с `FOR UPDATE`/`FOR NO KEY UPDATE`, поэтому запись `crm_task_id`/`crm_subtask_id` и удаление сущности гарантированно сериализуются, а не гонятся). `populate_existing=True` — объект мог быть загружен в сессию РАНЬШЕ, до CRM-вызова; без этого флага SQLAlchemy вернула бы устаревшую версию из identity map, а не свежую строку. Возвращает `None`, если сущность за это время удалена конкурентно. |
| `_compensate_orphan(db, row, aggregate_type, crm_id)` | `_do_create_task`/`_do_create_subtask`, когда `_lock_entity` вернула `None` **и** запись создана именно этой попыткой (`created_here=True`) | Локальная задача/подзадача исчезла, пока воркер создавал её в CRM — новая CRM-запись стала сиротой, о которой веб-сторона не знает (её `crm_id` ещё не был записан локально на момент удаления, поэтому обычное `delete`-событие не поставилось). Пытается удалить её сразу же; `CRMRecordNotFoundError` — уже неактуально, ничего не делает; при любом другом сбое ставит обычную `delete`-строку в `crm_outbox` (в той же транзакции) — дальше её ведёт штатный retry/reconcile, как любое другое событие. |

**Защита от гонок и повторной обработки не по порядку:**

| Функция | Что делает |
|---|---|
| `_has_older_unfinished(db, row)` | Есть ли у ТОЙ ЖЕ сущности более СТАРОЕ (`id < row.id`) ещё не завершённое (`pending`/`blocked`) событие. Структурный запрет на обгон — если да, `process_outbox_row` даже не начинает попытку (не тратит `attempts`), просто откладывается до следующего тика `reconcile_pending_outbox`. Без неё устаревшее событие могло бы довыполниться ПОСЛЕ более нового и перезаписать в CRM уже отправленные свежие данные — не только испортить `sync_status`, а реально исказить данные в самой CRM. |
| `_other_unfinished_events_exist(db, row)` | Есть ли у той же сущности ДРУГАЯ (`id != row.id`) строка `pending`/`blocked` — используется `_refresh_sync_status`, чтобы не пометить сущность `synced`, пока остаются незавершённые соседние события (иначе одно из двух параллельных `update` завершилось бы раньше и преждевременно «закрыло» статус). |
| `has_newer_done_sibling(db, row)` | Есть ли у сущности более новая строка со статусом именно `done` (успешно синхронизировалась). Используется ТОЛЬКО действием «Повторить» в sqladmin (`src/admin/outbox_admin.py`) — не переставлять в очередь `failed`-строку, которую перекрыло более новое успешное событие; воркер её не использует. |
| `_dependency_status(db, depends_on_event_id)` | Статус строки-зависимости по её `id` (`None`, если такой строки уже нет). Используется `process_outbox_row`, чтобы не начинать обработку, пока `depends_on_event_id` не станет `done` (межагрегатная зависимость create подзадачи от create задачи, или внутриагрегатная sync_files от create того же агрегата). |

**Пересчёт и явная запись `sync_status`:**

| Функция | Что делает |
|---|---|
| `_refresh_sync_status(db, row, *, failed)` | Единая точка пересчёта `Task.sync_status`/`Subtask.sync_status` при любом терминальном исходе (`row.status` стал `done` или `failed`) для ЛЮБОЙ операции. `failed=True` → `failed` безусловно (благодаря `_has_older_unfinished` более младшее событие физически не могло выполниться раньше и «подпортить» этот исход). `failed=False` → пересчитывается через `_other_unfinished_events_exist`: остались незавершённые соседи — `pending`, иначе `synced` (этим же путём статус сам восстанавливается после более раннего `failed` соседа). Сущности уже может не быть (удалена) — тогда ничего не делает. |
| `set_aggregate_sync_status(db, row, status, *, only_from=None)` | Прямая запись заданного статуса (не пересчёт). Единственный вызывающий — действие «Повторить» в sqladmin: сбрасывает `failed` обратно в `pending` перед повторной постановкой строки в очередь. |
| `_retry_delay_seconds(attempts)` | Сколько секунд строка должна отлежаться в `pending` после `N` неудачных попыток, прежде чем `reconcile_pending_outbox` возьмёт её снова: `0` попыток — без паузы, иначе `min(60·2^(attempts-1), 900)` — экспоненциально, потолок 15 минут. |
| `_format_error(exc)` | Текст для `CrmOutbox.last_error`: `"ТипИсключения: сообщение"`, обрезано до `LAST_ERROR_MAX_LEN` — ответ CRM может содержать фрагменты данных задачи, а само сообщение исключения — быть произвольной длины. |

**Обработчики конкретной операции** (`_HANDLERS_BY_AGGREGATE[aggregate_type][operation]` — таблица диспетчеризации, по которой `process_outbox_row` выбирает нужный на основе `row.aggregate_type`/`row.operation`; каждая пара `_do_*_task`/`_do_*_subtask` симметрична):

| Функция | Что делает |
|---|---|
| `_do_create_task` / `_do_create_subtask` | Идемпотентный retry: сначала `find_task(row.aggregate_id)`/`find_subtask(row.aggregate_id)` — точный поиск по «Local ID» (см. «Retry шардирован...» ниже), затем, если не найдено, — реальный `create_task`/`create_subtask` (с `local_id=row.aggregate_id`). `_do_create_subtask` дополнительно ждёт, пока родительская задача не получит `crm_task_id` (`depends_on_event_id`), и читает его из АКТУАЛЬНОЙ записи `Task`, не из своего payload. После CRM-вызова — `_lock_entity`, явная проверка владельца (`conflicting_owner`) перед присваиванием `crm_task_id`/`crm_subtask_id` — дешёвая защита от ошибок конфигурации, не основной путь. |
| `_do_sync_files_task` / `_do_sync_files_subtask` | Отправляет файл(ы) задачи/подзадачи в CRM «по состоянию», не «по снимку»: payload несёт только флаги `sync_specification`/`sync_other_files` — какие слоты затронуты, а сами `specification_path`/`other_file_paths` читаются заново из Task/Subtask в момент обработки, а не из снимка на момент вставки строки. `crm_task_id`/`crm_subtask_id` читается из payload, если уже был известен на момент вставки, иначе — из той же, только что загруженной записи (родитель к этому моменту гарантированно `done` благодаря `depends_on_event_id`). Сущности уже может не быть (`delete_task`/`delete_subtask` — синхронный CASCADE на веб-стороне до того, как эта строка дойдёт до Celery) — тогда обработчик тихо завершается `done` без обращения к CRM. Остаточный `FileNotFoundError` (микро-окно между чтением из БД и фактическим `read_bytes()`) — обычный сбой/retry. |
| `_do_update_task` / `_do_update_subtask` | Тонкая обёртка: передаёт заполненные поля payload (`title`/`description`/`completed`/`project`) в `TaskManager.update_task`/`SubtaskManager.update_subtask` по уже известному `crm_task_id`/`crm_subtask_id` из payload. Если `crm_*_id` в payload `None` (правка до выполнения `create`; строка зависит от `create` через `depends_on_event_id`), id читается из живой сущности. |
| `_do_delete_task` / `_do_delete_subtask` | Удаляет запись(и) в CRM по `crm_task_id`/`crm_subtask_id`/`crm_subtask_ids` из payload; `_do_delete_task` — каскадно, вместе со всеми подзадачами одной строкой. `CRMRecordNotFoundError` перехватывается отдельно для каждого id — «уже удалено» здесь означает достигнутую цель, а не сбой (иначе повтор после частичного успеха бесконечно наращивал бы `attempts` без реальной причины). |

##### Внутренняя функция `src/tasks/global_lists_tasks.py`

`sync_project_table` — тонкая обёртка (`run_celery_task` + асинхронная реализация `_sync_project_table_async`, тот же паттерн, что у остальных четырёх зарегистрированных задач); вся содержательная логика — в единственном хелпере:

| Функция | Что делает |
|---|---|
| `_upsert_project_rows(db, choices)` | Upsert локальной таблицы `project` по `crm_id` из ответа CRM (`INSERT ... ON CONFLICT DO UPDATE` — переименование опции в CRM подхватывается для ВСЕХ уже ссылающихся на неё задач автоматически, без правки задач вручную). Опции, пропавшие из ответа CRM (`choices`), переводятся в `is_active=False`, а не удаляются — удаление сломало бы FK у `Task.project_id` уже выбравших эту опцию задач. |

| Событие | CRM-операция | Что видит клиент |
|---|---|---|
| Создание задачи | `action=insert`, entity_id=29 | Задача сохраняется в БД, outbox-строка `create` поставлена в очередь — ответ `201 Created` содержит только `sync_status: "pending"`, CRM-id не отдаётся |
| Обновление задачи | `action=update`, `update_by_field={id: crm_task_id}` | Задача обновляется в БД; если уже была синхронизирована — outbox-строка `update` поставлена в очередь, иначе синхронизировать нечего |
| Удаление задачи | `action=delete`, `delete_by_field={id: crm_task_id}` | Задача удаляется из БД; аналогично — outbox-строка ставится, только если было что синхронизировать |
| Создание подзадачи | `action=insert`, entity_id=30 (только если `task.crm_task_id` не NULL — иначе `create` подзадачи ждёт `create` родителя через `depends_on_event_id`) | Подзадача сохраняется в БД, outbox-строка `create` поставлена в очередь |
| Обновление подзадачи | `action=update`, `update_by_field={id: crm_subtask_id}` (только если `crm_subtask_id` не NULL) | Аналогично обновлению задачи |
| Удаление подзадачи | `action=delete`, `delete_by_field={id: crm_subtask_id}` (только если `crm_subtask_id` не NULL) | Аналогично удалению задачи |

Клиент не видит результат CRM-вызова сразу (латентность CRM вынесена из HTTP-ответа в фоновый Celery-воркер), а
позже — только через поле **`sync_status`** в `TaskResponse`/`SubtaskResponse`. `crm_task_id`/`crm_subtask_id`,
шард, число попыток и причина сбоя обычным пользователям не отдаются. Статус показывается бейджем рядом со счётчиком
подзадач на `task-board` и в строке подзадачи на `subtask-board`; пока есть записи в состоянии `pending`, список сам
перечитывается раз в 3 секунды (не более 20 раз подряд):

| `sync_status` | Вид для пользователя | Смысл |
|---|---|---|
| `synced` | зелёная галочка `✓` без плашки (текст «Синхронизировано с CRM» — в `title` и `aria-label`) | всё отправлено |
| `pending` | жёлтая плашка со спиннером «В обработке» (`role="status"`) | данные передаются в CRM; после исчерпания опроса — «Обработка затянулась» |
| `unsynced` | серая плашка «Ожидает отправки» | синхронизация ещё не начиналась |
| `failed` | красная плашка «Не удалось отправить», в подсказке: «Данные сохранены. Администратор увидит проблему и повторит отправку» | 5 попыток исчерпаны |

Администратор видит CRM-id и историю попыток на странице `/admin/crm-sync` (`GET /admin/crm-sync-status/tasks`/`subtasks`)
и в sqladmin (раздел «CRM outbox»: статус, попытки, шард, зависимость, причина сбоя `last_error`); упавшее событие
возвращается в очередь действием «Повторить» после устранения причины. Повторы идут с экспоненциальной паузой
(60 → 120 → 240 → 480 с, потолок 900 с). «Повторить» пропускает (и явно помечает как устаревшие) `failed`-строки,
для которых у той же задачи/подзадачи уже есть более новое успешно синхронизированное событие — иначе повтор
применил бы устаревшие данные поверх уже отправленных в CRM свежих.

**Порядок событий одной сущности — структурный запрет на обгон, а не проверка постфактум.**
Топология деплоя (один `celery-worker-shard-N` процесс на шард, `--pool=solo`) не даёт двум ПРОЦЕССАМ
читать одну очередь одновременно, но не гарантирует, что ОДИН и тот же воркер обработает события
строго по `id`: у строки с растущей паузой перед повтором (60 → 120 → 240 → 480 с) есть окно, в которое
более новое событие той же задачи/подзадачи, поставленное в очередь сразу после вставки, успевает
пройти вперёд и завершиться раньше. `_has_older_unfinished` (`src/tasks/crm_outbox_tasks.py`) закрывает
это структурно: воркер не начинает попытку, пока у той же сущности есть более старое ещё не
завершённое (`pending`/`blocked`) событие — строка просто откладывается без траты попытки, следующий
тик `reconcile_pending_outbox` найдёт её снова.

Раз обгон структурно невозможен, `sync_status` (`_refresh_sync_status`) не «записывается» условно, а
ПЕРЕСЧИТЫВАЕТСЯ из текущего состояния событий сущности при каждом терминальном исходе — `failed`
безусловно (младшее событие физически не могло выполниться раньше и «испортить» его нечем),
`synced`/`pending` — по критерию «остались ли ещё незавершённые соседи» (`_other_unfinished_events_exist`).
Единая точка пересчёта работает для любой операции (`create`/`update`/`delete`/`sync_files`); `create`
`sync_status` сам не выставляет.

Отдельно от структурного запрета в действии «Повторить» sqladmin (`src/admin/outbox_admin.py`) работает
проверка `has_newer_done_sibling` — структурный запрет действует, пока строка сама ещё не завершилась;
администратор же вручную возвращает в очередь уже терминальную (`failed`) строку спустя произвольное
время, когда порядок никем больше не контролируется, поэтому там своя, независимая проверка на
устаревание остаётся нужна.

Retry шардирован по `id % N` (`task.crm_shard`, sticky-присвоение) — все события одной задачи и её подзадач гарантированно обрабатываются в одной очереди и в порядке создания.

**Идемпотентность retry `create` — точный поиск по служебному полю «Local ID».** `TaskManager.find_task`/`SubtaskManager.find_subtask` ищут уже созданную запись по ЕДИНСТВЕННОМУ точному фильтру — служебному полю CRM `field_{FIELD_LOCAL_ID}` (`CRM_TASK_FIELD_LOCAL_ID`/`CRM_SUBTASK_FIELD_LOCAL_ID`), в которое `create_task`/`create_subtask` пишут `local_id` — локальный `Task.id`/`Subtask.id`. `_do_create_task`/`_do_create_subtask` вызывают `find_task(row.aggregate_id)`/`find_subtask(row.aggregate_id)` напрямую.

Служебные поля в CRM: `entity_id=29` «Задачи» → `field_332`, `entity_id=30` «Подзадачи» → `field_333`. `local_id` — реальный первичный ключ PostgreSQL, уникален по определению, поэтому `find_task(local_id=X)` не может вернуть запись, принадлежащую ДРУГОЙ локальной сущности, ни при каких `title`/`description`/`creator_email`/таймингах удаления; двум разным `local_id` физически нечего делить, в том числе при одновременных `create` на разных шардах. `creator_email` сохраняется в CRM (человекочитаемый аудит), но в поиске не участвует. Явная проверка владельца перед присваиванием `crm_task_id`/`crm_subtask_id` в `_do_create_task`/`_do_create_subtask` и частичный уникальный индекс на `crm_task_id`/`crm_subtask_id` в БД (миграция 0019) — дешёвая защита от ошибок конфигурации (например, `CRM_TASK_FIELD_LOCAL_ID` при смене инстанса CRM по ошибке укажет не на то поле), а не основной путь.

**Актуальность `sync_files` — «по состоянию», не «по снимку».** `_do_sync_files_task`/`_do_sync_files_subtask` не читают `specification_path`/`other_file_paths` из payload — только флаги `sync_specification`/`sync_other_files` («какой слот затронут этим событием»); сами значения читаются заново из `Task`/`Subtask` в момент обработки строки. `attachments.py::_enqueue_sync_files` и внутренний payload `create_task`/`create_subtask` вставляют ТОЛЬКО эти флаги.

Какую бы `sync_files`-строку ни обрабатывал воркер, `task.specification_path`/`task.other_file_paths` в момент обработки — это то, что реально лежит на диске: запись файла на диск предшествует `commit`, поэтому путь, на который ссылается свежепрочитанная запись, физически существует и остаётся на месте минимум до следующего `commit`, который его заменит. Поэтому устаревание пути не требует специальной проверки «есть ли более новое событие» (`upload_specification`/`delete_specification`/`delete_other_file` удаляют старый файл с диска сразу после `commit`, не дожидаясь обработки строки в Celery); остаточное микро-окно между чтением из БД и фактическим чтением файла (`FileNotFoundError`) — обычный сбой с retry. Отсутствие сущности проверяется всегда (`delete_task`/`delete_subtask` — синхронный CASCADE на веб-стороне): событие тихо завершается `done` без обращения к CRM.

Если у сущности ещё нет `crm_id` (её `create` не выполнен) — файловая функция (`upload_specification`/`delete_specification`/`upload_other_files`/`delete_other_file`) ставит `sync_files` через `_enqueue_sync_files_pending_create` (`src/services/attachments.py`): та же строка, что и при известном `crm_id`, но зависимая от `create`-события ТОГО ЖЕ агрегата через `depends_on_event_id` (ровно так же, как внутриагрегатная строка в `create_task`/`create_subtask`), с `payload[crm_id_payload_key] = None`. Вызывается в ветке `else` при `crm_id is None`; актуальные `crm_id`/пути воркер читает «по состоянию», а `_has_older_unfinished`/`depends_on_event_id` гарантируют порядок и статус `blocked`, если `create` в итоге провалится окончательно. Если `create`-события у сущности нет вовсе (штатно не бывает), функция пишет ошибку в лог и строку не ставит.

Таблица `crm_outbox` не растёт бесконечно: Celery Beat-задача `cleanup_done_outbox` раз в сутки (03:00 UTC)
удаляет обработанные (`done`) строки старше `CRM_OUTBOX_RETENTION_DAYS` дней (по умолчанию 30); `failed`/`blocked`/`pending`
не трогаются никогда, а строка, на которую ещё ссылается `depends_on_event_id` другой, ещё не удалённой строки, не удаляется,
пока эта ссылка не исчезнет.

### Таймауты HTTP-запросов к CRM и большие файлы

Файлы ТЗ и «иных документов» передаются в CRM (`sync_files`) закодированными в base64
одним JSON-телом (`_file_to_crm`, `src/crm/client.py`) — при `MAX_FILE_SIZE=10` МБ
(`src/utils/file_utils.py`) это до ~13 МБ на файл, до ~133 МБ, если в одной операции
уходит сразу весь лимит «иных документов» (`MAX_OTHER_FILES=10`).

Общий HTTP-клиент к CRM (`_get_shared_http_client`, `src/crm/client.py`) использует
`httpx.Timeout(connect=30.0, write=120.0, read=120.0, pool=30.0)` — **не** один общий
таймаут на весь запрос: httpx разворачивает `timeout=<float>` в четыре независимых
бюджета (connect/write/read/pool), и каждый применяется к своей фазе отдельно. Бюджеты
`write`/`read` (по 120 с) рассчитаны на крупные файлы: при превышении запрос падает с
`httpx.TimeoutException` ("CRM request timed out" в `crm_outbox.last_error`), `sync_status`
задачи остаётся `pending`, пока durable-retry (см. выше) не исчерпает 5 попыток. `connect`/`pool`
равны 30 с: быстрый TCP/TLS-хендшейк, а воркер обрабатывает CRM-вызовы строго
последовательно (`--pool=solo`), конкуренции за пул соединений внутри процесса нет.

Redlock-лок шарда (`shard_lock`, `src/tasks/crm_shard_lock.py`) держится на время
всего вызова CRM и рассчитан на тот же худший сценарий: `_LOCK_TIMEOUT_SECONDS = 300` —
запас (~30 с на чтение файла с диска, сериализацию JSON, запись в БД) поверх потолка
`connect(<30) + write(<120) + read(<120) = 270` секунд честной последовательной
обработки. Эти два числа связаны: изменение таймаутов `httpx`-клиента требует
пересчитать `_LOCK_TIMEOUT_SECONDS` — иначе лок истечёт раньше, чем закончится ещё
идущий легитимный (не упавший по таймауту) CRM-вызов, и его сможет перехватить другой
процесс.

### Поле «Проект»

Задачи (не подзадачи) могут ссылаться на опцию глобального справочника CRM «Проект» (`list_id=11`, поле `field_327` сущности «Задачи»). Локальная таблица `project` — зеркало этого списка, наполняется периодической Celery-задачей `sync_project_table` (раз в `CRM_PROJECT_SYNC_INTERVAL_SECONDS`, по умолчанию 180 сек) — веб-процесс не ходит в CRM за этим списком на каждый запрос. Ручной триггер синхронизации без ожидания расписания: `POST /admin/crm-options/refresh` (только роль `admin`).

**`null` и `""` для `project` в `PATCH /tasks/{id}` неразличимы для обоих получателей — БД и CRM.** И `null`, и `""` одинаково резолвятся в `_resolve_project` как «очистить» (`project_id = None`). В `TaskManager.update_task` же `project=None` означает «не трогать поле» (`if project is not None: ...`), а `project=""` — «очистить», поэтому `update_data.pop("project") or ""` нормализует значение ДО формирования payload. Штатный UI (`task-detail.js`) при сбросе `<select>` шлёт `""`; прямой вызов API с буквальным `null` ведёт себя так же.

### Валидация PATCH: null в NOT NULL-полях

`TaskUpdate.title`/`description`/`completed` типизированы как `Optional[...] = None` — это нужно, чтобы `exclude_unset=True` отличал «поле не передано» (частичный `PATCH`) от «поле передано». Но сам тип схемы не отличает «не передано» от «передано со значением `null`» — оба дают `self.title is None`. Без отдельной проверки `PATCH {"title": null}` проходил бы валидацию Pydantic, доходил до `setattr(db_task, "title", None)` и `db.commit()`, где PostgreSQL отвергал `UPDATE` ограничением `NOT NULL` (sqlstate `23502`) — а единственный обработчик `except IntegrityError` в `update_task` (не различающий причину) превращал это в `409 "Task with title '...' already exists"`, хотя дубля `title` вообще не было.

`@model_validator(mode="after")` в `TaskUpdate`/`SubtaskUpdate` проверяет не само значение поля, а `self.model_fields_set` — множество полей, которые клиент передал ЯВНО (Pydantic заполняет его независимо от того, что именно было передано, включая `null`):

```python
for name in ("title", "description", "completed"):
    if name in self.model_fields_set and getattr(self, name) is None:
        raise ValueError(f"{name}: null не допускается...")
```

Это различает два случая, которые иначе неотличимы:

| Запрос | `"title" in model_fields_set` | `self.title` | Поведение |
|---|---|---|---|
| `{"description": "x"}` (`title` не упомянут) | `False` | `None` | не трогать `title` — штатный частичный `PATCH` |
| `{"title": null, "description": "x"}` | `True` | `None` | `422` — явная попытка обнулить NOT NULL-поле |

`ValueError` внутри `model_validator` FastAPI сам превращает в `422` ещё на этапе валидации тела запроса — до `update_task`, до БД. `project` в этот список не входит: `null` для него — легитимная явная очистка (см. выше), а не ошибка.

### Email создателя

Задача/подзадача при создании отправляет в CRM email пользователя, который её создал
(`field_328`/`field_329`). Email берётся сервером из `current_user` (`user.email`) в
`src/services/tasks.py::create_task`/`src/services/subtasks.py::create_subtask` — тело запроса
клиента (`TaskCreate`/`SubtaskCreate`) email вообще не содержит, поэтому подделать его через
DevTools нельзя. Поле заполняется только при создании — `update_task`/`update_subtask` его не
трогают (создатель записи не меняется при редактировании). В поиске `find_task`/`find_subtask`
(идемпотентность retry `create`) поле не участвует — это делает точный ключ «Local ID» (см. ниже);
`creator_email` — только сохраняемое значение для человекочитаемого аудита в самой CRM.

### Local ID

Служебное поле в обеих сущностях (`field_332` у «Задачи», `field_333` у «Подзадачи»), хранящее
локальный `Task.id`/`Subtask.id` из PostgreSQL. Заполняется только при создании — тот же принцип,
что и у email создателя выше. Единственное назначение — идемпотентность retry `create`:
`TaskManager.find_task`/`SubtaskManager.find_subtask` ищут по нему точным совпадением
(`condition: "include"`, которое для API CRM «Руководитель» означает точное совпадение, не
LIKE). `local_id` уникален по определению, поэтому `find_task(local_id=X)` физически не может
вернуть запись другой локальной сущности («усыновление» чужой CRM-записи структурно исключено).
Отдельного раздела в UI/API для этого поля нет — оно
не отдаётся ни в одном ответе приложения, только используется внутри `_do_create_task`/
`_do_create_subtask`.

**Backfill Local ID для уже существующих CRM-записей.** CRM-записи, созданные до появления поля Local ID,
этого поля не имеют — если `create`-событие такой записи ещё `pending`/`failed` (CRM-вставка уже прошла,
а локальный `crm_task_id`/`crm_subtask_id` — ещё нет), повторная попытка не найдёт существующую запись по
Local ID и создаст в CRM дубликат. `scripts/backfill_crm_local_id.py` (dry-run по умолчанию, флаг `--apply` —
реальная запись) безусловно дозаписывает Local ID на все записи, для которых `crm_task_id`/
`crm_subtask_id` уже известен локально — закрывает подавляющее большинство реальных данных. Саму узкую
гонку (CRM-запись существует, а локальный `crm_*_id` ещё не записан) скрипт не видит — безопасный
способ найти именно такую запись потребовал бы эвристики по `title`+`description`; перед продакшен-деплоем стоит вручную проверить в sqladmin (раздел «CRM outbox»,
поиск по `operation`/`status`) строки `operation='create'` в статусе `pending`/`failed` — их количество
мало и конечно.

### Сущности CRM

`entity_id` и номера полей ниже — это **дефолты** переменных окружения (`src/crm/crm_config.py`), а не константы в коде: генерируются внутри конкретной инсталляции CRM и могут отличаться на другом инстансе (production, другой клиент) — тогда меняется только `.dev.env`/`.env`, без правок кода. Значения ниже совпадают с demo-инстансом, на котором разрабатывался проект.

| Сущность | entity_id | Поля |
|---|---|---|
| Задачи | 29 | `field_317` — название, `field_318` — описание, `field_319` — статус (чекбокс: `"true"` / `"false"`), `field_320` — ТЗ (файл), `field_321` — иные документы (файлы), `field_327` — «Проект» (выпадающий список, ссылка на глобальный справочник `list_id=11`), `field_328` — email создателя (заполняется только при создании), `field_332` — Local ID (см. выше, заполняется только при создании) |
| Подзадачи | 30 | `field_322` — название, `field_323` — описание, `field_324` — статус (чекбокс: `"true"` / `"false"`), `field_325` — ТЗ (файл), `field_326` — иные документы (файлы), `field_329` — email создателя (заполняется только при создании), `field_333` — Local ID (см. выше, заполняется только при создании). Поля «Проект» нет — только у задач |

Файловые поля (`field_320`/`field_321`, `field_325`/`field_326`) принимают массив объектов `{"name": "...", "content": "<base64>"}`. Полная замена содержимого поля: `other_file_abs_paths=[]` очищает поле, `[p1, p2]` заменяет весь список — передать только новый файл нельзя, CRM потеряет остальные.

### Конфигурация

Переменные окружения в `src/.dev.env`:

```ini
CRM_API_URL=https://your-crm-host/api/rest.php
CRM_API_KEY=your_api_key
CRM_API_USER=api_user
CRM_API_PASSWORD=api_password
CRM_LOGIN_URL=https://your-crm-host/index.php?module=users/login
CRM_DEMO_ID=          # оставить пустым для production, заполнить для demo-инстанса

# entity_id сущностей/подсущностей и ID их полей (field_<ID> в payload) —
# генерируются внутри конкретной инсталляции CRM, при смене инстанса меняются
# только эти значения, без правок кода. Дефолты (см. src/crm/crm_config.py)
# совпадают со значениями ниже — переменные можно не задавать, если инстанс тот же.
CRM_TASK_ENTITY_ID=29
CRM_SUBTASK_ENTITY_ID=30

CRM_TASK_FIELD_TITLE=317
CRM_TASK_FIELD_DESCRIPTION=318
CRM_TASK_FIELD_COMPLETED=319
CRM_TASK_FIELD_SPECIFICATION=320
CRM_TASK_FIELD_OTHER_FILES=321
CRM_TASK_FIELD_CREATOR_EMAIL=328       # email создателя задачи; отправляется только при создании
CRM_TASK_FIELD_LOCAL_ID=332            # Local ID (Task.id); точный ключ для find_task, см. «Local ID» выше

CRM_SUBTASK_FIELD_TITLE=322
CRM_SUBTASK_FIELD_DESCRIPTION=323
CRM_SUBTASK_FIELD_COMPLETED=324
CRM_SUBTASK_FIELD_SPECIFICATION=325
CRM_SUBTASK_FIELD_OTHER_FILES=326
CRM_SUBTASK_FIELD_CREATOR_EMAIL=329    # email создателя подзадачи; отправляется только при создании
CRM_SUBTASK_FIELD_LOCAL_ID=333         # Local ID (Subtask.id); точный ключ для find_subtask

# Глобальный справочник «Проект» (см. раздел «Поле «Проект»» выше)
CRM_LIST_PROJECT=11                    # ID справочника в CRM
CRM_TASK_FIELD_PROJECT=327             # ID поля «Проект» у сущности «Задачи»
CRM_PROJECT_SYNC_INTERVAL_SECONDS=180  # интервал Celery Beat для sync_project_table

# Шардирование и rate-limit CRM-outbox-очереди (см. «Celery + Redis» ниже)
CRM_OUTBOX_SHARD_COUNT=4       # число шардов crm_sync.shard_0..shard_{N-1}
CRM_RATE_LIMIT_PER_SECOND=5    # лимит запросов к CRM в секунду (token-bucket)
CRM_OUTBOX_RETENTION_DAYS=30   # сколько дней хранить обработанные ('done') строки crm_outbox
```

### Структура модуля

```
src/crm/
├── crm_config.py           # чтение CRM_* переменных окружения через os.getenv(), включая entity_id/field_<ID>
├── client.py               # базовый HTTP-клиент (httpx async), метод _call(); CRMRecordNotFoundError
├── task_service.py         # CRUD-операции с задачами (entity_id по умолчанию 29) + find_task (идемпотентный retry create)
├── subtask_service.py      # CRUD-операции с подзадачами (entity_id по умолчанию 30) + find_subtask
└── global_lists_service.py # GlobalListsManager.get_choices(list_id) — чтение глобальных справочников (напр. «Проект»)
```

CRM-классы (`TaskManager`, `SubtaskManager`, `GlobalListsManager`) вызываются только из Celery-задач
(`src/tasks/`); веб-процесс на них не зависит, и `Protocol`/`Depends`-провайдеров вокруг них нет.

### HTTP-клиент

Один `httpx.AsyncClient` на весь срок жизни процесса, общий для всех CRM-классов (`TaskManager`, `SubtaskManager`) — module-level singleton (`_shared_http_client` в `src/crm/client.py`), а не атрибут класса `CRMClient`: запись `cls._http = ...` внутри `classmethod` создала бы атрибут в `__dict__` того подкласса, что передан как `cls` (`CRMClient` напрямую нигде не инстанцируется), и каждый наследник завёл бы свой собственный `AsyncClient`. Module-level переменная вне иерархии классов этой проблеме не подвержена. TCP-соединение к CRM переиспользуется между вызовами через HTTP/1.1 keep-alive.

`aclose_http_client()` вызывается явно в `lifespan()` (`src/main.py`) при shutdown — graceful-закрытие с drain in-flight запросов до `SIGKILL`, парная операция к ленивой инициализации при первом CRM-запросе.

### Формат запросов к API

Все запросы — HTTP POST на `/api/rest.php`, тело — JSON.

**Транспорт и аутентификация**

Три поля аутентификации присутствуют в каждом запросе:

```json
{
    "key":      "<API-ключ из Settings → API>",
    "username": "<логин пользователя с ролью API>",
    "password": "<пароль>",
    "action":   "insert|select|update|delete",
    "entity_id": 29
}
```

**action = insert — создание записей**

Поле `items` — массив словарей; каждый словарь — одна создаваемая запись.
Для сущности «Задачи» (entity_id=29) поля именуются `field_<ID>`, где ID — числовой
идентификатор поля в CRM. Статус — строковый чекбокс: `"true"` / `"false"`.

```json
{
    "key": "...", "username": "...", "password": "...",
    "action": "insert",
    "entity_id": 29,
    "items": [
        {
            "field_317": "Название задачи",
            "field_318": "Описание задачи",
            "field_319": "false"
        }
    ]
}
```

Ответ на успешный `insert` содержит ID созданной записи. ID возвращается строкой:

```json
{"status": "success", "data": {"id": "42"}}
```

**action = select — выборка записей**

Поле `select_fields` — идентификаторы полей через запятую.
Поле `filters` — словарь `{ID_поля: {value, condition}}`.
Условие `"include"` означает точное совпадение (не LIKE). Используется, например,
`TaskManager.find_task`/`SubtaskManager.find_subtask` для идемпотентного retry
`create` (поиск уже созданной записи по точному совпадению служебного поля
«Local ID» перед повторной вставкой, см. «Retry шардирован...» ниже).

**action = update — обновление записей**

Поле `data` — словарь обновляемых полей (только изменяемые, не весь объект).
Поле `update_by_field` — критерий поиска записи. `id` здесь — это CRM-ID,
хранящийся в локальной БД как `crm_task_id`.

```json
{
    "key": "...", "username": "...", "password": "...",
    "action": "update",
    "entity_id": 29,
    "data": {
        "field_317": "Новое название задачи",
        "field_319": "true"
    },
    "update_by_field": {"id": 42}
}
```

**action = delete — удаление записей**

Поле `delete_by_field` — критерий поиска удаляемой записи.

```json
{
    "key": "...", "username": "...", "password": "...",
    "action": "delete",
    "entity_id": 29,
    "delete_by_field": {"id": 42}
}
```

**Формат ответа**

CRM «Руководитель» не стандартизирует формат ответа между версиями и операциями.
Клиент проверяет все известные варианты признака успеха:

| Вариант ответа | Интерпретация |
|---|---|
| `{"success": true, ...}` | Успех |
| `{"status": "ok", ...}` | Успех |
| `{"status": "success", "data": {...}}` | Успех |
| `{"result": [...]}` без ключей `"error"` / `"error_message"` | Успех |
| `{"msg": "..."}` | Ошибка |
| `{"error_message": "..."}` | Ошибка |
| Любой ответ с ключом `"error"` | Ошибка |

---

## Deployment on a server running Ubuntu OS

«Голое железо»: приложение и все фоновые процессы запускаются напрямую на Ubuntu через
systemd, без контейнеров, а Nginx стоит перед uvicorn как reverse proxy и сам отдаёт
статику. Альтернатива — Docker (см. главу [«Docker Deployment»](#docker-deployment)
ниже: одна команда поднимает `web`, `redis`, `celery-*` и `flower` сразу). Ниже — полная
последовательность для чистого сервера Ubuntu 22.04/24.04 LTS с root/sudo-доступом и
доменным именем, у которого A-запись уже указывает на IP этого сервера. Как и в
Docker-варианте, PostgreSQL остаётся нативной службой ОС — единственный компонент,
который в обоих вариантах деплоя ставится и живёт одинаково.

Все пути ниже — `/opt/task-manager`; замените на свой, если используете другой. Везде, где встречается
`example.com` — подставьте свой домен.

### 1. Обновление системы и системные пакеты

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git nginx redis-server software-properties-common ca-certificates
```

`redis-server` из стандартного репозитория Ubuntu уже включает systemd-юнит `redis-server.service` и
автозапуск — отдельная настройка не нужна. Проверка:

```bash
sudo systemctl enable --now redis-server
redis-cli ping   # ожидаемый ответ: PONG
```

**Python 3.13.** В репозиториях Ubuntu 22.04/24.04 его нет — используем PPA
[deadsnakes](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa):

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.13 python3.13-venv python3.13-dev
```

`python3.13-venv` обязателен отдельно от самого `python3.13` — без него `python3.13 -m venv` падает с
ошибкой `ensurepip is not available`. `python3.13-dev` — на случай, если какой-то пакет из
`requirements.txt` не найдёт готовое wheel-колесо под конкретную архитектуру сервера и pip придётся
собирать его из исходников (для типовой связки Ubuntu x86_64 такого не происходит — все зависимости
проекта, включая `asyncpg`, ставятся из готовых wheel'ов).

**Системные библиотеки, нужные самому приложению** (см. `src/Dockerfile` — тот же список для Docker-образа):

```bash
sudo apt install -y libpq5 libmagic1
```

`libpq5` — рантайм-библиотека PostgreSQL для `asyncpg`. `libmagic1` — определение MIME-типа файла по
сигнатуре байтов (`python-magic`, см. «Файлы задач» выше) — без неё `import magic` в `src/utils/file_utils.py` падает `OSError`
ещё на старте приложения.

### 2. PostgreSQL 18

В стандартных репозиториях Ubuntu обычно более старая версия PostgreSQL, чем требуемая проектом `18.0`
(см. [«Technological Stack»](#technological-stack) выше). Подключаем официальный репозиторий PGDG:

```bash
sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc
sudo sh -c 'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
    https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list'
sudo apt update
sudo apt install -y postgresql-18
```

Пакет сам создаёт systemd-юнит и сразу его запускает:

```bash
sudo systemctl status postgresql   # active (exited) — управляющий юнит; сам процесс — postgresql@18-main
```

По умолчанию Ubuntu-сборка PostgreSQL уже слушает только `localhost` (`listen_addresses = 'localhost'`
в `/etc/postgresql/18/main/postgresql.conf`) и уже разрешает TCP-подключения с паролем с этого же хоста
(`host all all 127.0.0.1/32 scram-sha-256` в `/etc/postgresql/18/main/pg_hba.conf`) — это ровно то, что
нужно приложению (`DB_HOST=localhost` в `.env`, см. ниже). Отдельно редактировать эти файлы нужно, только
если PostgreSQL и приложение окажутся на разных хостах — это не наш случай.

Создаём роль и базу данных (пароль придумайте свой, он же пойдёт в `DB_PASS` ниже):

```bash
sudo -u postgres psql -c "CREATE USER task_manager_user WITH PASSWORD 'придумайте-надёжный-пароль';"
sudo -u postgres psql -c "CREATE DATABASE task_manager OWNER task_manager_user;"
```

### 3. Системный пользователь для приложения

Отдельная учётная запись ОС без права входа — то же соображение, что и `USER appuser` в
`src/Dockerfile` (не запускать процесс от root):

```bash
sudo useradd --system --shell /usr/sbin/nologin --home-dir /opt/task-manager --no-create-home taskmanager
sudo mkdir -p /opt/task-manager
sudo chown taskmanager:taskmanager /opt/task-manager
```

### 4. Код и виртуальное окружение

```bash
sudo -u taskmanager git clone --branch main https://github.com/bodyauza/task-manager.git /opt/task-manager
cd /opt/task-manager
sudo -u taskmanager python3.13 -m venv .venv
sudo -u taskmanager .venv/bin/pip install --no-cache-dir --upgrade pip
sudo -u taskmanager .venv/bin/pip install --no-cache-dir -r requirements.txt
```

`requirements-dev.txt` (pytest и т.п.) на production-сервере не нужен — это зависимости для запуска тестов,
не для работы приложения.

### 5. `src/.env` — переменные окружения для `API_MODE=prod`

`src/config.py` при `API_MODE=prod` читает `src/.env` (см. [«CRM «Руководитель» →
Конфигурация»](#конфигурация) выше — полный список CRM-переменных; здесь — итоговый файл
для production и то, чем он обязан отличаться от `.dev.env`). Файл не поставляется в
репозитории (гитигнорится, как и `.dev.env`/`.tests.env`) — создаём с нуля:

```bash
sudo -u taskmanager nano /opt/task-manager/src/.env
```

```ini
API_MODE=prod
APP_NAME=Task_Manager
ALGORITHM=HS256
ADMIN_EMAIL=admin@example.com
DB_DRIVER_SYNC=psycopg2
DB_DRIVER_ASYNC=asyncpg
DB_HOST=localhost
DB_PORT=5432
DB_USER=task_manager_user
DB_PASS=тот-же-пароль-что-и-в-CREATE-USER
DB_NAME=task_manager

# Каждый — отдельная случайная строка ≥ 32 символов, три РАЗНЫХ значения.
# Быстро сгенерировать: python3 -c "import secrets; print(secrets.token_urlsafe(48))"
ACCESS_SECRET=сгенерируйте-своё-значение
ACCESS_EXP=1800
REFRESH_SECRET=сгенерируйте-своё-значение-другое
REFRESH_EXP=604800
REG_TOKEN_SECRET=сгенерируйте-своё-значение-третье
REG_TOKEN_EXP=1200

SMTP_HOST=smtp.yandex.ru
SMTP_PORT=465
SMTP_USER=your@yandex.ru
SMTP_PASSWORD=app_password

# Реальный домен, а не localhost — иначе браузер заблокирует запросы фронтенда (CORS).
CORS_ORIGINS_CSV=https://example.com

# Redis слушает localhost — не в Docker-сети, отдельный "хост redis" не нужен.
REDIS_URL=redis://localhost:6379/0

ADMIN_TIMEZONE=Europe/Moscow
CHAT_HISTORY_MAX_LEN=500
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
# Публичный сервер — Swagger/ReDoc посторонним не нужны; DOCS_ENABLED не задан
# даёт тот же эффект (см. src/config.py::docs_enabled), но лучше явно.
DOCS_ENABLED=false
FLOWER_BASIC_AUTH=admin:придумайте-пароль

# ── CRM «Руководитель» — обязательные, без них приложение не стартует ──
# (см. src/crm/crm_config.py, _required_env/_required_int_env). Значения зависят от
# вашей инсталляции CRM — заполните каждое поле ниже, оставлять пустым нельзя.
CRM_API_URL=
CRM_API_KEY=
CRM_API_USER=
CRM_API_PASSWORD=
CRM_LOGIN_URL=

CRM_TASK_ENTITY_ID=
CRM_SUBTASK_ENTITY_ID=

CRM_TASK_FIELD_TITLE=
CRM_TASK_FIELD_DESCRIPTION=
CRM_TASK_FIELD_COMPLETED=
CRM_TASK_FIELD_SPECIFICATION=
CRM_TASK_FIELD_OTHER_FILES=
CRM_TASK_FIELD_CREATOR_EMAIL=
CRM_TASK_FIELD_LOCAL_ID=

CRM_SUBTASK_FIELD_TITLE=
CRM_SUBTASK_FIELD_DESCRIPTION=
CRM_SUBTASK_FIELD_COMPLETED=
CRM_SUBTASK_FIELD_SPECIFICATION=
CRM_SUBTASK_FIELD_OTHER_FILES=
CRM_SUBTASK_FIELD_CREATOR_EMAIL=
CRM_SUBTASK_FIELD_LOCAL_ID=

CRM_LIST_PROJECT=
CRM_TASK_FIELD_PROJECT=

# ── CRM «Руководитель» — необязательные, есть безопасный дефолт в src/crm/crm_config.py ──
CRM_DEMO_ID=                            # пусто для production; номер demo-инстанса для тестовой среды
CRM_PROJECT_SYNC_INTERVAL_SECONDS=180
CRM_OUTBOX_SHARD_COUNT=4
CRM_RATE_LIMIT_PER_SECOND=5
CRM_OUTBOX_RETENTION_DAYS=30
```

Значения обязательных `CRM_*`-переменных выше оставлены пустыми намеренно: `entity_id` и
номера полей (`field_<ID>`) генерируются внутри конкретной инсталляции CRM «Руководитель»
и отличаются между demo/production/другими клиентами — заполните их своими значениями
из панели администратора CRM. Таблицу соответствия полей и demo-значения, на которых
разрабатывался проект (для сверки формата) — см. [«CRM «Руководитель» →
Конфигурация»](#конфигурация) выше. `CRM_OUTBOX_SHARD_COUNT=4` — держите в уме это число,
оно определит, сколько systemd-юнитов шардов понадобится в шаге 10.

Права на файл с секретами — читает только владелец:

```bash
sudo chmod 600 /opt/task-manager/src/.env
```

### 6. Миграции Alembic и первый администратор

```bash
cd /opt/task-manager
sudo -u taskmanager env API_MODE=prod .venv/bin/alembic upgrade head
```

Миграции создают все таблицы (`role`, `user_role`, `person`, `task`, `subtask`, `project`, `crm_outbox`,
`registration_pending`); `role` заполняется ролями `user`/`admin` при первом старте приложения (lifespan,
не миграцией). Зарегистрируйте обычного пользователя через будущий `https://example.com/register`, затем
назначьте ему роль `admin` напрямую в БД — циклическая зависимость для самого первого администратора
(`PATCH /users/{id}` требует уже существующего admin'а):

```bash
sudo -u postgres psql -d task_manager -c "
INSERT INTO user_role (person_id, role_id)
SELECT id, 2 FROM person WHERE email = 'you@example.com'
ON CONFLICT (person_id, role_id) DO NOTHING;"
```

### 7. Каталог загруженных файлов

```bash
sudo -u taskmanager mkdir -p /opt/task-manager/src/uploads
sudo chmod 750 /opt/task-manager/src/uploads
```

`src/uploads/` — НЕ статика: `GET /uploads/{path}` защищён `Depends(current_user)` (см. «Файлы задач»
выше), Nginx не должен получить к нему прямой доступ через `alias`, поэтому владелец — только `taskmanager`, группа
`taskmanager` (не `www-data`), права `750`. Ниже в конфигурации Nginx (шаг 11) для этого каталога
намеренно нет отдельного `location` — запрос идёт в общий проксирующий `location /`, как и положено
для маршрута с проверкой авторизации.

### 8. Ручная проверка перед systemd

```bash
cd /opt/task-manager
sudo -u taskmanager env API_MODE=prod .venv/bin/uvicorn src.main:app --host 127.0.0.1 --port 8000
# в отдельном терминале:
curl -i http://127.0.0.1:8000/tasks/   # ожидаем 401 (валидной куки access_token нет) — значит приложение работает
```

Остановите (`Ctrl+C`) перед переходом к systemd — постоянную работу возьмёт на себя юнит ниже.

### 9. systemd — веб-приложение

```bash
sudo nano /etc/systemd/system/task-manager-web.service
```

```ini
[Unit]
Description=Task Manager — FastAPI web (uvicorn)
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
Type=simple
User=taskmanager
Group=taskmanager
WorkingDirectory=/opt/task-manager
Environment="API_MODE=prod"
Environment="UVICORN_WORKERS=2"
# --proxy-headers: доверять X-Forwarded-For/-Proto от Nginx (стоящего перед uvicorn) —
# без флага FastAPI видел бы во всех запросах адрес и протокол самого Nginx (127.0.0.1, http),
# а не реального клиента. По умолчанию uvicorn доверяет этим заголовкам только от 127.0.0.1 —
# Nginx именно там и работает, --forwarded-allow-ips не требуется отдельно.
ExecStart=/opt/task-manager/.venv/bin/uvicorn src.main:app --host 127.0.0.1 --port 8000 --workers ${UVICORN_WORKERS} --proxy-headers --ws-max-size 65536
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Число воркеров (`UVICORN_WORKERS`) подбирается так же, как для Docker-варианта — см. «Число воркеров
uvicorn (`UVICORN_WORKERS`)» ниже (≈ число ядер CPU, не `(2×CPU)+1`).

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now task-manager-web
sudo systemctl status task-manager-web
sudo journalctl -u task-manager-web -f   # логи в реальном времени; Ctrl+C для выхода
```

### 10. systemd — Celery: worker, шарды, beat, Flower

Ровно то же разделение процессов, что и в `src/docker-compose.yml` (см. [«Docker
Deployment»](#docker-deployment) ниже — там же список контейнеров), только вместо
контейнеров — systemd-юниты. Общий шаблон окружения — везде одинаковые
`User`/`Group`/`WorkingDirectory`/`Environment="API_MODE=prod"`, различается только `ExecStart`.

**10.1. Воркер дефолтной очереди** (`sync_project_table`, `reconcile_*`, `cleanup_done_outbox` —
несшардированные периодические задачи):

```bash
sudo nano /etc/systemd/system/task-manager-celery-worker.service
```

```ini
[Unit]
Description=Task Manager — Celery worker (default queue)
After=network.target postgresql.service redis-server.service
Requires=redis-server.service

[Service]
Type=simple
User=taskmanager
Group=taskmanager
WorkingDirectory=/opt/task-manager
Environment="API_MODE=prod"
ExecStart=/opt/task-manager/.venv/bin/celery -A src.celery_app worker --loglevel=info
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

**10.2. Шардированные воркеры CRM-outbox-очереди** — по одному процессу на шард, `concurrency=1`
(`--pool=solo`, тот же принцип, что и в Docker-варианте: ровно один процесс на шард, без параллелизма
внутри процесса — иначе теряется гарантия строгого порядка обработки событий одной задачи). Вместо
четырёх (по числу `CRM_OUTBOX_SHARD_COUNT`) почти одинаковых файлов — один **шаблонный** systemd-юнит
с параметром `%i` (тот же принцип DRY, что и YAML-якорь `&celery-worker-shard-base` в
`src/docker-compose.yml`):

```bash
sudo nano /etc/systemd/system/task-manager-celery-shard@.service
```

```ini
[Unit]
Description=Task Manager — Celery worker, outbox shard %i
After=network.target postgresql.service redis-server.service
Requires=redis-server.service

[Service]
Type=simple
User=taskmanager
Group=taskmanager
WorkingDirectory=/opt/task-manager
Environment="API_MODE=prod"
# %i — номер шарда (подставляется systemd из имени экземпляра юнита, например "0" из "...@0.service").
# %%h — экранированный литерал: без вторых % systemd попытался бы сам подставить сюда домашний
# каталог пользователя (это ЕГО спецификатор %h) вместо того, чтобы передать "%h" самой Celery,
# у которой это свой, отдельный спецификатор хоста воркера.
ExecStart=/opt/task-manager/.venv/bin/celery -A src.celery_app worker --loglevel=info --pool=solo -Q crm_sync.shard_%i -n shard%i@%%h
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Число экземпляров обязано совпадать с `CRM_OUTBOX_SHARD_COUNT` из `src/.env` (в примере выше — 4,
шарды `0`..`3`):

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now task-manager-celery-shard@0 task-manager-celery-shard@1 \
    task-manager-celery-shard@2 task-manager-celery-shard@3
```

Увеличение `CRM_OUTBOX_SHARD_COUNT` в будущем — правка `.env` + `systemctl enable --now
task-manager-celery-shard@4` для нового шарда, без изменения самого юнита. Уменьшать число шардов
нельзя, пока в `task.crm_shard` есть значения снятых шардов (то же ограничение, что и в Docker-варианте).

**10.3. Планировщик Beat** — ровно один экземпляр на весь деплой (иначе периодические задачи
ставились бы в очередь дважды):

```bash
sudo nano /etc/systemd/system/task-manager-celery-beat.service
```

```ini
[Unit]
Description=Task Manager — Celery beat (scheduler)
After=network.target postgresql.service redis-server.service
Requires=redis-server.service

[Service]
Type=simple
User=taskmanager
Group=taskmanager
WorkingDirectory=/opt/task-manager
Environment="API_MODE=prod"
ExecStart=/opt/task-manager/.venv/bin/celery -A src.celery_app beat --loglevel=info
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`WorkingDirectory=/opt/task-manager` — важно именно для Beat: файл `celerybeat-schedule`
создаётся в текущей рабочей директории процесса; `taskmanager` — владелец
`/opt/task-manager`, писать туда может.

**10.4. Flower (опционально)** — веб-мониторинг Celery, только на loopback, как и в Docker-варианте:

```bash
sudo nano /etc/systemd/system/task-manager-flower.service
```

```ini
[Unit]
Description=Task Manager — Flower (Celery monitoring)
After=network.target redis-server.service
Requires=redis-server.service

[Service]
Type=simple
User=taskmanager
Group=taskmanager
WorkingDirectory=/opt/task-manager
Environment="API_MODE=prod"
ExecStart=/opt/task-manager/.venv/bin/celery -A src.celery_app flower --address=127.0.0.1 --port=5555
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`--address=127.0.0.1` — то же соображение, что и `ports: - "127.0.0.1:5555:5555"` в
`docker-compose.yml`: Flower отдаёт содержимое задач и умеет управлять воркерами, наружу торчать не
должен. `FLOWER_BASIC_AUTH` подхватывается из `src/.env` (шаг 5) — без неё Flower стартует без
аутентификации (безопасно только пока порт не наружу). Доступ снаружи — через SSH-туннель:
`ssh -L 5555:127.0.0.1:5555 user@example.com`, затем `http://localhost:5555` в браузере на своей машине.

**Запуск всех юнитов и проверка:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now task-manager-celery-worker task-manager-celery-beat task-manager-flower
sudo systemctl status "task-manager-*"
```

### 11. Nginx — reverse proxy, статика, WebSocket

Директива `map` не может стоять внутри `server {}` — выносим её в отдельный файл, который nginx.conf
на Ubuntu уже подключает через `include /etc/nginx/conf.d/*.conf;`:

```bash
sudo nano /etc/nginx/conf.d/websocket-upgrade.conf
```

```nginx
map $http_upgrade $connection_upgrade {  # если заголовок Upgrade есть → "upgrade"; иначе → "close"
    default  upgrade;
    ''       close;
}
```

Сам конфиг сайта — сначала только HTTP (порт 443 добавит `certbot` в шаге 12, ссылаться на
несуществующие пока файлы сертификата нельзя — Nginx не запустится):

```bash
sudo nano /etc/nginx/sites-available/task-manager.conf
```

```nginx
upstream task_manager_backend {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name example.com;

    # Лимит размера ТЕЛА ЗАПРОСА, а не одного файла. Приложение разрешает файлы до 10 МБ
    # (MAX_FILE_SIZE), а самый большой запрос — создание задачи с ТЗ и 10 «иными
    # документами»: 11 × 10 МБ = 110 МБ плюс поля формы и multipart-разметка. Дефолтный
    # лимит Nginx (1 МБ) вернул бы 413 раньше, чем запрос вообще дошёл бы до FastAPI.
    client_max_body_size 120M;

    # --- Статика: css/js/img из src/static — Nginx отдаёт напрямую, в обход
    # Python-процесса (быстрее: sendfile() с нулевым копированием вместо
    # чтения файла в Python-объект и записи обратно). Те же файлы, что при
    # разработке отдаёт StaticFiles-mount в src/main.py — переопределять код
    # приложения не нужно, mount продолжает работать как фолбэк, если
    # когда-нибудь запустите uvicorn без Nginx перед ним.
    location /static/ {
        alias /opt/task-manager/src/static/;

        location ~* \.(css|js|png|jpg|jpeg|svg|ico|woff|woff2|ttf|eot)$ {
            expires 30d;
            add_header Cache-Control "public, no-transform";
            access_log off;
        }
    }

    # --- /uploads/{path} НАМЕРЕННО не описан отдельным location. Это защищённый
    # маршрут (Depends(current_user) в src/routers/uploads.py) — файлы задач и
    # подзадач видны только аутентифицированным пользователям. Alias в духе
    # location /static/ выше сделал бы их доступными БЕЗ проверки авторизации
    # напрямую с диска — запрос должен идти в приложение и проверяться там же,
    # поэтому /uploads/* обрабатывается общим location / ниже, как обычный маршрут.

    # --- WebSocket ---
    location /ws/ {
        proxy_pass http://task_manager_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600;   # WS-соединения живут часами; дефолтный таймаут 60с их обрывал бы
        proxy_send_timeout 3600;
    }

    # --- Всё остальное: REST API, HTML-страницы, /admin (sqladmin), /docs, /uploads/* ---
    location / {
        proxy_pass http://task_manager_backend;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Активируем сайт, отключаем дефолтный (иначе он конфликтует по `server_name`/`default_server`):

```bash
sudo ln -s /etc/nginx/sites-available/task-manager.conf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
```

Права на чтение статики для пользователя, от которого работает Nginx (`www-data`) — сам процесс
приложения (`taskmanager`) владеет файлами, `www-data` в эту группу не входит, поэтому нужны явные
права на обход каталогов и чтение файлов (`711`/`755`, не смена владельца):

```bash
sudo chmod 711 /opt/task-manager
sudo chmod -R 755 /opt/task-manager/src/static
```

`711` на корень проекта даёт только «право войти» (execute) кому угодно, но не «право посмотреть
список файлов» (read) — `www-data` может дойти до `src/static` по фиксированному пути из `alias`,
но не может просмотреть остальное содержимое `/opt/task-manager` (там же лежат `src/.env` с секретами
и `src/uploads` с чужими файлами). Проверяем конфигурацию и перезапускаем:

```bash
sudo nginx -t
sudo systemctl reload nginx
curl -i http://example.com/static/js/swagger-ui-bundle.js   # должен отдать файл, не 404/403
curl -i http://example.com/tasks/                            # должен вернуть 401 (проксируется в приложение)
```

### 12. SSL — Let's Encrypt через certbot

```bash
sudo apt install -y snapd
sudo snap install core && sudo snap refresh core
sudo snap install --classic certbot
sudo ln -s /snap/bin/certbot /usr/bin/certbot
sudo certbot --nginx -d example.com
```

`certbot --nginx` сам находит блок `server { listen 80; server_name example.com; }` в
`/etc/nginx/sites-available/task-manager.conf`, получает сертификат (HTTP-01 challenge — поэтому
DNS должен уже указывать на сервер и порт 80 быть доступен снаружи), дописывает `listen 443 ssl;` с
путями к сертификату в тот же server-блок и добавляет отдельный `server { listen 80; ...; return 301
https://...; }` для редиректа — вручную переписывать конфиг не нужно. Автопродление сертификата
certbot ставит сам как systemd-таймер:

```bash
sudo systemctl list-timers | grep certbot
sudo certbot renew --dry-run   # проверка, что автопродление сработает, без реального обновления
```

### 13. Firewall (ufw)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'   # открывает и 80, и 443 одним профилем
sudo ufw enable
sudo ufw status
```

PostgreSQL (5432), Redis (6379), сам uvicorn (8000) и Flower (5555) наружу не открываются вообще —
все слушают только `127.0.0.1`/`localhost` (см. шаги 2, 9, 10.4) и не упомянуты в правилах `ufw`
намеренно, тем же принципом, что и `ports: "127.0.0.1:6379:6379"` в `docker-compose.yml` для Redis.

### 14. Итоговая проверка всего стека

```bash
sudo systemctl status task-manager-web task-manager-celery-worker task-manager-celery-beat \
    "task-manager-celery-shard@0" "task-manager-celery-shard@1" \
    "task-manager-celery-shard@2" "task-manager-celery-shard@3" task-manager-flower nginx \
    postgresql redis-server

# Приложение доступно снаружи по HTTPS
curl -I https://example.com/

# Celery видит все запущенные воркеры (в т.ч. по шардам)
cd /opt/task-manager && sudo -u taskmanager env API_MODE=prod .venv/bin/celery -A src.celery_app inspect ping
```

**Redis через командную строку.** `redis-server` слушает `localhost:6379` (шаг 1), поэтому `redis-cli`
без дополнительных флагов идёт прямо на него:

```bash
# Все сообщения WS-чата — Redis List "chat:history" (RPUSH/LTRIM, см. src/realtime/chat_history.py).
# Каждый элемент — JSON-строка одного события (чат или CRUD-событие задачи/подзадачи), от старых к новым.
redis-cli LRANGE chat:history 0 -1

# Необработанные строки crm_outbox, ожидающие в очереди шарда 0 (та же схема шардирования, что и в
# Docker-варианте, здесь без Docker). В здоровой системе список короткий или пустой —
# task-manager-celery-shard@0 разбирает его почти сразу; длинный и растущий список — сигнал, что
# этот шардовый воркер не поднят или не справляется.
redis-cli LRANGE crm_sync.shard_0 0 -1

# Сколько задач сейчас ждёт в очереди каждого шарда (0..CRM_OUTBOX_SHARD_COUNT-1, по умолчанию 0-3)
for n in 0 1 2 3; do echo "shard_$n: $(redis-cli LLEN crm_sync.shard_$n)"; done
```

Дальше — как в обычном чек-листе после любого деплоя: открыть `https://example.com/register` в браузере,
пройти регистрацию, зайти, создать задачу с файлом (проверит `/uploads/*` через приложение, а не Nginx
напрямую), убедиться, что бейдж синхронизации доходит до `synced` (Celery-воркер шарда обработал
outbox-строку), открыть `/admin` под назначенным на шаге 6 администратором.

### 15. Обновление приложения (redeploy)

```bash
cd /opt/task-manager
sudo -u taskmanager git pull
sudo -u taskmanager .venv/bin/pip install --no-cache-dir -r requirements.txt
sudo -u taskmanager env API_MODE=prod .venv/bin/alembic upgrade head
sudo systemctl restart task-manager-web task-manager-celery-worker task-manager-celery-beat \
    "task-manager-celery-shard@0" "task-manager-celery-shard@1" \
    "task-manager-celery-shard@2" "task-manager-celery-shard@3" task-manager-flower
```

Порядок важен только в одном месте: `alembic upgrade head` — до перезапуска сервисов, иначе воркеры и
веб-процесс на короткое время окажутся на новом коде со старой схемой БД.

### 16. Логи и типичные проблемы

| Симптом | Где смотреть | Частая причина |
|---|---|---|
| `task-manager-web` не стартует | `journalctl -u task-manager-web -e` | Не создан `src/.env` (шаг 5) или ошибка в нём — `pydantic.ValidationError` в самом начале лога |
| `502 Bad Gateway` от Nginx | `journalctl -u task-manager-web -e`, `sudo tail -f /var/log/nginx/error.log` | `task-manager-web` упал или ещё не поднялся; `systemctl status task-manager-web` |
| Статика 403/404 через Nginx, но напрямую по `curl` с сервера работает | `sudo nginx -t`, права каталогов | Забыли `chmod 711 /opt/task-manager` (шаг 11) — `www-data` не может дойти по пути до `src/static` |
| Задачи не долетают до CRM, бейдж навсегда `pending` | `journalctl -u "task-manager-celery-shard@*" -e` | Не запущен(ы) шардированный(е) воркер(ы) — строка `crm_outbox` остаётся `pending`, пока `reconcile_pending_outbox` не подберёт её на следующий тик, но обработать её всё равно некому |
| WebSocket не подключается (в консоли браузера — сразу `close`) | `sudo tail -f /var/log/nginx/error.log`, `journalctl -u task-manager-web -e` | Не подключён `map` из `/etc/nginx/conf.d/websocket-upgrade.conf`, либо запрос ушёл не в `location /ws/`, а в `location /` (нет заголовков `Upgrade`/`Connection`) |
| `413 Request Entity Too Large` при загрузке файла ТЗ | `sudo tail -f /var/log/nginx/error.log` | Забыт `client_max_body_size 120M;` в конфиге Nginx (шаг 11) — приложение разрешает файлы крупнее дефолтного лимита Nginx в 1 МБ |
| `X-Forwarded-Proto`/реальный IP клиента не доходят до приложения | — | Забыт `--proxy-headers` в `ExecStart` юнита `task-manager-web` (шаг 9) |

---

## Docker Deployment

Приложение запускается в девяти контейнерах Docker Compose (проект `task-manager`): `web` (FastAPI + uvicorn, 2 воркера — WebSocket-события рассылаются между ними через Redis Pub/Sub), `redis` (брокер Celery, Pub/Sub, история чата), `celery-worker` (дефолтная очередь — синхронизация справочника «Проект», разбор зависших/заблокированных outbox-строк), `celery-beat` (планировщик периодических задач), `celery-worker-shard-0`..`celery-worker-shard-3` (по одному на шард CRM-outbox-очереди — гарантия строгого порядка обработки событий одной задачи внутри шарда) и `flower` (мониторинг Celery). **PostgreSQL в Docker не запускается** — это нативная служба на хосте, контейнеры обращаются к ней по `host.docker.internal`. Порт `redis` опубликован только на loopback хоста (`127.0.0.1:6379`, тот же принцип, что и у `flower`); у сервиса есть `healthcheck` (`redis-cli ping`), и все зависящие от него сервисы стартуют только после того, как он его пройдёт (`depends_on: condition: service_healthy`).

![9 Docker Compose containers](src\screenshots\9_Docker_Compose_containers.png)

### Структура файлов

```
task-manager/
├── src/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── .dev.env
└── ...
```

### Число воркеров uvicorn (`UVICORN_WORKERS`)

`src/Dockerfile` задаёт `ENV UVICORN_WORKERS=2` и запускает
`uvicorn src.main:app --workers ${UVICORN_WORKERS}` — переопределить значение
для своего окружения можно через переменную окружения контейнера `web`
(`docker-compose.yml` → `environment:` сервиса `web`, или `--env-file`),
без правки самого образа. При выборе числа воркеров для production:

- **Число воркеров ≈ числу ядер CPU, а не по формуле `(2 × CPU_cores) + 1`.**
  Эта формула — отправная точка для синхронных WSGI-приложений (Gunicorn +
  sync-воркеры, где каждый воркер простаивает большую часть времени, ожидая
  I/O в блокирующем вызове). Task Manager — asyncio-приложение (FastAPI +
  `asyncpg` + `httpx.AsyncClient`): один воркер уже обрабатывает много
  конкурентных запросов на одном event loop без блокировки, поэтому
  `(2 × CPU) + 1` для него обычно избыточно — больше воркеров, чем ядер,
  означает конкуренцию за CPU между процессами, а не дополнительную
  пропускную способность.
- **Каждый воркер — отдельный процесс с полной копией приложения в RAM.**
  Перед стартом в production проверьте: `UVICORN_WORKERS × память_на_воркер
  ≤ доступная_RAM_контейнера`. При превышении лимита контейнер падает по
  OOM (`OOMKilled` в `docker inspect`/`kubectl describe pod`), а не
  постепенно деградирует — планировать число воркеров нужно заранее, не по
  факту падения.
- **В Docker/Kubernetes не полагайтесь на автоопределение числа ядер.**
  `multiprocessing.cpu_count()`/`os.cpu_count()` внутри контейнера видит
  ядра **хоста**, а не CPU-лимит, выставленный контейнеру (`--cpus` в
  Docker, `resources.limits.cpu` в Kubernetes) — на хосте с 32 ядрами и
  лимитом контейнера в 2 CPU такой автоподбор завысит число воркеров в
  разы. Поэтому `UVICORN_WORKERS` в этом проекте — обычная переменная
  окружения с explicit-дефолтом (`2`, см. `src/Dockerfile`), а не результат
  вызова `cpu_count()` в коде: число воркеров нужно задавать явно, исходя из
  реального CPU-лимита деплоя, а не из того, что видит процесс.

Дополнительная причина держать число воркеров `web` **больше одного** именно
в этом проекте (помимо пропускной способности) — снижение blast radius
блокировки event loop одним CPU-bound запросом (например, `bcrypt.hash` при
логине/регистрации, см. `src/auth/manager.py`): при `N` воркерах такой
запрос замораживает `1/N` пользователей вместо всех сразу. WebSocket-рассылка
между несколькими воркерами не требует sticky-сессий и работает при любом
`UVICORN_WORKERS` — `broadcast()` публикует каждое событие в Redis Pub/Sub, и
каждый воркер подписан на тот же канал (раздел [WebSocket](#websocket) выше).

**`UVICORN_WORKERS` не связан с числом Celery-воркеров.** Это две независимые
оси масштабирования — `UVICORN_WORKERS` управляет обработкой HTTP/WebSocket-
запросов внутри контейнера `web`, а число Celery-воркеров (`celery-worker` +
`celery-worker-shard-0..3`, по одному процессу на шард outbox-очереди,
`CRM_OUTBOX_SHARD_COUNT` в `src/crm/crm_config.py`, по умолчанию 4) отвечает за пропускную способность
фоновой CRM-синхронизации. У них разные драйверы выбора числа: `UVICORN_WORKERS`
подбирается под конкурентные запросы и blast radius CPU-bound вызовов (см.
выше), а число `celery-worker-shard-N` жёстко равно `CRM_OUTBOX_SHARD_COUNT`
и обязано идти с `concurrency=1` (`--pool=solo`) на процесс — иначе ломается
гарантия строгого порядка обработки событий одной задачи внутри шарда.
Единственная точка соприкосновения — не конфигурационная, а по потоку данных:
каждый `web`-воркер при создании/изменении задачи ставит сообщение в очередь
Celery (`dispatch_outbox_row()` → `apply_async(...)`), но это fire-and-forget —
сколько бы `web`-воркеров одновременно ни писали в очередь одного шарда,
читает её всё равно ровно один `celery-worker-shard-N`-процесс, последовательно;
рост очереди при опережающей записи не ломает корректность (transactional
outbox + `reconcile_pending_outbox` как подстраховка). Изменение `UVICORN_WORKERS` не требует
синхронно менять число Celery-воркеров, и наоборот.

### Конфигурация

Перед первым запуском заполните `src/.dev.env`. Полный список CRM-переменных — [«CRM «Руководитель» →
Конфигурация»](#конфигурация) выше. Минимум для запуска в Docker:

```ini
DB_USER=your_db_user
DB_PASS=your_db_password
DB_NAME=task_manager
ACCESS_SECRET=your_random_secret_32_chars_min
REFRESH_SECRET=another_random_secret_32_chars_min
```

> `DB_HOST` и `REDIS_URL` указывать не нужно — `docker-compose.yml` переопределяет их для контейнеров: `DB_HOST=host.docker.internal`
> (нативный PostgreSQL на хосте) и `REDIS_URL=redis://redis:6379/0`. PostgreSQL на хосте должен принимать подключения из
> Docker-сети (`listen_addresses`, `pg_hba.conf`).

> Сервисы получают переменные через `env_file: .dev.env`. Подстановки `${VAR}` в самом `docker-compose.yml` больше нет, поэтому
> флаг `--env-file src/.dev.env` формально не обязателен — в командах ниже он оставлен для единообразия. Пароль Flower задаётся
> переменной `FLOWER_BASIC_AUTH` в том же `.dev.env`.

### Команды

```bash
# Сборка и запуск
docker compose --env-file src/.dev.env -f src/docker-compose.yml up --build

# Запуск в фоне
docker compose --env-file src/.dev.env -f src/docker-compose.yml up -d

# Логи
docker compose --env-file src/.dev.env -f src/docker-compose.yml logs -f web

# Логи фоновых Celery-процессов (worker дефолтной очереди, планировщик, один из шардов)
docker compose --env-file src/.dev.env -f src/docker-compose.yml logs -f celery-worker celery-beat celery-worker-shard-0

# Проверить Celery/Redis вручную, не дожидаясь расписания
docker compose --env-file src/.dev.env -f src/docker-compose.yml exec celery-worker \
  celery -A src.celery_app call src.tasks.global_lists_tasks.sync_project_table

# Redis работает в контейнере — redis-cli запускается ВНУТРИ него через "exec".

# Все сообщения WS-чата — Redis List "chat:history" (RPUSH/LTRIM, см. src/realtime/chat_history.py).
# Каждый элемент — JSON-строка одного события, от старых к новым.
docker compose --env-file src/.dev.env -f src/docker-compose.yml exec redis redis-cli LRANGE chat:history 0 -1

# Необработанные строки crm_outbox, ожидающие в очереди шарда 0. Короткий/пустой список — норма
# (celery-worker-shard-0 разбирает его почти сразу); длинный и растущий — сигнал, что этот
# шардовый воркер не поднят или не справляется.
docker compose --env-file src/.dev.env -f src/docker-compose.yml exec redis redis-cli LRANGE crm_sync.shard_0 0 -1

# Сколько задач сейчас ждёт в очереди каждого шарда (0..CRM_OUTBOX_SHARD_COUNT-1, по умолчанию 0-3)
for n in 0 1 2 3; do
  docker compose --env-file src/.dev.env -f src/docker-compose.yml exec redis redis-cli LLEN crm_sync.shard_$n
done

# Остановка
docker compose --env-file src/.dev.env -f src/docker-compose.yml down

# Остановка с удалением томов (после включения AOF — вместе с данными Redis,
# т.е. с историей WS-чата, см. «Сохранение данных Redis»; PostgreSQL нативный, его это не касается)
docker compose --env-file src/.dev.env -f src/docker-compose.yml down -v
```

### Сохранение данных Redis (том + AOF)

**Зачем.** По умолчанию сервис `redis` в `src/docker-compose.yml` работает без тома:
данные лежат внутри контейнера и **пропадают при его пересоздании**
(`docker compose down`, `up --force-recreate`, пересборка, смена образа). Даже
при обычном `restart` без настроек сохранности их возможная потеря зависит от
того, успел ли Redis записать снапшот. Что хранится в Redis:

| Данные | Ключи / очереди | Что будет при потере |
|---|---|---|
| **История WS-чата** | список `chat:history`, счётчик `chat:history:next_id` | История чата и действий исчезает целиком, нумерация `id` начинается заново. Единственные данные, которые нигде больше не хранятся. |
| Очереди Celery | `celery`, `crm_sync.shard_N` и служебные ключи | Не потеряется главное: CRM-события лежат в PostgreSQL (`crm_outbox`), а `reconcile_pending_outbox` заново ставит зависшие строки в очередь. Потеряется только то, что было «в полёте» (например, разовый запуск `sync_project_table`). |
| Служебные ключи | `crm_rate_limit` (TTL 1 с), `crm_shard_lock:*` (TTL 300 с, см. «Таймауты HTTP-запросов к CRM и большие файлы» выше) | Ничего: живут секунды/минуты. |
| Результаты задач Celery (`celery-task-meta-*`) | TTL 24 ч (дефолт Celery, не переопределён) | Ничего — см. «Celery result backend» ниже: эти данные и так никем не читаются, пока живы. |

Значит, том нужен в первую очередь ради истории WS-чата. Ограничить её размер можно
переменной `CHAT_HISTORY_MAX_LEN` (по умолчанию 500 записей).

### Celery result backend — Redis хранит ответ каждой задачи, но его никто не читает

`src/celery_app.py`: `Celery("task_manager", broker=settings.REDIS_URL, backend=settings.REDIS_URL)` —
у конструктора Celery два разных параметра. `broker` — куда кладутся задачи для
воркеров. `backend` — куда воркер после выполнения кладёт результат (что вернула
функция, статус success/failure, traceback при ошибке). В проекте оба указывают на
один и тот же Redis, значит при завершении **любой** задачи — `process_outbox_row`
(на каждое событие `crm_outbox`), `reconcile_pending_outbox`/`reconcile_blocked_outbox`
(раз в 60/300 сек), `sync_project_table` (раз в 180 сек), `cleanup_done_outbox`
(раз в сутки) — Celery пишет в Redis ключ `celery-task-meta-<task_id>` с результатом.

Ни одна строчка проекта этот результат не читает: нигде нет ни `AsyncResult`, ни
`.get()` на объекте задачи, ни `task_ignore_result=True` в конфигурации (то есть
Celery не попросили этого не делать). Все вызовы задач — чистый fire-and-forget
(`apply_async(...)` из `dispatch_outbox_row`/`reconcile_*`/admin-кнопки «Обновить
справочник», без ожидания ответа). `result_expires` не переопределён — действует
дефолт Celery (24 часа), поэтому ключи не растут бесконечно, а самоудаляются по
TTL, но всё это время Redis честно хранит результат каждой уже никому не нужной
задачи — постоянная, хоть и не критичная, паразитная запись без единого читателя.

Единственный вероятный потребитель — Flower (мониторинг Celery, порт 5555), и то не
через этот механизм: видимость задач в Flower обеспечивают отдельные флаги
`worker_send_task_events`/`task_send_sent_event` (тот же `celery_app.py`) — это канал
Celery events, не result backend; событие `task-succeeded` уже несёт усечённый
результат в своём теле. К result backend'у (`AsyncResult(id).result`) Flower
обращается только если администратор вручную откроет детальную карточку конкретной
задачи за полным (неусечённым) результатом — обычная работа приложения, включая
типовой мониторинг через Flower, до него не достаёт.

**Что даёт AOF.** AOF (Append Only File) — журнал всех команд записи, который Redis
дописывает на диск и проигрывает при старте. В отличие от RDB-снапшотов (по
умолчанию раз в несколько минут) при режиме `everysec` теряется не больше
~1 секунды записей при аварийной остановке.

#### Шаг 1. Изменить `src/docker-compose.yml`

В сервисе `redis` добавьте `command` и `volumes`, а в конец файла (на уровень
`services:`) — описание именованного тома:

```yaml
  redis:
    image: redis:7-alpine
    # AOF: журнал записей на диск, fsync раз в секунду (потеря ≤ ~1 с при аварии).
    # command переопределяет команду образа целиком — поэтому в начале
    # обязательно redis-server.
    command: redis-server --appendonly yes --appendfsync everysec
    volumes:
      - redis-data:/data        # образ redis пишет данные в /data
    ports:
      - "6379:6379"
    restart: unless-stopped

# ... остальные сервисы ...

volumes:
  redis-data:
```

Варианты `--appendfsync`: `everysec` (рекомендуется: баланс скорости и
сохранности), `always` (fsync после каждой записи: максимум сохранности, заметно
медленнее), `no` (решает ОС: быстрее всего, теряется больше).

#### Шаг 2. Применить

```bash
docker compose --env-file src/.dev.env -f src/docker-compose.yml up -d redis
```

Compose пересоздаст только контейнер `redis` (остальные сервисы не трогаются,
подключение восстановится само). **Однократно** при этом пропадёт текущее
содержимое Redis (том раньше не использовался): история чата начнётся заново.
Если её нужно сохранить, сначала сделайте копию:

```bash
docker compose --env-file src/.dev.env -f src/docker-compose.yml exec redis redis-cli SAVE
docker compose --env-file src/.dev.env -f src/docker-compose.yml cp redis:/data/dump.rdb ./redis-backup.rdb
```

#### Шаг 3. Проверить

```bash
# AOF включён? Ожидается: appendonly / yes
docker compose --env-file src/.dev.env -f src/docker-compose.yml exec redis redis-cli CONFIG GET appendonly

# Том создан и файлы журнала лежат на нём (Redis 7: каталог appendonlydir)
docker volume ls | grep redis-data
docker compose --env-file src/.dev.env -f src/docker-compose.yml exec redis ls -l /data/appendonlydir

# Сквозная проверка: отправьте сообщение в WS-чат, затем пересоздайте контейнер
docker compose --env-file src/.dev.env -f src/docker-compose.yml up -d --force-recreate redis
# перезагрузите страницу /task-board: сообщение должно остаться в панели
```

#### Эксплуатация

- **Остановка без потери истории:** `docker compose ... down` (без `-v`).
  **`down -v` удаляет тома, включая `redis-data`, и вместе с ними всю историю чата.**
- **Размер журнала.** Redis сам периодически переписывает AOF (по умолчанию при
  росте вдвое и не менее 64 МБ); вручную: `redis-cli BGREWRITEAOF`. История ограничена
  `CHAT_HISTORY_MAX_LEN`, поэтому журнал остаётся небольшим.
- **Резервная копия:** остановите запись или сделайте `redis-cli BGREWRITEAOF` и
  скопируйте каталог тома (`docker run --rm -v task-manager_redis-data:/data -v "$PWD":/backup alpine tar czf /backup/redis-data.tgz -C /data .`;
  имя тома начинается с имени проекта Compose, `task-manager`).
- **Ручная очистка чата** (том при этом сохраняется):
  `docker compose ... exec redis redis-cli DEL chat:history`.
- **Сбои Redis и целостность.** При аварийном обрыве записи Redis при старте сам
  обрежет неполный хвост журнала (`aof-load-truncated yes` по умолчанию); при
  повреждении в середине файла используйте `redis-check-aof --fix`.

#### Redis без Docker (варианты B и C выше)

Включите AOF в конфиге Redis и перезапустите службу:

```
appendonly yes
appendfsync everysec
dir /var/lib/redis        # каталог с данными; убедитесь, что у пользователя redis есть права на запись
```

- **Ubuntu:** файл `/etc/redis/redis.conf`, затем `sudo systemctl restart redis-server`;
  проверка — `redis-cli CONFIG GET appendonly`.
- **Windows (Memurai/сборка Redis):** правьте конфиг службы (`memurai.conf` /
  `redis.windows-service.conf`), перезапустите службу; проверка — та же команда.
- **WSL:** как на Ubuntu; данные лежат в файловой системе дистрибутива WSL.

### Применение миграций

```
docker compose --env-file src/.dev.env -f src/docker-compose.yml exec web alembic upgrade head
```

> `exec` подключается к уже запущенному контейнеру — `--env-file` здесь для единообразия
> команды с остальными (сам `exec` переменные `${VAR}` в YAML повторно не резолвит, но
> флаг безвреден, если контейнеры уже подняты корректно).

### Доступ

| Сервис | Адрес |
|---|---|
| API + UI | http://localhost:8000 |
| Swagger UI / ReDoc (если `DOCS_ENABLED` не выключен) | http://localhost:8000/docs, http://localhost:8000/redoc |
| sqladmin (только роль `admin`) | http://localhost:8000/admin |
| Flower (только с этого хоста) | http://localhost:5555 |
| PostgreSQL (нативная служба хоста) | localhost:5432 |
| Redis (только с этого хоста) | localhost:6379 |

`celery-worker`/`celery-beat`/`celery-worker-shard-0..3` — фоновые процессы без открытого сетевого порта; их состояние проверяется через логи (`docker compose ... logs -f celery-worker`), не через прямое подключение.

---

## Testing

Тесты используют отдельную базу данных `task_manager_test`, чтобы не затрагивать данные разработки.

### Локальный запуск

**1. Создать тестовую базу данных**

```sql
CREATE DATABASE task_manager_test;
```

Запускать миграции не нужно: фикстура `_test_schema` один раз за сессию делает `drop_all + create_all`
(схема всегда соответствует текущим ORM-моделям), а между тестами `setup_and_reset` чистит таблицы
через `TRUNCATE ... RESTART IDENTITY CASCADE`. Перед этим проверяется, что имя БД оканчивается на `_test`, — тесты
не запустятся на рабочей БД. **Не запускайте два `pytest` одновременно:** они делят одну тестовую БД и ломают друг друга.

**2. Установить dev-зависимости**

```
pip install -r requirements-dev.txt
```

`requirements-dev.txt` подключает `requirements.txt` и добавляет `pytest==8.3.5`, `pytest-asyncio==0.24.0`, `httpx==0.27.2`.

**3. Проверить `src/.tests.env`**

Файл содержит тестовые учётные данные (`DB_NAME=task_manager_test`).
Если PostgreSQL запущен с другими `DB_USER` / `DB_PASS` — отредактируйте файл.

Переключение на тестовую БД происходит автоматически: корневой `conftest.py` выставляет
`os.environ["API_MODE"] = "test"` до первого импорта `src.*`, а `src/config.py` сам
загружает файл текущего режима (`.tests.env`) первым — независимо от того, что лежит
в `.dev.env`. Реальная CRM не нужна — веб-процесс к ней вообще не обращается
(только Celery-воркер, замоканный отдельно, см. `mock_outbox_dispatch` ниже); SMTP
перехватывает фикстура `mock_smtp`.

**4. Запустить тесты**

```
pytest tests/ -v
```

**Покрытие кода** (необязательно): `pip install pytest-cov`, затем

```
pytest tests/ --cov=src --cov-config=.coveragerc --cov-report=term-missing
```

Создайте `.coveragerc` в корне проекта (в репозитории его нет):

```ini
[run]
source = src
concurrency = greenlet,thread
```

Без `concurrency = greenlet,thread` `coverage` не видит код внутри greenlet'ов SQLAlchemy async и занижает покрытие
(например, показывает непокрытыми тела эндпоинтов, которые тесты выполняют).

### Запуск тестов внутри Docker-контейнера

`src/config.py` грузит `.env`-файлы с `override=False`, поэтому `DB_HOST=host.docker.internal` (выставленный
docker-compose в окружении контейнера ещё до старта Python) не перезаписывается значением
`DB_HOST=localhost` из `.tests.env`.

```bash
# 1. Создать тестовую базу (один раз)
# PostgreSQL нативный (контейнера db больше нет) — psql с хоста:
psql -h 127.0.0.1 -U root -d task_manager -c "CREATE DATABASE task_manager_test WITH OWNER = root;"

# 2. Установить dev-зависимости в контейнер (один раз)
docker exec task-manager-web-1 pip install -r requirements-dev.txt -q

# 3. Запустить тесты
docker exec task-manager-web-1 python -m pytest tests/ -v
```

### Фикстуры

| Фикстура / функция | Scope | Назначение |
|---|---|---|
| `_test_schema` | session, autouse | `drop_all + create_all` **один раз за сессию** (отдельный NullPool-engine); перед этим проверяется, что имя БД оканчивается на `_test` |
| `setup_and_reset` | function, autouse | `TRUNCATE ... RESTART IDENTITY CASCADE` всех таблиц + вставка ролей (`user`=1, `admin`=2). Повторная проверка имени БД (`_test`) — защита от запуска на рабочей БД. **Не запускайте два `pytest` одновременно**: они делят одну тестовую БД |
| `fast_registration_code_hash` | function, autouse | Подменяет bcrypt-помощник кода подтверждения регистрации (rounds=6, продакшен-дефолт → 4 в тестах): небольшая, но заметная на сотнях вызовов экономия; пароли пользователей (argon2id) не затрагивает |
| `client` | function | `httpx.AsyncClient` с `ASGITransport` — HTTP-запросы к приложению без TCP |
| `mock_outbox_dispatch` | function, autouse | Патчит `dispatch_outbox_row` в `src.services.tasks`/`subtasks`/`attachments` — без него create/update/delete задачи или подзадачи в любом тесте пытались бы поставить настоящую Celery-задачу в очередь (реального брокера в тестах нет). Сама CRM-синхронизация проверяется через содержимое вставленных строк `crm_outbox`, не через мок CRM-клиента |
| `mock_realtime_redis` | function, autouse | Патчит `src.realtime.connection_manager._get_redis` — `broadcast_task_event` (вызывается из любого теста, создающего/меняющего/удаляющего задачу или подзадачу) не открывает реальное TCP-соединение к Redis |
| `mock_smtp` | function, autouse | Перехват `send_confirmation_code`; код сохраняется в `dict[email, code]` |
| `mock_magic` | function | Патч `magic.from_buffer` — определение MIME по сигнатурам без установки libmagic в CI |
| `upload_root` | function | Временная директория (`tmp_path/uploads`) для `UPLOAD_ROOT` в `src/services/attachments.py` — файловые тесты не трогают реальный диск |
| `registered_user` | function | Полный трёхшаговый flow регистрации через HTTP; возвращает `{"email": ..., "password": ...}` |
| `register_user` | — (async helper) | Три шага регистрации; **статус каждого шага проверяется** (при сбое тест падает сразу с понятным сообщением) |
| `login` / `register_and_login` | — (async helper) | Вход с проверкой `200` / регистрация + вход — общий хелпер вместо копий `_register_login`/`_auth` в тестовых файлах |
| `login_as_admin` | — (async helper) | Регистрация → повышение до `admin` → перелогин |
| `make_user` | — (async helper) | Пользователь напрямую в БД для сервисных тестов без HTTP |
| `promote_to_admin` | — (async helper) | Заменяет набор ролей пользователя на `[admin]` в БД (replace-семантика) |
| `admin_client` | function | Клиент, залогиненный в sqladmin-панель (`/admin`) как admin |

> `httpx.ASGITransport` не запускает ASGI lifespan (`startup`/`shutdown`).
> Инициализация схемы и ролей выполняется напрямую в `setup_and_reset`, а не через lifespan.

### Покрытие тестами

| Модуль | Сценарии |
|---|---|
| `test_auth.py` | Логин (успех / неверный пароль / несуществующий пользователь), logout (JS-вариант / без авторизации), refresh-токен (успех / без куки) |
| `test_registration_flow.py` | request-code (успех / нормализация email / невалидный email / дубль / rate-limit), verify-code (успех / нет pending / неверный код / счётчик попыток / лимит исчерпан / невалидный формат), complete (успех / без токена / слабый пароль / пустой firstname), полный flow + немедленный логин, patronymic (с отчеством / без / хранение NULL) |
| `test_tasks.py` | Создание (успех / дубль title / без авторизации / пустой title), чтение (пустой список / пагинация / вторая страница / невалидный limit / невалидный skip), поиск (найдено / не найдено / без авторизации), обновление (успех / частичное / дубль title / 404 / без авторизации), удаление (успех / 404 / без авторизации), совместный доступ, конкурентные `delete_task` + `create_subtask` (не должны давать 500 или вводящий в заблуждение 409) |
| `test_task_service.py` | Юнит-тесты `src/services/tasks.py` напрямую, без HTTP — сервис CRM не вызывает вовсе, только вставляет `crm_outbox` и диспатчит её (`dispatch_outbox_row` замокан фикстурой `mock_outbox_dispatch`): создание (успех, payload/`pending`-статус outbox-строки / дубль title не создаёт вторую строку), список (пагинация), обновление (404 / outbox поставлена, если задача уже синхронизирована / иначе пропущена), удаление (удаление строки + outbox поставлена / 404) |
| `test_subtask_service.py` | Юнит-тесты `src/services/subtasks.py` напрямую — тот же подход, что и `test_task_service.py`; в т.ч. `create_subtask` ставит outbox независимо от того, синхронизирован ли родитель (различается только `depends_on_event_id`) |
| `test_users.py` | Список (admin — успех / 403 / 401, без `hashed_password`), удаление (успех с проверкой БД / 403 / 404 / **запрет самоудаления** / каскад задач и подзадач), редактирование (успех / частичное / замена `role_ids` / неверные роли → 400 без изменений / 403 / 404) |
| `test_crm.py` | `TaskManager`: create_task / update_task / delete_task (успех и ошибки); _bool_to_crm |
| `test_pages.py` | HTML-маршруты: login / register / task-board (аутентифицированный и нет) |
| `test_subtasks.py` | Создание (успех / 401 / 404 / пустой title / whitespace нормализация / дубль в задаче / одинаковый title в разных задачах / без описания / outbox ставится независимо от синхронизации родителя), чтение списка (пустой / 401 / пагинация / вторая страница / невалидный limit/skip / несуществующий task_id), чтение по ID (успех / 404 / 401), обновление (успех / частичное / 404 / 401 / дубль title), удаление (успех / 404 / 401 / физическое удаление / cascade при удалении задачи) |
| `test_crm_subtask.py` | SubtaskManager: create_subtask (dict-ответ / list-ответ / completed=True / ConnectError / таймаут / CRM API error / невалидный JSON), update_subtask (успех / нет полей / ConnectError / CRM API error), delete_subtask (успех / ConnectError / CRM API error), _bool_to_crm |
| `test_task_files.py` | Загрузка/замена ТЗ (успех / 413 / 422 расширение / 422 MIME / 404 / 401), удаление ТЗ (успех / 404), иные документы (загрузка / превышение лимита 10 / удаление одного / удаление последнего / 404), появление outbox-строки `sync_files` при загрузке/удалении (только если задача уже синхронизирована с CRM), каскадная очистка при удалении задачи, WS-рассылка `task_files_updated`, конкурентная загрузка (lost update на `other_file_paths`) |
| `test_subtask_files.py` | Тот же набор сценариев для подзадач; событие `subtask_files_updated` |
| `test_realtime.py` | `ConnectionManager`: несколько соединений на пользователя, регистрация/снятие, `broadcast` (в т.ч. `exclude_user_id`, мёртвые соединения, публикация в Redis Pub/Sub, сообщения других воркеров), персистентность событий действий, `is_own` для каждого получателя без утечки `sender_user_id` |
| `test_project_field.py` | Поле «Проект»: `_resolve_project` (None / "" / неизвестный CRM-ID → 422 / найден / неактивная опция → 422), `create_task`/`update_task` с полем `project`, `list_tasks`/`search_tasks`/`get_task` возвращают `project`/`project_option_id`, сквозной HTTP-флоу create/patch |
| `test_global_lists_tasks.py` | Celery-задача `sync_project_table`: upsert новых опций, обновление изменившегося `label`, деактивация опций, пропавших из ответа CRM |
| `test_crm_outbox.py` | Durable-retry outbox (`Task`+`Subtask`, все операции `create`/`sync_files`/`update`/`delete`): продюсер — шардирование `id % N` и `depends_on_event_id` между `create` подзадачи и `create` родителя (остальное покрытие продюсера — `test_task_service.py`/`test_subtask_service.py`), `dispatch_outbox_row` не должна пробрасывать исключение `apply_async` наверх (брокер Celery/Redis временно недоступен), консьюмер (`_do_create_*` с идемпотентным `find_task`/`find_subtask`, `_do_delete_*` — идемпотентность при уже отсутствующей записи, `MAX_ATTEMPTS`), `depends_on_event_id` (ожидание/`blocked`/разблокировка), `reconcile_pending_outbox`/`reconcile_blocked_outbox`, два параллельных `pending`-события одной сущности не помечают её `synced` раньше, чем оба выполнены. Redis (Redlock, token-bucket) не поднимается — патчится в самом файле |
| `test_sharding.py` | `id % N`: точная равномерность на последовательных id, sticky-присвоение, границы |
| `test_uploads.py` | `GET /uploads/{path}`: 401 без входа, отдача файла, 404, каталог, path-traversal (закодированные `..%2f`, абсолютный путь, symlink) |
| `test_websocket.py` | Сам эндпоинт `/ws/tasks/{client_id}` через `TestClient.websocket_connect`: закрытие 1008 без куки / с неверным токеном / для неактивного пользователя, регистрация и снятие соединения, личность из куки (не из `client_id`), рассылка чата с `is_own` всем вкладкам автора, запись в историю |
| `test_shard_lock.py` | Redlock `shard_lock` на самодельном FakeRedis: имя, TTL и `blocking_timeout` лока, освобождение и закрытие клиента при сбое в блоке, `TimeoutError` при занятом шарде (тело не выполняется, чужой лок не трогается), новый клиент на каждый вызов; обработчик outbox идёт под локом шарда строки. Семантика настоящего redis-py Lock (SET NX PX) не проверяется — нужен живой Redis |
| `test_celery_runner.py` | Жизненный цикл запуска Celery-задач (синхронные тесты): `run_celery_task`/`run_isolated` — два запуска подряд, закрытие CRM-клиента и `engine.dispose()` в том числе при исключении; синхронные обёртки задач; реестр задач и соответствие расписания Beat зарегистрированным задачам; `acks_late` и события для Flower |
| `test_lifespan.py` | `create_initial_roles` (пустая таблица, идемпотентность, недостающая роль, чужие имена не перезаписываются, ошибка БД не роняет запуск), порядок шагов `lifespan` и его подключение к приложению (`TestClient` как контекст), подписка `ConnectionManager` на Redis Pub/Sub: `start_listening`/`stop_listening`/отписка |
| `test_admin_panel.py` | sqladmin: вход только `admin`, списки всех разделов, скрытые поля (`hashed_password`, `code_hash`), read-only разделы, действие «Повторить» для `crm_outbox` (только `failed`-строки; пропускает и явно помечает устаревшими строки, для которых у той же сущности уже есть более новое `done`-событие; flash-сообщение о результате: сколько возвращено в очередь / пропущено / устарело / ничего не выбрано, показывается один раз), ссылки на файлы |
| `test_admin_sync.py` | Страница и JSON-эндпоинты статуса CRM-синхронизации: доступ только `admin`, данные всех владельцев, последняя outbox-строка |
| `test_chat_endpoint.py` | `GET /chat/history`: аутентификация, пагинация (`before_id`, `limit`), `is_own` (в т.ч. для записей без `sender_user_id`), события действий без изменений |
| `test_chat_history.py` | Redis-список истории (`append_event`/`get_history_page`) на самодельном FakeRedis: возрастающие id, обрезка до `CHAT_HISTORY_MAX_LEN`, курсоры |
| `test_crm_rate_limit.py` | Token-bucket `acquire_slot`: лимит, TTL с `nx=True`, самовосстановление ключа без TTL, закрытие клиента |
| `test_sync_status_visibility.py` | `sync_status` в ответах списка, поиска и подзадач следует за результатом воркера; внутренние детали не раскрываются |
| `test_user_admin.py` | Форма создания пользователя и правка пароля в sqladmin: создание через `UserManager.create()`, валидация, отсутствие утечки пароля в ответ и логи |
| `test_task_create_files.py` | Атомарное создание задачи с файлами (`POST /create-task/`, multipart): ТЗ и «иные документы» сохраняются вместе с задачей, недопустимый файл → `422` и ничего не создаётся, сбой записи файла → задача создана + `file_upload_errors` |
| `test_subtask_create_files.py` | То же для подзадач (`POST /create-subtask/`) |
| `test_search_sql_injection.py` | `GET /tasks/search`: SQL-payload'ы (tautology, UNION, DROP/DELETE, `pg_sleep`) не дают 500 и не меняют таблицу; `%`, `_`, `\` ищутся буквально; невалидные `skip`/`limit` → `422` |
| `test_cleanup_outbox.py` | Celery Beat-задача `cleanup_done_outbox`: удаление старых `done`-строк, сохранение свежих `done` и всех `pending`/`failed`/`blocked` независимо от возраста, защита по `depends_on_event_id` (цепочка «съедается» с конца, держится и при `retention_days=0`), батчевое удаление за несколько итераций, гонка `SELECT`/`DELETE` (строка получает нового «должника» между выборкой и удалением) — `DELETE` падает `IntegrityError`, ничего не портит |
