from __future__ import annotations

import calendar
from datetime import datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session, joinedload

from app.core.time import as_utc, utc_now
from app.db.entities import (
    Exam,
    StudyPlan,
    StudyPlanTask,
    StudyTaskDependency,
    StudyTaskSeries,
    Topic,
)
from app.schemas.study import (
    StudyTaskPrerequisite,
    StudyTaskRead,
    StudyTaskRecurrence,
    StudyTaskStatus,
)

# Feste Tagesabstaende. "monthly" fehlt absichtlich: ein Monat ist nicht
# immer gleich lang, das braucht eine eigene Rechnung.
RECURRENCE_DAYS: dict[str, int] = {"daily": 1, "weekly": 7, "biweekly": 14}
RECURRENCES = ("daily", "weekly", "biweekly", "monthly")


class StudyPlanError(Exception):
    """Fehler, die der Task-Manager nicht in eine Datenbank-Kante pressen kann."""


def add_months(value: datetime, months: int) -> datetime:
    """Versetzt um ganze Monate und klemmt auf den letzten Tag des Monats.

    31. Januar plus einen Monat waere sonst der 3. Maerz. Fuer eine
    Faelligkeit ist der Monatsende der richtige Ersatz, nicht ein
    ueberlaufender Tag.
    """
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return value.replace(year=year, month=month, day=min(value.day, last_day))


def advance_due_on(value: datetime, recurrence: str) -> datetime:
    """Die naechste Faelligkeit einer Serie.

    Wirft ``StudyPlanError`` fuer ein unbekanntes ``recurrence``, weil ein
    stillschweigend fehlendes Intervall eine Serie einfrieren wuerde, ohne
    dass irgendetwas auffaellt.
    """
    current = as_utc(value)
    days = RECURRENCE_DAYS.get(recurrence)
    if days is not None:
        return current + timedelta(days=days)
    if recurrence == "monthly":
        return add_months(current, 1)
    raise StudyPlanError(f"Unbekannter Wiederholungstyp: {recurrence}")


def next_due_on(series: StudyTaskSeries) -> datetime | None:
    """Die Faelligkeit der naechsten Occurrence, oder ``None`` am Serienende.

    Zwei Grenzen beenden die Serie: ``until`` (Datum) und
    ``occurrence_limit`` (Anzahl). ``next_due_on`` ist bereits die
    Occurrence, die als naechstes erzeugt wird, deshalb wird hier nur noch
    das Ende geprueft.
    """
    candidate = series.next_due_on
    if candidate is None or series.status != "active":
        return None
    if series.until is not None and as_utc(candidate) > as_utc(series.until):
        return None
    if series.occurrence_limit is not None and series.occurrences >= series.occurrence_limit:
        return None
    return as_utc(candidate)


def peek_following_due_on(series: StudyTaskSeries) -> datetime | None:
    """Die Faelligkeit nach ``next_due_on``, ohne den Zaehler zu bewegen.

    Wird beim Anlegen gebraucht: die erste Occurrence entsteht sofort, und
    die Serie muss schon wissen, ob danach noch eine kommt.
    """
    current = next_due_on(series)
    if current is None:
        return None
    following = advance_due_on(current, series.recurrence)
    if series.until is not None and following > as_utc(series.until):
        return None
    if series.occurrence_limit is not None and series.occurrences + 1 >= series.occurrence_limit:
        return None
    return following


def advance_series(series: StudyTaskSeries) -> None:
    """Zaehler erhoehen und die naechste Faelligkeit eintragen.

    Nach dem Aufruf ist ``series.next_due_on`` die Occurrence, die erst mit
    dem naechsten Abschluss erzeugt wird - oder ``None``, wenn die Serie
    damit zu Ende ist.
    """
    current = next_due_on(series)
    if current is None:
        series.status = "finished"
        series.next_due_on = None
        return
    series.occurrences += 1
    following = advance_due_on(current, series.recurrence)
    if series.until is not None and following > as_utc(series.until):
        series.next_due_on = None
        series.status = "finished"
    elif series.occurrence_limit is not None and series.occurrences >= series.occurrence_limit:
        series.next_due_on = None
        series.status = "finished"
    else:
        series.next_due_on = following


