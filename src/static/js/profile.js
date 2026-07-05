const form = document.getElementById('logoutForm');
const btn  = document.getElementById('logoutBtn');

form.addEventListener('submit', async function (e) {
    e.preventDefault();
    btn.disabled = true;
    btn.textContent = 'Выход...';

    try {
        let r = await fetch('/auth/logout', { method: 'POST', credentials: 'include' });

        if (r.status === 401) {
            // access_token истёк — пробуем обновить и повторяем
            const refreshResp = await fetch('/auth/access-token', {
                method: 'POST',
                credentials: 'include',
            });
            if (refreshResp.ok) {
                r = await fetch('/auth/logout', { method: 'POST', credentials: 'include' });
            }
        }
    } catch {
        // сетевая ошибка — всё равно уходим на страницу входа
    }

    // replace() убирает профиль из истории браузера:
    // кнопка «назад» не вернёт кэшированную страницу после выхода
    window.location.replace('/');
});
