// Идентификатор задачи: считывается из <input type="hidden" id="taskId" value="{{ task_id }}">,
// который Jinja2 заполняет при рендере страницы на сервере. parseInt(..., 10) — явная
// десятичная база; строка "08" без радиуса трактовалась бы как восьмеричное число в ES3.
const taskId = parseInt(document.getElementById('taskId').value, 10);

// Срез данных текущей страницы: массив объектов подзадач, которые сейчас видны в списке.
// Заполняется в displaySubtasks() при каждом вызове loadSubtasks().
// Нужен в двух местах:
//   1. openEditModal() — поиск объекта по id для заполнения формы без дополнительного GET.
//   2. deleteSubtask() — проверка .length === 1: была ли удалённая запись последней на странице.
let currentSubtasks = [];

// Номер текущей страницы пагинации (отсчёт с 1, не с 0).
// Обновляется в loadSubtasks() при каждом переходе.
// Читается в deleteSubtask() для решения: перейти на предыдущую страницу или остаться.
// WS-обработчиков нет — subtask-board.js не подключает WebSocket.
let currentPage = 1;

// Размер страницы: сколько подзадач запрашивать за один вызов GET /subtasks/.
// Передаётся в URL как limit=5; бэкенд применяет его как SQL LIMIT.
// Определяет шаг skip: страница N → skip = (N-1) * perPage.
const perPage = 5;

// Общее число страниц. Пересчитывается после каждого GET по формуле:
//   totalPages = Math.max(1, Math.ceil(total / perPage))
// где total берётся из заголовка X-Total-Count ответа (SELECT COUNT(*) на бэкенде).
// Math.max(1, ...) гарантирует, что пустой список не даст totalPages = 0.
let totalPages = 1;

function escapeHtml(v) {
    // Экранирует HTML-спецсимволы через DOM: браузер сам заменяет < → &lt; и т.д.
    // Используется перед вставкой пользовательских строк в innerHTML.
    const d = document.createElement('div');
    d.textContent = String(v);
    return d.innerHTML;
}

function _updateCharCounter(inputEl, counterEl, limit) {
    const len = inputEl.value.length;
    counterEl.textContent = len;
    const w = counterEl.closest('.char-counter');
    // CSS-классы limit-near и limit-reached меняют цвет счётчика: жёлтый → красный.
    // 90% порог (limit * 0.9) даёт пользователю визуальное предупреждение за 10 символов до лимита.
    w.classList.toggle('limit-near', len >= limit * 0.9 && len < limit);
    w.classList.toggle('limit-reached', len >= limit);
}

function showToast(message, type = 'info') {
    // Контейнер создаётся лениво: при первом вызове его нет в HTML, добавляем в body.
    let c = document.getElementById('notifContainer');
    if (!c) {
        c = document.createElement('div');
        c.id = 'notifContainer';
        c.className = 'notif-container';
        document.body.appendChild(c);
    }
    const n = document.createElement('div');
    n.className = `notif notif-${type}`;
    n.textContent = message;
    c.appendChild(n);
    // requestAnimationFrame откладывает добавление класса до следующего кадра рендера:
    // без него браузер применил бы transition к уже финальному состоянию и анимации не было бы.
    requestAnimationFrame(() => n.classList.add('notif-show'));
    setTimeout(() => { n.classList.remove('notif-show'); setTimeout(() => n.remove(), 300); }, 4000);
}

// Глобальный промис обновления access-токена. Singleton-паттерн: если несколько
// параллельных запросов одновременно получат 401, POST /auth/access-token выполнится
// один раз; остальные ожидают тот же промис через await _refreshPromise.
let _refreshPromise = null;