def create_series_task(series: StudyTaskSeries, user_id: int, due_on: datetime, order_index: int) -> StudyPlanTask:
    """Die naechste Aufgabe einer Serie anlegen.

    Die Aufgabe erbt Vorlage, Dauer, Prioritaet und Fach aus der Serie. Sie
    bekommt bewusst keinen Termin (`scheduled_for`): bei einer Serie ist
    die Faelligkeit das Datum, nicht der Lernzeitpunkt.
    """
    return StudyPlanTask(
        user_id=user_id,
        study_plan_id=series.study_plan_id,
        topic_id=series.topic_id,
        series_id=series.id,
        title=series.title,
        description=series.description,
        duration_minutes=series.duration_minutes,
        due_date=as_utc(due_on),
        priority=series.priority,
        status="pending",
        order_index=order_index,
    )


def next_order_index(db: Session, study_plan_id: int) -> int:
    """Der naechste freie ``order_index`` in einem Plan.

    Notwendig, weil ``uq_study_plan_tasks_plan_order`` die Kombination aus
    Plan und Position eindeutig haelt: ein doppelter Index waere ein 409,
    kein Reihenfolge-Unentschied.
    """
    last = db.scalar(select(func.max(StudyPlanTask.order_index)).where(StudyPlanTask.study_plan_id == study_plan_id))
    return 0 if last is None else last + 1


def task_sort_clause(order: str) -> tuple:
    """Sortierklauseln je Bestellung.

    ``priority`` ist die Voreinstellung, weil genau das der Grund fuer die
    Spalte ist. Innerhalb einer Prioritaet gewinnt die Faelligkeit, danach
    der Termin, damit die Liste stabil bleibt.
    """
    nulls_last = StudyPlanTask.due_date.is_(None)
    if order == "due":
        return (nulls_last, StudyPlanTask.due_date, StudyPlanTask.id)
    if order == "position":
        return (StudyPlanTask.order_index, StudyPlanTask.id)
    if order == "priority":
        return (
            StudyPlanTask.priority.desc(),
            nulls_last,
            StudyPlanTask.due_date,
            StudyPlanTask.scheduled_for.is_(None),
            StudyPlanTask.scheduled_for,
            StudyPlanTask.id,
        )
    raise StudyPlanError(f"Unbekannte Sortierung: {order}")


def dependency_edges(db: Session, user_id: int) -> dict[int, list[int]]:
    """Alle Abhaengigkeiten des Nutzers als ``aufgabe -> vorgaenger``."""
    rows = db.execute(
        select(StudyTaskDependency.task_id, StudyTaskDependency.depends_on_task_id).where(
            StudyTaskDependency.user_id == user_id
        )
    ).all()
    edges: dict[int, list[int]] = {}
    for task_id, depends_on_task_id in rows:
        edges.setdefault(task_id, []).append(depends_on_task_id)
    return edges


def would_create_cycle(db: Session, user_id: int, task_id: int, depends_on_task_id: int) -> bool:
    """True, wenn die neue Kante einen Zyklus schliesst.

    Geprueft wird der Weg *von* ``depends_on_task_id`` zurueck zu
    ``task_id``: wartet die Voraussage mittelbar auf die Aufgabe, die auf
    sie warten soll, kann keiner der beiden jemals erledigt werden.
    """
    if task_id == depends_on_task_id:
        return True
    edges = dependency_edges(db, user_id)
    seen: set[int] = set()
    stack = [depends_on_task_id]
    while stack:
        current = stack.pop()
        if current == task_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(edges.get(current, ()))
    return False


