const taskId = parseInt(document.getElementById('taskId').value, 10);
const userId = document.getElementById('userId').value;
let currentTask = null;
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
    // URL: /uploads/{relPath} — аутентифицированный роутер (src/routers/uploads.py),
    // требует access_token; не StaticFiles mount.
    const block = document.getElementById('specCurrent');
    if (relPath) {
        const link = document.getElementById('specLink');
        link.href        = `/uploads/${relPath}`;           // routers/uploads.py, требует access_token
        link.textContent = relPath.split('/').pop();        // только имя файла для отображения
        block.style.display = 'flex';                       // показываем блок с файлом
    } else {
        block.style.display = 'none';                       // скрываем если файла нет
    }
}

let otherUploadInProgress = false;  // блокирует удаление задачи, пока идёт отправка файлов на сервер

// specDeleteInProgress: булев флаг, а не Set — на странице всего одна кнопка specDeleteBtn
// (поле "Техническое задание" одиночное, один файл на задачу), поэтому достаточно одного
// флага, чтобы заблокировать повторный клик, пока предыдущий DELETE ещё не получил ответ.
let specDeleteInProgress = false;

// specUploadInProgress: тот же принцип, что и specDeleteInProgress выше, но для кнопки
// "Загрузить" — булев флаг, а не Set, т.к. specUploadBtn на странице тоже одна.
let specUploadInProgress = false;

// otherFilesBeingDeleted: имена файлов "Иных документов", на которые уже отправлен DELETE.
// Set, а не булев флаг (как specDeleteInProgress выше) — потому что кнопок ✕ здесь может быть
// до 10 одновременно, по одной на файл, и удаление файла A не должно блокировать параллельное
// удаление файла B: у каждого файла своя независимая блокировка по имени. Повторный клик по
// ТОЙ ЖЕ кнопке, пока её DELETE ещё не ответил, находит имя файла в Set и показывает тот же
// toast «Файл уже удаляется», что и specDeleteInProgress — без второго одновременного запроса
// на удаление уже удаляемого файла (иначе второй DELETE получил бы 404 "не найден").
const otherFilesBeingDeleted = new Set();

// pendingOtherFiles: файлы, выбранные пользователем, но ещё не отправленные — накапливаются
// на фронтенде. Нужно, потому что нативный <input type="file"> при повторном открытии диалога
// ЗАМЕНЯЕТ свой FileList целиком, а не дополняет его — без этого массива второй выбранный файл
// стирал бы первый.
let pendingOtherFiles = [];

// uploadedOtherCount: обновляется в renderOtherFiles; нужен отдельно от pendingOtherFiles.length,
// чтобы счётчик "(N / 10)" мог показывать сумму уже загруженных и ещё не отправленных файлов.
let uploadedOtherCount = 0;

// Те же ограничения, что и на сервере (src/utils/file_utils.py: MAX_FILE_SIZE, ALLOWED) —
// продублированы здесь только для мгновенной обратной связи на клиенте. Сервер остаётся
// источником истины и всё равно перепроверит и размер, и MIME-тип по сигнатуре байтов.
const OTHER_FILES_MAX_SIZE = 100 * 1024 * 1024;  // 100 МБ
const OTHER_FILES_ALLOWED_EXT = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.jpg', '.jpeg', '.png', '.txt'];

function validateOtherFileClientSide(file) {
    // Возвращает текст ошибки или null, если файл прошёл клиентскую пре-проверку.
    const dotIndex = file.name.lastIndexOf('.');
    const ext = dotIndex >= 0 ? file.name.slice(dotIndex).toLowerCase() : '';
    if (!OTHER_FILES_ALLOWED_EXT.includes(ext)) {
        return `расширение «${ext || '(нет)'}» не поддерживается`;
    }
    if (file.size > OTHER_FILES_MAX_SIZE) {
        return `размер превышает лимит ${OTHER_FILES_MAX_SIZE / (1024 * 1024)} МБ`;
    }
    return null;
}

