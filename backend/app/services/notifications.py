"""Erinnerungen an faellige Lernaufgaben und Klausuren.

Drei Dinge, die man hier falsch bauen kann und die deshalb ausdruecklich
festgeschrieben sind:

**1. Die Spalte ``settings.notifications_enabled`` ist verdrahtet - als
Hauptschalter, nicht als Form des Einstellungsdialogs.** Sie war seit ``0001``
da und wurde nie gelesen. Der ToDo liess zwei Wege zu und der Weg "verdrahten"
ist der billigere, aber die Spalte ist trotzdem die falsche Form fuer das, was
Punkt 5 verlangt: Kanaele, Uhrzeit und Ruhezeiten sind **mehrere** Angaben, und
ein einzelnes Boolean skaliert nicht darauf. Die Aufteilung ist deshalb:

* ``settings.notifications_enabled`` = der Hauptschalter. Er beantwortet
  "wollte dieser Nutzer Erinnerungen ueberhaupt".
* ``settings.preferences["notifications"]`` = Kanaele, Uhrzeit, Ruhezeiten.
  Bewusst in derselben JSON-Spalte wie das Wochenziel der Lernverwaltung und die
  Notenfreigabe: ein zweiter Ort fuer Nutzereinstellungen waere eine dritte
  Wahrheit ueber dieselbe Sache.

**Jeder unlesbare Zustand heisst: nichts.** Kein Schluessel, ein falscher Typ,
kaputtes JSON, ein boeses ``channels``-Wissen - dann ist kein Kanal aktiv. Ein
Schalter, der durch einen kaputten Eintrag von selbst aufginge, waere kein
Schalter, und fuer eine Benachrichtigung ist "aus" der sichere Wert. Die
Ruhezeiten gehen den anderen Weg: **kaputt heisst ruhevoll** (22:00-07:00 ist
der Voreingestellt), denn dort ist der sichere Wert die Verschwiegenheit.

**2. Die Ruhezeit wird in der Zeitzone des Nutzers gerechnet, nicht in UTC.**
Termine und Faelligkeiten liegen als UTC in der Datenbank, ``22:00`` bis
``07:00`` ist aber eine lokale Angabe. ``quiet_covers`` bekommt deshalb eine
lokale Uhrzeit und ``daily_reminder_moment`` rechnet in derselben Zone wie der
Vergleich mit ``now``. Ein naives ``start <= now <= end`` faellt bei einer
Ruhezeit ueber Mitternacht komplett durch: ``22:00 <= 03:00`` ist falsch, also
waere die halbe Nacht still - deshalb der zweite Zweig in ``quiet_covers``.

**3. Eine Erinnerung, die in der Ruhezeit faellt, wird nachgeholt.** Sie
verschwindet nicht, und sie wird auch nicht mitten in der Nacht zugestellt:
``daily_reminder_moment`` legt sie auf das Ende der Ruhezeit, und weil der
``dedupe_key`` den **lokalen Tag** traegt, geht sie genau einmal raus.

Die Tabelle ist deshalb eine Tagesuebersicht und keine Meldung pro Sache: gibt
es um 07:05 am 1. Oktober fuenf offene Aufgaben, bekommt die Lernende **eine**
Erinnerung ueber alle fuenf. Umgekehrt gilt fuer die Tagesgrenze streng: in der
Ruhezeit geht nichts raus, auch nicht, wenn ein Lauf einmal zu spaet kommt
(``reminder_day``). Faellt der Versand ueber den Monatswechsel (23:00 bei
22:00-07:00), gehoert der **Vortag** in den Schluessel - sonst wuerde die
Erinnerung am 30. September ein zweites Mal entstehen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import log_event, log_warning
from app.core.time import as_utc, day_window, resolve_timezone_name, utc_now, zone_or_utc
from app.db.entities import (
    Exam,
    Notification,
    PushSubscription,
    StudyPlanTask,
    Subject,
    Topic,
    User,
    UserSettings,
)
from app.schemas.notifications import QuietHoursRead, QuietHoursWrite, ReminderPreferencesRead
from app.services import web_push

logger = logging.getLogger("school_assistant.notifications")

#: Die zwei Kanaele. Eine geschlossene Liste, gespiegelt als Literal in
#: ``app/schemas/notifications.py`` - dieselbe Regel wie bei den Notenarten.
CHANNELS: Final[tuple[str, ...]] = ("study_tasks", "exams")

#: Was der Kanal jeweils beobachtet. Steht in der API-Antwort, damit die
#: Oberflaeche die Zahl nicht selbst erfindet.
CHANNEL_LABELS: Final[dict[str, str]] = {
    "study_tasks": "Lernaufgaben",
    "exams": "Prüfungen",
}

#: Der Schluessel unter ``settings.preferences``. Ohne ``channels``-Objekt ist
#: alles aus - siehe Modul-Docstring.
PREFERENCE_KEY: Final[str] = "notifications"

#: Wie lange vor einer Klausur erinnert wird. Steht bewusst als Konstante und
#: nicht als weiterer Preference-Schluessel: es ist eine **Annahme ueber
#: Klausuren**, keine Einstellung der Lernenden. Die API liefert sie mit, damit
#: die Oberflaeche â€ždie nächsten 7 Tage" schreiben kann, ohne zu raten.
EXAM_LEAD_DAYS: Final[int] = 7

#: Wie viele Zeilen ein Lauf hoechstens anlegt. Eine Lernende mit 40 ueberfaelligen
#: Aufgaben soll keinen Stapel Systemmeldungen bekommen, sondern die fünf
#: dringendsten.
MAX_REMINDERS_PER_RUN: Final[int] = 5

#: Wieviele Zeilen die offene Seite auf einmal abholt.
MAX_PENDING_PER_READ: Final[int] = 20

#: Wie viele Aufgaben ein Lauf je Nutzer durchsieht, bevor er kuerzt. Ohne diese
#: Schranke laedt ein Nutzer mit tausend Aufgaben die halbe Tabelle.
TASK_SCAN_LIMIT: Final[int] = 50

#: Die Anker, auf die eine Erinnerung zeigen darf: Abschnitte der Uebersicht
#: (``/uebersicht``) - die Startseite ist der Assistent und hat keine Anker.
TASK_URL: Final[str] = "/uebersicht#study-tasks"
EXAM_URL: Final[str] = "/uebersicht#calendar"

#: Die Einstellungen, die gelten, wenn nichts gespeichert ist. Die Uhrzeit ist
#: ein spaeter Nachmittag: frueher stuende die Erinnerung zu many Klausuren zu
#: spaet im Weg, spaeter kaeme sie fuer den Folgetag zu spaet.
DEFAULT_REMINDER_TIME: Final[str] = "17:00"
DEFAULT_QUIET_START: Final[str] = "22:00"
DEFAULT_QUIET_END: Final[str] = "07:00"


class NotificationSettingsError(ValueError):
    """Eine Einstellung laesst sich nicht lesen oder nicht speichern."""


@dataclass(frozen=True, slots=True)
class QuietHours:
    """Eine Ruhezeit als **lokale** Uhrzeit.

    ``start == end`` ist verboten (sonst waere â€žimmer" oder â€žnie" die
    Bedeutung) - das Schema lehnt es ab, und ``quiet_covers`` faellt fuer den
    Fall trotzdem auf â€žnicht ruhig" zurueck, statt eine Schleife zu bauen.
    """

    enabled: bool
    start: time
    end: time

    def describe(self) -> str:
        if not self.enabled:
            return "Aus"
        if self.start < self.end:
            return f"{self.start:%H:%M} bis {self.end:%H:%M}"
        # Ueber Mitternacht: der Text muss es sagen, sonst liest es sich wie
        # ein Rechenfehler.
        return f"{self.start:%H:%M} bis {self.end:%H:%M} (über Mitternacht)"


@dataclass(frozen=True, slots=True)
class ReminderPreferences:
    """Die gespeicherte Lage aller Erinnerungseinstellungen eines Nutzers."""

    enabled: bool
    channels: dict[str, bool]
    reminder_time: time
    quiet: QuietHours
    exam_lead_days: int = EXAM_LEAD_DAYS

    def has_active_channel(self) -> bool:
        return any(self.channels.get(channel, False) for channel in CHANNELS)

    def channel_enabled(self, channel: str) -> bool:
        """Kanal an? Nur wenn der Hauptschalter ebenfalls an ist."""
        return self.enabled and self.channels.get(channel, False) is True

    def describe(self) -> str:
        if not self.enabled:
            return "Der Hauptschalter ist aus, es gibt keine Erinnerungen."
        if not self.has_active_channel():
            return "Kein Kanal ist freigegeben, es gibt keine Erinnerungen."
        aktive = [CHANNEL_LABELS[channel] for channel in CHANNELS if self.channels.get(channel)]
        ruhig = self.quiet.describe()
        return f"{', '.join(aktive)} um {self.reminder_time:%H:%M} Uhr, Ruhezeit {ruhig}."


@dataclass(frozen=True, slots=True)
class PendingReminder:
    """Eine faellige Sache, noch ohne Zeile in der Datenbank."""

    kind: str
    target_id: int
    title: str
    body: str
    url: str


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """Was ein Lauf fuer **einen** Benutzer getan hat."""

    user_id: int
    created: int
    skipped_duplicate: int
    delivered: int
    push_failed: int
    push_attempted: bool
    message: str


def default_preferences(enabled: bool = True) -> ReminderPreferences:
    """Die Lage, die ein Benutzer bekommt, der nichts angefasst hat.

    ``enabled`` kommt von der Spalte, der Rest ist der sichere Wert: **kein**
    Kanal, eine gesetzte Ruhezeit. Aus diesem Grund erzeugt ein frisches Konto
    ohne Handgriff keine einzige Erinnerung - das ist gewollt und in den
    Einstellungen sichtbar.
    """
    return ReminderPreferences(
        enabled=enabled,
        channels={channel: False for channel in CHANNELS},
        reminder_time=parse_clock(DEFAULT_REMINDER_TIME),
        quiet=QuietHours(enabled=True, start=parse_clock(DEFAULT_QUIET_START), end=parse_clock(DEFAULT_QUIET_END)),
    )


def parse_clock(value: str) -> time:
    """``"22:00"`` zu einer Uhrzeit. Unlesbar heisst hier **Fehler**.

    Anders als beim Lesen aus der Datenbank: wer ueber die API eine kaputte Uhrzeit
    schickt, bekommt ein 422 und nicht still den Voreingestellt-Wert zurueck.
    """
    text = str(value or "").strip()
    try:
        stunde, _, minute = text.partition(":")
        if not minute or not stunde.isdigit() or not minute.isdigit():
            raise ValueError(text)
        ergebnis = time(hour=int(stunde), minute=int(minute))
    except (TypeError, ValueError) as exc:
        raise NotificationSettingsError("Die Uhrzeit muss im Format HH:MM stehen, etwa 17:00.") from exc
    return ergebnis


def _safe_clock(value: Any, fallback: str) -> time:
    """Wie ``parse_clock``, aber mit dem sicheren Wert statt mit einem Fehler."""
    try:
        return parse_clock(value)
    except NotificationSettingsError:
        return parse_clock(fallback)


def _safe_bool(value: Any) -> bool:
    """Nur ein echtes ``True`` gilt als eingeschaltet.

    Absichtlich streng: ein ``"ja"`` oder eine ``1`` aus einer von Hand
    manipulierten Datenbank ist kein Widerspruch, sondern **nein**.
    """
    return value is True


def read_preferences(db: Session, user_id: int) -> ReminderPreferences:
    """Die Erinnerungseinstellungen lesen, ohne dass ein Zustand etwas kaputt macht.

    Fehlt die ``settings``-Zeile ganz (ein Testkonto hat sie nicht), gilt der
    Voreingestellt-Wert mit dem Hauptschalter aus: ein Konto ohne Zeile hat
    nichts eingestellt, und nichts eingestellt heisst keine Erinnerung.
    """
    row = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if row is None:
        return default_preferences(enabled=False)
    basis = default_preferences(enabled=bool(row.notifications_enabled))
    blob = row.preferences
    if not isinstance(blob, dict):
        return basis
    teil = blob.get(PREFERENCE_KEY)
    if not isinstance(teil, dict):
        return basis
    kanaele = teil.get("channels")
    channels = dict(basis.channels)
    if isinstance(kanaele, dict):
        for channel in CHANNELS:
            channels[channel] = _safe_bool(kanaele.get(channel))
    ruhig = teil.get("quiet_hours")
    quiet = basis.quiet
    if isinstance(ruhig, dict):
        start = _safe_clock(ruhig.get("start"), DEFAULT_QUIET_START)
        end = _safe_clock(ruhig.get("end"), DEFAULT_QUIET_END)
        # `start == end` waere "immer ruhig" oder "nie ruhig", je nachdem, wie
        # man es liest. Beides ist kein Wunsch, sondern ein Tippfehler.
        quiet = QuietHours(enabled=_safe_bool(ruhig.get("enabled")) and start != end, start=start, end=end)
    return ReminderPreferences(
        enabled=bool(row.notifications_enabled),
        channels=channels,
        reminder_time=_safe_clock(teil.get("reminder_time"), DEFAULT_REMINDER_TIME),
        quiet=quiet,
    )


def write_preferences(
    db: Session,
    user_id: int,
    *,
    enabled: bool | None = None,
    channels: dict[str, bool] | None = None,
    reminder_time: time | None = None,
    quiet: QuietHours | None = None,
) -> ReminderPreferences:
    """Aendern und die **gespeicherte** Lage zurueckgeben.

    Zwei Eigenschaften sind hier Pflicht und nicht Kosmetik:

    * ``preferences`` wird als **Ganzes** gelesen und wieder geschrieben, mit
      einer Kopie. Das ist ein fremdes Verzeichnis - das Wochenziel und die
      Notenfreigabe stehen dort, und eine Erinnerungseinstellung hat nichts
      dort verloren. Ohne die Kopie wuerde ein ``dict(...)`` mit veraendertem
      Schluessel auch die fremden Schluessel loeschen.
    * Nur gesetzte Felder werden angefasst. Ein ``PATCH`` ohne ``channels``
      laesst die Kanaele stehen.
    """
    row = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if row is None:
        row = UserSettings(user_id=user_id)
        db.add(row)
    aktuell = read_preferences(db, user_id) if row.id is not None else default_preferences(enabled=True)
    if enabled is not None:
        row.notifications_enabled = enabled
    neue_kanaele = dict(aktuell.channels)
    if channels is not None:
        neue_kanaele.update({channel: _safe_bool(value) for channel, value in channels.items()})
    neue_ruhe = quiet if quiet is not None else aktuell.quiet
    preferences: dict[str, Any] = dict(row.preferences) if isinstance(row.preferences, dict) else {}
    preferences[PREFERENCE_KEY] = {
        "channels": neue_kanaele,
        "reminder_time": (reminder_time or aktuell.reminder_time).strftime("%H:%M"),
        "quiet_hours": {
            "enabled": neue_ruhe.enabled,
            "start": neue_ruhe.start.strftime("%H:%M"),
            "end": neue_ruhe.end.strftime("%H:%M"),
        },
    }
    row.preferences = preferences
    db.flush()
    # Die Zeile wurde in dieser Sitzung geschrieben und ist deshalb noch im
    # Identity-Map-Cache: `read_preferences` sieht den neuen Stand, ohne dass
    # jemand neu laden muss.
    return ReminderPreferences(
        enabled=bool(row.notifications_enabled),
        channels=dict(neue_kanaele),
        reminder_time=reminder_time or aktuell.reminder_time,
        quiet=neue_ruhe,
    )


def preferences_response(prefs: ReminderPreferences, *, timezone_name: str) -> ReminderPreferencesRead:
    """Die Antwort der API: die Lage plus die Zahlen, die die Oberflaeche braucht."""
    return ReminderPreferencesRead(
        enabled=prefs.enabled,
        channels={channel: bool(prefs.channels.get(channel, False)) for channel in CHANNELS},
        channel_labels=dict(CHANNEL_LABELS),
        channels_available=list(CHANNELS),
        reminder_time=prefs.reminder_time.strftime("%H:%M"),
        quiet_hours=QuietHoursRead(
            enabled=prefs.quiet.enabled,
            start=prefs.quiet.start.strftime("%H:%M"),
            end=prefs.quiet.end.strftime("%H:%M"),
            spans_midnight=prefs.quiet.start > prefs.quiet.end,
        ),
        exam_lead_days=prefs.exam_lead_days,
        timezone=timezone_name,
        preference_key=PREFERENCE_KEY,
        summary=prefs.describe(),
    )


# --- Die Ruhezeit ------------------------------------------------------------


def quiet_covers(quiet: QuietHours, moment: time) -> bool:
    """Faellt ``moment`` (Ortszeit) in die Ruhezeit?

    Der zweite Zweig ist der ganze Punkt: bei ``22:00`` bis ``07:00`` ist
    ``start > end``, und die Nacht liegt **ausserhalb** von ``start <= x <=
    end``. Ein ``22:00 <= 03:00`` waere nie wahr, die halbe Nacht also immer
    frei - genau der Fehler, den eine Ruhezeit ueber Mitternacht macht.
    """
    if not quiet.enabled or quiet.start == quiet.end:
        return False
    if quiet.start < quiet.end:
        return quiet.start <= moment < quiet.end
    return moment >= quiet.start or moment < quiet.end


def daily_reminder_moment(prefs: ReminderPreferences, day: date, zone: ZoneInfo) -> datetime:
    """Wann geht die Tageserinnerung fuer den lokalen Tag ``day`` raus?

    Normalerweise das ist ``reminder_time`` an diesem Tag. Liegt die Uhrzeit in
    der Ruhezeit, wandert sie auf das **Ende der Ruhezeit** - und ueber den Tag
    hinaus, wenn das Ende schon vor dem gewuenschten Moment liegt. Bei 23:00 und
    22:00-07:00 also 07:00 am Folgetag, nicht 23:00 heute.
    """
    base = datetime.combine(day, prefs.reminder_time, tzinfo=zone)
    if not quiet_covers(prefs.quiet, prefs.reminder_time):
        return base
    ende = datetime.combine(day, prefs.quiet.end, tzinfo=zone)
    return ende if ende > base else ende + timedelta(days=1)


def reminder_day(prefs: ReminderPreferences, now: datetime, zone: ZoneInfo) -> date | None:
    """Der lokale Tag, fuer den die Erinnerung faellig ist - oder ``None``.

    Zwei Faelle, und beide sind noetig:

    * **Heute faellig.** Der Moment des heutigen Tages ist erreicht.
    * **Der Vortag ist nachgeholt.** Eine um 23:00 gewuenschte Erinnerung in
      einer Ruhezeit von 22:00 bis 07:00 faellt in die Ruhezeit und ist erst
      am Folgetag um 07:00 dran - gehoert aber weiterhin zum Vortag. Deshalb
      steht ihr Tag im ``dedupe_key``: geht sie am 1. Oktober um 07:05 raus,
      traegt sie den 30. September.

    Der zweite Fall greift **nur** fuer einen nach hinten verschobenen Moment.
    Ein normaler Versand um 17:00 rollt mit dem Tag mit und erzeugt keine zweite
    Runde am selben Tag - sonst kaeme ein Benutzer, der den Dienst erst nach
    Mitternacht zum ersten Mal startet, zweimal an einem Tag zu Wort.

    Und die eine Regel, die alles darueber entscheidet: **in der Ruhezeit geht
    nichts raus**, auch nicht, wenn ein Versandmoment verpasst wurde. Der Lauf
    um 23:30 liefert deshalb nichts, obwohl der Moment des Vortags um 07:00
    laengst vorbei ist. Das Nachholen passiert am Ende der Ruhezeit - genau
    dort, wo der verschobene Moment hingelegt wurde.
    """
    if not prefs.enabled or not prefs.has_active_channel():
        return None
    lokal = now.astimezone(zone)
    if quiet_covers(prefs.quiet, lokal.time()):
        return None
    heute = lokal.date()
    if now >= daily_reminder_moment(prefs, heute, zone):
        return heute
    gestern = heute - timedelta(days=1)
    moment_gestern = daily_reminder_moment(prefs, gestern, zone)
    mitternacht = datetime.combine(heute, time.min, tzinfo=zone)
    if moment_gestern > mitternacht and now >= moment_gestern:
        return gestern
    return None


def next_reminder_moment(prefs: ReminderPreferences, now: datetime, zone: ZoneInfo) -> datetime:
    """Der naechste Zeitpunkt, an dem ueberhaupt etwas gesendet werden darf.

    Wird von der Oberflaeche gebraucht, um "die naechste Erinnerung" zu sagen -
    und von den Tests, um den Mitternachtsfall ueber den Monatswechsel festzu
    halten. Kein Versand wird daran geplant: der Lauf kommt wieder, und der
    ``dedupe_key`` verhindert das Doppelte.
    """
    heute = now.astimezone(zone).date()
    # Der Vortag gehoert dazu, weil eine in der Ruhezeit verschobene
    # Erinnerung dort landet. Deshalb das Minimum ueber beide Kandidaten und
    # nicht einfach "der erste Tag in der Reihe".
    kandidaten = [daily_reminder_moment(prefs, heute + timedelta(days=versatz), zone) for versatz in range(-1, 4)]
    spaeter = [kandidat for kandidat in kandidaten if kandidat > now]
    if not spaeter:
        # Drei Tage sollten reichen (Fehler in den Einstellungen). Danach ist die
        # Frage sinnlos, also lieber melden als weiterrechnen.
        return now + timedelta(days=3)
    return min(spaeter)


def reminders_due(prefs: ReminderPreferences, now: datetime, zone: ZoneInfo) -> bool:
    """Darf dieser Lauf ueberhaupt etwas erzeugen?"""
    return reminder_day(prefs, now, zone) is not None


# --- Was gerade faellig ist -------------------------------------------------


def due_task_reminders(db: Session, user_id: int, *, day: date, zone: ZoneInfo, limit: int) -> list[PendingReminder]:
    """Offene Lernaufgaben, die heute faellig sind oder laenger.

    **Faellig** heisst ``due_date`` und nicht ``scheduled_for``: eine Aufgabe
    ohne Termin hat keine Faelligkeit, und eine Erinnerung dazu waere Rauschen.
    Der Klausur-Generator setzt ``due_date`` (das Ende des geplanten Lerntags),
    deshalb greift die Erinnerung auch bei erzeugten Aufgaben.

    Sortiert wird **absteigend nach Faelligkeit**: eine heute faellige Aufgabe
    ist wichtiger als eine, die seit drei Wochen offen ist - wer sie zuerst
    sieht, hat eher eine Chance, sie noch zu machen. Gleichstand loest die
    Prioritaet, und zwar absteigend, weil hoeher hier wichtiger bedeutet.

    Das Fach kommt ueber ``topic_id -> Topic.subject_id`` und **nicht** ueber
    eine Fachspalte der Aufgabe: ``StudyPlanTask`` hat keine. Eine Aufgabe ohne
    Thema gehoert damit zu den Aufgaben ohne Fach, genau wie im Lernprotokoll.
    """
    _, ende = day_window(day, zone)
    rows = db.execute(
        select(StudyPlanTask, Subject.name)
        .join(Topic, Topic.id == StudyPlanTask.topic_id, isouter=True)
        .join(Subject, Subject.id == Topic.subject_id, isouter=True)
        .where(
            StudyPlanTask.user_id == user_id,
            StudyPlanTask.due_date.is_not(None),
            StudyPlanTask.due_date < ende,
            StudyPlanTask.status.notin_(("completed", "cancelled")),
        )
        .order_by(StudyPlanTask.due_date.desc(), StudyPlanTask.priority.desc(), StudyPlanTask.id)
        .limit(min(limit, TASK_SCAN_LIMIT))
    ).all()
    reminders: list[PendingReminder] = []
    for task, fachname in rows:
        assert isinstance(task, StudyPlanTask)
        reminders.append(
            PendingReminder(
                kind="study_task_due",
                target_id=task.id,
                title=task.title,
                body=_task_body(task, fachname, day, zone),
                url=TASK_URL,
            )
        )
    return reminders


def _task_body(task: StudyPlanTask, subject_name: str | None, day: date, zone: ZoneInfo) -> str:
    """Ein Satz, der die **Handlung** nennt - kein â€ždu hast 3 Aufgaben"."""
    assert task.due_date is not None
    faellig = as_utc(task.due_date).astimezone(zone).date()
    if faellig < day:
        versatz = (day - faellig).days
        text = "seit gestern überfällig" if versatz == 1 else f"seit {versatz} Tagen überfällig"
    else:
        text = "heute fällig"
    teile = [text, f"{task.duration_minutes} Minuten"]
    if subject_name:
        teile.append(subject_name)
    return " · ".join(teile)


