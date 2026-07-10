const taskId = parseInt(document.getElementById('taskId').value, 10);
let currentTask = null;

function escapeHtml(v) {
    const d = document.createElement('div');
    d.textContent = String(v);
    return d.innerHTML;
}

function _updateCharCounter(inputEl, counterEl, limit) {
    const len = inputEl.value.length;
    counterEl.textContent = len;
    const w = counterEl.closest('.char-counter');
    w.classList.toggle('limit-near', len >= limit * 0.9 && len < limit);
    w.classList.toggle('limit-reached', len >= limit);
}

function showToast(message, type = 'info') {
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
    requestAnimationFrame(() => n.classList.add('notif-show'));
    setTimeout(() => { n.classList.remove('notif-show'); setTimeout(() => n.remove(), 300); }, 4000);
}

let _refreshPromise = null;

async function fetchWithAuth(url, options = {}) {
    const opts = { credentials: 'include', ...options };
    let resp = await fetch(url, opts);
    if (resp.status === 401) {
        if (!_refreshPromise) {
            _refreshPromise = fetch('/auth/access-token', { method: 'POST', credentials: 'include' })
                .finally(() => { _refreshPromise = null; });
        }
        const r = await _refreshPromise;
        if (!r.ok) { window.location.href = '/'; return null; }
        resp = await fetch(url, opts);
    }
    return resp;
}

function subtaskLabel(n) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return `${n} подзадача`;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return `${n} подзадачи`;
    return `${n} подзадач`;
}

function renderTask(t) {
    currentTask = t;
    document.getElementById('viewTitle').textContent = t.title;
    document.getElementById('viewDescription').textContent = t.description || '—';

    const statusEl = document.getElementById('viewStatus');
    statusEl.innerHTML = `<span class="status-badge ${t.completed ? 'status-completed' : 'status-pending'}">${t.completed ? 'Выполнена' : 'В работе'}</span>`;

    const countEl = document.getElementById('viewSubtaskCount');
    const count = t.subtask_count != null ? t.subtask_count : 0;
    countEl.innerHTML = `<span class="subtask-count">${subtaskLabel(count)}</span>`;

    const crmEl = document.getElementById('viewCrm');
    if (t.crm_task_id != null) {
        crmEl.innerHTML = `<span class="crm-badge-ok">Синхронизирована (ID ${t.crm_task_id})</span>`;
    } else {
        crmEl.innerHTML = '<span class="crm-badge">Отсутствует в CRM</span>';
    }

    document.getElementById('subtasksLink').href = `/subtask-board/${taskId}`;
}

async function loadTask() {
    try {
        const resp = await fetchWithAuth(`/tasks/${taskId}`);
        if (!resp) return;
        if (resp.ok) {
            renderTask(await resp.json());
        } else {
            alert('Задача не найдена');
            window.location.href = '/task-board';
        }
    } catch (e) {
        alert('Не удалось загрузить задачу');
    }
}

function openEditForm() {
    if (!currentTask) return;
    const titleEl = document.getElementById('editTitle');
    titleEl.value = currentTask.title;
    document.getElementById('editDescription').value = currentTask.description;
    document.getElementById('editCompleted').checked = currentTask.completed;
    _updateCharCounter(titleEl, document.getElementById('editTitleCounter'), 100);
    document.getElementById('editForm').classList.add('visible');
    document.getElementById('editToggleBtn').style.display = 'none';
}

function closeEditForm() {
    document.getElementById('editForm').classList.remove('visible');
    document.getElementById('editToggleBtn').style.display = '';
}

async function saveTask() {
    const title       = document.getElementById('editTitle').value.trim();
    const description = document.getElementById('editDescription').value;
    const completed   = document.getElementById('editCompleted').checked;
    if (!title) { alert('Название не может быть пустым'); return; }

    try {
        const resp = await fetchWithAuth(`/tasks/${taskId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, description, completed }),
        });
        if (!resp) return;
        if (resp.ok) {
            const t = await resp.json();
            if (t.crm_synced === false) showToast('Сохранено без синхронизации с CRM', 'warning');
            renderTask(t);
            closeEditForm();
        } else if (resp.status === 422) {
            const err = await resp.json();
            const msg = Array.isArray(err.detail) ? err.detail.map(e => e.msg).join('; ') : err.detail;
            alert(`Ошибка валидации: ${msg}`);
        } else {
            const err = await resp.json();
            alert(`Ошибка: ${err.detail}`);
        }
    } catch (e) {
        alert('Не удалось сохранить задачу');
    }
}

async function deleteTask() {
    if (!confirm('Удалить задачу? Все подзадачи будут удалены вместе с ней.')) return;
    try {
        const resp = await fetchWithAuth(`/delete-task/${taskId}`, { method: 'DELETE' });
        if (!resp) return;
        if (resp.ok) {
            const t = await resp.json();
            if (t.crm_synced === false) showToast('Удалено без синхронизации с CRM', 'warning');
            window.location.href = '/task-board';
        } else {
            const err = await resp.json();
            alert(`Ошибка: ${err.detail}`);
        }
    } catch (e) {
        alert('Не удалось удалить задачу');
    }
}

window.addEventListener('load', function() {
    loadTask();

    document.getElementById('editToggleBtn').addEventListener('click', openEditForm);
    document.getElementById('cancelBtn').addEventListener('click', closeEditForm);
    document.getElementById('saveBtn').addEventListener('click', saveTask);
    document.getElementById('deleteBtn').addEventListener('click', deleteTask);

    const editTitleInput   = document.getElementById('editTitle');
    const editTitleCounter = document.getElementById('editTitleCounter');
    editTitleInput.addEventListener('input', () => _updateCharCounter(editTitleInput, editTitleCounter, 100));
});