function updateOtherCountLabel() {
    const el = document.getElementById('otherCount');
    const pending = pendingOtherFiles.length;
    // Пока ничего не поставлено в очередь — обычный формат "(N / 10)", чтобы не отвлекать
    // пользователя показом "+0" на пустом месте.
    el.textContent = pending > 0
        ? `(${uploadedOtherCount} / 10, +${pending} в очереди)`
        : `(${uploadedOtherCount} / 10)`;
}

function renderPendingOtherFiles() {
    const ul      = document.getElementById('otherPendingList');
    const caption = document.getElementById('otherPendingCaption');
    caption.style.display = pendingOtherFiles.length ? '' : 'none';
    ul.innerHTML = '';
    pendingOtherFiles.forEach((file, index) => {
        const li = document.createElement('li');
        const nameSpan = document.createElement('span');
        // .file-pending-name (серый курсив), а не .file-link (синий, как у кликабельных файлов) —
        // этот файл ещё не сохранён на сервере и не открывается по клику.
        nameSpan.className = 'file-pending-name';
        nameSpan.textContent = file.name;
        const btn = document.createElement('button');
        btn.className     = 'btn-delete-file';
        btn.dataset.index = index;  // индекс в pendingOtherFiles — используется делегированием клика
        btn.textContent   = '✕';
        btn.title             = `Убрать «${file.name}» из списка на загрузку`;
        btn.setAttribute('aria-label', `Убрать «${file.name}» из списка на загрузку`);
        li.appendChild(nameSpan);
        li.appendChild(btn);
        ul.appendChild(li);
    });
    updateOtherCountLabel();
}

function renderOtherFiles(paths) {
    // paths: массив rel-путей, например ["tasks/3/other/c3d4_doc.pdf"].
    const ul = document.getElementById('otherFilesList');
    ul.innerHTML = '';                                      // очищаем список перед перерисовкой
    uploadedOtherCount = paths.length;
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
        btn.title             = `Удалить файл «${name}»`;
        btn.setAttribute('aria-label', `Удалить файл «${name}»`);
        li.appendChild(a);
        li.appendChild(btn);
        ul.appendChild(li);
    }
    updateOtherCountLabel();
}

