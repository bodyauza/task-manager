# Currency Converter

## Screenshots

### Protected Page

![Admin Page](https://github.com/bodyauza/currency-converter/blob/main/src/screenshots/protected2.png)

### Swagger UI

![Swagger UI](https://github.com/bodyauza/currency-converter/blob/main/src/screenshots/Swagger1.png)

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
   If the entered password is correct, the server generates access and refresh tokens and returns them to the client.

3. **Using the Access Token**  
   The client uses the received access token to interact with the API. All subsequent requests to protected routes must
   include this token in the cookie.

4. **Access Token Renewal**  
   The access token has a validity period, usually 5 minutes. When the validity of this token expires, the client sends
   the refresh token to the server and receives a new access token. This process is repeated until the refresh token
   expires.

5. **Extending the Refresh Token**  
   The refresh token is issued for 30 days. Approximately 1-5 days before the expiration of the refresh token, the
   client sends a request with valid refresh token to obtain a new pair of tokens.

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
Create or modify the .env file in the project root and specify the following variables:

```
DB_USER=...
DB_PASS=...
```

### 5. Obtain a currency converter API key (for example, from [API Layer](https://apilayer.com/marketplace/currency_data-api))
```
CURRENCY_API_KEY=...
```

### 6. FastAPI applications run on the Uvicorn server. To start the server open a terminal and run the command:
```
uvicorn main:app --reload
```

Access the interactive Swagger UI documentation at: [http://localhost:8000/docs](http://localhost:8000/docs)

### 7. To run all tests of the project, run the command:
```
pytest
```
#### Additional useful commands:

Run tests from a specific file:
```
pytest tests/test_main.py
```
Stop on the first failed test:
```
pytest -x
```
Rerun only failed tests:
```
pytest --lf
```
Run tests changed since the last commit (if `pytest-xdist` is installed):
```
pytest --last-failed
```
Run tests with execution time measurements:
```
pytest --durations=5
```
