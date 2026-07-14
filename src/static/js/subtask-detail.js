const subtaskId = parseInt(document.getElementById('subtaskId').value, 10);
const taskId    = parseInt(document.getElementById('taskId').value, 10);
const userId    = document.getElementById('userId').value;
let currentSubtask = null;
let socket;

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

// ── Файлы: рендер и загрузка ─────────────────────────────────────────────────

function renderSpecification(relPath) {
    // relPath: "subtasks/7/specification/a1b2_tz.pdf" или null.
    // URL: /uploads/{relPath} — StaticFiles mount отдаёт файл из src/static/uploads/.
    const block = document.getElementById('specCurrent');
    if (relPath) {
        const link = document.getElementById('specLink');
        link.href        = `/uploads/${relPath}`;        // путь для скачивания/открытия
        link.textContent = relPath.split('/').pop();     // только имя файла для отображения
        block.style.display = 'flex';                    // показываем блок с файлом
    } else {
        block.style.display = 'none';                    // скрываем если файла нет
    }
}

function renderOtherFiles(paths) {
    // paths: массив rel-путей ["subtasks/7/other/c3d4_doc.pdf", ...].
    const ul    = document.getElementById('otherFilesList');
    const count = document.getElementById('otherCount');
    ul.innerHTML = '';                                   // очищаем список перед перерисовкой
    count.textContent = `(${paths.length} / 10)`;       // счётчик (текущее / лимит)
    for (const relPath of paths) {
        const name = relPath.split('/').pop();           // имя файла с UUID-префиксом
        const li   = document.createElement('li');
        const a    = document.createElement('a');
        a.className   = 'file-link';
        a.href        = `/uploads/${relPath}`;
        a.target      = '_blank';
        a.textContent = name;
        const btn = document.createElement('button');
        btn.className        = 'btn-delete-file';
        btn.dataset.filename = name;
        btn.textContent      = '✕';
        li.appendChild(a);
        li.appendChild(btn);
        ul.appendChild(li);
    }
}

document.addEventListener('DOMContentLoaded', function() {
    // Загрузка файла ТЗ: POST /subtasks/{id}/specification с multipart/form-data
    document.getElementById('specUploadBtn').addEventListener('click', async () => {
        const input = document.getElementById('specInput');
        if (!input.files.length) { showToast('Выберите файл', 'warning'); return; }
        const fd = new FormData();
        fd.append('file', input.files[0]);    // поле "file" — FastAPI File(...)
        const resp = await fetchWithAuth(`/subtasks/${subtaskId}/specification`, { method: 'POST', body: fd });
        if (!resp) return;
        if (resp.ok) {
            const data = await resp.json();
            renderSpecification(data.specification_path);
            input.value = '';                 // сбрасываем выбор файла в input
            showToast('Файл ТЗ загружен', 'info');
        } else {
            const err = await resp.json();
            showToast(err.detail || 'Ошибка загрузки', 'warning');
        }
    });

    // Удаление файла ТЗ: DELETE /subtasks/{id}/specification
    document.getElementById('specDeleteBtn').addEventListener('click', async () => {
        if (!confirm('Удалить файл ТЗ?')) return;
        const resp = await fetchWithAuth(`/subtasks/${subtaskId}/specification`, { method: 'DELETE' });
        if (!resp) return;
        if (resp.ok) {
            renderSpecification(null);
            showToast('Файл ТЗ удалён', 'info');
        } else {
            const err = await resp.json();
            showToast(err.detail || 'Ошибка удаления', 'warning');
        }
    });

    document.getElementById('otherFilesList').addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-delete-file');
        if (btn) deleteOtherFile(btn.dataset.filename);
    });

    // Загрузка иных документов: POST /subtasks/{id}/files
    document.getElementById('otherUploadBtn').addEventListener('click', async () => {
        const input = document.getElementById('otherInput');
        if (!input.files.length) { showToast('Выберите файлы', 'warning'); return; }
        const fd = new FormData();
        for (const f of input.files) fd.append('files', f);
        const resp = await fetchWithAuth(`/subtasks/${subtaskId}/files`, { method: 'POST', body: fd });
        if (!resp) return;
        if (resp.ok) {
            const data = await resp.json();
            renderOtherFiles(data.other_file_paths);
            const count = input.files.length;
            input.value = '';
            showToast(`Загружено файлов: ${count}`, 'info');
        } else {
            const err = await resp.json();
            showToast(err.detail || 'Ошибка загрузки файлов', 'warning');
        }
    });
});

async function deleteOtherFile(filename) {
    // DELETE /subtasks/{id}/files/{filename} — роутер ищет путь по имени в JSON-списке.
    if (!confirm(`Удалить файл «${filename}»?`)) return;
    const resp = await fetchWithAuth(
        `/subtasks/${subtaskId}/files/${encodeURIComponent(filename)}`, { method: 'DELETE' }
    );
    if (!resp) return;
    if (resp.ok) {
        const data = await resp.json();
        renderOtherFiles(data.other_file_paths);
        showToast('Файл удалён', 'info');
    } else {
        const err = await resp.json();
        showToast(err.detail || 'Ошибка удаления файла', 'warning');
    }
}

// ─────────────────────────────────────────────────────────────────────────────

function renderSubtask(s) {
    currentSubtask = s;
    document.getElementById('viewTitle').textContent = s.title;
    document.getElementById('viewDescription').textContent = s.description || '—';

    // Отрисовываем файлы при каждом вызове renderSubtask (через loadSubtask).
    renderSpecification(s.specification_path || null);
    renderOtherFiles(s.other_file_paths || []);

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

// ── WebSocket ─────────────────────────────────────────────────────────────────
// Симметрично task-detail.js: реагирует на файловые события этой подзадачи.

function connectWebSocket() {
    try {
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        socket = new WebSocket(`${wsProtocol}//${window.location.host}/ws/tasks/${userId}`);

        socket.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'subtask_files_updated' && data.subtask_id === subtaskId) {
                    loadSubtask();  // перечитываем актуальное состояние с сервера
                    showToast(`${data.sender}: файлы подзадачи обновлены`, 'info');
                }
            } catch (e) { /* нераспознанное сообщение — игнорируем */ }
        };

        socket.onclose = function(event) {
            if (event.code === 1008) { window.location.href = '/'; return; }
            setTimeout(connectWebSocket, 3000);   // переподключение при разрыве
        };
    } catch (error) {
        setTimeout(connectWebSocket, 3000);
    }
}

window.addEventListener('load', function() {
    loadSubtask();
    connectWebSocket();

    document.getElementById('editToggleBtn').addEventListener('click', openEditForm);
    document.getElementById('cancelBtn').addEventListener('click', closeEditForm);
    document.getElementById('saveBtn').addEventListener('click', saveSubtask);
    document.getElementById('deleteBtn').addEventListener('click', deleteSubtask);

    const editTitleInput   = document.getElementById('editTitle');
    const editTitleCounter = document.getElementById('editTitleCounter');
    editTitleInput.addEventListener('input', () => _updateCharCounter(editTitleInput, editTitleCounter, 100));
});
