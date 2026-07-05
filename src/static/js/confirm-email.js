const EMAIL_KEY   = 'reg_email';
const SENT_AT_KEY = 'reg_code_sent_at';
const RATE_LIMIT_MS = 60_000;

let _countdownTimer = null;

function formatError(detail) {
    if (!detail) return 'Произошла ошибка';
    if (detail === 'NO_PENDING_REGISTRATION') return 'Запрос кода не найден. Запросите новый код';
    if (detail === 'CODE_EXPIRED')            return 'Код истёк. Запросите новый код';
    if (detail === 'TOO_MANY_ATTEMPTS')       return 'Превышено число попыток. Запросите новый код';
    if (detail.startsWith('INVALID_CODE:')) {
        const rem = detail.split(':')[1];
        return rem === '0'
            ? 'Неверный код. Следующая ошибка заблокирует текущий код'
            : `Неверный код. Осталось попыток: ${rem}`;
    }
    if (detail.startsWith('RATE_LIMIT:')) {
        const sec = detail.split(':')[1];
        return `Подождите ${sec} секунд перед повторной отправкой`;
    }
    if (detail === 'SMTP_ERROR') return 'Ошибка отправки письма. Обратитесь в техподдержку';
    return detail;
}

function startCountdown(resendBtn) {
    clearTimeout(_countdownTimer);

    function tick() {
        const sentAt  = parseInt(sessionStorage.getItem(SENT_AT_KEY) || '0', 10);
        const elapsed = Date.now() - sentAt;
        const remaining = Math.max(0, Math.ceil((RATE_LIMIT_MS - elapsed) / 1000));

        if (remaining <= 0) {
            resendBtn.disabled = false;
            resendBtn.textContent = 'Отправить повторно';
            return;
        }
        resendBtn.disabled = true;
        resendBtn.textContent = `Отправить повторно (${remaining} сек)`;
        _countdownTimer = setTimeout(tick, 1000);
    }

    tick();
}

window.addEventListener('DOMContentLoaded', () => {
    const email = sessionStorage.getItem(EMAIL_KEY);
    if (!email) {
        window.location.href = '/register';
        return;
    }

    document.getElementById('emailDisplay').textContent = email;

    const codeInput = document.getElementById('code');
    const submitBtn = document.getElementById('submitBtn');
    const errorDiv  = document.getElementById('errorMessage');
    const resendBtn = document.getElementById('resendBtn');

    startCountdown(resendBtn);

    // Разрешаем вводить только цифры
    codeInput.addEventListener('input', () => {
        codeInput.value = codeInput.value.replace(/\D/g, '');
    });

    document.getElementById('confirmForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        errorDiv.textContent = '';
        submitBtn.disabled = true;

        try {
            const r = await fetch('/auth/register/verify-code', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, code: codeInput.value }),
            });

            if (r.ok) {
                sessionStorage.removeItem(SENT_AT_KEY);
                window.location.href = '/complete-registration';
            } else {
                const data = await r.json();
                errorDiv.textContent = formatError(data.detail);

                // После исчерпания попыток или истечения кода — активируем кнопку повтора
                if (
                    data.detail === 'TOO_MANY_ATTEMPTS' ||
                    data.detail === 'CODE_EXPIRED' ||
                    data.detail === 'NO_PENDING_REGISTRATION'
                ) {
                    clearTimeout(_countdownTimer);
                    resendBtn.disabled = false;
                    resendBtn.textContent = 'Отправить новый код';
                }
            }
        } catch {
            errorDiv.textContent = 'Ошибка соединения с сервером';
        } finally {
            submitBtn.disabled = false;
        }
    });

    resendBtn.addEventListener('click', async () => {
        errorDiv.textContent = '';
        resendBtn.disabled = true;

        try {
            const r = await fetch('/auth/register/request-code', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email }),
            });

            if (r.ok) {
                sessionStorage.setItem(SENT_AT_KEY, String(Date.now()));
                codeInput.value = '';
                codeInput.focus();
                startCountdown(resendBtn);
            } else {
                const data = await r.json();
                errorDiv.textContent = formatError(data.detail);
                resendBtn.disabled = false;
                resendBtn.textContent = 'Отправить повторно';
            }
        } catch {
            errorDiv.textContent = 'Ошибка соединения с сервером';
            resendBtn.disabled = false;
            resendBtn.textContent = 'Отправить повторно';
        }
    });
});