document.addEventListener('DOMContentLoaded', function() {
    // Загрузка файла ТЗ: POST /tasks/{id}/specification с multipart/form-data
    document.getElementById('specUploadBtn').addEventListener('click', async () => {
        // Блокировка повторного клика: пока предыдущий POST ещё не ответил, specUploadInProgress
        // остаётся true — второй клик просто показывает toast и не шлёт второй одновременный
        // запрос на загрузку (без этого второй запрос впустую перезаписал бы тот же файл).
        if (specUploadInProgress) { showToast('Файл уже загружается, подождите', 'warning'); return; }
        const input = document.getElementById('specInput');
        if (!input.files.length) { showToast('Выберите файл', 'warning'); return; }
        specUploadInProgress = true;
        try {
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
        } finally {
            // finally, а не только в ветке успеха: флаг обязан сброситься при любом исходе —
            // успех, ошибка сервера и сетевой сбой — иначе кнопка осталась бы заблокированной навсегда.
            specUploadInProgress = false;
        }
    });

    // Удаление файла ТЗ: DELETE /tasks/{id}/specification
    document.getElementById('specDeleteBtn').addEventListener('click', async () => {
        // Блокировка повторного клика: пока предыдущий DELETE ещё не ответил, specDeleteInProgress
        // остаётся true — второй клик (например, случайный двойной клик) просто показывает toast
        // и не шлёт второй одновременный запрос на удаление уже удаляемого файла.
        if (specDeleteInProgress) { showToast('Файл уже удаляется', 'warning'); return; }
        if (!confirm('Удалить файл ТЗ?')) return;
        specDeleteInProgress = true;
        try {
            const resp = await fetchWithAuth(`/tasks/${taskId}/specification`, { method: 'DELETE' });
            if (!resp) return;
            if (resp.ok) {
                renderSpecification(null);
                showToast('Файл ТЗ удалён', 'info');
            } else {
                const err = await resp.json();
                showToast(err.detail || 'Ошибка удаления', 'warning');
            }
        } finally {
            // finally, а не только в ветке успеха: флаг обязан сброситься при любом исходе —
            // успех, ошибка сервера (resp не ok) и сетевой сбой (исключение внутри try) — иначе
            // после первой неудачной попытки кнопка осталась бы заблокированной навсегда.
            specDeleteInProgress = false;
        }
    });

    // Делегирование клика на кнопки удаления файлов — CSP запрещает inline onclick.
    // Слушатель на <ul> перехватывает клики от всех дочерних .btn-delete-file.
    document.getElementById('otherFilesList').addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-delete-file');
        if (btn) deleteOtherFile(btn.dataset.filename);
    });

    // Выбор файлов: input копится в pendingOtherFiles, а не отправляется сразу — отправка
    // произойдёт только по явному клику на otherUploadBtn ниже. Каждый файл проходит клиентскую
    // пре-проверку (размер/расширение) до постановки в очередь — иначе "плохой" файл обнаружился
    // бы только на batch-отправке и заблокировал бы загрузку остальных корректных файлов пачки.
    document.getElementById('otherInput').addEventListener('change', (e) => {
        const files = Array.from(e.target.files);
        e.target.value = '';  // сброс: позволяет открыть диалог повторно и выбрать ещё файлы,
                               // не потеряв уже накопленные в pendingOtherFiles
        if (!files.length) return;

        const accepted = [];
        for (const file of files) {
            const error = validateOtherFileClientSide(file);
            if (error) {
                showToast(`«${file.name}»: ${error}`, 'warning');
            } else {
                accepted.push(file);
            }
        }
        if (!accepted.length) return;

        pendingOtherFiles.push(...accepted);
        renderPendingOtherFiles();
    });

    // Удаление файла из ещё не отправленного списка — делегирование клика по .btn-delete-file.
    document.getElementById('otherPendingList').addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-delete-file');
        if (!btn) return;
        pendingOtherFiles.splice(Number(btn.dataset.index), 1);
        renderPendingOtherFiles();
    });

    // Явная отправка: POST /tasks/{id}/files с одним batch-запросом на все накопленные файлы.
    document.getElementById('otherUploadBtn').addEventListener('click', async () => {
        if (otherUploadInProgress) { showToast('Файлы уже загружаются, подождите', 'warning'); return; }
        if (!pendingOtherFiles.length) { showToast('Выберите файлы', 'warning'); return; }

        const currentCount = (currentTask && currentTask.other_file_paths || []).length;
        if (currentCount + pendingOtherFiles.length > 10) {
            showToast(
                `Лимит файлов: 10. Сейчас: ${currentCount}, выбрано: ${pendingOtherFiles.length}`,
                'warning',
            );
            return;
        }

        const fd = new FormData();
        // поле "files" — FastAPI принимает list[UploadFile] = File(...)
        for (const f of pendingOtherFiles) fd.append('files', f);

        otherUploadInProgress = true;
        try {
            const resp = await fetchWithAuth(`/tasks/${taskId}/files`, { method: 'POST', body: fd });
            if (!resp) return;
            if (resp.ok) {
                const data = await resp.json();
                renderOtherFiles(data.other_file_paths);
                const count = pendingOtherFiles.length;
                pendingOtherFiles = [];
                renderPendingOtherFiles();
                showToast(`Загружено файлов: ${count}`, 'info');
            } else {
                const err = await resp.json();
                showToast(err.detail || 'Ошибка загрузки файлов', 'warning');
            }
        } finally {
            // finally, а не только после успешного resp: без него сетевой сбой
            // (fetchWithAuth бросает исключение) оставлял бы otherUploadInProgress=true
            // навсегда — не только блокируя повторную загрузку файлов, но и не давая
            // удалить задачу через deleteTask() (тот же флаг проверяется и там).
            otherUploadInProgress = false;
        }
    });
});

