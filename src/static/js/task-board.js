// user.id из <input type="hidden" id="userId" value="{{ user }}">,
// заполненного Jinja2 при рендере task-board.html.
// Нужен для URL WebSocket-соединения: /ws/tasks/{userId}.
const userId = document.getElementById('userId').value;

// Активное WS-соединение. Глобальная ссылка позволяет переиспользовать объект
// при переподключении в connectWebSocket() без создания замыканий.
let socket;

// DOM-элементы чата сохраняются один раз при загрузке скрипта.
// Это дешевле, чем вызывать getElementById в каждом addMessage().
const messagesDiv = document.getElementById('messages');
const statusDiv   = document.getElementById('status');

// Номер текущей страницы (отсчёт с 1). Обновляется в loadTasks() при каждом переходе.
// Читается в:
//   WS-обработчиках — для перезагрузки той же страницы при событиях от других пользователей.
//   deleteTask() — для решения, остаться на текущей странице или перейти на предыдущую.
let currentPage   = 1;

// Размер страницы: сколько задач запрашивать за один вызов GET /tasks/.
// Передаётся в URL как limit=5; бэкенд транслирует его в SQL LIMIT.
// Константа намеренно объявлена здесь — изменение одного значения применяется
// к skip-формуле, totalPages-расчёту и updatePagination() автоматически.
const tasksPerPage = 5;

// Общее число страниц. Пересчитывается после каждого GET /tasks/ по формуле:
//   Math.max(1, Math.ceil(X-Total-Count / tasksPerPage))
// Math.max(1, ...) исключает totalPages = 0 при пустом списке задач.
let totalPages    = 1;

// Ключ для localStorage. Вынесен в константу: опечатку в строке компилятор не поймает,
// но опечатку в имени переменной — поймает.
const CHAT_HISTORY_KEY = 'websocket_chat_history';

function escapeHtml(value) {
    // Экранирует HTML-спецсимволы: < → &lt;  > → &gt;  & → &amp;  " → &quot;
    // Метод: браузер сам выполняет экранирование при установке textContent.
    // innerHTML возвращает уже безопасную строку для вставки в другой innerHTML.
    const div = document.createElement('div');
    div.textContent = String(value);
    return div.innerHTML;
}

function _updateCharCounter(inputEl, counterEl, limit) {
    const len = inputEl.value.length;
    counterEl.textContent = len;
    const wrapper = counterEl.closest('.char-counter');
    // 90% порог даёт визуальное предупреждение за ~10 символов до лимита в 100.
    wrapper.classList.toggle('limit-near', len >= limit * 0.9 && len < limit);
    wrapper.classList.toggle('limit-reached', len >= limit);
}

function _getToastContainer() {
    // Контейнер уведомлений создаётся лениво: его нет в статичном HTML,
    // он добавляется в body при первом вызове showToast().
    let c = document.getElementById('notifContainer');
    if (!c) {
        c = document.createElement('div');
        c.id = 'notifContainer';
        c.className = 'notif-container';
        document.body.appendChild(c);
    }
    return c;
}

function showToast(message, type = 'info') {
    const container = _getToastContainer();
    const notif = document.createElement('div');
    notif.className = `notif notif-${type}`;
    notif.textContent = message;
    container.appendChild(notif);
    // requestAnimationFrame откладывает добавление класса до следующего кадра рендера.
    // Если добавить класс сразу после appendChild, браузер не успевает зафиксировать
    // начальное состояние transition (opacity: 0) и анимации появления не будет.
    requestAnimationFrame(() => { notif.classList.add('notif-show'); });
    setTimeout(() => {
        notif.classList.remove('notif-show');
        // Второй setTimeout ждёт завершения CSS-перехода (300 мс) перед удалением узла из DOM.
        setTimeout(() => notif.remove(), 300);
    }, 4000);
}

// Срез задач текущей страницы. Обновляется в displayTasks() при каждом GET /tasks/.
// Используется в:
//   openEditModal() — поиск задачи по id для заполнения формы редактирования без GET.
//   deleteTask() — проверка .length === 1 перед решением о переходе на предыдущую страницу.
//   displaySearchResults() — дедупликация: результаты поиска добавляются к кэшу,
//     чтобы openEditModal() находил задачи из поиска, а не только из текущей страницы списка.
let currentTasks = [];

