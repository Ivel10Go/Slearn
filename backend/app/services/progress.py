"""Lernfortschritt: Lernstand je Thema und Auswertung des Lernprotokolls.

Zwei Dinge wohnen hier, weil sie dieselbe Quelle lesen:

* **Lernstand** (``LearningProgress``): eine Konfidenz je Thema, die die
  Lernende selbst einschaetzt, und daraus ein schlichtes Wiederholungsintervall.
  Der Plan bezieht dieselbe Konfidenz fuer Reihenfolge und Prioritaet ein -
  ein geschriebener Wert aendert also sichtbar, was der Klausur-Generator
  erzeugt.
* **Lernprotokoll** (``StudySession``): gelernte Zeit je Fach, Verteilung auf
  die Wochentage, Serie und Wochenziel.

Die Zeitfallen, die hier entschieden werden:

* **Alles rechnet in der Zeitzone des Nutzers.** ``StudySession.started_at``
  liegt als UTC in der Datenbank. Ein Lernblock von Sonntag 23:30 bis Montag
  00:10 ist in ``Europe/Berlin`` ein Sonntag, in UTC ein Montag. Fuer die
  Wochentagsverteilung und die Serie wird deshalb jede Zeile genau einmal
  nach ``zone`` umgerechnet und danach nur noch mit ``date`` gerechnet -
  dasselbe Muster wie ``_next_lesson()`` im Willkommenssatz.
* **Eine Session zaehlt an dem Tag, an dem sie begonnen wurde.** Der Start ist
  der Lerntag; wann sie endet, ist fuer die Statistik nebensache.
* **Offene Sessions zaehlen nicht mit.** Sie haben keine Dauer, also wuerden
  sie in den Summen fehlen und beim Beenden ein zweites Mal auftauchen. Sie
  stehen deshalb getrennt in ``open_sessions`` und ``active_minutes``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.time import as_utc, utc_now
from app.db.entities import (
    LearningProgress,
    StudyPlanTask,
    StudySession,
    Subject,
    Topic,
    UserSettings,
)
from app.schemas.progress import (
    ConfidenceStep,
    DayTotal,
    LearningInsights,
    StreakRead,
    SubjectStudyTime,
    TopicProgressRead,
    WeekdayStudyTime,
    WeeklyGoalRead,
)

# Wo das Wochenziel liegt, wenn es keines gibt: fuenf Stunden in einer
# Kalenderwoche sind eine erreichbare Groesse, kein Wunschwert.
DEFAULT_WEEKLY_GOAL_MINUTES = 300

# Fenster der Serie. Ein laengerer Blick kostet nur Rechenzeit und beantwortet
# keine andere Frage: wer 300 Tage nichts gelernt hat, hat keine Serie.
STREAK_WINDOW_DAYS = 365

# Fenster der Wochentagsverteilung. Gewohnheiten verschieben sich, und ein
# Jahr ist eine Antwort auf eine andere Frage als "wann lerne ich meistens".
WEEKDAY_WINDOW_DAYS = 84

# Ein Tag zaehlt erst, wenn mindestens so viele Minuten darauf liegen.
# Sonst wuerde ein Start ohne Stopp - oder ein sofort gestoppter Block - eine
# Serie erzeugen, die nie stattgefunden hat.
MIN_DAY_MINUTES = 1

# Schluessel des Wochenziels in `settings.preferences`. Bewusst dort und nicht
# in einer eigenen Spalte: die Spalte gibt es schon, und ein zweiter Ort fuer
# Nutzereinstellungen waere eine dritte Wahrheit ueber dieselbe Sache.
GOAL_PREFERENCE_KEY = "weekly_goal_minutes"

NO_SUBJECT_LABEL = "Ohne Fach"

# 0 = Montag, 6 = Sonntag - wie `datetime.weekday()` und `startOfWeek()`.
WEEKDAY_LABELS: tuple[str, ...] = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)

# Die fuenf Konfidenzstufen. Jede Stufe hat **eine** Bezeichnung und **ein**
# Intervall, damit die Oberflaeche nicht an einer zweiten Tabelle neben der
# arbeitet: sie schickt den Wert, das Backend nennt Label und Intervall.
#
# Bewusst keine Kurve, kein faktor und keine Zufallsjaehrung: die Regel soll in
# einem Satz erklaerbar sein - "je sicherer das Thema, desto laenger bis zur
# naechsten Wiederholung" - und der Lernende soll sie ohne Code lesen koennen.
CONFIDENCE_STEPS: tuple[tuple[float, str, int], ...] = (
    (0.0, "unsicher", 1),
    (0.25, "geübt", 3),
    (0.5, "sicher", 7),
    (0.75, "fest", 14),
    (1.0, "gefestigt", 30),
)


def step_for(confidence: float) -> tuple[float, str, int]:
    """Die Stufe zu einer Konfidenz: ``(untere Grenze, Bezeichnung, Intervall)``.

    Gesucht ist die hoechste Stufe, deren untere Grenze noch passt. Alles
    unterhalb von 0.0 faellt auf die unterste Stufe, alles oberhalb von 1.0
    auf die oberste - die Pruefklause der Datenbank laesst beides nicht zu,
    der Aufrufer schon.
    """
    bounded = min(1.0, max(0.0, float(confidence)))
    chosen = CONFIDENCE_STEPS[0]
    for step in CONFIDENCE_STEPS:
        if bounded >= step[0]:
            chosen = step
    return chosen


def confidence_label(confidence: float) -> str:
    return step_for(confidence)[1]


def confidence_steps() -> list[ConfidenceStep]:
    """Die Stufenliste fuer die Oberflaeche: Wert, Bezeichnung, Intervall.

    Sie geht als Vertrag mit jeder Antwort mit. Eine eigene Tabelle im Frontend
    waere eine zweite Wahrheit ueber dieselbe Frage - und die Waelle, in der die
    Auswahl "sicher" heisst, das Backend aber mit sieben Tagen rechnet.
    """
    return [
        ConfidenceStep(value=wert, label=bezeichnung, interval_days=tage) for wert, bezeichnung, tage in CONFIDENCE_STEPS
    ]


def review_interval_days(confidence: float) -> int:
    return step_for(confidence)[2]


def next_review_on(last_reviewed_at: datetime | None, confidence: float, zone: ZoneInfo) -> date | None:
    """Der Kalendertag der naechsten Wiederholung, oder ``None``.

    Ohne ``last_reviewed_at`` gibt es keinen Anker und damit keinen Termin -
    nicht "heute", denn das waere eine Behauptung, die niemand belegen kann.
    Der Termin ist ein **Datum in der Zeitzone des Nutzers** und kein
    Zeitstempel: 23:30 Ortszeit ist nicht der Unterschied zwischen heute und
    morgen, der Mittag schon.
    """
    if last_reviewed_at is None:
        return None
    reviewed_on = as_utc(last_reviewed_at).astimezone(zone).date()
    return reviewed_on + timedelta(days=review_interval_days(confidence))


def week_bounds(today: date) -> tuple[date, date]:
    """Montag und Sonntag der Woche, in der ``today`` liegt."""
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


@dataclass
class DayBucket:
    """Gelernte Minuten und Sessions eines Kalendertages."""

    minutes: int = 0
    sessions: int = 0

    def add(self, minutes: int) -> None:
        self.minutes += minutes
        self.sessions += 1

    @property
    def counts_as_studied(self) -> bool:
        return self.minutes >= MIN_DAY_MINUTES


def _credited_minutes(completed_at: datetime | None, duration_minutes: int | None) -> int:
    """Wie viele Minuten einer Session angerechnet werden.

    Eine offene Session zaehlt **null**, nicht ihre bisherige Laufzeit. Sonst
    waere jede laufende Session zweimal in der Statistik: einmal als
    Zwischenstand und nach dem Beenden noch einmal mit der echten Dauer.
    """
    if completed_at is None or duration_minutes is None:
        return 0
    return max(0, duration_minutes)


def local_day(value: datetime, zone: ZoneInfo) -> date:
    """Der Kalendertag eines Zeitstempels in der Zeitzone des Nutzers."""
    return as_utc(value).astimezone(zone).date()


def _session_slices(db: Session, user_id: int, since: datetime) -> list[tuple[datetime, datetime | None, int | None]]:
    """Die Lernsessions eines Nutzers seit ``since``, als schlanke Tupel.

    Nur drei Spalten und keine ORM-Objekte: ausgewertet werden hier nur
    Zeitstempel und Dauer, und eine Zeile je Task im Speicher waere bei
    dreissigtausend Sessions der Unterschied zwischen einer Karte und einem
    ausgelasteten Server.
    """
    rows = db.execute(
        select(StudySession.started_at, StudySession.completed_at, StudySession.duration_minutes).where(
            StudySession.user_id == user_id, StudySession.started_at >= since
        )
    ).all()
    return [(row[0], row[1], row[2]) for row in rows]


def _buckets_by_day(slices: list[tuple[datetime, datetime | None, int | None]], zone: ZoneInfo) -> dict[date, DayBucket]:
    """Tage mit angerechneter Lernzeit, in der Zeitzone des Nutzers.

    Der Key ist bewusst ein ``date`` in ``zone``. Nach diesem Schritt wird
    nicht mehr umgerechnet - sonst koennte dieselbe Session am Monatswechsel
    in zwei Tagen landen.
    """
    buckets: dict[date, DayBucket] = {}
    for started_at, completed_at, duration_minutes in slices:
        minutes = _credited_minutes(completed_at, duration_minutes)
        if minutes <= 0:
            continue
        day = local_day(started_at, zone)
        bucket = buckets.get(day)
        if bucket is None:
            bucket = DayBucket()
            buckets[day] = bucket
        bucket.add(minutes)
    return buckets


def _open_session_minutes(
    slices: list[tuple[datetime, datetime | None, int | None]],
    zone: ZoneInfo,
    today: date,
    now: datetime,
) -> int:
    """Laufzeit der offenen Sessions, die heute begonnen haben.

    Getrennt von allen Summen, damit die Lernende sieht, dass gerade gelernt
    wird, ohne dass der Wert spaeter doppelt auftaucht.
    """
    minutes = 0
    for started_at, completed_at, _duration in slices:
        if completed_at is not None:
            continue
        if local_day(started_at, zone) != today:
            continue
        elapsed = int((as_utc(now) - as_utc(started_at)).total_seconds() // 60)
        minutes += max(0, elapsed)
    return minutes


def current_streak(studied_days: set[date], today: date) -> int:
    """Die noch laufende Serie aufeinanderfolgender Lerntage, ab heute rueckwaerts.

    **Heute ist noch offen.** Wer morgens in die Karte sieht und gestern
    gelernt hat, hat die Serie nicht schon verloren - sonst wuerde die Zahl
    jeden Morgen um eins fallen und der Begriff nichts mehr bedeuten. Erst wenn
    gestern *und* heute nichts gelernt wurde, ist die Serie bei null.
    """
    if today in studied_days:
        cursor = today
    elif today - timedelta(days=1) in studied_days:
        cursor = today - timedelta(days=1)
    else:
        return 0
    length = 0
    while cursor in studied_days:
        length += 1
        cursor -= timedelta(days=1)
    return length


def longest_streak(studied_days: set[date]) -> int:
    """Die laengste Serie innerhalb des uebergebenen Fensters."""
    best = 0
    run = 0
    previous: date | None = None
    for day in sorted(studied_days):
        run = run + 1 if previous is not None and day == previous + timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return best


def _subject_totals(db: Session, user_id: int) -> dict[int | None, tuple[int, int, datetime | None]]:
    """``Fach -> (Minuten, Sessions, zuletzt gelernt)`` ueber die ganze Historie.

    Eine Summe braucht keine Zeitzone, deshalb laeuft das als eine
    SQL-Aggregation statt in Python. Der Weg von der Session zum Fach geht ueber
    ``StudyPlanTask.topic_id -> Topic.subject_id``; eine Aufgabe ohne Thema hat
    damit kein Fach und landet unter ``None``.
    """
    query = (
        select(
            Topic.subject_id,
            func.coalesce(func.sum(StudySession.duration_minutes), 0),
            func.count(StudySession.id),
            func.max(StudySession.started_at),
        )
        .select_from(StudySession)
        .join(StudyPlanTask, StudyPlanTask.id == StudySession.task_id)
        .outerjoin(Topic, Topic.id == StudyPlanTask.topic_id)
        .where(
            StudySession.user_id == user_id,
            StudySession.completed_at.is_not(None),
            StudySession.duration_minutes.is_not(None),
        )
        .group_by(Topic.subject_id)
    )
    rows = db.execute(query).all()
    return {row[0]: (int(row[1] or 0), int(row[2] or 0), row[3]) for row in rows}


def _topic_totals(db: Session, user_id: int) -> dict[int, tuple[int, int]]:
    """``Thema -> (Minuten, Sessions)`` aus den Lernsessions seiner Aufgaben."""
    query = (
        select(
            StudyPlanTask.topic_id,
            func.coalesce(func.sum(StudySession.duration_minutes), 0),
            func.count(StudySession.id),
        )
        .select_from(StudySession)
        .join(StudyPlanTask, StudyPlanTask.id == StudySession.task_id)
        .where(
            StudySession.user_id == user_id,
            StudySession.completed_at.is_not(None),
            StudySession.duration_minutes.is_not(None),
            StudyPlanTask.topic_id.is_not(None),
        )
        .group_by(StudyPlanTask.topic_id)
    )
    rows = db.execute(query).all()
    return {int(row[0]): (int(row[1] or 0), int(row[2] or 0)) for row in rows}


def weekly_goal_minutes(db: Session, user_id: int) -> int:
    """Das Wochenziel aus den Nutzereinstellungen, sonst der Standard.

    Drei Faelle, alle mit demselben Ergebnis: keine ``settings``-Zeile (der
    Bootstrap legt sie an, ein Testkonto nicht), kein Schluessel darin oder ein
    Schluessel mit dem falschen Typ. Eine kaputte Einstellung darf den Lernenden
    nicht den ganzen Fortschritt kosten.
    """
    settings = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if settings is None:
        return DEFAULT_WEEKLY_GOAL_MINUTES
    preferences: Any = settings.preferences
    if not isinstance(preferences, dict):
        return DEFAULT_WEEKLY_GOAL_MINUTES
    value = preferences.get(GOAL_PREFERENCE_KEY)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return DEFAULT_WEEKLY_GOAL_MINUTES
    return value


def set_weekly_goal_minutes(db: Session, user_id: int, goal_minutes: int) -> int:
    """Das Wochenziel speichern und den gespeicherten Wert zurueckgeben.

    Die Zeile wird bei Bedarf angelegt, und die uebrigen Schluessel in
    ``preferences`` bleiben stehen: das ist ein fremdes Verzeichnis, und ein
    Lernziel hat dort nichts zu suchen.
    """
    settings = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if settings is None:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
    preferences: dict[str, Any] = dict(settings.preferences) if isinstance(settings.preferences, dict) else {}
    preferences[GOAL_PREFERENCE_KEY] = goal_minutes
    settings.preferences = preferences
    db.flush()
    return goal_minutes


def _progress_read(
    topic: Topic,
    progress: LearningProgress | None,
    minutes: int,
    sessions: int,
    zone: ZoneInfo,
    today: date,
) -> TopicProgressRead:
    """Ein Thema als Antwortobjekt, mit Konfidenzstufe und Wiederholungstermin."""
    confidence = float(progress.confidence) if progress is not None else 0.0
    step_value, label, _days = step_for(confidence)
    reviewed_at = progress.last_reviewed_at if progress is not None else None
    upcoming = next_review_on(reviewed_at, confidence, zone)
    # Ohne Anker kein Termin und kein Intervall: beides waere eine Zusage, die
    # auf keinem Datum beruht. Die Bezeichnung "unsicher" bleibt trotzdem
    # stehen, denn der fehlende Lernstand ist eine Aussage ueber das Thema.
    interval_days = review_interval_days(confidence) if upcoming is not None else None
    days_until = (upcoming - today).days if upcoming is not None else None
    subject = topic.subject
    return TopicProgressRead(
        topic_id=topic.id,
        name=topic.name,
        subject_id=topic.subject_id,
        subject_name=subject.name if subject else None,
        confidence=confidence,
        confidence_step=step_value,
        confidence_label=label,
        last_reviewed_at=as_utc(reviewed_at) if reviewed_at is not None else None,
        notes=progress.notes if progress is not None else None,
        interval_days=interval_days,
        next_review_on=upcoming,
        days_until_review=days_until,
        is_due=upcoming is not None and days_until is not None and days_until <= 0,
        studied_minutes=minutes,
        session_count=sessions,
        updated_at=as_utc(progress.updated_at) if progress is not None else as_utc(topic.updated_at),
    )


def topic_progress(
    db: Session,
    user_id: int,
    zone: ZoneInfo,
    now: datetime | None = None,
    subject_id: int | None = None,
) -> list[TopicProgressRead]:
    """Der Lernstand aller Themen, optional auf ein Fach eingeschraenkt.

    Sortiert nach dem naechsten faelligen Termin, danach nach Konfidenz
    aufsteigend: was zuerst wiederholt werden muss, steht oben, und ein Thema
    ohne Wiederholungstermin wandert nach unten statt ganz vorne zu stehen.
    """
    today = as_utc(now or utc_now()).astimezone(zone).date()
    totals = _topic_totals(db, user_id)
    query = (
        select(Topic)
        .options(joinedload(Topic.subject), joinedload(Topic.progress))
        .where(Topic.user_id == user_id)
    )
    if subject_id is not None:
        query = query.where(Topic.subject_id == subject_id)
    topics = db.scalars(query.order_by(Topic.name, Topic.id)).all()
    reads = [
        _progress_read(topic, topic.progress, *totals.get(topic.id, (0, 0)), zone=zone, today=today) for topic in topics
    ]
    return sorted(reads, key=_due_first)


def one_topic_progress(
    db: Session,
    user_id: int,
    topic_id: int,
    zone: ZoneInfo,
    now: datetime | None = None,
) -> TopicProgressRead | None:
    """Ein einzelnes Thema als Antwortobjekt, oder ``None`` wenn es dem Nutzer nicht gehoert.

    Nach dem Schreiben braucht es den fertigen Antwortkoerper, und der darf
    nicht aus einem anderen Weg gebaut werden als beim Lesen - sonst zeigt die
    Oberflaeche nach dem Speichern einen anderen Termin als die naechste
    Ladespalte. Deshalb laeuft auch diese Antwort durch dieselbe Funktion.
    """
    topic = db.scalar(
        select(Topic)
        .options(joinedload(Topic.subject), joinedload(Topic.progress))
        .where(Topic.id == topic_id, Topic.user_id == user_id)
        # `populate_existing`: beim Schreiben ist das Thema derselben Sitzung
        # schon geladen, und ein bereits geladenes Attribut wird sonst nicht
        # erneuert. Die Antwort zeigte dann den alten Lernstand - der Aufruf
        # waere still erfolgreich und die Oberflaeche zeigte etwas Falsches.
        .execution_options(populate_existing=True)
    )
    if topic is None:
        return None
    minutes, sessions = _topic_totals(db, user_id).get(topic_id, (0, 0))
    today = local_day(now or utc_now(), zone)
    return _progress_read(topic, topic.progress, minutes, sessions, zone, today)


def _due_first(read: TopicProgressRead) -> tuple[int, int, float, str, int]:
    """Sortierschluessel: faellig zuerst, dann der naechste Termin, dann die
    niedrigste Konfidenz, und zuletzt der Name, damit die Reihenfolge stabil
    bleibt.

    Die zweite Zahl ist bei faelligen Themen negativ und zieht sie deshalb vor
    einen faelligen Termin, der erst in fuenf Tagen liegt. Das ist gewollt:
    ueberfaellig schlaegt knapp faellig.
    """
    due_rank = 0 if read.is_due else (1 if read.next_review_on is not None else 2)
    return (due_rank, read.days_until_review or 0, read.confidence, read.name, read.topic_id)


def _weekday_distribution(
    buckets: dict[date, DayBucket],
    today: date,
) -> list[WeekdayStudyTime]:
    """Lernzeit je Wochentag im Fenster der letzten ``WEEKDAY_WINDOW_DAYS`` Tage.

    Alle sieben Tage werden geliefert, auch die ohne Lernzeit: eine Luecke ist
    eine Aussage, und eine Balkenliste mit Luecken liest sich anders als eine,
    bei der die Luecken fehlen.
    """
    first_day = today - timedelta(days=WEEKDAY_WINDOW_DAYS - 1)
    totals: dict[int, DayBucket] = {weekday: DayBucket() for weekday in range(7)}
    for day, bucket in buckets.items():
        if day < first_day or day > today:
            continue
        totals[day.weekday()].minutes += bucket.minutes
        totals[day.weekday()].sessions += bucket.sessions
    return [
        WeekdayStudyTime(
            weekday=weekday,
            label=WEEKDAY_LABELS[weekday],
            minutes=totals[weekday].minutes,
            sessions=totals[weekday].sessions,
        )
        for weekday in range(7)
    ]


def _subject_distribution(db: Session, user_id: int, totals: dict[int | None, tuple[int, int, datetime | None]]) -> list[SubjectStudyTime]:
    """Lernzeit je Fach, absteigend nach Minuten.

    Die Prozentzahl bezieht sich auf die Summe ueber alle Faecher einschliesslich
    des Fachlos-Korbs - sonst waeren es nicht die Anteile an der Lernzeit, die
    man in der Summe sieht.
    """
    subject_ids = [key for key in totals if key is not None]
    names: dict[int, str] = {}
    if subject_ids:
        names = {
            int(subject_id): name
            for subject_id, name in db.execute(
                select(Subject.id, Subject.name).where(Subject.user_id == user_id, Subject.id.in_(subject_ids))
            ).all()
        }
    overall = sum(minutes for minutes, _sessions, _last in totals.values())
    ordered = sorted(totals.items(), key=lambda entry: (-entry[1][0], names.get(entry[0] or 0, "")))
    return [
        SubjectStudyTime(
            subject_id=subject_id,
            subject_name=names.get(subject_id, NO_SUBJECT_LABEL) if subject_id is not None else NO_SUBJECT_LABEL,
            minutes=minutes,
            sessions=sessions,
            share_percent=round((minutes / overall) * 100, 1) if overall else 0.0,
            last_studied_at=as_utc(last) if last is not None else None,
        )
        for subject_id, (minutes, sessions, last) in ordered
    ]


def _weekly_read(
    buckets: dict[date, DayBucket],
    goal_minutes: int,
    monday: date,
    sunday: date,
    today: date,
) -> WeeklyGoalRead:
    """Wochenziel und Ist-Stand derselben Woche.

    Das Ziel wird **nicht** anteilig auf die vergangenen Tage gekuerzt: die Zahl
    ist das Wochenziel, und ein Rueckstand an einem Mittwoch ist eine
    Information, kein Rechenfehler.
    """
    week_days = [monday + timedelta(days=offset) for offset in range(7)]
    minutes = sum(buckets[day].minutes for day in week_days if day in buckets)
    days_studied = sum(1 for day in week_days if day in buckets and buckets[day].counts_as_studied)
    return WeeklyGoalRead(
        goal_minutes=goal_minutes,
        studied_minutes=minutes,
        goal_reached=minutes >= goal_minutes,
        percent=round((minutes / goal_minutes) * 100, 1) if goal_minutes else 0.0,
        days_studied=days_studied,
        days_remaining=(sunday - today).days + 1,
        week_starts_on=monday,
        week_ends_on=sunday,
    )


def learning_insights(
    db: Session,
    user_id: int,
    zone: ZoneInfo,
    timezone_name: str,
    now: datetime | None = None,
) -> LearningInsights:
    """Das Lernprotokoll ausgewertet: Serie, Wochenziel, Fach und Wochentag.

    ``now`` ist ein Parameter und nicht ueberall ``utc_now()``, weil die
    Auswertung sonst in keinem Test einen bestimmten Tag treffen koennte - und
    ein Streak, den man nur am echten Heute pruefen kann, ist kein Test, sondern
    eine Behauptung.
    """
    current = as_utc(now or utc_now())
    local_now = current.astimezone(zone)
    today = local_now.date()
    monday, sunday = week_bounds(today)

    # Eine einzige Abfrage fuer alles Zeitbezogene: Streak, Tages- und
    # Wochenwerte sowie die Wochentagsverteilung kommen aus denselben Zeilen.
    # Damit kann keine Session in zwei Summen auftauchen, weil es keine
    # zweite gibt.
    window_start = as_utc(datetime.combine(today - timedelta(days=STREAK_WINDOW_DAYS - 1), time.min, tzinfo=zone))
    slices = _session_slices(db, user_id, window_start)
    buckets = _buckets_by_day(slices, zone)
    studied_days = {day for day, bucket in buckets.items() if bucket.counts_as_studied}

    # Auch die offenen Sessions kommen aus diesem Fenster. Eine laufende Session
    # aelter als ein Jahr ist kein Fall, fuer den es sich lohnt, das Fenster
    # zu verlassen.
    open_sessions = [row for row in slices if row[1] is None]
    subject_totals = _subject_totals(db, user_id)
    overall_minutes = sum(minutes for minutes, _sessions, _last in subject_totals.values())
    overall_sessions = sum(sessions for _minutes, sessions, _last in subject_totals.values())

    last_studied_on = max(studied_days) if studied_days else None
    days_since = (today - last_studied_on).days if last_studied_on is not None else None
    streak = StreakRead(
        current_days=current_streak(studied_days, today),
        longest_days=longest_streak(studied_days),
        studied_today=today in studied_days,
        last_studied_on=last_studied_on,
        days_since_last=days_since,
        window_days=STREAK_WINDOW_DAYS,
    )
    today_bucket = buckets.get(today, DayBucket())
    return LearningInsights(
        generated_at=current,
        timezone=timezone_name,
        today=DayTotal(date=today, minutes=today_bucket.minutes, sessions=today_bucket.sessions),
        week=_weekly_read(buckets, weekly_goal_minutes(db, user_id), monday, sunday, today),
        streak=streak,
        by_subject=_subject_distribution(db, user_id, subject_totals),
        by_weekday=_weekday_distribution(buckets, today),
        confidence_steps=confidence_steps(),
        total_minutes=overall_minutes,
        total_sessions=overall_sessions,
        open_sessions=len(open_sessions),
        active_minutes=_open_session_minutes(slices, zone, today, local_now),
        weekday_window_days=WEEKDAY_WINDOW_DAYS,
    )
