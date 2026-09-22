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
// Передаётся в URL как limit=; бэкенд транслирует его в SQL LIMIT.
// TASKS_PAGE_SIZE — общая константа из common.js (изменение применяется
// к skip-формуле, totalPages-расчёту и updatePagination() автоматически).

// Общее число страниц. Пересчитывается после каждого GET /tasks/ по формуле:
//   Math.max(1, Math.ceil(X-Total-Count / TASKS_PAGE_SIZE))
// Math.max(1, ...) исключает totalPages = 0 при пустом списке задач.
let totalPages    = 1;

// escapeHtml, _updateCharCounter, showToast, fetchWithAuth, subtaskLabel — общие функции,
// вынесены в common.js (подключён в task-board.html до этого скрипта).

// Срез задач текущей страницы. Обновляется в displayTasks() при каждом GET /tasks/.
// Используется в:
//   openEditModal() — поиск задачи по id для заполнения формы редактирования без GET.
//   deleteTask() — проверка .length === 1 перед решением о переходе на предыдущую страницу.
//   displaySearchResults() — дедупликация: результаты поиска добавляются к кэшу,
//     чтобы openEditModal() находил задачи из поиска, а не только из текущей страницы списка.
let currentTasks = [];

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

// ── Chat history (Redis List на сервере, GET /chat/history) ────────────────
// Единственный источник истории — сервер (src/realtime/chat_history.py):
// общая для всех пользователей и вкладок, переживает перезагрузку страницы
// и переподключение WS. localStorage не используется вовсе — см.
// docs/chat_history_redis_list_guide.md.

const CHAT_HISTORY_PAGE_SIZE = 50;
let oldestLoadedChatId = null;    // курсор для подгрузки более старых сообщений — id самого старого отрисованного
let hasMoreChatHistory  = true;   // false — сервер вернул страницу короче CHAT_HISTORY_PAGE_SIZE, дальше листать некуда
let isLoadingChatHistory = false; // защита от повторного запроса, пока предыдущий не завершился

// Подпись собственных сообщений и действий в панели — единая для чата, CRUD- и
// файловых событий, вживую и в восстановленной истории.
const SELF_LABEL = 'You';

// Своя ли запись. Чат-сообщения несут is_own (его для каждого получателя
// выставляет сервер — ConnectionManager._deliver_local для живой рассылки и
// GET /chat/history для истории; внутренний id отправителя клиенту не
// отдаётся). События действий несут actor_id (его же используют страницы
// деталей, см. task-detail.js) — сравниваем с id текущего пользователя.
// Работает одинаково во всех вкладках одного пользователя.
function isOwnEntry(data) {
    if (data.is_own !== undefined) return data.is_own === true;
    return data.actor_id !== undefined && String(data.actor_id) === userId;
}

// Строит текст одной записи панели — общая логика для живых WS-событий
// (MESSAGE_HANDLERS ниже) И для восстановленной из Redis истории
// (loadInitialChatHistory/loadOlderChatHistory) — один и тот же вид что при
// первом получении, что после перезагрузки страницы: "You: ..." для своих,
// "email: ..." для чужих.
function buildEventMessage(data) {
    const who = isOwnEntry(data) ? SELF_LABEL : data.sender;
    const filesVerb = data.action === 'deleted' ? 'Удалены файлы у' : 'Добавлены файлы к';
    switch (data.type) {
        case 'chat':            return `${who}: ${data.text}`;
        case 'task_created':    return `${who}: Создана задача: ${data.title}`;
        case 'task_updated':    return `${who}: Обновлена задача: ${data.title}`;
        case 'task_deleted':    return `${who}: Удалена задача: ${data.title}`;
        case 'task_files_updated':
            return `${who}: ${filesVerb} ${data.action === 'deleted' ? 'задачи' : 'задаче'} «${data.title}»`;
        case 'subtask_created': return `${who}: Создана подзадача «${data.title}» [${data.task_title}]`;
        case 'subtask_updated': return `${who}: Обновлена подзадача «${data.title}» [${data.task_title}]`;
        case 'subtask_deleted': return `${who}: Удалена подзадача «${data.title}» [${data.task_title}]`;
        case 'subtask_files_updated':
            return `${who}: ${filesVerb} ${data.action === 'deleted' ? 'подзадачи' : 'подзадаче'} «${data.title}» [${data.task_title}]`;
        default: return null;   // неизвестный тип — не должен сюда попасть
    }
}

