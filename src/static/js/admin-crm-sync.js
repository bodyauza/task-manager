// Admin-only страница статуса CRM-синхронизации (src/routers/admin.py::
// admin_crm_sync_page, GET /admin/crm-sync). Единственное место, где видны
// crm_task_id/crm_subtask_id и история попыток; сам sync_status обычные
// пользователи видят бейджем на task-board/subtask-board (TaskResponse/
// SubtaskResponse.sync_status). Данные — из двух JSON-эндпоинтов, требующих
// require_role("admin") на сервере; сама эта страница уже гейтится тем же
// require_role при рендере (403 до отдачи HTML), поэтому JS-часть не
// дублирует проверку роли — только показывает ошибку, если что-то пошло не так.

// SYNC_STATUS_LABELS — общая константа из common.js.

function syncStatusBadge(status) {
    const label = SYNC_STATUS_LABELS[status] || status;
    return `<span class="sync-badge sync-badge-${escapeHtml(status)}">${escapeHtml(label)}</span>`;
}

function formatDateTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'medium' });
}

function renderTasksTable(tasks) {
    const container = document.getElementById('tasksSyncTable');
    if (!tasks.length) {
        container.innerHTML = '<p class="empty-note">Задач нет.</p>';
        return;
    }
    const rows = tasks.map(t => `
        <tr>
            <td>${t.id}</td>
            <td>${escapeHtml(t.title)}</td>
            <td>${escapeHtml(t.owner_email)}</td>
            <td>${t.crm_task_id ?? '—'}</td>
            <td>${syncStatusBadge(t.sync_status)}</td>
            <td>${t.last_operation ? escapeHtml(t.last_operation) : '—'}</td>
            <td>${t.last_outbox_status ? escapeHtml(t.last_outbox_status) : '—'}</td>
            <td>${t.last_attempts ?? '—'}</td>
            <td>${formatDateTime(t.last_updated_at)}</td>
        </tr>
    `).join('');
    container.innerHTML = `
        <table class="sync-table">
            <thead>
                <tr>
                    <th>ID</th><th>Название</th><th>Владелец</th><th>CRM ID</th>
                    <th>Статус</th><th>Посл. операция</th><th>Посл. попытка</th>
                    <th>Попыток</th><th>Обновлено</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

function renderSubtasksTable(subtasks) {
    const container = document.getElementById('subtasksSyncTable');
    if (!subtasks.length) {
        container.innerHTML = '<p class="empty-note">Подзадач нет.</p>';
        return;
    }
    const rows = subtasks.map(s => `
        <tr>
            <td>${s.id}</td>
            <td>${escapeHtml(s.title)}</td>
            <td><a href="/task/${s.task_id}">${escapeHtml(s.task_title)}</a></td>
            <td>${s.crm_subtask_id ?? '—'}</td>
            <td>${syncStatusBadge(s.sync_status)}</td>
            <td>${s.last_operation ? escapeHtml(s.last_operation) : '—'}</td>
            <td>${s.last_outbox_status ? escapeHtml(s.last_outbox_status) : '—'}</td>
            <td>${s.last_attempts ?? '—'}</td>
            <td>${formatDateTime(s.last_updated_at)}</td>
        </tr>
    `).join('');
    container.innerHTML = `
        <table class="sync-table">
            <thead>
                <tr>
                    <th>ID</th><th>Название</th><th>Задача</th><th>CRM ID</th>
                    <th>Статус</th><th>Посл. операция</th><th>Посл. попытка</th>
                    <th>Попыток</th><th>Обновлено</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

async function loadSyncStatus() {
    try {
        const [tasksResp, subtasksResp] = await Promise.all([
            fetchWithAuth('/admin/crm-sync-status/tasks'),
            fetchWithAuth('/admin/crm-sync-status/subtasks'),
        ]);
        if (!tasksResp || !subtasksResp) return;
        if (!tasksResp.ok || !subtasksResp.ok) {
            showToast('Не удалось загрузить статус синхронизации', 'warning');
            return;
        }
        renderTasksTable(await tasksResp.json());
        renderSubtasksTable(await subtasksResp.json());
    } catch (e) {
        showToast('Не удалось загрузить статус синхронизации', 'warning');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadSyncStatus();
    document.getElementById('refreshSyncBtn').addEventListener('click', loadSyncStatus);
});