def due_exam_reminders(
    db: Session, user_id: int, *, day: date, zone: ZoneInfo, lead_days: int, limit: int
) -> list[PendingReminder]:
    """Klausuren von heute bis ``lead_days`` Tage weit.

    Das Fenster ist ``[heute 00:00, (heute + lead_days + 1) 00:00)`` in der
    Zone des Nutzers. Eine Klausur, die in einer Stunde stattfindet, gehoert
    genauso hinein wie eine in zwei Wochen - der Vorlauf ist der ganze Zweck der
    Zahl, und ``+ 1`` ist kein Rechenfehler, sondern der Rand: ``lead_days = 7``
    heisst "die naechsten sieben Tage **einschliesslich** heute".
    """
    start, _ = day_window(day, zone)
    _, ende = day_window(day + timedelta(days=lead_days), zone)
    rows = db.execute(
        select(Exam, Subject.name)
        .join(Subject, Subject.id == Exam.subject_id, isouter=True)
        .where(Exam.user_id == user_id, Exam.date >= start, Exam.date < ende)
        .order_by(Exam.date, Exam.id)
        .limit(max(1, min(limit, MAX_REMINDERS_PER_RUN)))
    ).all()
    reminders: list[PendingReminder] = []
    for exam, fachname in rows:
        assert isinstance(exam, Exam)
        reminders.append(
            PendingReminder(
                kind="exam_due",
                target_id=exam.id,
                title=exam.title,
                body=_exam_body(exam, fachname, day, zone),
                url=EXAM_URL,
            )
        )
    return reminders