async function fetchWithAuth(url, options = {}) {
    // credentials: 'include' — браузер прикрепляет куки к запросу и принимает Set-Cookie
    // из ответа. Без этого access_token и refresh_token не передаются кросс-доменно.
    const opts = { credentials: 'include', ...options };
    let resp = await fetch(url, opts);
    if (resp.status === 401) {
        if (!_refreshPromise) {
            // POST /auth/access-token использует refresh_token (кука httpOnly) и выдаёт
            // новый access_token. finally сбрасывает промис после завершения — следующий 401
            // запустит новый refresh, не дублируя текущий.
            _refreshPromise = fetch('/auth/access-token', { method: 'POST', credentials: 'include' })
                .finally(() => { _refreshPromise = null; });
        }
        const r = await _refreshPromise;
        if (!r.ok) { window.location.href = '/'; return null; }
        // Повторяем исходный запрос уже с обновлённым токеном в куке.
        resp = await fetch(url, opts);
    }
    return resp;
}

// Делегированный обработчик кликов. Кнопки «Изменить» и «Удалить» генерируются
// динамически в displaySubtasks() — на момент выполнения этого кода их ещё нет в DOM.
// Вместо того чтобы вешать addEventListener на каждую кнопку внутри forEach,
// один обработчик на document перехватывает всплытие события (event bubbling).
// e.target.closest('[data-action="..."]') поднимается по DOM от точки клика вверх
// до первого элемента с нужным атрибутом.
document.addEventListener('click', function(e) {
    const editBtn = e.target.closest('[data-action="edit"]');
    if (editBtn) { openEditModal(parseInt(editBtn.dataset.id, 10)); return; }
    const deleteBtn = e.target.closest('[data-action="delete"]');
    if (deleteBtn) { deleteSubtask(parseInt(deleteBtn.dataset.id, 10)); return; }
});

function openEditModal(id) {
    // Данные берём из currentSubtasks (кэш текущей страницы), не из DOM и не из GET.
    // Это позволяет избежать лишнего сетевого запроса при открытии модального окна.
    const s = currentSubtasks.find(s => s.id === id);
    if (!s) return;
    document.getElementById('editSubtaskId').value = id;
    const titleEl = document.getElementById('editTitle');
    titleEl.value = s.title;
    document.getElementById('editDescription').value = s.description;
    document.getElementById('editCompleted').checked = s.completed;
    _updateCharCounter(titleEl, document.getElementById('editTitleCounter'), 100);
    document.getElementById('editModal').style.display = 'flex';
}

function closeEditModal() {
    document.getElementById('editModal').style.display = 'none';
}

async function submitEdit() {
    const id          = parseInt(document.getElementById('editSubtaskId').value, 10);
    const title       = document.getElementById('editTitle').value.trim();
    const description = document.getElementById('editDescription').value;
    const completed   = document.getElementById('editCompleted').checked;
    if (!title) { alert('Название не может быть пустым'); return; }
    closeEditModal();
    await updateSubtask(id, title, description, completed);
}

// Клик вне модального окна (на затемнённый оверлей) — закрывает окно.
// e.target === this: клик именно на оверлее, не на дочернем элементе (форме).
document.getElementById('editModal').addEventListener('click', function(e) {
    if (e.target === this) closeEditModal();
});

