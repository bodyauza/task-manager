const EMAIL_REGEX = /^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$/;

function formatError(detail) {
    if (!detail) return 'Произошла ошибка';
    if (detail === 'EMAIL_ALREADY_REGISTERED') return 'Пользователь с таким email уже зарегистрирован';
    if (detail === 'INVALID_EMAIL') return 'Некорректный формат email';
    if (detail === 'SMTP_ERROR') return 'Ошибка отправки письма. Обратитесь в техподдержку';
    if (detail.startsWith('RATE_LIMIT:')) {
        const sec = detail.split(':')[1];
        return `Подождите ${sec} секунд перед повторной отправкой`;
    }
    return detail;
}

document.getElementById('registerForm').addEventListener('submit', async function (e) {
    e.preventDefault();

    const email     = document.getElementById('email').value.trim();
    const errorDiv  = document.getElementById('errorMessage');
    const submitBtn = document.getElementById('submitBtn');

    errorDiv.textContent = '';

    if (!EMAIL_REGEX.test(email)) {
        errorDiv.textContent = 'Некорректный формат email';
        return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Отправка...';

    try {
        const r = await fetch('/auth/register/request-code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email }),
        });

        if (r.ok) {
            // reg_email и reg_code_sent_at хранятся в sessionStorage (не localStorage):
            // данные доступны только в текущей вкладке и сбрасываются при закрытии.
            // confirm-email.js читает reg_code_sent_at для реконструкции обратного отсчёта
            // после перезагрузки страницы — без него таймер начинался бы заново.
            sessionStorage.setItem('reg_email', email);
            sessionStorage.setItem('reg_code_sent_at', String(Date.now()));
            window.location.href = '/confirm-email';
        } else {
            const data = await r.json();
            errorDiv.textContent = formatError(data.detail);
        }
    } catch {
        errorDiv.textContent = 'Ошибка соединения с сервером';
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Получить код';
    }
});
