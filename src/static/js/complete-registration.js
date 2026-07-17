const PASSWORD_REGEX = /^(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()\-_=+\[\]{};:'",.<>/?]).{5,72}$/;

const SVG_EYE = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const SVG_EYE_OFF = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

function formatError(detail) {
    if (!detail) return 'Произошла ошибка';
    if (detail === 'MISSING_REG_TOKEN' || detail === 'REG_TOKEN_INVALID') {
        return 'Сессия регистрации истекла. Начните регистрацию заново';
    }
    if (detail === 'EMAIL_ALREADY_REGISTERED') return 'Пользователь с таким email уже зарегистрирован';
    if (detail === 'CRM_UNAVAILABLE') return 'CRM недоступна. Обратитесь в техподдержку';
    if (Array.isArray(detail)) return detail.map(e => e.msg).join('; ');
    return detail;
}

document.addEventListener('DOMContentLoaded', () => {
    const toggleBtn = document.getElementById('togglePassword');

    toggleBtn.addEventListener('click', () => {
        const input = document.getElementById('password');
        const hidden = input.type === 'password';
        input.type = hidden ? 'text' : 'password';
        toggleBtn.innerHTML = hidden ? SVG_EYE_OFF : SVG_EYE;
        toggleBtn.setAttribute('aria-label', hidden ? 'Скрыть пароль' : 'Показать пароль');
    });

    document.getElementById('completeForm').addEventListener('submit', async (e) => {
        e.preventDefault();

        const firstname  = document.getElementById('firstname').value.trim();
        const lastname   = document.getElementById('lastname').value.trim();
        // .trim() || null: пустая строка после обрезки пробелов приводится к null.
        // Бэкенд хранит NULL, а не пустую строку, чтобы различать «не указано» и «пусто».
        const patronymic = document.getElementById('patronymic').value.trim() || null;
        const password   = document.getElementById('password').value;
        const errorDiv  = document.getElementById('errorMessage');
        const submitBtn = document.getElementById('submitBtn');

        errorDiv.textContent = '';

        if (!firstname) { errorDiv.textContent = 'Введите имя'; return; }
        if (!lastname)  { errorDiv.textContent = 'Введите фамилию'; return; }
        if (!PASSWORD_REGEX.test(password)) {
            errorDiv.textContent =
                'Пароль должен содержать: заглавную букву, цифру и специальный символ. От 5 до 72 символов.';
            return;
        }

        submitBtn.disabled = true;
        submitBtn.textContent = 'Регистрация...';

        try {
            const r = await fetch('/auth/register/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ firstname, lastname, patronymic, password }),
            });

            if (r.ok) {
                sessionStorage.removeItem('reg_email');
                window.location.href = '/';
            } else {
                const data = await r.json();
                const msg = formatError(data.detail);
                errorDiv.textContent = msg;

                // Если токен недействителен — предлагаем начать заново
                if (
                    data.detail === 'MISSING_REG_TOKEN' ||
                    data.detail === 'REG_TOKEN_INVALID'
                ) {
                    setTimeout(() => { window.location.href = '/register'; }, 2500);
                }
            }
        } catch {
            errorDiv.textContent = 'Ошибка соединения с сервером';
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Зарегистрироваться';
        }
    });
});