def _exam_body(exam: Exam, subject_name: str | None, day: date, zone: ZoneInfo) -> str:
    tag = as_utc(exam.date).astimezone(zone).date()
    versatz = (tag - day).days
    when = {0: "heute", 1: "morgen"}.get(versatz, f"am {tag:%d.%m.%Y}")
    teile = [when]
    if subject_name:
        teile.append(subject_name)
    return " · ".join(teile)


# --- Eine Erinnerung anlegen ------------------------------------------------


def create_due_reminders(
    db: Session,
    user_id: int,
    *,
    now: datetime | None = None,
    zone: ZoneInfo,
    prefs: ReminderPreferences | None = None,
) -> list[Notification]:
    """Die faelligen Erinnerungen als Zeilen anlegen - je Tag und Sache genau eine.

    Die Idempotenz sitzt in ``dedupe_key`` und in der eindeutigen
    Beschraenkung darauf. Vorher geprueft und danach trotzdem noch einmal mit
    einem Savepoint: der Lauf kann gleichzeitig mit einem manuellen Knopfdruck
    laufen, und dann entscheidet die Datenbank, nicht das ``SELECT``.
    """
    moment = as_utc(now or utc_now())
    einstellungen = prefs if prefs is not None else read_preferences(db, user_id)
    # Der Tag, fuer den **zugestellt** wird, und der Tag, dessen Faelligkeiten
    # geprueft werden, sind nicht immer derselbe: eine in der Ruhezeit nach hinten
    # verschobene Erinnerung geht am Folgetag raus und gehoert trotzdem zum Vortag.
    bezugstag = reminder_day(einstellungen, moment, zone)
    if bezugstag is None:
        return []
    tag = moment.astimezone(zone).date()
    stichwort = bezugstag.isoformat()
    kandidaten: list[PendingReminder] = []
    if einstellungen.channel_enabled("study_tasks"):
        rest = max(0, MAX_REMINDERS_PER_RUN - len(kandidaten))
        kandidaten.extend(due_task_reminders(db, user_id, day=tag, zone=zone, limit=rest))
    if einstellungen.channel_enabled("exams"):
        rest = max(0, MAX_REMINDERS_PER_RUN - len(kandidaten))
        kandidaten.extend(
            due_exam_reminders(
                db,
                user_id,
                day=tag,
                zone=zone,
                lead_days=einstellungen.exam_lead_days,
                limit=rest,
            )
        )

    if not kandidaten:
        return []

    vorhanden = set(
        db.scalars(
            select(Notification.dedupe_key).where(
                Notification.user_id == user_id,
                Notification.dedupe_key.like(f"%:{stichwort}"),
            )
        ).all()
    )
    created: list[Notification] = []
    for kandidat in kandidaten:
        key = f"{kandidat.kind}:{kandidat.target_id}:{stichwort}"
        if key in vorhanden:
            continue
        vorhanden.add(key)
        zeile = Notification(
            user_id=user_id,
            kind=kandidat.kind,
            title=kandidat.title[:240],
            body=kandidat.body[:400],
            url=kandidat.url,
            target_id=kandidat.target_id,
            dedupe_key=key,
            push_status="skipped",
        )
        try:
            with db.begin_nested():
                db.add(zeile)
                db.flush()
        except IntegrityError:
            # Der Lauf einer anderen Sitzung war schneller. Kein Fehler, aber
            # auch nichts Neues.
            log_event(logger, "notification_duplicate_skipped", user_id=user_id, kind=kandidat.kind)
            continue
        created.append(zeile)
    if created:
        log_event(
            logger,
            "notifications_created",
            user_id=user_id,
            created=len(created),
            local_day=stichwort,
        )
    return created