async function loadSubtasks(page = 1) {
    currentPage = page;
    // skip — SQL OFFSET; переводим номер страницы (с 1) в смещение строк (с 0).
    // page=1 → skip=0  (первые 6 строк таблицы: OFFSET 0 LIMIT 6)
    // page=2 → skip=6  (следующие 6: OFFSET 6 LIMIT 6)
    // page=3 → skip=12 (OFFSET 12 LIMIT 6) и т.д.
    const skip = (page - 1) * perPage;
    try {
        // task_id фильтрует строки конкретной задачи на бэкенде (WHERE subtask.task_id = taskId).
        // skip и limit транслируются бэкендом в OFFSET/LIMIT в SQL-запросе.
        const resp = await fetchWithAuth(`/subtasks/?task_id=${taskId}&skip=${skip}&limit=${perPage}`);
        if (!resp) return;
        if (resp.ok) {
            const subtasks = await resp.json();
            // X-Total-Count — нестандартный заголовок; бэкенд записывает в него результат
            // отдельного SELECT COUNT(*) без LIMIT. Клиент использует это число для вычисления
            // totalPages: оно не выводится в теле ответа, чтобы не смешивать данные и метаданные.
            // Fallback subtasks.length применяется если заголовок отсутствует; тогда totalPages
            // вычислится только из длины текущей страницы, что занизит счётчик — но не выбросит NaN.
            const total = parseInt(resp.headers.get('X-Total-Count') || subtasks.length, 10);
            totalPages = Math.max(1, Math.ceil(total / perPage));
            displaySubtasks(subtasks);
            updatePagination();
        } else {
            const err = await resp.json();
            alert(`Ошибка загрузки: ${err.detail}`);
        }
    } catch (e) {
        alert('Не удалось загрузить подзадачи');
    }
}

function displaySubtasks(subtasks) {
    // Перезаписываем кэш текущей страницы — только то, что пришло в этом ответе.
    currentSubtasks = subtasks;
    const container = document.getElementById('subtaskList');
    container.innerHTML = '';
    if (!subtasks.length) { container.innerHTML = '<p>Подзадачи отсутствуют</p>'; return; }

    const ul = document.createElement('ul');
    ul.className = 'task-list';
    subtasks.forEach(s => {
        const li = document.createElement('li');
        li.className = `task-item ${s.completed ? 'completed' : ''}`;
        const crmBadge = s.crm_subtask_id == null
            ? '<span class="crm-badge">Отсутствует в CRM</span>'
            : '';
        // Описание обрезается до 20 символов для компактности карточки.
        // slice(0, 20) не мутирует строку; '…' — типографское многоточие (U+2026), не три точки.
        // escapeHtml применяется к обрезанному фрагменту, а не к исходной строке:
        // HTML-сущность (&amp;) длиннее одного символа, поэтому обрезать надо до экранирования.
        const shortDesc = s.description.length > 20
            ? escapeHtml(s.description.slice(0, 20)) + '…'
            : escapeHtml(s.description);
        li.innerHTML = `
            <div class="task-header">
                <div class="task-title-row">
                    <div class="task-title">${escapeHtml(s.title)}</div>
                    ${crmBadge}
                </div>
                <span class="task-status ${s.completed ? 'status-completed' : 'status-pending'}">
                    ${s.completed ? 'Выполнена' : 'В работе'}
                </span>
            </div>
            <div class="task-description">${shortDesc}</div>
            <div class="task-actions">
                <a class="btn-open" href="/subtask/${s.id}">Открыть</a>
                <button class="update" data-action="edit" data-id="${s.id}">Изменить</button>
                <button class="delete" data-action="delete" data-id="${s.id}">Удалить</button>
            </div>
        `;
        ul.appendChild(li);
    });
    container.appendChild(ul);
}

function updatePagination() {
    const p = document.getElementById('pagination');
    p.innerHTML = '';
    for (let i = 1; i <= totalPages; i++) {
        const btn = document.createElement('button');
        btn.textContent = i;
        btn.className = i === currentPage ? 'active' : '';
        // IIFE фиксирует значение i в отдельном замыкании для каждой кнопки.
        // Без IIFE все обработчики ссылались бы на одну переменную i; после окончания цикла
        // i равно totalPages + 1, и клик по любой кнопке вызывал бы loadSubtasks(totalPages + 1).
        // IIFE создаёт новую область видимости на каждой итерации с собственным параметром page.
        btn.addEventListener('click', (function(page) { return () => loadSubtasks(page); })(i));
        p.appendChild(btn);
    }
}

