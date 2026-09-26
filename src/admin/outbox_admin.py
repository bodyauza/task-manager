"""ModelView для crm_outbox — очередь CRM-синхронизации Task/Subtask.

Нужна для отладки: видно, какие события pending/blocked/failed, сколько было
попыток, на каком шарде и от какого события зависят. Строки пишут продюсеры
(services/tasks.py, services/subtasks.py) и обрабатывает Celery
(src/tasks/crm_outbox_tasks.py), поэтому create/edit/delete отключены — ручная
правка сломала бы гарантии доставки. payload (снимок данных задачи) и
last_error (причина последнего сбоя) видны только на детальной странице.

Единственная запись — действие «Повторить» (см. retry ниже): возвращает
failed-событие в очередь после устранения причины сбоя.
"""

from sqladmin import ModelView, action
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import RedirectResponse

from src.admin.formatters import TYPE_FORMATTERS
from src.database import async_session_maker
from src.task_logic.models import CrmOutbox
from src.tasks.crm_outbox_tasks import (
    has_newer_done_sibling,
    dispatch_outbox_row,
    set_aggregate_sync_status,
)


def flash(request: Request, text: str, level: str = "success") -> None:
    """Одноразовое сообщение для следующей страницы: sqladmin не имеет flash-
    механизма, поэтому кладём его в сессию, а layout.html показывает и удаляет."""
    request.session["admin_flash"] = {"text": text, "level": level}


class CrmOutboxAdmin(ModelView, model=CrmOutbox):
    name = "CRM-событие"
    name_plural = "CRM outbox"
    icon = "fa-solid fa-inbox"

    can_create = False
    can_edit = False
    can_delete = False

    column_type_formatters = TYPE_FORMATTERS
    column_list = [
        CrmOutbox.id, CrmOutbox.aggregate_type, CrmOutbox.aggregate_id,
        CrmOutbox.operation, CrmOutbox.status, CrmOutbox.attempts,
        CrmOutbox.shard, CrmOutbox.depends_on_event_id,
        CrmOutbox.created_at, CrmOutbox.updated_at,
    ]
    column_searchable_list = [CrmOutbox.aggregate_type, CrmOutbox.operation, CrmOutbox.status]
    column_sortable_list = [
        CrmOutbox.id, CrmOutbox.aggregate_id, CrmOutbox.status,
        CrmOutbox.attempts, CrmOutbox.created_at, CrmOutbox.updated_at,
    ]
    column_default_sort = [(CrmOutbox.id, True)]

    @action(
        name="retry",
        label="Повторить",
        confirmation_message=(
            "Вернуть выбранные failed-события в очередь? Сначала устраните причину сбоя "
            "(см. last_error на карточке события) — иначе через 5 попыток они снова станут failed. "
            "События в других статусах, а также устаревшие относительно более нового успешно "
            "синхронизированного события той же сущности, будут пропущены."
        ),
        add_in_list=True,
        add_in_detail=True,
    )
    async def retry(self, request: Request) -> RedirectResponse:
        """failed → pending с attempts = 0 (без сброса attempts строка после
        одной же неудачи снова стала бы failed) и немедленный диспатч в
        очередь её шарда. Зависимые blocked-строки разблокирует
        reconcile_blocked_outbox (раз в 5 минут), когда эта станет done.
        Затрагиваются только failed — pending/done/blocked пропускаются
        (blocked разблокируется сам, когда выполнится его зависимость).

        Защита порядка: failed-строка, для которой у той же сущности
        (aggregate_type, aggregate_id) уже есть более новое (больший id) 'done'
        событие, НЕ переставляется в очередь — иначе повтор применил бы
        устаревшие данные ПОВЕРХ уже синхронизированных свежих (например,
        failed update #10 после того, как update #11 той же задачи уже успешно
        применился). Такая строка считается устаревшей и попадает в отдельную
        категорию flash-сообщения, а не в «возвращено в очередь»."""
        pks = [int(p) for p in request.query_params.get("pks", "").split(",") if p.strip().isdigit()]
        requeued: list[CrmOutbox] = []
        stale: list[CrmOutbox] = []
        if pks:
            async with async_session_maker() as session:
                candidates = list((
                    await session.execute(
                        select(CrmOutbox).where(CrmOutbox.id.in_(pks), CrmOutbox.status == "failed")
                    )
                ).scalars().all())
                for row in candidates:
                    if await has_newer_done_sibling(session, row):
                        stale.append(row)
                        continue
                    row.status = "pending"
                    row.attempts = 0
                    if row.operation != "delete":
                        await set_aggregate_sync_status(session, row, "pending", only_from=("failed",))
                    requeued.append(row)
                await session.commit()
            # После commit: диспатч — оптимизация задержки, не механизм
            # надёжности (сбой брокера проглатывается, строку подберёт reconcile).
            for row in requeued:
                dispatch_outbox_row(row)

        not_failed = len(set(pks)) - len(requeued) - len(stale)
        if not pks:
            flash(request, "Ничего не выбрано: отметьте события со статусом failed.", "warning")
        elif not requeued and not stale:
            flash(request, f"Ничего не возвращено в очередь: выбранные события ({not_failed}) не в статусе failed.", "warning")
        elif not requeued and stale:
            flash(
                request,
                f"Ничего не возвращено в очередь: для всех выбранных событий ({len(stale)}) уже есть "
                "более новое успешно синхронизированное событие той же сущности.",
                "warning",
            )
        elif stale or not_failed:
            parts = [f"Возвращено в очередь: {len(requeued)}."]
            if stale:
                parts.append(f"Пропущено как устаревшие (есть более новое done): {len(stale)}.")
            if not_failed:
                parts.append(f"Пропущено (статус не failed или не найдено): {not_failed}.")
            flash(request, " ".join(parts), "warning")
        else:
            flash(request, f"Возвращено в очередь: {len(requeued)}.")

        referer = request.headers.get("Referer")
        if referer:
            return RedirectResponse(referer, status_code=302)
        return RedirectResponse(request.url_for("admin:list", identity=self.identity), status_code=302)
