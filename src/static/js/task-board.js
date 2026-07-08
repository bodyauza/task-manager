const userId = document.getElementById('userId').value;

let socket;
const messagesDiv = document.getElementById('messages');
const statusDiv   = document.getElementById('status');
let currentPage   = 1;
const tasksPerPage = 5;
let totalPages    = 1;
const CHAT_HISTORY_KEY = 'websocket_chat_history';

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = String(value);
    return div.innerHTML;
}

function _updateCharCounter(inputEl, counterEl, limit) {
    const len = inputEl.value.length;
    counterEl.textContent = len;
    const wrapper = counterEl.closest('.char-counter');
    wrapper.classList.toggle('limit-near', len >= limit * 0.9 && len < limit);
    wrapper.classList.toggle('limit-reached', len >= limit);
}

function _getToastContainer() {
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
    requestAnimationFrame(() => { notif.classList.add('notif-show'); });
    setTimeout(() => {
        notif.classList.remove('notif-show');
        setTimeout(() => notif.remove(), 300);
    }, 4000);
}

let currentTasks = [];

// ── Делегированный обработчик для кнопок, генерируемых динамически ──────────
// Заменяет inline onclick="openEditModal(...)" и onclick="deleteTask(...)"
// в innerHTML-шаблонах displayTasks/displaySearchResults.
// data-action="edit"   + data-id  → openEditModal
// data-action="delete" + data-id  → deleteTask
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
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }
    } catch (e) {
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
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        socket = new WebSocket(`${wsProtocol}//${window.location.host}/ws/tasks/${userId}`);

        socket.onopen = function() {
            statusDiv.textContent = 'Status: Connected';
            statusDiv.className = 'connection-status status-connected';
            loadTasks(1);
        };

        socket.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'chat') {
                    addMessage(`${data.sender}: ${data.text}`);
                } else if (data.type === 'task_created') {
                    addMessage(`${data.sender}: New task created: ${data.title}`);
                    loadTasks(currentPage);
                } else if (data.type === 'task_updated') {
                    addMessage(`${data.sender}: Task "${data.title}" updated`);
                    loadTasks(currentPage);
                } else if (data.type === 'task_deleted') {
                    addMessage(`${data.sender}: Task "${data.title}" deleted`);
                    loadTasks(currentPage);
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
            // 1008 = Policy Violation (auth failure); повтор вызовет бесконечный цикл
            if (event.code === 1008) {
                window.location.href = '/';
                return;
            }
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

// Глобальный промис обновления токена: если несколько параллельных запросов
// одновременно получат 401, refresh выполнится один раз, остальные ждут его.
let _refreshPromise = null;

async function fetchWithAuth(url, options = {}) {
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
            window.location.href = '/';
            return null;
        }
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
        taskItem.innerHTML = `
            <div class="task-header">
                <div class="task-title-row">
                    <div class="task-title">${escapeHtml(task.title)}</div>
                    ${crmBadge}
                </div>
                <span class="task-status ${task.completed ? 'status-completed' : 'status-pending'}">
                    ${task.completed ? 'Completed' : 'Pending'}
                </span>
            </div>
            <div class="task-description">${escapeHtml(task.description)}</div>
            <div class="task-actions">
                <button class="update" data-action="edit" data-id="${task.id}">Update</button>
                <button class="delete" data-action="delete" data-id="${task.id}">Delete</button>
            </div>
        `;
        resultList.appendChild(taskItem);
    });

    container.appendChild(resultList);
}

async function loadTasks(page = 1) {
    currentPage = page;
    const skip = (page - 1) * tasksPerPage;

    try {
        const response = await fetchWithAuth(`/tasks/?skip=${skip}&limit=${tasksPerPage}`);
        if (!response) return;

        if (response.ok) {
            const tasks = await response.json();
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
        taskItem.innerHTML = `
            <div class="task-header">
                <div class="task-title-row">
                    <div class="task-title">${escapeHtml(task.title)}</div>
                    ${crmBadge}
                </div>
                <span class="task-status ${task.completed ? 'status-completed' : 'status-pending'}">
                    ${task.completed ? 'Completed' : 'Pending'}
                </span>
            </div>
            <div class="task-description">${escapeHtml(task.description)}</div>
            <div class="task-actions">
                <button class="update" data-action="edit" data-id="${task.id}">Update</button>
                <button class="delete" data-action="delete" data-id="${task.id}">Delete</button>
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
        // addEventListener вместо pageButton.onclick = () => loadTasks(i)
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
            loadTasks(currentPage);
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
    loadChatHistory();
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

    window.addEventListener('beforeunload', saveChatHistory);
});