# --- Der Transport ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DispatchReport:
    """Ergebnis des Push-Versands fuer eine Liste von Zeilen."""

    attempted: bool
    sent: int
    failed: int
    removed: int
    message: str

    def describe(self) -> str:
        return self.message


def _target_of(row: PushSubscription) -> web_push.PushTarget:
    return web_push.PushTarget(endpoint=row.endpoint, p256dh=row.p256dh, auth=row.auth)


def dispatch_reminders(
    db: Session,
    notifications: list[Notification],
    *,
    settings: Settings,
    client: httpx.Client | None = None,
) -> DispatchReport:
    """Die Zeilen an die Abonnements des Nutzers schicken.

    Drei Faelle, alle drei benannt - es wird **kein** Erfolg erfunden:

    * kein Abonnement oder kein Schluesselpaar: ``push_status`` bleibt
      ``skipped`` und ``delivered_at`` ``None``. Die Zeile wartet damit auf die
      offene Seite - das ist der lokale Weg, kein Fehler.
    * der Dienst nimmt an: ``sent`` und ``delivered_at`` gesetzt.
    * der Dienst lehnt ab: ``failed``, ``delivered_at`` bleibt ``None``, damit
      die offene Seite sie trotzdem anzeigt. ``404``/``410`` loeschen zusaetzlich
      das Abonnement, denn der Browser wird es nie wieder benutzen.
    """
    if not notifications:
        return DispatchReport(attempted=False, sent=0, failed=0, removed=0, message="Keine neuen Erinnerungen.")
    user_id = notifications[0].user_id
    abos = list(db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)).all())
    if not abos:
        for zeile in notifications:
            zeile.push_status = "skipped"
        return DispatchReport(
            attempted=False,
            sent=0,
            failed=0,
            removed=0,
            message="Kein Endgerät angemeldet - die Erinnerung wartet auf die geöffnete Seite.",
        )
    if not web_push.push_is_configured(settings):
        for zeile in notifications:
            zeile.push_status = "skipped"
        return DispatchReport(
            attempted=False,
            sent=0,
            failed=0,
            removed=0,
            message="Web-Push ist auf diesem Server nicht eingerichtet - die Erinnerung wartet auf die geöffnete Seite.",
        )

    moment = as_utc(utc_now())
    sent = 0
    failed = 0
    removed = 0
    letzter_fehler: str | None = None
    try:
        keypair = web_push.resolve_vapid_keypair(settings)
    except web_push.WebPushError as exc:
        for zeile in notifications:
            zeile.push_status = "skipped"
        return DispatchReport(
            attempted=False,
            sent=0,
            failed=0,
            removed=0,
            message=f"Das Schlüsselpaar ist unbrauchbar: {exc}",
        )
    for zeile in notifications:
        payload = {
            "title": zeile.title,
            "body": zeile.body,
            "url": zeile.url,
            "tag": f"{zeile.kind}:{zeile.target_id}",
            "notification_id": zeile.id,
        }
        zustellungen: list[bool] = []
        for abo in list(abos):
            try:
                ergebnis = web_push.send_push(
                    target=_target_of(abo), payload=payload, settings=settings, keypair=keypair, client=client
                )
            except web_push.WebPushError as exc:
                letzter_fehler = web_push.describe_push_failure(exc)
                abo.failures += 1
                abo.last_failure_at = moment
                abo.last_error = letzter_fehler
                zustellungen.append(False)
                continue
            web_push.log_push_result(user_id=user_id, notification_id=zeile.id, result=ergebnis, endpoint=abo.endpoint)
            if ergebnis.ok:
                abo.failures = 0
                abo.last_success_at = moment
                abo.last_error = None
                zustellungen.append(True)
                continue
            abo.failures += 1
            abo.last_failure_at = moment
            abo.last_error = ergebnis.message
            letzter_fehler = ergebnis.message
            zustellungen.append(False)
            if ergebnis.gone:
                db.delete(abo)
                abos.remove(abo)
                removed += 1
        if zustellungen and any(zustellungen):
            zeile.push_status = "sent"
            zeile.delivered_at = moment
            sent += 1
        elif zustellungen:
            zeile.push_status = "failed"
            failed += 1
    nachricht = (
        f"{sent} Erinnerung(en) an {len(abos) or removed} Endgerät(e) gesendet."
        if sent
        else f"Kein Push angenommen{': ' + letzter_fehler if letzter_fehler else '.'}"
    )
    # Einmal schreiben, nicht je Abonnement: der Aufrufer entscheidet ueber die
    # Transaktion, aber der Zustand soll innerhalb dieser Sitzung stimmen - ein
    # Test und ein Folgelauf in derselben Sitzung sehen sonst noch den alten
    # Stand aus dem Identity-Map-Cache.
    db.flush()
    return DispatchReport(attempted=True, sent=sent, failed=failed, removed=removed, message=nachricht)


