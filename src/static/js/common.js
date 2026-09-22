// Общие функции, ранее продублированные в task-board.js, subtask-board.js,
// task-detail.js и subtask-detail.js. Подключается <script>-тегом ДО
// страничного скрипта (defer сохраняет порядок выполнения по документу),
// поэтому определения ниже уже доступны как глобальные к моменту его запуска.
//
// Без ES-модулей (проект не использует <script type="module">) — общий
// глобальный scope между классическими <script>. Ниже — function-декларации
// (безопасны при повторном определении) и top-level const с общими бизнес-
// константами; страничные скрипты НЕ должны повторно объявлять те же имена
// (const/let в одном global scope дважды → SyntaxError: Identifier has
// already been declared) — они просто читают эти константы как глобальные.

// Общие бизнес-константы, ранее продублированные как самостоятельные литералы
// в task-board.js/task-detail.js/subtask-board.js/subtask-detail.js. Значения
// должны совпадать с ограничениями backend — при изменении лимита там нужно
// поменять константу здесь, а не литерал в нескольких файлах:
//   TITLE_MAX_LENGTH      — max_length=100 в task_schemas.py/subtask_schemas.py
//                           (String(100) в src/task_logic/models.py)
//   OTHER_FILES_MAX_SIZE  — MAX_FILE_SIZE в src/utils/file_utils.py (100 МБ);
//                           дублируется на клиенте только для мгновенной
//                           обратной связи до отправки — сервер всё равно
//                           перепроверяет размер и MIME-тип сам.
//   MAX_OTHER_FILES       — одноимённая константа там же (10 файлов)
//   TASKS_PAGE_SIZE        — limit=, который task-board.js передаёт в GET /tasks/
//   SUBTASKS_PAGE_SIZE     — limit=, который subtask-board.js передаёт в GET /subtasks/
//   WS_RECONNECT_DELAY_MS — пауза перед повторным connectWebSocket() при разрыве соединения
const TITLE_MAX_LENGTH = 100;
const OTHER_FILES_MAX_SIZE = 100 * 1024 * 1024;
const MAX_OTHER_FILES = 10;
const TASKS_PAGE_SIZE = 5;
const SUBTASKS_PAGE_SIZE = 5;
const WS_RECONNECT_DELAY_MS = 3000;

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

// openModal/closeModal — общие хелперы поверх .modal-overlay (см. task-board.html/
// subtask-board.html): раньше каждая страница определяла свои open*Modal/close*Modal
// с идентичным телом (style.display = 'flex'/'none'), продублированным для editModal
// и (после появления второй модалки создания на тех же страницах) для createModal.
function openModal(id) { document.getElementById(id).style.display = 'flex'; }
function closeModal(id) { document.getElementById(id).style.display = 'none'; }

// hasUnsavedFormData(container) — есть ли непустой текст в input[type=text]/textarea
// или выбранный файл хотя бы в одном input[type=file] внутри container. Подходит для
// форм/модалок СОЗДАНИЯ, у которых исходное состояние — пустота (любое заполненное
// поле уже означает "есть что терять"). НЕ подходит для форм РЕДАКТИРОВАНИЯ — там
// поля изначально предзаполнены существующими данными, и проверку "пусто/не пусто"
// нужно заменять сравнением со снимком, снятым в момент открытия формы (см., например,
// _editModalHasUnsavedChanges в task-board.js/subtask-board.js или снимок формы в
// task-detail.js/subtask-detail.js).
function hasUnsavedFormData(container) {
    const textFields = container.querySelectorAll('input[type="text"], textarea');
    for (const el of textFields) {
        if (el.value.trim()) return true;
    }
    const fileFields = container.querySelectorAll('input[type="file"]');
    for (const el of fileFields) {
        if (el.files && el.files.length > 0) return true;
    }
    return false;
}

// registerModalCloseGuard/requestCloseModal — общий реестр "проверок перед закрытием"
// по id модалки. Крестик/фон-подложка/«Отмена» должны вызывать requestCloseModal
// вместо closeModal напрямую: closeModal() выполняется только если для этой модалки
// не зарегистрирован guard, либо guard сообщает об отсутствии несохранённых данных,
// либо пользователь явно подтвердил закрытие в confirm(). message переопределяется
// на регистрации — текст «отменить создание»/«отменить редактирование» отличается
// по контексту конкретной модалки.
const _modalCloseGuards = {};

function registerModalCloseGuard(modalId, checkFn, message) {
    _modalCloseGuards[modalId] = {
        checkFn,
        message: message || 'Отменить создание? Несохранённые данные будут потеряны.',
    };
}

function requestCloseModal(modalId) {
    const guard = _modalCloseGuards[modalId];
    if (guard && guard.checkFn() && !confirm(guard.message)) return;
    closeModal(modalId);
}

// Статус синхронизации с CRM (Task.sync_status/Subtask.sync_status).
// SYNC_STATUS_LABELS — технические подписи для администратора (admin-crm-sync.js).
const SYNC_STATUS_LABELS = {
    unsynced: 'Не синхронизировано',
    pending: 'В очереди',
    synced: 'Синхронизировано',
    failed: 'Ошибка синхронизации',
};