def open_prerequisites_by_task(db: Session, user_id: int, task_ids: set[int]) -> dict[int, list[StudyPlanTask]]:
    """Offene Vorbedingungen je Aufgabe, gruppiert.

    ``cancelled`` zaehlt nicht als erledigt: eine stornierte Aufgabe wird
    nie fertig, und ihre Waerter duerfen nicht fuer immer blockieren. Weil
    das eine gebraeuchliche Operation ist (jede Listenansicht), kommt sie
    mit einem Lauf und nicht pro Aufgabe.
    """
    if not task_ids:
        return {}
    rows = db.execute(
        select(StudyTaskDependency.task_id, StudyPlanTask)
        .join(StudyPlanTask, StudyPlanTask.id == StudyTaskDependency.depends_on_task_id)
        .where(
            StudyTaskDependency.user_id == user_id,
            StudyTaskDependency.task_id.in_(task_ids),
            StudyPlanTask.status.notin_(("completed", "cancelled")),
        )
        .order_by(StudyPlanTask.order_index, StudyPlanTask.id)
    ).all()
    grouped: dict[int, list[StudyPlanTask]] = {}
    for task_id, task in rows:
        grouped.setdefault(task_id, []).append(task)
    return grouped


def blocking_task_ids(db: Session, user_id: int, task_ids: set[int]) -> dict[int, list[int]]:
    """Je Aufgabe die Aufgaben, die auf sie warten."""
    if not task_ids:
        return {}
    rows = db.execute(
        select(StudyTaskDependency.depends_on_task_id, StudyTaskDependency.task_id).where(
            StudyTaskDependency.user_id == user_id,
            StudyTaskDependency.depends_on_task_id.in_(task_ids),
        )
    ).all()
    grouped: dict[int, list[int]] = {}
    for depends_on_task_id, task_id in rows:
        grouped.setdefault(depends_on_task_id, []).append(task_id)
    for values in grouped.values():
        values.sort()
    return grouped


def blocking_prerequisites(db: Session, user_id: int, task_id: int) -> list[StudyPlanTask]:
    """Offene Vorbedingungen einer einzelnen Aufgabe, fuer den Start-Check."""
    return open_prerequisites_by_task(db, user_id, {task_id}).get(task_id, [])


def task_query(user_id: int) -> Select[tuple[StudyPlanTask]]:
    """Basisabfrage einer Aufgabe mit Fach und Thema.

    ``joinedload`` statt eines zweiten Laufs: die Abbildung braucht bei
    jedem Task das Fach, und ein Lazy Load je Zeile ist der klassische
    N+1, der auf dem Dashboard mit 60 Aufgaben auffaellt.
    """
    return (
        select(StudyPlanTask)
        .options(joinedload(StudyPlanTask.topic).joinedload(Topic.subject))
        .where(StudyPlanTask.user_id == user_id)
    )


def _exam_titles(db: Session, user_id: int, tasks: list[StudyPlanTask]) -> dict[int, str]:
    """Klausurtitel je Klausur-ID, damit die Spalte einen Text zeigt.

    Der Titel kommt mit, statt nur der Nummer: eine Aufgabe, die an einer
    Klausur haengt, ist ohne deren Namen nicht zuzuordnen.
    """
    exam_ids = {task.exam_id for task in tasks if task.exam_id is not None}
    if not exam_ids:
        return {}
    rows = db.execute(select(Exam.id, Exam.title).where(Exam.user_id == user_id, Exam.id.in_(exam_ids))).all()
    return {int(exam_id): title for exam_id, title in rows}