# --- Der Lauf ueber alle Benutzer -------------------------------------------


def active_reminder_users(db: Session) -> list[tuple[int, str]]:
    """Alle aktiven Benutzer mit ihrer Zeitzone, in stabiler Reihenfolge.

    Wie beim WebUntis-Lauf ist die Reihenfolge Absicht: ein Fehler trifft damit
    immer denselben Benutzer, und die Tests sehen eine vorhersehbare Folge. Es
    sind **alle** aktiven Benutzer, nicht nur die mit einem Abonnement - die
    Zeilen fuer den lokalen Weg entstehen auch ohne Push.
    """
    rows = db.execute(select(User.id, User.timezone).where(User.is_active.is_(True)).order_by(User.id)).all()
    return [(int(row[0]), str(row[1] or "UTC")) for row in rows]


def sweep_for_user(
    db: Session,
    user_id: int,
    timezone_name: str,
    *,
    settings: Settings,
    now: datetime | None = None,
    client: httpx.Client | None = None,
) -> SweepOutcome:
    """Alles fuer **einen** Benutzer: lesen, anlegen, zustellen.

    Die Schleife ueber die Benutzer ruft nur das hier auf und faengt alles -
    das ist die Nutzerisolation: ein Fehler bei A darf B nicht aufhalten.
    """
    moment = as_utc(now or utc_now())
    zone = zone_or_utc(resolve_timezone_name(timezone_name, settings.timezone))
    prefs = read_preferences(db, user_id)
    if not prefs.enabled:
        return SweepOutcome(
            user_id=user_id,
            created=0,
            skipped_duplicate=0,
            delivered=0,
            push_failed=0,
            push_attempted=False,
            message="Der Hauptschalter ist aus.",
        )
    if not prefs.has_active_channel():
        return SweepOutcome(
            user_id=user_id,
            created=0,
            skipped_duplicate=0,
            delivered=0,
            push_failed=0,
            push_attempted=False,
            message="Kein Kanal freigegeben.",
        )
    if not reminders_due(prefs, moment, zone):
        naechste = next_reminder_moment(prefs, moment, zone)
        return SweepOutcome(
            user_id=user_id,
            created=0,
            skipped_duplicate=0,
            delivered=0,
            push_failed=0,
            push_attempted=False,
            message=f"Noch nicht fällig - nächste Erinnerung ab {naechste.astimezone(zone):%d.%m. %H:%M}.",
        )
    try:
        created = create_due_reminders(db, user_id, now=moment, zone=zone, prefs=prefs)
    except SQLAlchemyError as error:
        # Ein Datenbankfehler ist kein Fehler des Nutzers, deshalb wird nichts
        # geschrieben: die Zeilen sind dann ohnehin nicht entstanden.
        db.rollback()
        log_warning(
            logger,
            "notification_sweep_database_error",
            user_id=user_id,
            error_type=type(error).__name__,
        )
        return SweepOutcome(
            user_id=user_id,
            created=0,
            skipped_duplicate=0,
            delivered=0,
            push_failed=0,
            push_attempted=False,
            message="Die Datenbank war beim Erinnern nicht erreichbar.",
        )
    except Exception as error:
        # Alles andere landet als Meldung im Ergebnis und nicht als Ausnahme
        # nach aussen - die Schleife ueber die Benutzer macht mit dem naechsten
        # weiter.
        db.rollback()
        log_warning(
            logger,
            "notification_sweep_failed",
            user_id=user_id,
            error_type=type(error).__name__,
        )
        return SweepOutcome(
            user_id=user_id,
            created=0,
            skipped_duplicate=0,
            delivered=0,
            push_failed=0,
            push_attempted=False,
            message=f"Die Erinnerungen konnten nicht angelegt werden: {error}",
        )
    if not created:
        return SweepOutcome(
            user_id=user_id,
            created=0,
            skipped_duplicate=0,
            delivered=0,
            push_failed=0,
            push_attempted=False,
            message="Nichts Neues fällig.",
        )
    report = dispatch_reminders(db, created, settings=settings, client=client)
    return SweepOutcome(
        user_id=user_id,
        created=len(created),
        skipped_duplicate=0,
        delivered=report.sent,
        push_failed=report.failed,
        push_attempted=report.attempted,
        message=f"{len(created)} Erinnerung(en) angelegt. {report.describe()}",
    )