// Подписи и подсказки для ОБЫЧНОГО пользователя (task-board/subtask-board):
// без CRM-жаргона и без тревожных формулировок там, где пользователь ничего не
// может сделать. failed: данные сохранены, а причину увидит и исправит
// администратор (/admin → «CRM outbox», действие «Повторить»).
const USER_SYNC_STATUS = {
    unsynced: { text: 'Ожидает отправки', title: 'Данные сохранены и ещё не отправлены в CRM' },
    pending: { text: 'В обработке', title: 'Данные передаются в CRM' },
    synced: { text: 'Синхронизировано с CRM', title: 'Синхронизировано с CRM' },
    failed: {
        text: 'Не удалось отправить',
        title: 'Данные сохранены. Администратор увидит проблему и повторит отправку',
    },
};

// Бейдж статуса рядом со счётчиком подзадач (тот же класс .subtask-count, что и у
// счётчика; цвет и вид задаёт модификатор .sync-status-<status> в стилях страницы).
//   synced  — только галочка (без плашки с текстом: в списке из десятков строк
//             одинаковые «Синхронизировано» были бы шумом); текст — в title и
//             aria-label для скринридера;
//   pending — спиннер и «В обработке» (role="status" — смена состояния
//             озвучивается), список сам обновляется (createSyncPoller);
//   прочее  — короткая текстовая плашка с пояснением в title.
// Пустая строка, если статус не пришёл (старый ответ API).
function syncStatusTag(status) {
    if (!status) return '';
    const info = USER_SYNC_STATUS[status];
    const cls = `subtask-count sync-status sync-status-${escapeHtml(status)}`;
    if (!info) {
        return `<span class="${cls}">${escapeHtml(status)}</span>`;
    }
    const title = escapeHtml(info.title);
    if (status === 'synced') {
        return `<span class="${cls}" role="img" aria-label="${title}" title="${title}">\u2713</span>`;
    }
    if (status === 'pending') {
        return `<span class="${cls}" role="status" title="${title}">` +
               `<span class="sync-spinner" aria-hidden="true"></span>${escapeHtml(info.text)}</span>`;
    }
    return `<span class="${cls}" title="${title}">${escapeHtml(info.text)}</span>`;
}

// Опрос закончился (maxAttempts), а запись всё ещё pending: спиннер, который
// крутится вечно, вводил бы в заблуждение — заменяем его спокойным сообщением.
// Данные при этом сохранены; актуальный статус покажет обновление страницы.
function markSyncStalled() {
    document.querySelectorAll('.sync-status-pending').forEach(el => {
        el.classList.add('sync-status-stalled');
        el.title = 'Обработка занимает дольше обычного. Данные сохранены; обновите страницу, чтобы проверить статус';
        el.textContent = 'Обработка затянулась';
    });
}

// Автообновление списка, пока хотя бы у одной записи статус 'pending': воркер
// выставляет 'synced' через секунды после ответа API, а WS-событий о смене
// статуса нет. reload() перечитывает текущую страницу; не более maxAttempts
// подряд (воркер лежит — не опрашиваем бесконечно). Вызов onRender(items, false)
// из «обычной» отрисовки (загрузка, WS-событие, смена страницы) обнуляет счётчик;
// onRender(items, true) — из самого опроса.
function createSyncPoller(reload, intervalMs = 3000, maxAttempts = 20) {
    let timer = null;
    let attempts = 0;
    return function onRender(items, isPoll) {
        clearTimeout(timer);
        if (!isPoll) attempts = 0;
        const hasPending = items.some(item => item.sync_status === 'pending');
        if (!hasPending) return;
        if (attempts >= maxAttempts) {
            markSyncStalled();
            return;
        }
        attempts++;
        timer = setTimeout(reload, intervalMs);
    };
}

// subtaskLabel — русское склонение числительных для счётчика подзадач.
// Алгоритм работает по последней цифре (mod10), с отдельной обработкой чисел 11–14 (mod100):
//   11, 12, 13, 14 — всегда «подзадач» (исключение из правила «1 → подзадача»).
//   mod100 !== 11 в первом условии именно для этого: 11 % 10 === 1, но склонение иное.
// Используется только на страницах задач (task-board.js, task-detail.js) — у подзадач
// нет собственного счётчика под-подзадач, но функция общая и не зависит от контекста страницы.
function subtaskLabel(n) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return `${n} подзадача`;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return `${n} подзадачи`;
    return `${n} подзадач`;
}

// Singleton-промис для обновления access-токена. Общий на страницу (а не на функцию,
// вызывающую fetchWithAuth): если несколько запросов параллельно получат 401, POST
// /auth/access-token выполнится один раз — остальные await-ят тот же промис, вместо
// того чтобы каждый дублирующе запрашивал один и тот же новый access_token.
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

    return resp;
}
