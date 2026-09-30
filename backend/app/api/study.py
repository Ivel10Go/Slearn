from datetime import datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.api.helpers import normalized_datetime, owned_or_404
from app.core.config import Settings
from app.core.time import as_utc, resolve_timezone_name, utc_now, zone_or_utc
from app.db.entities import (
    Exam,
    StudyPlan,
    StudyPlanTask,
    StudySession,
    StudyTaskDependency,
    StudyTaskSeries,
    Topic,
    User,
)
from app.schemas.common import MessageResponse
from app.schemas.study import (
    StudyPlanCreate,
    StudyPlanRead,
    StudyPlanStatus,
    StudyPlanUpdate,
    StudySessionRead,
    StudyTaskCompleteRequest,
    StudyTaskCreate,
    StudyTaskDependencyCreate,
    StudyTaskDependencyRead,
    StudyTaskOrder,
    StudyTaskRead,
    StudyTaskRecurrence,
    StudyTaskSeriesCreate,
    StudyTaskSeriesRead,
    StudyTaskSeriesStatus,
    StudyTaskSeriesUpdate,
    StudyTaskUpdate,
)
from app.services import rewards as rewards_service
from app.services import study as study_service
from app.services.study import StudyPlanError

plans_router = APIRouter(prefix="/api/v1/study/plans", tags=["study"])
router = APIRouter(prefix="/api/v1/study/tasks", tags=["study"])
series_router = APIRouter(prefix="/api/v1/study/series", tags=["study"])

TASK_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _unprocessable(detail: str) -> HTTPException:
    """Eine Verletzung aus der Anfrage des Nutzers: 422, nicht 409.

    Ein Zyklus, eine fremde Klausur oder ein unbekannter Wiederholungstyp
    kommt aus dem Request, nicht aus einem Datenbankkonflikt.
    """
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _study_plan_error(exc: StudyPlanError) -> HTTPException:
    return _unprocessable(str(exc))


def _series_read(series: StudyTaskSeries) -> StudyTaskSeriesRead:
    subject = series.topic.subject if series.topic else None
    return StudyTaskSeriesRead(
        id=series.id,
        study_plan_id=series.study_plan_id,
        topic_id=series.topic_id,
        subject_name=subject.name if subject else None,
        subject_color=subject.color if subject else None,
        topic_name=series.topic.name if series.topic else None,
        title=series.title,
        description=series.description,
        recurrence=cast(StudyTaskRecurrence, series.recurrence),
        duration_minutes=series.duration_minutes,
        priority=series.priority,
        starts_on=normalized_datetime(series.starts_on),
        until=normalized_datetime(series.until),
        next_due_on=normalized_datetime(series.next_due_on),
        occurrences=series.occurrences,
        occurrence_limit=series.occurrence_limit,
        status=cast(StudyTaskSeriesStatus, series.status),
        created_at=normalized_datetime(series.created_at) or utc_now(),
        updated_at=normalized_datetime(series.updated_at) or utc_now(),
    )


def _owned_task_for_update(db: Session, task_id: int, user_id: int) -> StudyPlanTask:
    task = db.scalar(
        select(StudyPlanTask)
        .where(StudyPlanTask.id == task_id, StudyPlanTask.user_id == user_id)
        .with_for_update()
    )
    if task is None:
        raise _not_found("Study task not found")
    return task


def _open_sessions(db: Session, user_id: int, task_id: int) -> list[StudySession]:
    return list(
        db.scalars(
            select(StudySession)
            .where(
                StudySession.user_id == user_id,
                StudySession.task_id == task_id,
                StudySession.completed_at.is_(None),
            )
            .order_by(StudySession.started_at, StudySession.id)
        ).all()
    )


