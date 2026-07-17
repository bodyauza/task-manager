const EMAIL_REGEX = /^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$/;

const SVG_EYE = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const SVG_EYE_OFF = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

document.getElementById('togglePassword').addEventListener('click', function () {
    const input = document.getElementById('password');
    const isHidden = input.type === 'password';
    input.type = isHidden ? 'text' : 'password';
    this.innerHTML = isHidden ? SVG_EYE_OFF : SVG_EYE;
    this.setAttribute('aria-label', isHidden ? 'Скрыть пароль' : 'Показать пароль');
});

function formatError(error) {
    if (!error) return 'Произошла ошибка';
    if (error.detail === 'LOGIN_BAD_CREDENTIALS') return 'Неверный email или пароль';
    if (error.detail === 'CRM_UNAVAILABLE') return 'CRM недоступна, обратитесь в техподдержку Предприятия';
    if (Array.isArray(error.detail)) return error.detail.map(e => e.msg).join('; ');
    return error.detail || 'Произошла ошибка';
}

document.getElementById('loginForm').addEventListener('submit', async function(e) {
    e.preventDefault();

    const email     = document.getElementById('email').value;
    const password  = document.getElementById('password').value;
    const errorDiv  = document.getElementById('errorMessage');
    const submitBtn = document.getElementById('submitBtn');

    errorDiv.textContent = '';

    if (!EMAIL_REGEX.test(email)) {
        errorDiv.textContent = 'Некорректный формат email';
        return;
    }
    // Формат пароля здесь намеренно не проверяется — см. пояснение в
    // src/auth/endpoints.py::login(). Неверный пароль вернёт LOGIN_BAD_CREDENTIALS
    // с сервера, formatError() ниже покажет его пользователю.

    submitBtn.disabled = true;
    submitBtn.textContent = 'Вход...';

    // OAuth2 Password Flow ожидает application/x-www-form-urlencoded, а не JSON.
    // Поле называется 'username', а не 'email' — стандарт OAuth2 (RFC 6749).
    const formData = new URLSearchParams();
    formData.append('username', email);
    formData.append('password', password);

    try {
        const response = await fetch('/auth/login', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: formData.toString(),
        });

        if (response.ok) {
            window.location.href = '/task-board';
        } else {
            const error = await response.json();
            errorDiv.textContent = formatError(error);
        }
    } catch (err) {
        errorDiv.textContent = 'Ошибка соединения с сервером';
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Войти';
    }
});