def read_tasks(db: Session, user_id: int, tasks: list[StudyPlanTask]) -> list[StudyTaskRead]:
    """Eine Aufgabenliste inklusive Abhaengigkeiten und Klausurnamen lesen.

    Drei Abfragen statt einer je Task: die Abhaengigkeiten brauchen eine
    Gruppenabfrage, die Klausurtitel eine weitere. Genau das ist der
    N+1-Fall, der auf dem Dashboard erst mit 60 Aufgaben auffaellt.
    """
    if not tasks:
        return []
    ids = {task.id for task in tasks}
    blocked_by = open_prerequisites_by_task(db, user_id, ids)
    blocking = blocking_task_ids(db, user_id, ids)
    titles = _exam_titles(db, user_id, tasks)
    reads: list[StudyTaskRead] = []
    for task in tasks:
        subject = task.topic.subject if task.topic else None
        series = task.series
        reads.append(
            StudyTaskRead(
                id=task.id,
                study_plan_id=task.study_plan_id,
                topic_id=task.topic_id,
                subject_name=subject.name if subject else None,
                subject_color=subject.color if subject else None,
                topic_name=task.topic.name if task.topic else None,
                exam_id=task.exam_id,
                exam_title=titles.get(task.exam_id) if task.exam_id is not None else None,
                series_id=task.series_id,
                series_recurrence=cast(StudyTaskRecurrence, series.recurrence) if series else None,
                title=task.title,
                description=task.description,
                duration_minutes=task.duration_minutes,
                scheduled_for=as_utc(task.scheduled_for) if task.scheduled_for is not None else None,
                due_date=as_utc(task.due_date) if task.due_date is not None else None,
                priority=task.priority,
                status=cast(StudyTaskStatus, task.status),
                order_index=task.order_index,
                completed_at=as_utc(task.completed_at) if task.completed_at is not None else None,
                blocked_by=[
                    StudyTaskPrerequisite(
                        task_id=prerequisite.id,
                        title=prerequisite.title,
                        status=cast(StudyTaskStatus, prerequisite.status),
                        due_date=as_utc(prerequisite.due_date) if prerequisite.due_date is not None else None,
                    )
                    for prerequisite in blocked_by.get(task.id, [])
                ],
                blocking_task_ids=blocking.get(task.id, []),
            )
        )
    return reads


def exam_topics(db: Session, user_id: int, subject_id: int | None) -> list[Topic]:
    """Klausurthemen nach Lernstand sortiert: das schwaechste zuerst.

    Die Reihenfolge ist die Lernreihenfolge. Wer am woechsten Thema
    anfangen will, faengt nicht bei null Konfidenz an. Ohne
    Fortschrittswert gilt ein Thema als unsicher und kommt nach vorn.
    """
    if subject_id is None:
        return []
    topics = db.scalars(
        select(Topic)
        .options(joinedload(Topic.progress))
        .where(Topic.user_id == user_id, Topic.subject_id == subject_id)
        .order_by(Topic.name, Topic.id)
    ).all()
    return sorted(
        topics,
        key=lambda topic: (
            float(topic.progress.confidence) if topic.progress is not None else 0.0,
            topic.name,
            topic.id,
        ),
    )


def topic_priority(confidence: float) -> int:
    """Prioritaet aus der Konfidenz: 0 bis 4.

    Ohne Fortschrittswert gilt das Thema als unsicher, also hoechste
    Prioritaet. Ein Lernstand, den es nicht gibt, ist keine gute Nachricht.
    """
    bounded = min(1.0, max(0.0, confidence))
    return max(0, min(4, round((1.0 - bounded) * 4)))


def plan_day_starts(
    zone: ZoneInfo,
    exam_date: datetime,
    lead_days: int,
    count: int,
    scheduled_hour: int,
    today: datetime | None = None,
) -> list[datetime]:
    """Die Lerntage eines Klausurplans, in UTC.

    Der Plan reicht ``lead_days`` Tage zurueck, aber nicht vor heute: eine
    Klausur von vorgestern erzeugt Aufgaben fuer heute, nicht fuer einen
    vergangenen Tag. Der Klausurtag selbst ist Lerntag, damit
    ``lead_days = 1`` nicht den Vortag meint.

    Die Themen bekommen die Tage in Lernreihenfolge von vorn, also das
    schwaechste zuerst - es braucht am meisten Vorlauf. Mehr Themen als
    Tage landen deshalb am Ende des Fensters, nicht am Anfang.
    """
    local_today = (today or utc_now()).astimezone(zone)
    last_day = as_utc(exam_date).astimezone(zone).date()
    if last_day < local_today.date():
        last_day = local_today.date()
    span = max(0, min(lead_days, (last_day - local_today.date()).days + 1))
    first_day = last_day - timedelta(days=span)
    scheduled_at = time(hour=scheduled_hour)
    days: list[datetime] = []
    for index in range(count):
        # `combine` statt `date.replace`: ein `date` kennt keine Stunde.
        local = datetime.combine(first_day + timedelta(days=min(index, span)), scheduled_at, tzinfo=zone)
        days.append(as_utc(local))
    return days