function renderChatMessage(entry, prepend) {
    const text = buildEventMessage(entry);
    if (text === null) return;
    const el = document.createElement('div');
    el.className = 'message';
    el.textContent = text;
    if (prepend) {
        messagesDiv.insertBefore(el, messagesDiv.firstChild);
    } else {
        messagesDiv.appendChild(el);
    }
}

async function loadInitialChatHistory() {
    // Вызывается один раз при загрузке страницы (см. блок Init ниже) — НЕ из
    // socket.onopen: в отличие от loadTasks(currentPage), которая должна
    // повторяться при каждом реконнекте, повторный вызов этой функции
    // добавил бы дубликаты сообщений в начало уже заполненного #messages.
    const response = await fetchWithAuth(`/chat/history?limit=${CHAT_HISTORY_PAGE_SIZE}`);
    if (!response || !response.ok) return;
    const rows = await response.json();
    rows.forEach(entry => renderChatMessage(entry, false));
    if (rows.length > 0) oldestLoadedChatId = rows[0].id;
    hasMoreChatHistory = rows.length === CHAT_HISTORY_PAGE_SIZE;
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

async function loadOlderChatHistory() {
    if (isLoadingChatHistory || !hasMoreChatHistory || oldestLoadedChatId === null) return;
    isLoadingChatHistory = true;
    try {
        const response = await fetchWithAuth(
            `/chat/history?before_id=${oldestLoadedChatId}&limit=${CHAT_HISTORY_PAGE_SIZE}`
        );
        if (!response || !response.ok) return;
        const rows = await response.json();
        // Сообщения вставляются В НАЧАЛО — scrollHeight контейнера вырастет;
        // без компенсации ниже браузер визуально "дёрнул" бы видимую область
        // вниз на высоту вставки в момент подгрузки.
        const scrollHeightBefore = messagesDiv.scrollHeight;
        rows.forEach(entry => renderChatMessage(entry, true));
        messagesDiv.scrollTop += messagesDiv.scrollHeight - scrollHeightBefore;
        if (rows.length > 0) oldestLoadedChatId = rows[0].id;
        hasMoreChatHistory = rows.length === CHAT_HISTORY_PAGE_SIZE;
    } finally {
        isLoadingChatHistory = false;
    }
}

// Порог 20px, не строго scrollTop === 0 — пользователь обычно не докручивает
// ровно до пикселя перед тем, как продолжить листать дальше вверх.
messagesDiv.addEventListener('scroll', () => {
    if (messagesDiv.scrollTop <= 20) loadOlderChatHistory();
});

function addMessage(message) {
    const el = document.createElement('div');
    el.className = 'message';
    el.textContent = message;
    messagesDiv.appendChild(el);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// ── WebSocket ─────────────────────────────────────────────────────────────────

// Живая запись панели: рисуется как и историческая (renderChatMessage) и
// прокручивает панель вниз — собственное сообщение автора теперь тоже
// приходит эхом с сервера, а не рисуется сразу.
function renderLiveMessage(data) {
    renderChatMessage(data, false);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// Диспетчер по data.type — замена цепочки if/else if. Объект-поиск по ключу
// вместо последовательных сравнений: не требует ни if/else if, ни switch,
// и делает добавление нового типа события локальным изменением (один новый ключ),
// а не правкой середины длинной цепочки условий.
const MESSAGE_HANDLERS = {
    // renderChatMessage — единая функция рендера и для живых WS-событий, и
    // для восстановленной истории (buildEventMessage внутри неё); персистируются
    // "chat" и 6 CRUD-типов ниже (src/realtime/events.py::_PERSISTED_EVENT_TYPES) —
    // переживают перезагрузку страницы, см. docs/chat_history_redis_list_guide.md.
    chat: (data) => renderLiveMessage(data),

    task_created: (data) => {
        renderLiveMessage(data);
        // При событиях от других пользователей перезагружаем текущую страницу,
        // а не страницу 1: пользователь не теряет своё местоположение в списке.
        loadTasks(currentPage);
    },
    task_updated: (data) => {
        renderLiveMessage(data);
        loadTasks(currentPage);
    },
    task_deleted: (data) => {
        renderLiveMessage(data);
        loadTasks(currentPage);
    },

    subtask_created: (data) => {
        renderLiveMessage(data);
        loadTasks(currentPage);
    },
    subtask_updated: (data) => {
        renderLiveMessage(data);
        loadTasks(currentPage);
    },
    subtask_deleted: (data) => {
        renderLiveMessage(data);
        loadTasks(currentPage);
    },

    // Файловые события — такие же записи чата, как и остальные действия
    // ("You: ..." / "email: ..."), персистируются в истории. Список задач не
    // показывает файлы — loadTasks() здесь не нужен: событие влияет только на
    // открытые страницы деталей (task-detail.js/subtask-detail.js).
    task_files_updated: (data) => renderLiveMessage(data),
    subtask_files_updated: (data) => renderLiveMessage(data),
};

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
                const handler = MESSAGE_HANDLERS[data.type];
                if (handler) handler(data); else addMessage(event.data);
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
            setTimeout(connectWebSocket, WS_RECONNECT_DELAY_MS);
        };

        socket.onerror = function() {
            statusDiv.textContent = 'Status: Error';
            statusDiv.className = 'connection-status status-disconnected';
            addMessage('System: Connection error');
        };
    } catch (error) {
        addMessage('System: Failed to connect - ' + error);
        setTimeout(connectWebSocket, WS_RECONNECT_DELAY_MS);
    }
}