// subtaskLabel — русское склонение числительных для счётчика подзадач.
// Алгоритм работает по последней цифре (mod10), с отдельной обработкой чисел 11–14 (mod100):
//   11, 12, 13, 14 — всегда «подзадач» (исключение из правила «1 → подзадача»).
//   mod100 !== 11 в первом условии именно для этого: 11 % 10 === 1, но склонение иное.
function subtaskLabel(n) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return `${n} подзадача`;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return `${n} подзадачи`;
    return `${n} подзадач`;
}

// ── Делегированный обработчик для кнопок, генерируемых динамически ──────────
// Кнопки «Изменить» и «Удалить» создаются в displayTasks/displaySearchResults через innerHTML.
// На момент выполнения этого кода их ещё нет в DOM, поэтому addEventListener на конкретные
// элементы не работает. Вместо этого один обработчик на document перехватывает событие
// на стадии всплытия (bubbling): click от любой кнопки поднимается до document.
// e.target.closest(selector) ищет ближайшего предка с атрибутом data-action,
// что позволяет кликать внутри кнопки (например, на иконку) и всё равно найти обёртку.
document.addEventListener('click', function(e) {
    const editBtn = e.target.closest('[data-action="edit"]');
    if (editBtn) { openEditModal(parseInt(editBtn.dataset.id, 10)); return; }

    const deleteBtn = e.target.closest('[data-action="delete"]');
    if (deleteBtn) { deleteTask(parseInt(deleteBtn.dataset.id, 10)); return; }
});

// ── Chat history ──────────────────────────────────────────────────────────────

function saveChatHistory() {
    const messages = Array.from(messagesDiv.querySelectorAll('.message')).map(el => el.textContent);
    localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(messages));
}

function loadChatHistory() {
    try {
        const saved = localStorage.getItem(CHAT_HISTORY_KEY);
        if (saved) {
            JSON.parse(saved).forEach(msg => {
                const el = document.createElement('div');
                el.className = 'message';
                el.textContent = msg;
                messagesDiv.appendChild(el);
            });
            // scrollTop = scrollHeight прокручивает к последнему сообщению.
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }
    } catch (e) {
        // Если localStorage содержит невалидный JSON (ручная правка, битые данные),
        // очищаем его и начинаем с чистого листа вместо падения в цикле.
        localStorage.removeItem(CHAT_HISTORY_KEY);
    }
}

function clearChatHistory() {
    localStorage.removeItem(CHAT_HISTORY_KEY);
    messagesDiv.innerHTML = '';
}

function addMessage(message) {
    const el = document.createElement('div');
    el.className = 'message';
    el.textContent = message;
    messagesDiv.appendChild(el);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
    saveChatHistory();
}

// ── WebSocket ─────────────────────────────────────────────────────────────────