def generate_exam_tasks(
    db: Session,
    exam: Exam,
    plan: StudyPlan,
    user_id: int,
    zone: ZoneInfo,
    duration_minutes: int,
    lead_days: int,
    scheduled_hour: int,
    only_missing: bool,
    chain_dependencies: bool,
) -> tuple[list[StudyPlanTask], int]:
    """Aus den Klausurthemen Aufgaben erzeugen.

    Liefert die neu angelegten Aufgaben und die Zahl der uebersprungenen
    Themen. Uebersprungen wird ein Thema, wenn im selben Plan fuer dieselbe
    Klausur und dasselbe Thema schon eine offene Aufgabe haengt - sonst
    waere ein zweiter Klick auf "Aufgaben erzeugen" eine Duplikatserie.
    """
    topics = exam_topics(db, user_id, exam.subject_id)
    if not topics:
        raise StudyPlanError("Zu dieser Klausur sind keine Themen hinterlegt")

    skip_topic_ids: set[int] = set()
    if only_missing:
        existing = db.scalars(
            select(StudyPlanTask.topic_id).where(
                StudyPlanTask.study_plan_id == plan.id,
                StudyPlanTask.exam_id == exam.id,
                StudyPlanTask.topic_id.is_not(None),
                StudyPlanTask.status != "cancelled",
            )
        ).all()
        skip_topic_ids = {topic_id for topic_id in existing if topic_id is not None}

    pending = [topic for topic in topics if topic.id not in skip_topic_ids]
    skipped = len(topics) - len(pending)
    if not pending:
        return [], skipped

    days = plan_day_starts(zone, exam.date, lead_days, len(pending), scheduled_hour)
    end_of_day = time(hour=23, minute=59)
    order_index = next_order_index(db, plan.id)
    created: list[StudyPlanTask] = []
    previous: StudyPlanTask | None = None
    for offset, (topic, scheduled_for) in enumerate(zip(pending, days, strict=True)):
        progress = topic.progress
        task = StudyPlanTask(
            user_id=user_id,
            study_plan_id=plan.id,
            topic_id=topic.id,
            exam_id=exam.id,
            title=f"{topic.name} lernen",
            description=f"Vorbereitung auf {exam.title}",
            duration_minutes=duration_minutes,
            scheduled_for=scheduled_for,
            # Faellig ist die Aufgabe am Ende ihres eigenen Lerntags, nicht
            # erst am Klausurtag. `end_of_day` haengt an denselben Tag, weil
            # `scheduled_for` bereits in die Ortszeit des Nutzers zeigt.
            due_date=as_utc(datetime.combine(scheduled_for.astimezone(zone).date(), end_of_day, tzinfo=zone)),
            priority=topic_priority(float(progress.confidence) if progress is not None else 0.0),
            status="pending",
            order_index=order_index + offset,
        )
        db.add(task)
        db.flush()
        if chain_dependencies and previous is not None:
            db.add(StudyTaskDependency(user_id=user_id, task_id=task.id, depends_on_task_id=previous.id))
        previous = task
        created.append(task)
    return created, skipped


def plan_open_task_counts(db: Session, user_id: int) -> dict[int, tuple[int, int]]:
    """``plan_id -> (aufgaben, davon offen)`` fuer die Planliste."""
    open_marker = case((StudyPlanTask.status.notin_(("completed", "cancelled")), 1), else_=0)
    rows = db.execute(
        select(StudyPlanTask.study_plan_id, func.count(StudyPlanTask.id), func.sum(open_marker))
        .where(StudyPlanTask.user_id == user_id)
        .group_by(StudyPlanTask.study_plan_id)
    ).all()
    counts: dict[int, tuple[int, int]] = {}
    for plan_id, total, open_total in rows:
        counts[plan_id] = (int(total or 0), int(open_total or 0))
    return counts
