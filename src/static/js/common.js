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

    if (resp.status === 503) {
        const body = await resp.clone().json().catch(() => ({}));
        if (body.detail === 'CRM_UNAVAILABLE') {
            alert('CRM недоступна, обратитесь в техподдержку Предприятия');
        }
    }

    return resp;
}
