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

// ── Файлы: рендер и загрузка ─────────────────────────────────────────────────

function renderSpecification(relPath) {
    // relPath: "tasks/3/specification/a1b2_tz.pdf" или null.
    // URL: /uploads/{relPath} — отдаётся StaticFiles mount на /uploads.
    const block = document.getElementById('specCurrent');
    if (relPath) {
        const link = document.getElementById('specLink');
        link.href        = `/uploads/${relPath}`;           // StaticFiles mount /uploads
        link.textContent = relPath.split('/').pop();        // только имя файла для отображения
        block.style.display = 'flex';                       // показываем блок с файлом
    } else {
        block.style.display = 'none';                       // скрываем если файла нет
    }
}

function renderOtherFiles(paths) {
    // paths: массив rel-путей, например ["tasks/3/other/c3d4_doc.pdf"].
    const ul    = document.getElementById('otherFilesList');
    const count = document.getElementById('otherCount');
    ul.innerHTML = '';                                      // очищаем список перед перерисовкой
    count.textContent = `(${paths.length} / 10)`;          // счётчик (текущее / лимит)
    for (const relPath of paths) {
        const name = relPath.split('/').pop();              // "a1b2_doc.pdf" из пути
        const li   = document.createElement('li');
        const a    = document.createElement('a');
        a.className   = 'file-link';
        a.href        = `/uploads/${relPath}`;
        a.target      = '_blank';
        a.textContent = name;
        const btn = document.createElement('button');
        btn.className        = 'btn-delete-file';
        btn.dataset.filename = name;  // data-filename вместо onclick: inline handlers блокирует CSP
        btn.textContent      = '✕';
        li.appendChild(a);
        li.appendChild(btn);
        ul.appendChild(li);
    }
}

document.addEventListener('DOMContentLoaded', function() {
    // Загрузка файла ТЗ: POST /tasks/{id}/specification с multipart/form-data
    document.getElementById('specUploadBtn').addEventListener('click', async () => {
        const input = document.getElementById('specInput');
        if (!input.files.length) { showToast('Выберите файл', 'warning'); return; }
        const fd = new FormData();
        fd.append('file', input.files[0]);    // поле "file" — FastAPI File(...)
        const resp = await fetchWithAuth(`/tasks/${taskId}/specification`, { method: 'POST', body: fd });
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

    // Удаление файла ТЗ: DELETE /tasks/{id}/specification
    document.getElementById('specDeleteBtn').addEventListener('click', async () => {
        if (!confirm('Удалить файл ТЗ?')) return;
        const resp = await fetchWithAuth(`/tasks/${taskId}/specification`, { method: 'DELETE' });
        if (!resp) return;
        if (resp.ok) {
            renderSpecification(null);
            showToast('Файл ТЗ удалён', 'info');
        } else {
            const err = await resp.json();
            showToast(err.detail || 'Ошибка удаления', 'warning');
        }
    });

    // Делегирование клика на кнопки удаления файлов — CSP запрещает inline onclick.
    // Слушатель на <ul> перехватывает клики от всех дочерних .btn-delete-file.
    document.getElementById('otherFilesList').addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-delete-file');
        if (btn) deleteOtherFile(btn.dataset.filename);
    });

    // Загрузка иных документов: POST /tasks/{id}/files с несколькими файлами
    document.getElementById('otherUploadBtn').addEventListener('click', async () => {
        const input = document.getElementById('otherInput');
        if (!input.files.length) { showToast('Выберите файлы', 'warning'); return; }
        const fd = new FormData();
        // поле "files" — FastAPI принимает list[UploadFile] = File(...)
        for (const f of input.files) fd.append('files', f);
        const resp = await fetchWithAuth(`/tasks/${taskId}/files`, { method: 'POST', body: fd });
        if (!resp) return;
        if (resp.ok) {
            const data = await resp.json();
            renderOtherFiles(data.other_file_paths);
            const count = input.files.length; // сохранить до сброса: input.value='' обнуляет FileList
            input.value = '';
            showToast(`Загружено файлов: ${count}`, 'info');
        } else {
            const err = await resp.json();
            showToast(err.detail || 'Ошибка загрузки файлов', 'warning');
        }
    });
});

async function deleteOtherFile(filename) {
    // filename: имя файла с UUID-префиксом, например "a1b2c3d4_doc.pdf".
    // DELETE /tasks/{id}/files/{filename} — роутер ищет путь по имени в списке.
    if (!confirm(`Удалить файл «${filename}»?`)) return;
    const resp = await fetchWithAuth(
        `/tasks/${taskId}/files/${encodeURIComponent(filename)}`, { method: 'DELETE' }
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

function renderTask(t) {
    currentTask = t;
    document.getElementById('viewTitle').textContent = t.title;
    document.getElementById('viewDescription').textContent = t.description || '—';

    // Отрисовываем файлы: renderSpecification и renderOtherFiles вызываются каждый раз
    // при loadTask() — это гарантирует актуальность состояния после перезагрузки страницы.
    renderSpecification(t.specification_path || null);
    renderOtherFiles(t.other_file_paths || []);

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