function connectWebSocket() {
    try {
        // wss: при HTTPS, ws: при HTTP — зеркалит протокол страницы.
        // Браузеры блокируют ws: на HTTPS-странице как mixed content.
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        socket = new WebSocket(`${wsProtocol}//${window.location.host}/ws/tasks/${userId}`);

        socket.onopen = function() {
            statusDiv.textContent = 'Status: Connected';
            statusDiv.className = 'connection-status status-connected';
            // Первичная загрузка задач выполняется здесь, а не в window.onload:
            // гарантирует, что список обновится после восстановления разорванного соединения.
            // loadTasks(currentPage), а не loadTasks(1): connectWebSocket() вызывается и при
            // первом подключении, и при каждом реконнекте (onclose → setTimeout(connectWebSocket)) —
            // с хардкодом loadTasks(1) любой кратковременный обрыв связи (сон ноутбука, просадка
            // сети) молча переносил пользователя на первую страницу списка, даже если он листал
            // пятую. currentPage изначально равен 1, так что для самого первого подключения
            // поведение не меняется.
            loadTasks(currentPage);
        };

        socket.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'chat') {
                    addMessage(`${data.sender}: ${data.text}`);
                } else if (data.type === 'task_created') {
                    addMessage(`${data.sender}: Создана задача: ${data.title}`);
                    // При событиях от других пользователей перезагружаем текущую страницу,
                    // а не страницу 1: пользователь не теряет своё местоположение в списке.
                    loadTasks(currentPage);
                } else if (data.type === 'task_updated') {
                    addMessage(`${data.sender}: Обновлена задача: ${data.title}`);
                    loadTasks(currentPage);
                } else if (data.type === 'task_deleted') {
                    addMessage(`${data.sender}: Удалена задача: ${data.title}`);
                    loadTasks(currentPage);
                } else if (data.type === 'subtask_created') {
                    if (String(data.actor_id) === userId) {
                        addMessage(`Subtask for task '${data.task_title}' created: '${data.title}'`);
                    } else {
                        addMessage(`${data.sender}: Создана подзадача «${data.title}» [${data.task_title}]`);
                    }
                    loadTasks(currentPage);
                } else if (data.type === 'subtask_updated') {
                    if (String(data.actor_id) === userId) {
                        addMessage(`Subtask for task '${data.task_title}' updated: '${data.title}'`);
                    } else {
                        addMessage(`${data.sender}: Обновлена подзадача «${data.title}» [${data.task_title}]`);
                    }
                    loadTasks(currentPage);
                } else if (data.type === 'subtask_deleted') {
                    if (String(data.actor_id) === userId) {
                        addMessage(`Subtask for task '${data.task_title}' deleted: '${data.title}'`);
                    } else {
                        addMessage(`${data.sender}: Удалена подзадача «${data.title}» [${data.task_title}]`);
                    }
                    loadTasks(currentPage);
                } else if (data.type === 'task_files_updated') {
                    // Список задач не показывает файлы — loadTasks() здесь не нужен,
                    // событие влияет только на открытую страницу деталей задачи (task-detail.js).
                    if (String(data.actor_id) === userId) {
                        addMessage(data.action === 'deleted'
                            ? `Files removed from task "${data.title}"`
                            : `Files added to task "${data.title}"`);
                    } else {
                        addMessage(data.action === 'deleted'
                            ? `${data.sender}: Удалены файлы у задачи «${data.title}»`
                            : `${data.sender}: Добавлены файлы к задаче «${data.title}»`);
                    }
                } else if (data.type === 'subtask_files_updated') {
                    if (String(data.actor_id) === userId) {
                        addMessage(data.action === 'deleted'
                            ? `Files removed from subtask "${data.title}" [${data.task_title}]`
                            : `Files added to subtask "${data.title}" [${data.task_title}]`);
                    } else {
                        addMessage(data.action === 'deleted'
                            ? `${data.sender}: Удалены файлы у подзадачи «${data.title}» [${data.task_title}]`
                            : `${data.sender}: Добавлены файлы к подзадаче «${data.title}» [${data.task_title}]`);
                    }
                } else {
                    addMessage(event.data);
                }
            } catch (e) {
                addMessage(event.data);
            }
        };

        socket.onclose = function(event) {
            statusDiv.textContent = 'Status: Disconnected';
            statusDiv.className = 'connection-status status-disconnected';
            addMessage('System: Connection closed');
            // 1008 = Policy Violation: сервер закрыл соединение из-за невалидного токена.
            // Повторное подключение приведёт к тому же результату — редиректим на логин.
            if (event.code === 1008) {
                window.location.href = '/';
                return;
            }
            // Нормальный разрыв (сеть, таймаут сервера) — переподключаемся через 3 секунды.
            setTimeout(connectWebSocket, 3000);
        };

        socket.onerror = function() {
            statusDiv.textContent = 'Status: Error';
            statusDiv.className = 'connection-status status-disconnected';
            addMessage('System: Connection error');
        };
    } catch (error) {
        addMessage('System: Failed to connect - ' + error);
        setTimeout(connectWebSocket, 3000);
    }
}