function sendMessage() {
    const messageInput = document.getElementById('messageInput');
    const message = messageInput.value.trim();
    if (message && socket && socket.readyState === WebSocket.OPEN) {
        socket.send(message);
        // Своё сообщение здесь НЕ рисуется: сервер рассылает его всем, включая
        // отправителя и все его вкладки (с is_own=true), и оно появится как
        // «You: ...» по эху — единый путь для всех вкладок автора.
        messageInput.value = '';
    } else if (!message) {
        alert('Please enter a message');
    } else {
        alert('WebSocket is not connected');
    }
}

// fetchWithAuth (и singleton _refreshPromise) — общая функция, вынесена в common.js.

// ── Modal ─────────────────────────────────────────────────────────────────────

// Снимок полей editModal на момент открытия — editTitle/editDescription/editCompleted
// изначально ПРЕДЗАПОЛНЕНЫ данными редактируемой задачи (не пусты), поэтому здесь
// нельзя использовать hasUnsavedFormData (проверка "пусто/не пусто"); нужен именно
// снимок + сравнение с текущим состоянием на момент закрытия.
let _editModalSnapshot = null;

function _readEditModalFields() {
    return {
        title: document.getElementById('editTitle').value,
        description: document.getElementById('editDescription').value,
        project: document.getElementById('editProject').value,
        completed: document.getElementById('editCompleted').checked,
    };
}

function _editModalHasUnsavedChanges() {
    if (!_editModalSnapshot) return false;
    const current = _readEditModalFields();
    return current.title !== _editModalSnapshot.title
        || current.description !== _editModalSnapshot.description
        || current.project !== _editModalSnapshot.project
        || current.completed !== _editModalSnapshot.completed;
}

registerModalCloseGuard(
    'editModal', _editModalHasUnsavedChanges,
    'Отменить редактирование? Несохранённые данные будут потеряны.',
);

