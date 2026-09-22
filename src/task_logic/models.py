import datetime
from typing import Any, List, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, TIMESTAMP, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.database import Base


class Project(Base):
    """Локальное зеркало глобального списка «Проект» CRM «Руководитель»
    (list_id=11) — источник истины остаётся CRM, эта таблица только кэширует
    её значения для формы задачи. Наполняется/обновляется исключительно
    Celery-задачей sync_project_table (src/tasks/global_lists_tasks.py) по
    расписанию Celery Beat — веб-процесс никогда не пишет сюда напрямую и не
    обращается к CRM за этим списком (см. docs/project_field_crm_implementation_guide.md,
    §1.2/§1.4).
    """
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ID опции списка в самой CRM — натуральный ключ синхронизации (upsert по
    # нему в sync_project_table), не то же самое, что id выше (локальный PK).
    crm_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Опция пропала из последнего ответа CRM — не удаляется (на неё могут
    # ссылаться существующие Task.project_id — удаление физически сломало бы
    # FK), только помечается неактивной и перестаёт предлагаться в <select>
    # для новых/редактируемых задач.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    synced_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    def __str__(self) -> str:
        # Подпись в ajax-полях sqladmin (str(model)).
        return self.label


class CrmOutbox(Base):
    """Durable outbox для CRM-операций, выполняемых ПОСЛЕ основного db.commit()
    (создание/обновление полей/синхронизация файлов/удаление — для Task И
    Subtask) — см. подробный разбор риска и решения в services/tasks.py и
    services/subtasks.py (докстринги create_*/update_*/delete_*) и
    docs/task-manager-documentation.md, «Векторы развития проекта», п. 14
    (полный план: шардирование id % N со sticky-присвоением, depends_on_event_id,
    идемпотентность, Redlock, token-bucket — см. src/tasks/sharding.py,
    src/tasks/crm_shard_lock.py, src/tasks/crm_rate_limit.py).

    Строка вставляется В ТОЙ ЖЕ транзакции, что и основное изменение
    Task/Subtask — если процесс упадёт в любой момент после этого commit (в
    том числе до того, как успеет попытаться вызвать CRM синхронно), строка
    остаётся status='pending' в PostgreSQL и будет найдена и повторно
    обработана периодической задачей reconcile_pending_outbox
    (src/tasks/crm_outbox_tasks.py, Celery Beat) — независимо от того,
    добрался ли исходный процесс до самой попытки вызова CRM или не добрался
    вовсе.
    """
    __tablename__ = "crm_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 'task' | 'subtask' — какая локальная сущность стоит за aggregate_id.
    # Определяет, каким CRM-менеджером (TaskManager/SubtaskManager) и каким
    # обработчиком в crm_outbox_tasks.py обрабатывается строка.
    aggregate_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="task")
    # Не FK на task.id/subtask.id: к моменту обработки строки operation='delete'
    # самой сущности в БД уже не существует (удалена/каскадно удалена) —
    # payload содержит весь снимок данных, нужный для CRM-вызова, независимо
    # от локальной строки.
    aggregate_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # 'create' | 'sync_files' | 'update' | 'delete' — см. _HANDLERS в
    # src/tasks/crm_outbox_tasks.py за тем, как каждый интерпретируется.
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    # Снимок Task.crm_shard НА МОМЕНТ вставки этой строки — не FK/lookup в
    # реальном времени намеренно: строка 'delete' переживает каскадное
    # удаление самого Task (его crm_shard физически недоступен к моменту
    # обработки), а reconcile_pending_outbox должен знать очередь для
    # диспетчеризации без похода в уже, возможно, отсутствующую сущность.
    # Продюсер (services/tasks.py/subtasks.py) всегда знает это значение на
    # момент вставки — либо уже сохранённое task.crm_shard, либо только что
    # вычисленное shard_for_id (id % N) на первом событии агрегата.
    shard: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Всё, что нужно для CRM-вызова, без обращения к Task/Subtask (см. выше) —
    # например, для 'sync_files': {"crm_task_id": 42, "specification_path": "...", ...}.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # 'pending' → 'done' (успех) | 'failed' (попытки исчерпаны, см. MAX_ATTEMPTS
    # в crm_outbox_tasks.py) | 'blocked' (ждёт событие-зависимость, см.
    # depends_on_event_id). 'pending' старше грейс-периода — то, что находит
    # reconcile_pending_outbox; 'blocked' — то, что находит reconcile_blocked_outbox.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Причина последнего сбоя: "ТипИсключения: текст", обрезается до
    # LAST_ERROR_MAX_LEN символов (см. src/tasks/crm_outbox_tasks.py::
    # _format_error). Очищается при успехе. Показывается администратору в
    # карточке события (src/admin/outbox_admin.py) — для разбора failed/blocked
    # без чтения логов воркера. Текст ответа CRM может содержать фрагменты
    # данных задачи — поэтому обрезка, и поле не попадает ни в один API-ответ.
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Событие, от которого зависит эта строка (два вида зависимости — см.
    # docs/task-manager-documentation.md, «Два вида depends_on_event_id»):
    # межагрегатная (Subtask.create зависит от Task.create) и внутриагрегатная
    # (sync_files зависит от create того же агрегата). NULL — нет зависимости.
    depends_on_event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("crm_outbox.id"), nullable=True
    )
    # Дедупликация повторных запусков ОДНОЙ И ТОЙ ЖЕ Celery-задачи на нашей
    # стороне (например, при восстановлении воркера после сбоя посреди
    # выполнения) — независимо от идемпотентности самого CRM-вызова.
    # gen_random_uuid() — встроена в PostgreSQL с версии 13, без расширений.
    idempotency_key: Mapped[str] = mapped_column(
        String(36), nullable=False, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Task(Base):
    __tablename__ = "task"
    # UniqueConstraint на (title, owner_id): один пользователь не может иметь
    # две задачи с одинаковым названием, но разные пользователи могут.
    # Два одновременных запроса одного пользователя с одинаковым title пройдут
    # Pydantic-проверку до commit, но один получит IntegrityError → HTTP 409.
    __table_args__ = (UniqueConstraint("title", "owner_id", name="uq_task_title_owner"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str] = mapped_column(String(2000))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("person.id", ondelete="CASCADE"))
    owner: Mapped["User"] = relationship("User", back_populates="tasks")
    subtasks: Mapped[List["Subtask"]] = relationship(
        "Subtask", back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )
    # NULL = задача не синхронизирована с CRM (CRM был недоступен при создании
    # или задача создана до интеграции). Тип int, а не UUID: CRM присваивает числовые ID.
    crm_task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)

    # Путь к файлу ТЗ относительно src/uploads/, например "tasks/3/specification/a1b2_tz.pdf".
    # NULL — файл не загружен. Хранится строка, не байты: файл лежит на диске.
    specification_path: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)

    # JSONB-список путей к «Иным документам».
    # PostgreSQL хранит как бинарный JSON (JSONB) — индексируется, не требует json.loads/dumps.
    # SQLAlchemy передаёт list[str] напрямую; asyncpg сериализует в JSONB при записи.
    # NULL — нет файлов. Максимум 10 элементов — проверяется в роутере, не в модели.
    # Пример значения в Python: ["tasks/3/other/a1b2_doc.pdf", "tasks/3/other/c3d4_img.jpg"].
    other_file_paths: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True, default=None)

    # Проект из глобального справочника CRM (см. класс Project выше). Только у
    # Task — Subtask такого поля не получает ни в локальной БД, ни в CRM
    # (entity_id=30), см. docs/project_field_crm_implementation_guide.md, §1.3.
    # Единственная FK-колонка на project у Task — foreign_keys= в relationship
    # не требуется (не путать с многими FK на общую generic-таблицу).
    project_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("project.id"), nullable=True
    )
    # Названа project_ref, НЕ project: TaskResponse.project — производное строковое
    # поле (значение опции, из project_ref.label), а Pydantic model_validate(...,
    # from_attributes=True) читает атрибуты ORM-объекта по именам полей схемы —
    # одноимённая relationship-колонка (объект Project, не str) провалила бы
    # валидацию поля project: Optional[str] ещё на этапе model_validate(), до того,
    # как services/tasks.py успел бы её переопределить явно. lazy="selectin":
    # task.project_ref.label в async-коде без активной сессии иначе упал бы
    # MissingGreenlet (тот же принцип, что у User.roles в этом проекте).
    project_ref: Mapped[Optional["Project"]] = relationship("Project", lazy="selectin")

    # Шард шины CRM-синхронизации (crm_sync.shard_N в Redis) — назначается
    # ОДИН РАЗ, на первом outbox-событии задачи (shard_for_id, см.
    # src/tasks/sharding.py), и больше не пересчитывается (sticky assignment) —
    # все последующие события этой задачи И ВСЕХ её Subtask читают это же
    # значение, не консультируя кольцо заново. NULL — задаче ещё не назначен
    # шард (либо она создана до внедрения шардирования, либо у неё пока не
    # было ни одного CRM-события) — присваивается лениво при первом же событии.
    crm_shard: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Заменяет прямое вычисление crm_synced из crm_task_id is not None —
    # 'unsynced' (CRM-операция ещё не предпринята) | 'pending' (outbox-строка
    # создана, ждёт/повторяет попытку) | 'synced' (успех) | 'failed' (попытки
    # исчерпаны). TaskResponse.crm_synced выводится из этого поля (см.
    # services/tasks.py) — внешний контракт API не меняется, меняется только
    # источник значения.
    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="unsynced")

    def __str__(self) -> str:
        # Подпись в ajax-полях sqladmin (str(model)).
        return self.title


class Subtask(Base):
    __tablename__ = "subtask"
    __table_args__ = (UniqueConstraint("title", "task_id", name="uq_subtask_title_task"),)
    # UniqueConstraint: пара (title, task_id) уникальна — нельзя создать две подзадачи
    # с одинаковым title в одной задаче; в разных задачах одноимённые подзадачи допустимы

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(2000), server_default="")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("task.id", ondelete="CASCADE"), index=True
    )
    task: Mapped["Task"] = relationship("Task", back_populates="subtasks")
    crm_subtask_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)

    # Путь к файлу ТЗ подзадачи относительно src/uploads/.
    # Пример: "subtasks/7/specification/e5f6_spec.pdf". NULL — файл не загружен.
    specification_path: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)

    # JSONB-список путей к иным документам подзадачи. Структура аналогична task.other_file_paths.
    other_file_paths: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True, default=None)

    # См. Task.sync_status выше — та же семантика и то же назначение (заменяет
    # прямое вычисление crm_synced из crm_subtask_id is not None). У Subtask
    # нет своего crm_shard: шард всегда берётся из родительской task.crm_shard.
    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="unsynced")

    def __str__(self) -> str:
        return self.title