document.getElementById('createSubtaskForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    const title       = document.getElementById('title').value;
    const description = document.getElementById('description').value;
    try {
        const resp = await fetchWithAuth('/create-subtask/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            // task_id обязателен для SubtaskCreate: бэкенд проверяет существование задачи
            // перед созданием подзадачи (404, если задача не найдена).
            body: JSON.stringify({ task_id: taskId, title, description }),
        });
        if (!resp) return;
        if (resp.ok) {
            const s = await resp.json();
            if (s.crm_synced === false) showToast('Подзадача создана без синхронизации с CRM', 'warning');
            document.getElementById('createSubtaskForm').reset();
            // После создания остаёмся на текущей странице: новая запись может попасть
            // на другую страницу (сортировка по id ASC), но перезагрузка currentPage
            // обновляет счётчик и кнопки пагинации.
            loadSubtasks(currentPage);
        // } else if (resp.status === 403) {
        //     showToast('Нет доступа: вы не являетесь владельцем этой задачи', 'error');
        } else if (resp.status === 422) {
            const err = await resp.json();
            const msg = Array.isArray(err.detail) ? err.detail.map(e => e.msg).join('; ') : err.detail;
            alert(`Ошибка валидации: ${msg}`);
        } else {
            const err = await resp.json();
            alert(`Ошибка: ${err.detail}`);
        }
    } catch (e) {
        alert('Не удалось создать подзадачу');
    }
});

async function updateSubtask(id, title, description, completed) {
    try {
        const resp = await fetchWithAuth(`/subtasks/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, description, completed }),
        });
        if (!resp) return;
        if (resp.ok) {
            const s = await resp.json();
            if (s.crm_synced === false) showToast('Подзадача обновлена без синхронизации с CRM', 'warning');
            loadSubtasks(currentPage);
        // } else if (resp.status === 403) {
        //     showToast('Нет доступа: вы не являетесь владельцем этой задачи', 'error');
        } else {
            const err = await resp.json();
            alert(`Ошибка: ${err.detail}`);
        }
    } catch (e) {
        alert('Не удалось обновить подзадачу');
    }
}

async function deleteSubtask(id) {
    if (!confirm('Удалить подзадачу?')) return;
    try {
        const resp = await fetchWithAuth(`/delete-subtask/${id}`, { method: 'DELETE' });
        if (!resp) return;
        if (resp.ok) {
            const s = await resp.json();
            if (s.crm_synced === false) showToast('Подзадача удалена без синхронизации с CRM', 'warning');
            // currentSubtasks.length === 1: на странице была ровно одна запись — только что удалённая.
            // После loadSubtasks(currentPage) страница вернулась бы пустой.
            // Проверяем длину ДО перезагрузки: именно сейчас массив содержит удалённый объект.
            // currentPage > 1: есть куда возвращаться; на первой странице пустой список — штатная ситуация.
            if (currentSubtasks.length === 1 && currentPage > 1) {
                loadSubtasks(currentPage - 1);
            } else {
                loadSubtasks(currentPage);
            }
        // } else if (resp.status === 403) {
        //     showToast('Нет доступа: вы не являетесь владельцем этой задачи', 'error');
        } else {
            const err = await resp.json();
            alert(`Ошибка: ${err.detail}`);
        }
    } catch (e) {
        alert('Не удалось удалить подзадачу');
    }
}

window.addEventListener('load', function() {
    // 'load' срабатывает после полной загрузки DOM и ресурсов;
    // все getElementById ниже вернут не null.
    loadSubtasks(1);

    document.getElementById('saveEditBtn').addEventListener('click', submitEdit);
    document.getElementById('cancelEditBtn').addEventListener('click', closeEditModal);

    const titleInput   = document.getElementById('title');
    const titleCounter = document.getElementById('titleCounter');
    titleInput.addEventListener('input', () => _updateCharCounter(titleInput, titleCounter, 100));

    const editTitleInput   = document.getElementById('editTitle');
    const editTitleCounter = document.getElementById('editTitleCounter');
    editTitleInput.addEventListener('input', () => _updateCharCounter(editTitleInput, editTitleCounter, 100));
});