function openEditModal(id) {
    // Поиск в кэше currentTasks: избегает дополнительного GET-запроса при открытии модала.
    // task.project_option_id (не task.project — та же лейбл-строка, а не CRM-ID) —
    // именно поэтому list_tasks/search_tasks обязаны отдавать это поле, не только
    // get_task (см. src/services/tasks.py::_attach_project_option_id).
    const task = currentTasks.find(t => t.id === id);
    if (!task) { alert('Task not found'); return; }
    document.getElementById('editTaskId').value = id;
    const editTitleEl = document.getElementById('editTitle');
    editTitleEl.value = task.title;
    document.getElementById('editDescription').value = task.description;
    document.getElementById('editProject').value = task.project_option_id || '';
    document.getElementById('editCompleted').checked = task.completed;
    _updateCharCounter(editTitleEl, document.getElementById('editTitleCounter'), TITLE_MAX_LENGTH);
    _editModalSnapshot = _readEditModalFields();
    openModal('editModal');
}

function closeEditModal() {
    closeModal('editModal');
}

async function submitEdit() {
    const id          = parseInt(document.getElementById('editTaskId').value, 10);
    const title       = document.getElementById('editTitle').value.trim();
    const description = document.getElementById('editDescription').value;
    const project      = document.getElementById('editProject').value;
    const completed   = document.getElementById('editCompleted').checked;
    if (!title) { alert('Title cannot be empty'); return; }
    // Прямой closeEditModal(), не requestCloseModal: сохранение — осознанное действие,
    // подтверждение не нужно (см. общий принцип в common.js::requestCloseModal).
    closeEditModal();
    await updateTask(id, title, description, completed, project);
}

// Клик вне модального окна (на затемнённый оверлей) — запрашивает закрытие через guard.
document.getElementById('editModal').addEventListener('click', function(e) {
    if (e.target === this) requestCloseModal('editModal');
});

// ── Task CRUD ─────────────────────────────────────────────────────────────────

// ── Create task modal (атомарное создание задачи + файлов одним запросом) ──────

// Файлы, выбранные в модалке, но ещё не отправленные на сервер — отправляются
// одним запросом вместе с текстовыми полями при клике на "Создать", а не сразу
// при выборе (в отличие от specInput/otherInput на task-detail.html, которые
// оперируют уже существующей задачей и грузят файл немедленно).
let createPendingSpecFile = null;
let createPendingOtherFiles = [];

// createSpecInput — обычный input[type=file], его выбранный файл уже виден
// hasUnsavedFormData() через сам DOM-элемент. createOtherInput — не виден: его
// value сбрасывается сразу после выбора (см. обработчик change ниже), а сами файлы
// живут в отдельном JS-массиве createPendingOtherFiles — общий чекер формы этого
// не увидит, поэтому очередь проверяется здесь отдельно, явно.
registerModalCloseGuard(
    'createTaskModal',
    // createProject — <select>, hasUnsavedFormData() его не проверяет (общий чекер
    // смотрит только на input[type=text]/textarea/input[type=file]) — выбранный
    // проект без единого другого изменения иначе не считался бы "есть что терять".
    () => hasUnsavedFormData(document.getElementById('createTaskModal'))
        || createPendingOtherFiles.length > 0
        || document.getElementById('createProject').value !== '',
    'Отменить создание? Несохранённые данные будут потеряны.',
);

function _resetCreateTaskModal() {
    document.getElementById('createTitle').value = '';
    document.getElementById('createDescription').value = '';
    document.getElementById('createProject').value = '';
    _updateCharCounter(
        document.getElementById('createTitle'),
        document.getElementById('createTitleCounter'),
        TITLE_MAX_LENGTH,
    );
    createPendingSpecFile = null;
    createPendingOtherFiles = [];
    document.getElementById('createSpecInput').value = '';
    document.getElementById('createOtherInput').value = '';
    _renderCreateSpecPending();
    _renderCreateOtherPending();
}

function openCreateTaskModal() {
    _resetCreateTaskModal();
    openModal('createTaskModal');
}