function sendMessage() {
    const messageInput = document.getElementById('messageInput');
    const message = messageInput.value.trim();
    if (message && socket && socket.readyState === WebSocket.OPEN) {
        socket.send(message);
        addMessage('You: ' + message);
        messageInput.value = '';
    } else if (!message) {
        alert('Please enter a message');
    } else {
        alert('WebSocket is not connected');
    }
}

// ── Auth helpers ──────────────────────────────────────────────────────────────

// Singleton-промис для обновления access-токена.
// Сценарий без синглтона: loadTasks() и updateTask() параллельно получают 401,
// каждый вызывает POST /auth/access-token — без синглтона это лишний дублирующий
// round-trip к серверу за одним и тем же новым access_token.
// Синглтон: первый 401 стартует refresh, остальные await-ят тот же промис — один round-trip.
let _refreshPromise = null;

async function fetchWithAuth(url, options = {}) {
    // credentials: 'include' — браузер прикрепляет httpOnly-куки (access_token, refresh_token)
    // и сохраняет Set-Cookie из ответа. Без этого CORS-запрос не передаёт куки.
    const opts = { credentials: 'include', ...options };
    let resp = await fetch(url, opts);

    if (resp.status === 401) {
        if (!_refreshPromise) {
            _refreshPromise = fetch('/auth/access-token', {
                method: 'POST',
                credentials: 'include',
            }).finally(() => { _refreshPromise = null; });
        }
        const refreshResp = await _refreshPromise;
        if (!refreshResp.ok) {
            // refresh-токен истёк или отозван — сессия невосстановима.
            window.location.href = '/';
            return null;
        }
        // Повторяем исходный запрос; к этому моменту браузер уже сохранил новый access_token.
        resp = await fetch(url, opts);
    }

    if (resp.status === 503) {
        const body = await resp.clone().json().catch(() => ({}));
        if (body.detail === 'CRM_UNAVAILABLE') {
            alert('CRM недоступна, обратитесь в техподдержку Предприятия');
        }
    }

    return resp;
}

// ── Modal ─────────────────────────────────────────────────────────────────────

function openEditModal(id) {
    // Поиск в кэше currentTasks: избегает дополнительного GET-запроса при открытии модала.
    const task = currentTasks.find(t => t.id === id);
    if (!task) { alert('Task not found'); return; }
    document.getElementById('editTaskId').value = id;
    const editTitleEl = document.getElementById('editTitle');
    editTitleEl.value = task.title;
    document.getElementById('editDescription').value = task.description;
    document.getElementById('editCompleted').checked = task.completed;
    _updateCharCounter(editTitleEl, document.getElementById('editTitleCounter'), 100);
    document.getElementById('editModal').style.display = 'flex';
}

function closeEditModal() {
    document.getElementById('editModal').style.display = 'none';
}

async function submitEdit() {
    const id          = parseInt(document.getElementById('editTaskId').value, 10);
    const title       = document.getElementById('editTitle').value.trim();
    const description = document.getElementById('editDescription').value;
    const completed   = document.getElementById('editCompleted').checked;
    if (!title) { alert('Title cannot be empty'); return; }
    closeEditModal();
    await updateTask(id, title, description, completed);
}

// Клик вне модального окна (на затемнённый оверлей) — закрывает окно.
document.getElementById('editModal').addEventListener('click', function(e) {
    if (e.target === this) closeEditModal();
});

// ── Task CRUD ─────────────────────────────────────────────────────────────────

document.getElementById('createTaskForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    const title       = document.getElementById('title').value;
    const description = document.getElementById('description').value;

    try {
        const response = await fetchWithAuth('/create-task/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, description }),
        });
        if (!response) return;

        if (response.ok) {
            const task = await response.json();
            if (task.crm_synced === false) {
                showToast('Задача создана без синхронизации с CRM', 'warning');
            }
            addMessage(`Task created: ${task.title}`);
            document.getElementById('createTaskForm').reset();
            loadTasks();
        } else if (response.status === 422) {
            const error = await response.json();
            const msg = Array.isArray(error.detail)
                ? error.detail.map(e => e.msg).join('; ')
                : (error.detail || 'Ошибка валидации');
            alert(`Ошибка валидации: ${msg}`);
        } else {
            const error = await response.json();
            alert(`Error creating task: ${error.detail}`);
        }
    } catch (error) {
        console.error('Error:', error);
        alert('Failed to create task');
    }
});