def _elapsed_minutes(started_at: datetime, completed_at: datetime, minimum: int) -> int:
    seconds = max(0, int((as_utc(completed_at) - as_utc(started_at)).total_seconds()))
    return max(minimum, seconds // 60)


def _close_sessions(sessions: list[StudySession], completed_at: datetime, minimum: int) -> None:
    for session in sessions:
        session.completed_at = as_utc(completed_at)
        session.duration_minutes = _elapsed_minutes(session.started_at, completed_at, minimum)


def _read_tasks(db: Session, user_id: int, tasks: list[StudyPlanTask]) -> list[StudyTaskRead]:
    """Eine Liste von Aufgaben lesen, inklusive Abhaengigkeiten.

    Drei Laufen statt einem je Task: die Abhaengigkeiten brauchen eine
    Gruppenabfrage, die Klausurtitel einen zweiten. Ein Lazy Load pro
    Zeile waere der N+1-Fall, der auf dem Dashboard erst mit 60 Aufgaben
    auffaellt.
    """
    return study_service.read_tasks(db, user_id, tasks)


def _read_task(db: Session, user_id: int, task: StudyPlanTask) -> StudyTaskRead:
    return _read_tasks(db, user_id, [task])[0]


def _require_unblocked(db: Session, user_id: int, task: StudyPlanTask) -> None:
    """Blockiert eine offene Vorbedingung, darf die Aufgabe nicht starten."""
    prerequisites = study_service.blocking_prerequisites(db, user_id, task.id)
    if not prerequisites:
        return
    names = ", ".join(prerequisite.title for prerequisite in prerequisites[:3])
    more = "" if len(prerequisites) <= 3 else f" (+{len(prerequisites) - 3})"
    raise _conflict(f"Die Aufgabe wartet noch auf: {names}{more}")


def _spawn_next_occurrence(db: Session, user: User, task: StudyPlanTask) -> StudyPlanTask | None:
    """Nach dem Abschluss die naechste Aufgabe der Serie erzeugen.

    Bewusst nur eine Occurrence: eine Serie, die im Voraus 30 Aufgaben anlegt,
    waere eine Liste, die man durchklickt statt erledigt. Erledigt man die
    letzte, endet die Serie von selbst.
    """
    if task.series_id is None:
        return None
    series = db.get(StudyTaskSeries, task.series_id)
    if series is None or series.user_id != user.id:
        return None
    due_on = study_service.next_due_on(series)
    if due_on is None:
        return None
    occurrence = study_service.create_series_task(
        series, user.id, due_on, study_service.next_order_index(db, series.study_plan_id)
    )
    db.add(occurrence)
    study_service.advance_series(series)
    db.flush()
    return occurrence


def _owned_topic(db: Session, topic_id: int | None, user_id: int) -> None:
    if topic_id is None:
        return
    topic = db.get(Topic, topic_id)
    if topic is None or topic.user_id != user_id:
        raise _not_found("Topic not found")


def _owned_exam(db: Session, exam_id: int | None, user_id: int) -> None:
    if exam_id is None:
        return
    exam = db.get(Exam, exam_id)
    if exam is None or exam.user_id != user_id:
        raise _not_found("Exam not found")


def _zone(current_user: User, settings: Settings) -> ZoneInfo:
    """Die Zeitzone des Nutzers, in der auch der Tagespunkt der Punkte faellt.

    Bewusst dieselbe Aufloesung wie im Lernfortschritt und im Willkommenssatz:
    die Lernsessions liegen als UTC in der Datenbank, und ohne diese Zone waere ein
    kurz vor Mitternacht begonnener Block ein *gestriger* Lerntag - und damit ein
    anderes Punktekonto.
    """
    return zone_or_utc(resolve_timezone_name(current_user.timezone, settings.timezone))


def _belohnen(
    db: Session,
    current_user: User,
    settings: Settings,
    *,
    task: StudyPlanTask | None = None,
    sessions: list[StudySession] | None = None,
    now: datetime | None = None,
) -> bool:
    """Punkte und Auszeichnungen fuer dieses Ereignis vergeben - ohne Commit.

    Der Aufruf committet absichtlich nicht: die Buchung gehoert in dieselbe
    Transaktion wie die Aenderung, die sie ausgeloest hat. Ein Abschluss ohne
    Punkte waere ein halber Zustand, und ein Punktehalt ohne Abschluss ein anderer.

    Zurueck kommt, ob ueberhaupt etwas geschrieben wurde - ein Aufruf, der nichts
    neues zu sagen hat, soll nicht jedes Mal eine Aenderung vormerken.
    """
    return rewards_service.record_progress(
        db,
        current_user.id,
        zone=_zone(current_user, settings),
        task=task,
        sessions=tuple(sessions or ()),
        now=now,
    ).changed


# --------------------------------------------------------------------------
# Lernplaene
# --------------------------------------------------------------------------


def _plan_read(plan: StudyPlan, counts: dict[int, tuple[int, int]]) -> StudyPlanRead:
    total, open_total = counts.get(plan.id, (0, 0))
    return StudyPlanRead(
        id=plan.id,
        title=plan.title,
        description=plan.description,
        status=cast(StudyPlanStatus, plan.status),
        starts_on=normalized_datetime(plan.starts_on),
        ends_on=normalized_datetime(plan.ends_on),
        task_count=total,
        open_task_count=open_total,
        created_at=normalized_datetime(plan.created_at) or utc_now(),
        updated_at=normalized_datetime(plan.updated_at) or utc_now(),
    )


@plans_router.get("", response_model=list[StudyPlanRead])
def list_plans(
    current_user: CurrentUser,
    db: DatabaseSession,
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[StudyPlanRead]:
    query = select(StudyPlan).where(StudyPlan.user_id == current_user.id)
    if status_filter:
        query = query.where(StudyPlan.status == status_filter)
    plans = db.scalars(query.order_by(StudyPlan.id)).all()
    counts = study_service.plan_open_task_counts(db, current_user.id)
    return [_plan_read(plan, counts) for plan in plans]


@plans_router.post("", response_model=StudyPlanRead, status_code=status.HTTP_201_CREATED)
def create_plan(payload: StudyPlanCreate, current_user: CurrentUser, db: DatabaseSession) -> StudyPlanRead:
    values = payload.model_dump()
    values["starts_on"] = normalized_datetime(values["starts_on"])
    values["ends_on"] = normalized_datetime(values["ends_on"])
    plan = StudyPlan(user_id=current_user.id, **values)
    db.add(plan)
    try:
        db.commit()
        db.refresh(plan)
    except IntegrityError as exc:
        db.rollback()
        raise _conflict("Study plan could not be created") from exc
    return _plan_read(plan, {})


@plans_router.get("/{plan_id}", response_model=StudyPlanRead)
def get_plan(plan_id: int, current_user: CurrentUser, db: DatabaseSession) -> StudyPlanRead:
    plan = owned_or_404(db, StudyPlan, plan_id, current_user.id)
    counts = study_service.plan_open_task_counts(db, current_user.id)
    return _plan_read(plan, counts)


@plans_router.patch("/{plan_id}", response_model=StudyPlanRead)
def update_plan(plan_id: int, payload: StudyPlanUpdate, current_user: CurrentUser, db: DatabaseSession) -> StudyPlanRead:
    plan = owned_or_404(db, StudyPlan, plan_id, current_user.id)
    values = payload.model_dump(exclude_unset=True)
    for key in ("starts_on", "ends_on"):
        if key in values:
            values[key] = normalized_datetime(values[key])
    starts_on = values.get("starts_on", plan.starts_on)
    ends_on = values.get("ends_on", plan.ends_on)
    if starts_on is not None and ends_on is not None and as_utc(ends_on) < as_utc(starts_on):
        raise _unprocessable("ends_on must not be before starts_on")
    for key, value in values.items():
        setattr(plan, key, value)
    db.commit()
    db.refresh(plan)
    counts = study_service.plan_open_task_counts(db, current_user.id)
    return _plan_read(plan, counts)


@plans_router.delete("/{plan_id}", response_model=MessageResponse)
def delete_plan(plan_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    """Loescht den Plan mitsamt Aufgaben, Serien und Lernsessions.

    Die Abhaengigkeiten der Aufgaben fallen ueber die Kaskade mit. Das ist
    Absicht: eine Abhaengigkeit auf eine geloeschte Aufgabe koennte nie
    erfuellt werden, also waere sie schlimmer als keine.
    """
    plan = owned_or_404(db, StudyPlan, plan_id, current_user.id)
    db.delete(plan)
    db.commit()
    return MessageResponse(message="Study plan deleted")


# --------------------------------------------------------------------------
# Serien
# --------------------------------------------------------------------------


@series_router.get("", response_model=list[StudyTaskSeriesRead])
def list_series(
    current_user: CurrentUser,
    db: DatabaseSession,
    study_plan_id: int | None = Query(default=None, alias="study_plan_id"),
) -> list[StudyTaskSeriesRead]:
    query = select(StudyTaskSeries).where(StudyTaskSeries.user_id == current_user.id)
    if study_plan_id is not None:
        query = query.where(StudyTaskSeries.study_plan_id == study_plan_id)
    items = db.scalars(query.order_by(StudyTaskSeries.id)).all()
    return [_series_read(item) for item in items]


@series_router.post("", response_model=StudyTaskSeriesRead, status_code=status.HTTP_201_CREATED)
def create_series(
    payload: StudyTaskSeriesCreate, current_user: CurrentUser, db: DatabaseSession
) -> StudyTaskSeriesRead:
    """Eine Serie anlegen und ihre erste Aufgabe sofort erzeugen.

    Die erste Occurrence entsteht gleich mit, sonst haette der Nutzer eine
    leere Serie und muesste auf eine unsichtbare Logik warten. Ab da
    entsteht jede weitere erst, wenn die vorige abgeschlossen wird.
    """
    plan = owned_or_404(db, StudyPlan, payload.study_plan_id, current_user.id)
    _owned_topic(db, payload.topic_id, current_user.id)
    starts_on = payload.starts_on
    try:
        study_service.advance_due_on(starts_on, payload.recurrence)
    except StudyPlanError as exc:
        raise _study_plan_error(exc) from exc
    series = StudyTaskSeries(
        user_id=current_user.id,
        study_plan_id=plan.id,
        topic_id=payload.topic_id,
        title=payload.title,
        description=payload.description,
        recurrence=payload.recurrence,
        duration_minutes=payload.duration_minutes,
        priority=payload.priority,
        starts_on=starts_on,
        until=payload.until,
        next_due_on=starts_on,
        occurrence_limit=payload.occurrence_limit,
    )
    db.add(series)
    db.flush()
    first = study_service.create_series_task(
        series, current_user.id, starts_on, study_service.next_order_index(db, plan.id)
    )
    db.add(first)
    # `advance_series` erhoeht den Zaehler auf 1 und merkt sich die zweite
    # Faelligkeit. Ohne Aufruf stuende die Serie bei `occurrences = 0` und
    # wuerste die erste Aufgabe als bonus zaehlen.
    study_service.advance_series(series)
    try:
        db.commit()
        db.refresh(series)
    except IntegrityError as exc:
        db.rollback()
        raise _conflict("Study task series could not be created") from exc
    return _series_read(series)


@series_router.get("/{series_id}", response_model=StudyTaskSeriesRead)
def get_series(series_id: int, current_user: CurrentUser, db: DatabaseSession) -> StudyTaskSeriesRead:
    return _series_read(owned_or_404(db, StudyTaskSeries, series_id, current_user.id))


@series_router.patch("/{series_id}", response_model=StudyTaskSeriesRead)
def update_series(
    series_id: int, payload: StudyTaskSeriesUpdate, current_user: CurrentUser, db: DatabaseSession
) -> StudyTaskSeriesRead:
    """Vorlage einer Serie aendern - ohne die bereits erzeugten Aufgaben.

    Wirkt erst die naechste Occurrence: eine Serie, deren Vergangenheit
    sich nachtraeglich umschreiben laesst, waere keine mehr.
    """
    series = owned_or_404(db, StudyTaskSeries, series_id, current_user.id)
    values = payload.model_dump(exclude_unset=True)
    _owned_topic(db, values.get("topic_id"), current_user.id)
    if "until" in values:
        values["until"] = normalized_datetime(values["until"])
        if values["until"] is not None and series.starts_on is not None:
            if as_utc(values["until"]) < as_utc(series.starts_on):
                raise _unprocessable("until must not be before starts_on")
    for key, value in values.items():
        setattr(series, key, value)
    if values.get("status") == "cancelled":
        # Eine abgebrochene Serie darf keine offene Faelligkeit mehr
        # tragen, sonst erzeugt der naechste Abschluss doch noch eine.
        series.next_due_on = None
    db.commit()
    db.refresh(series)
    return _series_read(series)


@series_router.delete("/{series_id}", response_model=MessageResponse)
def delete_series(series_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    """Beendet eine Serie und storniert ihre offenen Aufgaben.

    Erledigte bleiben stehen. Der Verlauf gehoert dem Nutzer, und ein
    geloeschter Eintrag in der Lernstatistik waere wertlos.
    """
    series = owned_or_404(db, StudyTaskSeries, series_id, current_user.id)
    open_tasks = db.scalars(
        select(StudyPlanTask).where(
            StudyPlanTask.series_id == series.id,
            StudyPlanTask.user_id == current_user.id,
            StudyPlanTask.status.notin_(("completed", "cancelled")),
        )
    ).all()
    for task in open_tasks:
        task.status = "cancelled"
        task.completed_at = None
    series.status = "cancelled"
    series.next_due_on = None
    db.commit()
    return MessageResponse(message="Study task series cancelled")


# --------------------------------------------------------------------------
# Aufgaben
# --------------------------------------------------------------------------


@router.get("", response_model=list[StudyTaskRead])
def list_tasks(
    current_user: CurrentUser,
    db: DatabaseSession,
    status_filter: str | None = Query(default=None, alias="status"),
    study_plan_id: int | None = Query(default=None, alias="study_plan_id"),
    order: StudyTaskOrder = Query(default="priority"),
) -> list[StudyTaskRead]:
    query = study_service.task_query(current_user.id)
    if status_filter:
        query = query.where(StudyPlanTask.status == status_filter)
    if study_plan_id is not None:
        query = query.where(StudyPlanTask.study_plan_id == study_plan_id)
    try:
        sort_clause = study_service.task_sort_clause(order)
    except StudyPlanError as exc:
        raise _study_plan_error(exc) from exc
    tasks = db.scalars(query.order_by(*sort_clause)).all()
    return _read_tasks(db, current_user.id, list(tasks))


@router.post("", response_model=StudyTaskRead, status_code=status.HTTP_201_CREATED)
def create_task(payload: StudyTaskCreate, current_user: CurrentUser, db: DatabaseSession) -> StudyTaskRead:
    plan = owned_or_404(db, StudyPlan, payload.study_plan_id, current_user.id)
    _owned_topic(db, payload.topic_id, current_user.id)
    _owned_exam(db, payload.exam_id, current_user.id)
    values = payload.model_dump()
    values.pop("study_plan_id")
    if "order_index" not in payload.model_fields_set:
        values["order_index"] = study_service.next_order_index(db, plan.id)
    values["scheduled_for"] = normalized_datetime(values["scheduled_for"])
    values["due_date"] = normalized_datetime(values["due_date"])
    task = StudyPlanTask(user_id=current_user.id, study_plan_id=plan.id, **values)
    db.add(task)
    try:
        db.commit()
        db.refresh(task)
    except IntegrityError as exc:
        db.rollback()
        raise _conflict("Study task already exists") from exc
    return _read_task(db, current_user.id, task)


@router.post("/{task_id}/start", response_model=StudySessionRead)
def start_task(task_id: int, current_user: CurrentUser, db: DatabaseSession) -> StudySessionRead:
    task = _owned_task_for_update(db, task_id, current_user.id)
    if task.status not in {"pending", "in_progress"}:
        raise _conflict("Study task cannot be started in its current status")
    # Eine blockierte Aufgabe laesst sich nicht starten. Die Reihenfolge im
    # Plan ist sonst nur ein Vorschlag, den man umgehen koennte.
    _require_unblocked(db, current_user.id, task)

    existing_sessions = _open_sessions(db, current_user.id, task.id)
    if existing_sessions:
        task.status = "in_progress"
        task.completed_at = None
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise _conflict("Study task could not be started") from exc
        return StudySessionRead.model_validate(existing_sessions[0])

    started_at = utc_now()
    session = StudySession(user_id=current_user.id, task_id=task.id, started_at=started_at)
    task.status = "in_progress"
    task.completed_at = None
    db.add(session)
    try:
        db.commit()
        db.refresh(session)
    except IntegrityError as exc:
        db.rollback()
        task = _owned_task_for_update(db, task_id, current_user.id)
        existing_sessions = _open_sessions(db, current_user.id, task.id)
        if not existing_sessions:
            raise _conflict("Study task could not be started") from exc
        task.status = "in_progress"
        task.completed_at = None
        db.commit()
        return StudySessionRead.model_validate(existing_sessions[0])
    return StudySessionRead.model_validate(session)


@router.post("/{task_id}/complete", response_model=StudyTaskRead)
def complete_task(
    task_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    payload: StudyTaskCompleteRequest | None = Body(default=None),
) -> StudyTaskRead:
    task = _owned_task_for_update(db, task_id, current_user.id)
    if task.status == "cancelled":
        raise _conflict("Cancelled study task cannot be completed")
    if task.status == "completed":
        open_sessions = _open_sessions(db, current_user.id, task.id)
        if open_sessions:
            completed_at = as_utc(task.completed_at or utc_now())
            _close_sessions(open_sessions, completed_at, 1)
            # Auch hier wird gepunktet: eine spaet beendete Session ist genauso
            # gelernte Zeit wie eine im ersten Aufruf geschlossene. Dass der
            # Aufruf idempotent ist, steht nicht an dieser Zeile, sondern in
            # ``reward_entries`` - eine Buchung, die es schon gibt, wird nicht
            # wiederholt, egal wie oft man hier vorbeikommt.
            _belohnen(db, current_user, settings, sessions=open_sessions, now=completed_at)
            db.commit()
        return _read_task(db, current_user.id, task)
    if task.status not in {"pending", "in_progress"}:
        raise _conflict("Study task cannot be completed in its current status")

    completed_at = utc_now()
    notes = payload.notes if payload is not None else None
    open_sessions = _open_sessions(db, current_user.id, task.id)
    if open_sessions:
        _close_sessions(open_sessions, completed_at, 1)
        if notes is not None:
            for session in open_sessions:
                session.notes = notes
    else:
        session = StudySession(
            user_id=current_user.id,
            task_id=task.id,
            started_at=completed_at - timedelta(minutes=task.duration_minutes),
            completed_at=completed_at,
            duration_minutes=task.duration_minutes,
            notes=notes,
        )
        db.add(session)
        # Die erfundene Session gehoert zur Belohnung dazu. Sie ist die einzige
        # Lernzeit dieses Abschlusses, und ein leeres `open_sessions` wuerde den
        # Fachtag buchen, den es ohne sie gar nicht gibt.
        open_sessions = [session]
    task.status = "completed"
    task.completed_at = completed_at
    spawned = _spawn_next_occurrence(db, current_user, task)
    try:
        # Die Buchung gehoert in dieselbe Transaktion wie der Abschluss. Sie
        # steht deshalb **vor** dem Commit und nicht danach: entweder ist die
        # Aufgabe erledigt *und* gepunktet, oder gar nichts.
        _belohnen(db, current_user, settings, task=task, sessions=open_sessions, now=completed_at)
        db.commit()
        if spawned is not None:
            db.refresh(spawned)
        db.refresh(task)
    except IntegrityError as exc:
        db.rollback()
        raise _conflict("Study task could not be completed") from exc
    return _read_task(db, current_user.id, task)


@router.post("/{task_id}/stop", response_model=StudyTaskRead)
def stop_task(task_id: int, current_user: CurrentUser, db: DatabaseSession, settings: AppSettings) -> StudyTaskRead:
    task = _owned_task_for_update(db, task_id, current_user.id)
    if task.status not in {"pending", "in_progress"}:
        raise _conflict("Study task cannot be stopped in its current status")
    completed_at = utc_now()
    open_sessions = _open_sessions(db, current_user.id, task.id)
    _close_sessions(open_sessions, completed_at, 0)
    task.status = "pending"
    task.completed_at = None
    try:
        # Auch ein Stopp zaehlt als gelernte Zeit: der Block fand statt, er wurde
        # nur nicht zu Ende gebracht. Ohne Thema gibt es trotzdem keinen Fachtag -
        # die Buchung entscheidet das, nicht dieser Endpunkt.
        if open_sessions:
            _belohnen(db, current_user, settings, sessions=open_sessions, now=completed_at)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict("Study task could not be stopped") from exc
    return _read_task(db, current_user.id, task)


@router.get("/{task_id}", response_model=StudyTaskRead)
def get_task(task_id: int, current_user: CurrentUser, db: DatabaseSession) -> StudyTaskRead:
    return _read_task(db, current_user.id, owned_or_404(db, StudyPlanTask, task_id, current_user.id))


@router.patch("/{task_id}", response_model=StudyTaskRead)
def update_task(
    task_id: int, payload: StudyTaskUpdate, current_user: CurrentUser, db: DatabaseSession, settings: AppSettings
) -> StudyTaskRead:
    task = owned_or_404(db, StudyPlanTask, task_id, current_user.id)
    values = payload.model_dump(exclude_unset=True)
    if "topic_id" in values:
        _owned_topic(db, values["topic_id"], current_user.id)
    if "exam_id" in values:
        _owned_exam(db, values["exam_id"], current_user.id)
    if "scheduled_for" in values:
        values["scheduled_for"] = normalized_datetime(values["scheduled_for"])
    if "due_date" in values:
        values["due_date"] = normalized_datetime(values["due_date"])
    if "status" in values and values["status"] not in TASK_STATUSES:
        raise _unprocessable("Unsupported task status")
    for key, value in values.items():
        setattr(task, key, value)
    if values.get("status") == "completed" and task.completed_at is None:
        task.completed_at = utc_now()
    elif "status" in values and values["status"] != "completed":
        task.completed_at = None
    try:
        # Auch der Weg ueber den `PATCH` zahlt. Sonst waere
        # ``PATCH {"status": "completed"}`` eine Nebenhand, an der die Punkte
        # vorbeikommen - und damit eine Luecke, die man nicht braucht, um
        # doppelt zu punkten.
        if task.status == "completed":
            _belohnen(db, current_user, settings, task=task)
        db.commit()
        db.refresh(task)
    except IntegrityError as exc:
        db.rollback()
        raise _conflict("Study task already exists") from exc
    return _read_task(db, current_user.id, task)


@router.delete("/{task_id}", response_model=MessageResponse)
def delete_task(task_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    """Loescht die Aufgabe und raeumt ihre Abhaengigkeiten in beide Richtungen.

    Waeter, die auf sie gewartet haben, wuerden sonst fuer immer blockiert
    bleiben, ohne dass es etwas zu loesen gaebe.
    """
    task = owned_or_404(db, StudyPlanTask, task_id, current_user.id)
    db.query(StudyTaskDependency).filter(
        StudyTaskDependency.user_id == current_user.id,
        (StudyTaskDependency.task_id == task_id) | (StudyTaskDependency.depends_on_task_id == task_id),
    ).delete(synchronize_session=False)
    db.delete(task)
    db.commit()
    return MessageResponse(message="Study task deleted")


# --------------------------------------------------------------------------
# Abhaengigkeiten
# --------------------------------------------------------------------------


@router.get("/{task_id}/dependencies", response_model=list[StudyTaskDependencyRead])
def list_task_dependencies(
    task_id: int, current_user: CurrentUser, db: DatabaseSession
) -> list[StudyTaskDependencyRead]:
    owned_or_404(db, StudyPlanTask, task_id, current_user.id)
    rows = db.scalars(
        select(StudyTaskDependency)
        .where(StudyTaskDependency.user_id == current_user.id, StudyTaskDependency.task_id == task_id)
        .order_by(StudyTaskDependency.created_at, StudyTaskDependency.depends_on_task_id)
    ).all()
    return [StudyTaskDependencyRead.model_validate(row) for row in rows]


@router.post("/{task_id}/dependencies", response_model=StudyTaskDependencyRead, status_code=status.HTTP_201_CREATED)
def add_task_dependency(
    task_id: int,
    payload: StudyTaskDependencyCreate,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> StudyTaskDependencyRead:
    """``payload.depends_on_task_id`` zur Voraussetzung von ``task_id`` machen.

    Beide Aufgaben muessen dem Nutzer gehoeren, und beide muessen im
    selben Plan liegen: eine Abhaengigkeit ueber Plangrenzen hinweg waere
    eine Reihenfolge, die beim Sortieren des einen Plans sichtbar wird und
    im anderen nicht.
    """
    task = owned_or_404(db, StudyPlanTask, task_id, current_user.id)
    prerequisite = owned_or_404(db, StudyPlanTask, payload.depends_on_task_id, current_user.id)
    if task.id == prerequisite.id:
        raise _unprocessable("A study task cannot depend on itself")
    if task.study_plan_id != prerequisite.study_plan_id:
        raise _unprocessable("Dependencies are only allowed within the same study plan")
    if study_service.would_create_cycle(db, current_user.id, task.id, prerequisite.id):
        raise _unprocessable("The dependency would create a cycle")
    existing = db.get(StudyTaskDependency, (task.id, prerequisite.id))
    if existing is not None:
        return StudyTaskDependencyRead.model_validate(existing)
    dependency = StudyTaskDependency(user_id=current_user.id, task_id=task.id, depends_on_task_id=prerequisite.id)
    db.add(dependency)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict("Study task dependency already exists") from exc
    return StudyTaskDependencyRead.model_validate(dependency)


@router.delete("/{task_id}/dependencies/{depends_on_task_id}", response_model=MessageResponse)
def remove_task_dependency(
    task_id: int, depends_on_task_id: int, current_user: CurrentUser, db: DatabaseSession
) -> MessageResponse:
    owned_or_404(db, StudyPlanTask, task_id, current_user.id)
    dependency = db.get(StudyTaskDependency, (task_id, depends_on_task_id))
    if dependency is None or dependency.user_id != current_user.id:
        raise _not_found("Study task dependency not found")
    db.delete(dependency)
    db.commit()
    return MessageResponse(message="Study task dependency removed")