function _renderCreateSpecPending() {
    const row = document.getElementById('createSpecPendingRow');
    const name = document.getElementById('createSpecPendingName');
    if (createPendingSpecFile) {
        name.textContent = createPendingSpecFile.name;
        row.style.display = 'flex';
    } else {
        row.style.display = 'none';
    }
}

function _renderCreateOtherPending() {
    const list = document.getElementById('createOtherPendingList');
    const counter = document.getElementById('createOtherCount');
    counter.textContent = `(${createPendingOtherFiles.length} / ${MAX_OTHER_FILES})`;
    list.innerHTML = '';
    createPendingOtherFiles.forEach((file, index) => {
        const li = document.createElement('li');
        li.innerHTML = `
            <span class="file-pending-name">${escapeHtml(file.name)}</span>
            <button class="btn-delete-file" data-remove-pending="${index}">✕</button>
        `;
        list.appendChild(li);
    });
}

// validateOtherFileClientSide — то же правило, что и на task-detail.js (расширение +
// размер, до отправки на сервер): дублируется здесь, а не выносится в common.js,
// т.к. там же не вынесено на момент этой доработки (не расширяем область правки).
function _validateOtherFileClientSide(file) {
    const dotIndex = file.name.lastIndexOf('.');
    const ext = dotIndex >= 0 ? file.name.slice(dotIndex).toLowerCase() : '';
    const allowed = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.jpg', '.jpeg', '.png', '.txt'];
    if (!allowed.includes(ext)) return `расширение «${ext || '(нет)'}» не поддерживается`;
    if (file.size > OTHER_FILES_MAX_SIZE) return `размер превышает лимит ${OTHER_FILES_MAX_SIZE / (1024 * 1024)} МБ`;
    return null;
}

document.getElementById('openCreateTaskModalBtn').addEventListener('click', openCreateTaskModal);
document.getElementById('createTaskCancelBtn').addEventListener('click', function() { requestCloseModal('createTaskModal'); });
document.getElementById('createTaskModal').addEventListener('click', function(e) {
    if (e.target === this) requestCloseModal('createTaskModal');
});

document.getElementById('createSpecInput').addEventListener('change', function(e) {
    createPendingSpecFile = e.target.files[0] || null;
    _renderCreateSpecPending();
});

document.getElementById('createSpecRemoveBtn').addEventListener('click', function() {
    createPendingSpecFile = null;
    document.getElementById('createSpecInput').value = '';
    _renderCreateSpecPending();
});

document.getElementById('createOtherInput').addEventListener('change', function(e) {
    const chosen = Array.from(e.target.files);
    e.target.value = ''; // диалог можно открыть заново без потери уже выбранных файлов
    for (const file of chosen) {
        const err = _validateOtherFileClientSide(file);
        if (err) { showToast(`«${file.name}»: ${err}`, 'warning'); continue; }
        if (createPendingOtherFiles.length >= MAX_OTHER_FILES) {
            showToast(`Превышен лимит файлов (${MAX_OTHER_FILES} штук)`, 'warning');
            break;
        }
        createPendingOtherFiles.push(file);
    }
    _renderCreateOtherPending();
});

document.getElementById('createOtherPendingList').addEventListener('click', function(e) {
    const btn = e.target.closest('[data-remove-pending]');
    if (!btn) return;
    createPendingOtherFiles.splice(parseInt(btn.dataset.removePending, 10), 1);
    _renderCreateOtherPending();
});