async function searchTasksByTitle() {
    const title = document.getElementById('taskTitleSearchInput').value.trim();
    if (!title) { alert('Please enter a task title to search'); return; }

    try {
        // encodeURIComponent экранирует спецсимволы URL в строке поиска:
        // пробел → %20, & → %26 и т.д. Без этого строка «задача & подзадача»
        // разобьёт URL на два параметра.
        const response = await fetchWithAuth(`/tasks/search?title=${encodeURIComponent(title)}`);
        if (!response) return;

        if (response.ok) {
            displaySearchResults(await response.json());
        } else {
            const error = await response.json();
            alert(`Error: ${error.detail}`);
        }
    } catch (error) {
        console.error('Error:', error);
        alert('Failed to search tasks');
    }
}

function displaySearchResults(tasks) {
    // Дедупликация: результаты поиска добавляются к currentTasks, если такой id ещё не в массиве.
    // Это позволяет openEditModal() найти задачу из результатов поиска,
    // даже если она не находится на текущей странице основного списка.
    currentTasks = [...currentTasks, ...tasks].filter(
        (task, index, arr) => arr.findIndex(t => t.id === task.id) === index
    );

    const container = document.getElementById('singleTaskResult');
    container.innerHTML = '';

    if (tasks.length === 0) {
        container.innerHTML = '<p>No matching tasks found</p>';
        return;
    }

    const resultList = document.createElement('ul');
    resultList.className = 'task-list';

    tasks.forEach(task => {
        const taskItem = document.createElement('li');
        taskItem.className = `task-item ${task.completed ? 'completed' : ''}`;
        const crmBadge = task.crm_task_id == null
            ? '<span class="crm-badge">Отсутствует в CRM</span>'
            : '';
        const countBadge = task.subtask_count != null
            ? `<span class="subtask-count">${subtaskLabel(task.subtask_count)}</span>`
            : '';
        // Описание обрезается до 20 символов. escapeHtml применяется к фрагменту:
        // HTML-сущности (например, &amp;) длиннее одного символа, поэтому обрезать
        // надо до экранирования, иначе сущность может разорваться посередине.
        const shortDesc = task.description.length > 20
            ? escapeHtml(task.description.slice(0, 20)) + '…'
            : escapeHtml(task.description);
        taskItem.innerHTML = `
            <div class="task-header">
                <div class="task-title-row">
                    <div class="task-title">${escapeHtml(task.title)}</div>
                    ${crmBadge}
                    ${countBadge}
                </div>
                <span class="task-status ${task.completed ? 'status-completed' : 'status-pending'}">
                    ${task.completed ? 'Выполнено' : 'В работе'}
                </span>
            </div>
            <div class="task-description">${shortDesc}</div>
            <div class="task-actions">
                <a class="btn-open" href="/task/${task.id}">Открыть</a>
                <a class="btn-subtasks" href="/subtask-board/${task.id}">Подзадачи</a>
                <button class="update" data-action="edit" data-id="${task.id}">Изменить</button>
                <button class="delete" data-action="delete" data-id="${task.id}">Удалить</button>
            </div>
        `;
        resultList.appendChild(taskItem);
    });

    container.appendChild(resultList);
}