# --- Die offene Seite holt hier ---------------------------------------------


def pending_notifications(db: Session, user_id: int, *, after: int | None = None, limit: int = MAX_PENDING_PER_READ) -> list[Notification]:
    """Die Zeilen, die noch niemand gezeigt hat.

    ``delivered_at IS NULL`` heisst: kein Push-Dienst hat sie angenommen. Genau
    diese Zeilen holt die geoeffnete Seite ueber die ``Notification``-API der
    Seite ab - das ist der Fallback, der ohne Push-Dienst funktioniert. Eine
    Zeile, die schon zugestellt wurde, wird **nicht** noch einmal als
    Systemmeldung gezeigt, sonst kaeme jede Erinnerung doppelt an.
    """
    stmt = select(Notification).where(
        Notification.user_id == user_id,
        Notification.read_at.is_(None),
        Notification.delivered_at.is_(None),
    )
    if after is not None:
        stmt = stmt.where(Notification.id > after)
    return list(db.scalars(stmt.order_by(Notification.id).limit(max(1, min(limit, MAX_PENDING_PER_READ)))).all())


def mark_read(db: Session, user_id: int, notification_ids: list[int]) -> int:
    """Zeilen als gelesen markieren. Fremde Ids werden nicht angefasst.

    Der Aufrufer hat die Ids vorher gegen den Nutzer geprueft (404 bei fremden),
    deshalb ist das hier nur noch ``UPDATE`` mit dem Nutzerfilter - ohne
    zweiten ``SELECT`` je Id.
    """
    if not notification_ids:
        return 0
    moment = as_utc(utc_now())
    treffer = 0
    for zeile in db.scalars(
        select(Notification).where(
            Notification.user_id == user_id,
            Notification.id.in_(notification_ids),
            Notification.read_at.is_(None),
        )
    ).all():
        zeile.read_at = moment
        treffer += 1
    db.flush()
    return treffer


