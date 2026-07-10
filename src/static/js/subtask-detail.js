const subtaskId = parseInt(document.getElementById('subtaskId').value, 10);
const taskId    = parseInt(document.getElementById('taskId').value, 10);
let currentSubtask = null;

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

function renderSubtask(s) {
    currentSubtask = s;
    document.getElementById('viewTitle').textContent = s.title;
    document.getElementById('viewDescription').textContent = s.description || '—';

    const statusEl = document.getElementById('viewStatus');
    statusEl.innerHTML = `<span class="status-badge ${s.completed ? 'status-completed' : 'status-pending'}">${s.completed ? 'Выполнена' : 'В работе'}</span>`;

    const crmEl = document.getElementById('viewCrm');
    if (s.crm_subtask_id != null) {
        crmEl.innerHTML = `<span class="crm-badge-ok">Синхронизирована (ID ${s.crm_subtask_id})</span>`;
    } else {
        crmEl.innerHTML = '<span class="crm-badge">Отсутствует в CRM</span>';
    }
}

async function loadSubtask() {
    try {
        const resp = await fetchWithAuth(`/subtasks/${subtaskId}`);
        if (!resp) return;
        if (resp.ok) {
            renderSubtask(await resp.json());
        } else {
            alert('Подзадача не найдена');
            window.location.href = `/subtask-board/${taskId}`;
        }
    } catch (e) {
        alert('Не удалось загрузить подзадачу');
    }
}

function openEditForm() {
    if (!currentSubtask) return;
    const titleEl = document.getElementById('editTitle');
    titleEl.value = currentSubtask.title;
    document.getElementById('editDescription').value = currentSubtask.description;
    document.getElementById('editCompleted').checked = currentSubtask.completed;
    _updateCharCounter(titleEl, document.getElementById('editTitleCounter'), 100);
    document.getElementById('editForm').classList.add('visible');
    document.getElementById('editToggleBtn').style.display = 'none';
}

function closeEditForm() {
    document.getElementById('editForm').classList.remove('visible');
    document.getElementById('editToggleBtn').style.display = '';
}

async function saveSubtask() {
    const title       = document.getElementById('editTitle').value.trim();
    const description = document.getElementById('editDescription').value;
    const completed   = document.getElementById('editCompleted').checked;
    if (!title) { alert('Название не может быть пустым'); return; }

    try {
        const resp = await fetchWithAuth(`/subtasks/${subtaskId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, description, completed }),
        });
        if (!resp) return;
        if (resp.ok) {
            const s = await resp.json();
            if (s.crm_synced === false) showToast('Сохранено без синхронизации с CRM', 'warning');
            renderSubtask(s);
            closeEditForm();
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
        alert('Не удалось сохранить подзадачу');
    }
}

async function deleteSubtask() {
    if (!confirm('Удалить подзадачу? Действие необратимо.')) return;
    try {
        const resp = await fetchWithAuth(`/delete-subtask/${subtaskId}`, { method: 'DELETE' });
        if (!resp) return;
        if (resp.ok) {
            const s = await resp.json();
            if (s.crm_synced === false) showToast('Удалено без синхронизации с CRM', 'warning');
            window.location.href = `/subtask-board/${taskId}`;
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
    loadSubtask();

    document.getElementById('editToggleBtn').addEventListener('click', openEditForm);
    document.getElementById('cancelBtn').addEventListener('click', closeEditForm);
    document.getElementById('saveBtn').addEventListener('click', saveSubtask);
    document.getElementById('deleteBtn').addEventListener('click', deleteSubtask);

    const editTitleInput   = document.getElementById('editTitle');
    const editTitleCounter = document.getElementById('editTitleCounter');
    editTitleInput.addEventListener('input', () => _updateCharCounter(editTitleInput, editTitleCounter, 100));
});