async function loadTasks(page = 1) {
    currentPage = page;
    // skip — SQL OFFSET: сколько строк пропустить с начала таблицы.
    // Формула (page - 1) * tasksPerPage переводит номер страницы (с 1) в смещение (с 0):
    //   page=1 → skip=0  → OFFSET 0  LIMIT 5 (строки 1–5)
    //   page=2 → skip=5  → OFFSET 5  LIMIT 5 (строки 6–10)
    //   page=3 → skip=10 → OFFSET 10 LIMIT 5 (строки 11–15)
    const skip = (page - 1) * tasksPerPage;

    try {
        const response = await fetchWithAuth(`/tasks/?skip=${skip}&limit=${tasksPerPage}`);
        if (!response) return;

        if (response.ok) {
            const tasks = await response.json();
            // X-Total-Count — нестандартный заголовок; бэкенд пишет в него результат
            // отдельного SELECT COUNT(*) без LIMIT/OFFSET. Клиент использует его для
            // вычисления количества страниц: нельзя определить totalPages только по длине
            // тела ответа, потому что последняя страница может содержать меньше tasksPerPage записей.
            // Fallback tasks.length применяется если заголовок отсутствует (например, в тестах):
            // totalPages будет равен 1, что технически неверно, но не приведёт к ошибке.
            const totalHeader = response.headers.get('X-Total-Count');
            const total = totalHeader !== null ? parseInt(totalHeader, 10) : tasks.length;
            totalPages = Math.max(1, Math.ceil(total / tasksPerPage));
            displayTasks(tasks);
            updatePagination();
        } else {
            const error = await response.json();
            alert(`Error loading tasks: ${error.detail}`);
        }
    } catch (error) {
        console.error('Error:', error);
        alert('Failed to load tasks');
    }
}

function displayTasks(tasks) {
    // Перезаписываем кэш целиком: текущая страница всегда содержит только то,
    // что пришло в последнем ответе. Задачи предыдущей страницы в кэше не остаются.
    currentTasks = tasks;
    const taskList = document.getElementById('taskList');
    taskList.innerHTML = '';

    if (tasks.length === 0) {
        taskList.innerHTML = '<p>No tasks found</p>';
        return;
    }

    const taskListElement = document.createElement('ul');
    taskListElement.className = 'task-list';

    tasks.forEach(task => {
        const taskItem = document.createElement('li');
        taskItem.className = `task-item ${task.completed ? 'completed' : ''}`;
        const crmBadge = task.crm_task_id == null
            ? '<span class="crm-badge">Отсутствует в CRM</span>'
            : '';
        const countBadge = task.subtask_count != null
            ? `<span class="subtask-count">${subtaskLabel(task.subtask_count)}</span>`
            : '';
        const shortDesc = task.description.length > 20
            ? escapeHtml(task.description.slice(0, 20)) + '…'
            : escapeHtml(task.description);
        taskItem.innerHTML = `
            <div class="task-header">
                <div class="task-title-row">
                    <div class="task-title">${escapeHtml(task.title)}</div>
                    ${crmBadge}
                    ${countBadge}
                </div>
                <span class="task-status ${task.completed ? 'status-completed' : 'status-pending'}">
                    ${task.completed ? 'Выполнено' : 'В работе'}
                </span>
            </div>
            <div class="task-description">${shortDesc}</div>
            <div class="task-actions">
                <a class="btn-open" href="/task/${task.id}">Открыть</a>
                <a class="btn-subtasks" href="/subtask-board/${task.id}">Подзадачи</a>
                <button class="update" data-action="edit" data-id="${task.id}">Изменить</button>
                <button class="delete" data-action="delete" data-id="${task.id}">Удалить</button>
            </div>
        `;
        taskListElement.appendChild(taskItem);
    });

    taskList.appendChild(taskListElement);
}

function updatePagination() {
    const pagination = document.getElementById('pagination');
    pagination.innerHTML = '';

    for (let i = 1; i <= totalPages; i++) {
        const pageButton = document.createElement('button');
        pageButton.textContent = i;
        pageButton.className = i === currentPage ? 'active' : '';
        // IIFE (Immediately Invoked Function Expression) фиксирует i в замыкании.
        // Без IIFE: все addEventListener-обработчики захватывают переменную i по ссылке.
        // После завершения цикла i === totalPages + 1, клик по любой кнопке вызвал бы
        // loadTasks(totalPages + 1). IIFE создаёт отдельную область видимости с параметром page,
        // который получает текущее значение i в момент вызова IIFE — не после цикла.
        pageButton.addEventListener('click', (function(page) {
            return function() { loadTasks(page); };
        })(i));
        pagination.appendChild(pageButton);
    }
}