document.getElementById('createTaskSubmitBtn').addEventListener('click', async function() {
    const title = document.getElementById('createTitle').value;
    const description = document.getElementById('createDescription').value;
    const project = document.getElementById('createProject').value;

    const fd = new FormData();
    // project || null: пустая строка ("— не выбран —") превращается в null, а не
    // отправляется как "" — на create "" резолвилась бы иначе, чем "не выбрано"
    // (см. src/services/tasks.py::_resolve_project — "" зарезервирована для явной
    // очистки при PATCH, здесь такого смысла нет, т.к. задача только создаётся).
    fd.append('data', JSON.stringify({ title, description, project: project || null }));
    if (createPendingSpecFile) fd.append('specification', createPendingSpecFile);
    for (const file of createPendingOtherFiles) fd.append('other_files', file);

    try {
        // Content-Type не выставляется вручную — браузер сам проставит multipart
        // boundary при теле FormData; явный 'application/json' здесь сломал бы запрос.
        const response = await fetchWithAuth('/create-task/', { method: 'POST', body: fd });
        if (!response) return;

        if (response.ok) {
            const task = await response.json();
            if (task.file_upload_errors) {
                for (const [name, msg] of Object.entries(task.file_upload_errors)) {
                    showToast(`«${name}»: ${msg}`, 'warning');
                }
            }
            // Сообщение "Вы: Создана задача: ..." теперь приходит через сам живой
            // WS-broadcast (task_created больше не исключает актора, см.
            // services/tasks.py::create_task) — отдельное локальное эхо здесь
            // убрано, чтобы не дублировать одно и то же сообщение дважды.
            closeModal('createTaskModal'); // без confirm — данные уже успешно отправлены
            loadTasks();
        } else {
            const error = await response.json();
            const msg = Array.isArray(error.detail)
                ? error.detail.map(e => e.msg).join('; ')
                : (error.detail || 'Ошибка создания задачи');
            // Модалка остаётся открытой: пользователь не теряет введённые данные/файлы.
            showToast(msg, 'warning');
        }
    } catch (error) {
        console.error('Error:', error);
        showToast('Не удалось создать задачу', 'warning');
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
        const countBadge = task.subtask_count != null
            ? `<span class="subtask-count">${subtaskLabel(task.subtask_count)}</span>`
            : '';
        const syncBadge = syncStatusTag(task.sync_status);
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
                    ${countBadge}
                    ${syncBadge}
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

// Автообновление, пока у какой-либо задачи sync_status = 'pending' (см. createSyncPoller
// в common.js). isPoll — вызов из самого опроса: ошибки в нём не показываются alert'ом.
const syncPoller = createSyncPoller(() => loadTasks(currentPage, true));

async function loadTasks(page = 1, isPoll = false) {
    currentPage = page;
    // skip — SQL OFFSET: сколько строк пропустить с начала таблицы.
    // Формула (page - 1) * TASKS_PAGE_SIZE переводит номер страницы (с 1) в смещение (с 0):
    //   page=1 → skip=0  → OFFSET 0  LIMIT 5 (строки 1–5)
    //   page=2 → skip=5  → OFFSET 5  LIMIT 5 (строки 6–10)
    //   page=3 → skip=10 → OFFSET 10 LIMIT 5 (строки 11–15)
    const skip = (page - 1) * TASKS_PAGE_SIZE;

    try {
        const response = await fetchWithAuth(`/tasks/?skip=${skip}&limit=${TASKS_PAGE_SIZE}`);
        if (!response) return;

        if (response.ok) {
            const tasks = await response.json();
            // X-Total-Count — нестандартный заголовок; бэкенд пишет в него результат
            // отдельного SELECT COUNT(*) без LIMIT/OFFSET. Клиент использует его для
            // вычисления количества страниц: нельзя определить totalPages только по длине
            // тела ответа, потому что последняя страница может содержать меньше TASKS_PAGE_SIZE записей.
            // Fallback tasks.length применяется если заголовок отсутствует (например, в тестах):
            // totalPages будет равен 1, что технически неверно, но не приведёт к ошибке.
            const totalHeader = response.headers.get('X-Total-Count');
            const total = totalHeader !== null ? parseInt(totalHeader, 10) : tasks.length;
            totalPages = Math.max(1, Math.ceil(total / TASKS_PAGE_SIZE));
            displayTasks(tasks);
            updatePagination();
            syncPoller(tasks, isPoll);
        } else if (!isPoll) {
            const error = await response.json();
            alert(`Error loading tasks: ${error.detail}`);
        }
    } catch (error) {
        console.error('Error:', error);
        if (!isPoll) alert('Failed to load tasks');
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
        const countBadge = task.subtask_count != null
            ? `<span class="subtask-count">${subtaskLabel(task.subtask_count)}</span>`
            : '';
        const syncBadge = syncStatusTag(task.sync_status);
        const projectBadge = task.project
            ? `<span class="subtask-count">${escapeHtml(task.project)}</span>`
            : '';
        const shortDesc = task.description.length > 20
            ? escapeHtml(task.description.slice(0, 20)) + '…'
            : escapeHtml(task.description);
        taskItem.innerHTML = `
            <div class="task-header">
                <div class="task-title-row">
                    <div class="task-title">${escapeHtml(task.title)}</div>
                    ${countBadge}
                    ${syncBadge}
                    ${projectBadge}
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

async function updateTask(id, title, description, completed, project) {
    try {
        const response = await fetchWithAuth(`/tasks/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, description, completed, project }),
        });
        if (!response) return;

        if (response.ok) {
            // "Вы: Обновлена задача: ..." приходит через живой WS-broadcast
            // (task_updated актора больше не исключает) — см. createTaskSubmitBtn выше.
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
            // "Вы: Удалена задача: ..." приходит через живой WS-broadcast
            // (task_deleted актора больше не исключает) — см. createTaskSubmitBtn выше.
            //
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

// Уведомление после серверного редиректа с 404-страницы задачи/подзадачи
// (src/errors_handlers.py): ?notice=<ключ>. Тексты живут здесь, в URL — только ключ.
const REDIRECT_NOTICES = {
    task_not_found: 'Задача не найдена — возможно, она была удалена',
    subtask_not_found: 'Подзадача не найдена — возможно, она была удалена',
    not_found: 'Страница не найдена — возможно, запись была удалена',
};

function showRedirectNotice() {
    const params = new URLSearchParams(window.location.search);
    const message = REDIRECT_NOTICES[params.get('notice')];
    if (!message) return;
    showToast(message, 'warning');
    // Убираем параметр из адресной строки — иначе тост повторится при перезагрузке.
    params.delete('notice');
    const query = params.toString();
    history.replaceState(null, '', window.location.pathname + (query ? `?${query}` : ''));
}

// ── Init ──────────────────────────────────────────────────────────────────────

window.addEventListener('load', function() {
    // 'load' (не 'DOMContentLoaded') гарантирует полную загрузку страницы,
    // включая CSS и изображения; все getElementById вернут не null.
    showRedirectNotice();
    loadInitialChatHistory();
    // connectWebSocket вызывает loadTasks(1) в обработчике socket.onopen.
    // Если WS не подключится, список задач не загрузится — намеренно:
    // без WS real-time обновления не работают, UI был бы частично функционален.
    connectWebSocket();

    document.getElementById('sendBtn').addEventListener('click', sendMessage);
    document.getElementById('searchBtn').addEventListener('click', searchTasksByTitle);
    document.getElementById('saveEditBtn').addEventListener('click', submitEdit);
    document.getElementById('cancelEditBtn').addEventListener('click', function() { requestCloseModal('editModal'); });

    const createTitleInput = document.getElementById('createTitle');
    const createCounter    = document.getElementById('createTitleCounter');
    const editTitleInput   = document.getElementById('editTitle');
    const editCounter      = document.getElementById('editTitleCounter');

    createTitleInput.addEventListener('input', () => _updateCharCounter(createTitleInput, createCounter, TITLE_MAX_LENGTH));
    editTitleInput.addEventListener('input', () => _updateCharCounter(editTitleInput, editCounter, TITLE_MAX_LENGTH));

    document.getElementById('messageInput').addEventListener('keypress', function(event) {
        if (event.key === 'Enter') sendMessage();
    });
    document.getElementById('taskTitleSearchInput').addEventListener('keypress', function(event) {
        if (event.key === 'Enter') searchTasksByTitle();
    });
});