async function deleteOtherFile(filename) {
    // filename: имя файла с UUID-префиксом, например "a1b2c3d4_doc.pdf".
    // Проверяем ДО confirm(): если по этому конкретному файлу уже есть летящий DELETE —
    // не открываем даже диалог подтверждения повторно, сразу отвечаем toast'ом. Другие файлы
    // (не в otherFilesBeingDeleted) при этом продолжают удаляться независимо — блокировка
    // именная, по filename, а не общая на все кнопки ✕ сразу.
    if (otherFilesBeingDeleted.has(filename)) { showToast('Файл уже удаляется', 'warning'); return; }
    // DELETE /tasks/{id}/files/{filename} — роутер ищет путь по имени в списке.
    if (!confirm(`Удалить файл «${filename}»?`)) return;
    otherFilesBeingDeleted.add(filename);  // помечаем именно этот файл как "в процессе удаления"
    try {
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
    } finally {
        // Снимаем блокировку для конкретного filename независимо от исхода (успех/ошибка/сеть) —
        // другие файлы, которые могли удаляться параллельно, в otherFilesBeingDeleted не затронуты.
        otherFilesBeingDeleted.delete(filename);
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
    if (otherUploadInProgress) { showToast('Дождитесь завершения загрузки файлов', 'warning'); return; }
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

// ── WebSocket ─────────────────────────────────────────────────────────────────
// Упрощённая версия connectWebSocket из task-board.js — без чата, только
// реакция на файловые события для задачи, открытой на этой странице.

function connectWebSocket() {
    try {
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        socket = new WebSocket(`${wsProtocol}//${window.location.host}/ws/tasks/${userId}`);

        socket.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'task_updated' && data.task_id === taskId) {
                    // Задачу отредактировал другой пользователь (PATCH /tasks/{id}), пока эта
                    // страница была открыта — без перечитывания здесь остались бы старые
                    // название/описание/статус. exclude_user_id на сервере уже не пускает
                    // актора получить собственное событие — свою правку он увидел из ответа PATCH.
                    loadTask();
                    showToast(`${data.sender}: задача обновлена`, 'info');
                } else if (data.type === 'task_files_updated' && data.task_id === taskId) {
                    // actor_id теперь приходит и самому актору (task_files_updated больше не
                    // исключает его через exclude_user_id — см. task-board.js, где актор должен
                    // увидеть собственное сообщение в чате). Свою же загрузку/удаление актор уже
                    // увидел напрямую из ответа fetch (renderOtherFiles/renderSpecification) —
                    // повторный loadTask()+toast по этому же событию был бы дублирующим.
                    if (String(data.actor_id) === userId) return;
                    loadTask();  // перечитываем актуальное состояние с сервера
                    showToast(`${data.sender}: файлы задачи обновлены`, 'info');
                } else if (data.type === 'task_deleted' && data.task_id === taskId) {
                    // Задачу удалил другой пользователь, пока эта страница была открыта —
                    // дальнейшие действия (PATCH/DELETE) получили бы 404 Task not found.
                    // exclude_user_id на сервере не даёт актору получить собственное событие —
                    // его страница уже уходит на /task-board через deleteTask() выше.
                    alert('Задача была удалена другим пользователем');
                    window.location.href = '/task-board';
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
    loadTask();
    connectWebSocket();

    document.getElementById('editToggleBtn').addEventListener('click', openEditForm);
    document.getElementById('cancelBtn').addEventListener('click', closeEditForm);
    document.getElementById('saveBtn').addEventListener('click', saveTask);
    document.getElementById('deleteBtn').addEventListener('click', deleteTask);

    const editTitleInput   = document.getElementById('editTitle');
    const editTitleCounter = document.getElementById('editTitleCounter');
    editTitleInput.addEventListener('input', () => _updateCharCounter(editTitleInput, editTitleCounter, 100));
});