async function updateTask(id, title, description, completed) {
    try {
        const response = await fetchWithAuth(`/tasks/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, description, completed }),
        });
        if (!response) return;

        if (response.ok) {
            const task = await response.json();
            if (task.crm_synced === false) {
                showToast('Задача обновлена без синхронизации с CRM', 'warning');
            }
            addMessage(`Task updated: ${task.title}`);
            loadTasks(currentPage);
            // Если в момент редактирования активен поиск — обновляем и его результаты.
            const searchInput = document.getElementById('taskTitleSearchInput');
            if (searchInput.value.trim()) searchTasksByTitle();
        } else if (response.status === 422) {
            const error = await response.json();
            const msg = Array.isArray(error.detail)
                ? error.detail.map(e => e.msg).join('; ')
                : (error.detail || 'Ошибка валидации');
            alert(`Ошибка валидации: ${msg}`);
        } else {
            const error = await response.json();
            alert(`Error updating task: ${error.detail}`);
        }
    } catch (error) {
        console.error('Error:', error);
        alert('Failed to update task');
    }
}

async function deleteTask(id) {
    if (!confirm('Are you sure you want to delete this task?')) return;

    try {
        const response = await fetchWithAuth(`/delete-task/${id}`, { method: 'DELETE' });
        if (!response) return;

        if (response.ok) {
            const task = await response.json();
            if (task.crm_synced === false) {
                showToast('Задача удалена без синхронизации с CRM', 'warning');
            }
            addMessage(`Task deleted: ${task.title}`);
            // currentTasks.length === 1: на странице была ровно одна запись — та, которую удалили.
            // Проверяем ДО loadTasks(), пока массив ещё содержит этот объект.
            // После перезагрузки страница была бы пустой; вместо этого переходим на предыдущую.
            // currentPage > 1: первая страница не имеет предыдущей; там пустой список — норма.
            if (currentTasks.length === 1 && currentPage > 1) {
                loadTasks(currentPage - 1);
            } else {
                loadTasks(currentPage);
            }
            // Очищаем результаты поиска: удалённая задача могла там присутствовать.
            document.getElementById('singleTaskResult').innerHTML = '';
        } else {
            const error = await response.json();
            alert(`Error deleting task: ${error.detail}`);
        }
    } catch (error) {
        console.error('Error:', error);
        alert('Failed to delete task');
    }
}

// ── Init ──────────────────────────────────────────────────────────────────────

window.addEventListener('load', function() {
    // 'load' (не 'DOMContentLoaded') гарантирует полную загрузку страницы,
    // включая CSS и изображения; все getElementById вернут не null.
    loadChatHistory();
    // connectWebSocket вызывает loadTasks(1) в обработчике socket.onopen.
    // Если WS не подключится, список задач не загрузится — намеренно:
    // без WS real-time обновления не работают, UI был бы частично функционален.
    connectWebSocket();

    document.getElementById('sendBtn').addEventListener('click', sendMessage);
    document.getElementById('clearHistoryBtn').addEventListener('click', clearChatHistory);
    document.getElementById('searchBtn').addEventListener('click', searchTasksByTitle);
    document.getElementById('saveEditBtn').addEventListener('click', submitEdit);
    document.getElementById('cancelEditBtn').addEventListener('click', closeEditModal);

    const titleInput     = document.getElementById('title');
    const titleCounter   = document.getElementById('titleCounter');
    const editTitleInput = document.getElementById('editTitle');
    const editCounter    = document.getElementById('editTitleCounter');

    titleInput.addEventListener('input', () => _updateCharCounter(titleInput, titleCounter, 100));
    editTitleInput.addEventListener('input', () => _updateCharCounter(editTitleInput, editCounter, 100));

    document.getElementById('messageInput').addEventListener('keypress', function(event) {
        if (event.key === 'Enter') sendMessage();
    });
    document.getElementById('taskTitleSearchInput').addEventListener('keypress', function(event) {
        if (event.key === 'Enter') searchTasksByTitle();
    });

    // beforeunload: последний момент перед уходом со страницы.
    // Сохраняем историю чата синхронно — async-операции здесь не успевают выполниться.
    window.addEventListener('beforeunload', saveChatHistory);
});
