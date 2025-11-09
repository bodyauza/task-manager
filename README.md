# Task Manager

## Screenshots

### Protected Page

![Task Board](https://github.com/bodyauza/task-manager/blob/main/src/screenshots/task-board.png)

### Swagger UI

![Swagger UI](https://github.com/bodyauza/task-manager/blob/main/src/screenshots/swagger.png)

## Technological Stack

<p align="center">
  <img src="https://raw.githubusercontent.com/fastapi-users/fastapi-users/master/logo.svg?sanitize=true" alt="FastAPI Users">
</p>

<p align="center">
    <em>Ready-to-use and customizable users management for <a href="https://fastapi.tiangolo.com/">FastAPI</a></em>
</p>

[![PyPI version](https://badge.fury.io/py/fastapi-users.svg)](https://badge.fury.io/py/fastapi-users)

---

**Documentation**: <a href="https://fastapi-users.github.io/fastapi-users/" target="_blank">https://fastapi-users.github.io/fastapi-users/</a>

**Source Code**: <a href="https://github.com/fastapi-users/fastapi-users" target="_blank">https://github.com/fastapi-users/fastapi-users</a>

---

### Backend
- **Python**: 3.13.5
- **FastAPI**: 0.115.14
- **FastAPI Users**: 14.0.1

### ASGI web server
- **uvicorn**: 0.35.0

### Database
- **PostgreSQL**: 17.5
- **SQLAlchemy**: 2.0.41

### Testing Tools
- **Swagger UI**: 5.26.0

### Frontend
- **HTML5**, **CSS3**, **JavaScript**
- **Jinja2**: 3.1.6

## Authentication Process

1. **Login and Password Request**  
   The client sends a request to the server with an object containing the user's login and password.

2. **Token Generation**  
   If the entered password is correct, the server generates access token and returns it to the client.

3. **Using the Access Token**  
   The client uses the received access token to interact with the API. All subsequent requests to protected routes must
   include this token in the cookie.

4. **Access Token Renewal**  
   The access token has a validity period, usually 40 minutes.
   When the token expires, the client sends a request to the server and receives a new access token. 

## Endpoints

Доступ к интерактивной документации Swagger UI и маршрутам аутентификации
можно получить по адресу: [http://localhost:8000/docs](http://localhost:8000/docs)

- `POST http://localhost:8000/auth/login` - JWT аутентификация (получение токена).
- `POST http://localhost:8000/auth/logout` - Выход из системы.
- `POST http://localhost:8000/auth/register` - Регистрация нового пользователя.
- `POST http://localhost:8000/auth/access-token` - Получение нового access токена.

Маршруты задач:

- `GET http://localhost:8000/tasks/`: Получение списка задач (защищённая конечная точка).
- `POST http://localhost:8000/create-task/`: Создание новой задачи (защищённая конечная точка).
- `GET http://localhost:8000/tasks/{task_id}`: Получить определённую задачу (защищённая конечная точка).
- `PUT http://localhost:8000/update-task/{task_id}`: Обновить определённую задачу (защищённая конечная точка).
- `DELETE http://localhost:8000/delete-task/{task_id}`: Удалить определённую задачу (защищённая конечная точка).

**WebSocket** — протокол связи поверх TCP-соединения (см. Модель OSI), предназначенный для обмена сообщениями между браузером и веб-сервером,
используя постоянное соединение:

  - Использует собственный протокол `ws://` или `wss://` поверх TCP-соединения.
  - Соединение остается открытым, позволяя серверу и клиенту обмениваться данными в реальном времени без повторных запросов.
  - Сервер может самостоятельно инициировать отправку данных клиенту (например, уведомления, чаты, онлайн-игры).
  - Данные передаются в виде кадров (frames) с минимальными накладными расходами.

Для получения обновлений статуса задачи в режиме реального времени используйте WebSocket-подключения к `ws://localhost:8000/ws/tasks/{client_id}`

## Local development

### 1. Setting Up a Virtual Environment
Run the following command in the project's root folder to create a virtual environment:

```
python -m venv venv
```
`venv` – the name of the folder in which the virtual environment will be created.

### 2. Activate the virtual environment:

Windows:
```
venv\Scripts\activate
```
Linux/MacOS:
```
source venv/bin/activate
```

### 3. After activating the virtual environment, install all required packages from requirements.txt:

```
pip install -r requirements.txt
```

### 4. Configuring Environment Variables
Rename the .env.example file to .env in the root of the project and specify the following variables:

```
DB_USER=...
DB_PASS=...
```

### 5. Generate a secret key for signing JWT access tokens in .env file:

```
ACCESS_SECRET=
```

### 6. Create a `clients` database in PostgreSQL:
```sql
CREATE DATABASE clients;
```

### 7. FastAPI applications run on the Uvicorn server. To start the server open a terminal and run the command:
```
uvicorn main:app --reload
```