# --- Abonnements ------------------------------------------------------------


def register_subscription(
    db: Session,
    user_id: int,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None = None,
) -> PushSubscription:
    """Ein Abonnement anlegen oder das vorhandene erneuern.

    Der Endpunkt ist der Schluessel: derselbe Browser liefert bei jedem
    Anmeldeversuch denselben Endpunkt, und ohne diese Wiederverwendung haette
    ein Nutzer nach drei Reloads drei Zeilen - und dreimal dieselbe
    Erinnerung auf dem Geraet.
    """
    existing = db.scalar(
        select(PushSubscription).where(
            PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint
        )
    )
    if existing is None:
        existing = PushSubscription(user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth=auth)
        db.add(existing)
    else:
        existing.p256dh = p256dh
        existing.auth = auth
    existing.user_agent = user_agent
    existing.failures = 0
    existing.last_error = None
    db.flush()
    return existing


def subscriptions_of(db: Session, user_id: int) -> list[PushSubscription]:
    return list(
        db.scalars(
            select(PushSubscription)
            .where(PushSubscription.user_id == user_id)
            .order_by(PushSubscription.id)
        ).all()
    )


def remove_subscription(db: Session, user_id: int, subscription_id: int) -> PushSubscription | None:
    """Ein Abonnement loeschen. Fremde Ids kommen als ``None`` zurueck."""
    row = db.get(PushSubscription, subscription_id)
    if row is None or row.user_id != user_id:
        return None
    db.delete(row)
    db.flush()
    return row


def with_quiet(prefs: ReminderPreferences, *, enabled: bool, start: time, end: time) -> ReminderPreferences:
    """Die Ruhezeit tauschen - mit derselben Ablehnung wie beim Schreiben."""
    if start == end:
        raise NotificationSettingsError("Beginn und Ende der Ruhezeit dürfen nicht gleich sein.")
    return replace(prefs, quiet=QuietHours(enabled=enabled, start=start, end=end))


def quiet_hours_from_write(payload: QuietHoursWrite | None) -> QuietHours | None:
    """Aus dem Schreibschema eine Ruhezeit machen, oder ``None`` fuer unveraendert."""
    if payload is None:
        return None
    return QuietHours(
        enabled=payload.enabled,
        start=parse_clock(payload.start),
        end=parse_clock(payload.end),
    )
