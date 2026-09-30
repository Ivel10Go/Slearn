"""Punkte und Auszeichnungen - die Regeln und das, was daraus folgt.

Dieses Modul ist bewusst **die einzige** Stelle, die ueber Punkte entscheidet, und
es liest dafuer genau drei Dinge: ``StudyPlanTask`` (was erledigt wurde),
``StudySession`` (wie lange gelernt wurde) und ``Topic``/``Subject`` (**was**
gelernt wurde). Es liest keine Noten, keine Konfidenz und keinen Prompt - dazu
unten mehr.

Vier Entscheidungen tragen das Ganze. Sie stehen jeweils an der Stelle, an der sie
fallen, weil eine Belohnung, die man nicht begruenden kann, irgendwann zur
Selbstverstaendlichkeit wird.

**1. Der Zaehler ist eine Liste, kein Feld.** ``RewardEntry`` traegt je Zeile ein
Ereignis, und der Stand ist die Summe ueber die Zeilen. Das ist der Grund, warum
``POST /complete`` zweimal aufgerufen werden darf: das Ereignis traegt den
Schluessel ``task:41``, und die Eindeutigkeit darauf sitzt in der Datenbank. Ein
Zaehler in einer Spalte waere die kuerzere Loesung und genau die, die beim
zweiten Klick doppelt gutschreibt.

**2. Punkte haengen am Inhalt, nicht am Abhaken.** Die Einheit ist nicht die
Aufgabe, sondern der **Fachtag**: ein Fach an einem Kalendertag in der Zeitzone
des Nutzers. Ein Fachtag zahlt nur, wenn

* die Lernzeit auf **abgeschlossene** Sessions mit einem **Thema** entfaellt - eine
  Aufgabe ohne Thema hat keinen Fachweg, und ohne Fach gibt es nichts zu belohnen.
  Eine reine Zeitmessung wuerde belohnen, dass jemand sitzt;
* und mindestens ``MIN_SUBJECT_DAY_MINUTES`` Minuten darauf liegen. Unterhalb der
  Schwelle wird nichts gebucht, nicht einmal eine Zeile mit 0 Punkten: dafuer ist
  eine Luecke ehrlicher als eine Buchung ueber nichts.

Die Punkte selbst sind **gestaffelt, nicht linear**. Eine Minute mehr ist nicht
durchgehend eine Minute wert, sonst waere die Belohnung fuer das Sitzen genauso
gross wie die fuer einen durchgearbeiteten Fachtag. Nach der obersten Stufe wird
die zusaetzliche Zeit nur noch gezaehlt, aber gedeckelt - und diese Deckel ist die
Stelle, an der die Belohnung aufhoert zu wachsen.

**3. Die Menge ist gedeckelt.** ``MAX_DAY_POINTS`` gilt fuer **einen Tag** und
nicht fuer eine Woche. Das ist die einfachere und ehrlichere Zusage: heute gibt es
so viele Punkte und nicht mehr, und morgen geht es weiter. Ist das Konto gefuellt,
wird **weiter gebucht** (mit 0 Punkten) statt die Buchung zu verweigern - die
Oberflaeche sieht damit, dass gelernt wurde und dass die Regel gegriffen hat,
statt eine Luecke zu zeigen. Es gibt keine Nachzahlung am Folgetag.

**4. Punkte sind ausschliesslich eine Nebenwirkung.** Kein Feld dieses Moduls
fliesst in ``exam_priority()``, ``grade_priority()``, ``project_average()`` oder
in einen KI-Kontext. Das ist nicht nur eine Abwesenheit, sondern eine
Vorbedingung: eine Belohnung, die den Notenstand oder die Prioritaet einer Aufgabe
verschiebt, erzeugt Druck zum Zaehlen statt zum Lernen, und ein Modell, das Punkte
sieht, lernt die falsche Sprache. Der Test, der das absichert, heisst
``test_belohnungen_veraendern_keine_prioritaet_und_keinen_schnitt`` und schaut
beide Seiten an - die Werte *und* die Importliste dieses Moduls.

**Warum die Auszeichnungen eine eigene Tabelle sind und nicht die UI-Badge-
Komponente.** ``components/ui/badge.tsx`` ist eine reine Darstellung: sie bekommt
einen ``variant`` und rendert eine Zeile, mehr nicht. Eine Auszeichnung ist ein
Objekt mit Lebenslauf - Bedingung, Fortschritt, Zeitpunkt und die Frage, ob sie
wiederholt erreichbar ist. Das sind vier Stuecke Zustand, und Zustand gehoert ins
Modell. Die Komponente darf eine fertige Zeile anzeigen.

**Einmalig oder wiederholt?** Das steht im Katalog, denn es ist eine Eigenschaft
der Bedingung und nicht des Nutzers. Fuer eine wiederholte Auszeichnung bekommt
jeder Zeitraum seine **eigene Zeile** (``week:2026-W40``), statt einen Zaehler in
derselben Zeile zurueckzusetzen. Ein Zaehler waere die zweite Wahrheit ueber
dieselbe Frage.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.time import as_utc, utc_now
from app.db.entities import (
    REWARD_KIND_SUBJECT_DAY,
    REWARD_KIND_TASK,
    SCOPE_GLOBAL,
    RewardBadge,
    RewardEntry,
    StudyPlanTask,
    StudySession,
    Subject,
    Topic,
    User,
)
from app.schemas.rewards import (
    RewardBadgeDefinition,
    RewardBadgeRead,
    RewardEntryRead,
    RewardRules,
    RewardStep,
    RewardSummary,
)
from app.services.progress import local_day, longest_streak, studied_days, week_bounds

# --- Die Punktregel ---------------------------------------------------------

#: Punkte je erledigter Aufgabe **mit Thema**. Ohne Thema gibt es keinen Fachweg,
#: und damit nichts, was die Buchung an Inhalt binden koennte - eine Aufgabe ohne
#: Thema zahlt deshalb nichts. Das ist die Kernaussage dieses Moduls in einer Zeile.
TASK_POINTS: Final[int] = 15

#: Unterhalb dieser Minuten zahlt ein Fachtag nicht. Der Wert ist zugleich die
#: unterste Stufe der Staffel, damit die beiden nicht auseinanderlaufen koennen.
MIN_SUBJECT_DAY_MINUTES: Final[int] = 10

#: Die Stufen eines Fachtags: ab so vielen Minuten gibt es so viele Punkte. Bewusst
#: eine Treppe und kein Satz Punkte pro Minute - siehe Punkt 2 oben.
SUBJECT_DAY_STEPS: Final[tuple[tuple[int, int], ...]] = ((10, 5), (25, 15), (50, 30))

#: Nach der obersten Stufe zaehlt jede weitere Viertelstunde, aber gedeckelt.
SUBJECT_DAY_EXTRA_MINUTES: Final[int] = 25
SUBJECT_DAY_EXTRA_POINTS: Final[int] = 10

#: Obergrenze eines Fachtags. Ab hier waechst die Belohnung fuer mehr Zeit nicht
#: mehr: ein zweiter Nachmittag am selben Fach ist Lernen, aber kein Grund, den
#: ganzen Tag dafuer zu opfern.
MAX_SUBJECT_DAY_POINTS: Final[int] = 40

#: Obergrenze fuer **einen** Kalendertag, ueber alle Faecher. Die eine Zusage, die
#: die Karte nennt und die der Nutzer nachrechnen kann.
MAX_DAY_POINTS: Final[int] = 80

#: Wie viele Buchungen die Karte zeigt. Die Liste ist ein Nachweis, kein Archiv;
#: die Summe laeuft ueber alle Zeilen und nicht ueber die angezeigten.
ENTRY_LIMIT: Final[int] = 20
ENTRY_LIMIT_MAX: Final[int] = 100

#: Wie weit der Klartext einer Buchung getrimmt wird, damit er in die Spalte passt.
DETAIL_LIMIT: Final[int] = 200

#: fuer ein Fach ohne Namen. Ein Fach ohne Namen gibt es nicht, aber eine geloeschte
#: Sache kann ohne Namen in der Antwort stehen - dann sagt die Karte es auch.
NO_SUBJECT_LABEL: Final[str] = "Ohne Fach"


def subject_day_points(minutes: int) -> int:
    """Die Punkte eines Fachtags bei so vielen angerechneten Minuten.

    Die Treppe laeuft von unten nach oben und nimmt den groessten passenden Wert,
    nicht die Summe der Stufen - sonst waeren 50 Minuten gleich 5 + 15 + 30.
    Unterhalb von ``MIN_SUBJECT_DAY_MINUTES`` ist das Ergebnis 0, und der Aufrufer
    bucht dann gar nichts.
    """
    if minutes < MIN_SUBJECT_DAY_MINUTES:
        return 0
    punkte = SUBJECT_DAY_STEPS[0][1]
    for grenze, wert in SUBJECT_DAY_STEPS:
        if minutes >= grenze:
            punkte = wert
    oberste = SUBJECT_DAY_STEPS[-1][0]
    if minutes >= oberste:
        punkte += ((minutes - oberste) // SUBJECT_DAY_EXTRA_MINUTES) * SUBJECT_DAY_EXTRA_POINTS
    return min(punkte, MAX_SUBJECT_DAY_POINTS)


def reward_rules() -> RewardRules:
    """Die Regel in Zahlen, damit die Oberflaeche sie nicht nachbaut."""
    return RewardRules(
        task_points=TASK_POINTS,
        day_cap=MAX_DAY_POINTS,
        subject_day_cap=MAX_SUBJECT_DAY_POINTS,
        min_subject_day_minutes=MIN_SUBJECT_DAY_MINUTES,
        subject_day_steps=[RewardStep(minutes=grenze, points=wert) for grenze, wert in SUBJECT_DAY_STEPS],
    )


# --- Der Katalog der Auszeichnungen -----------------------------------------


@dataclass(frozen=True)
class BadgeDefinition:
    """Eine Bedingung samt Bezeichnung. Der Zustand steht in ``RewardBadge``.

    ``unit`` und ``unit_plural`` stehen beide hier, weil die deutsche Grammatik nicht
    im Frontend erraten werden sollte: "2 von 5 Tage" ist falsch, "2 von 5 Tagen"
    stimmt - und welche Form noetig ist, weiss der Katalog, nicht der Browser.
    """

    code: str
    title: str
    description: str
    target: int
    unit: str
    unit_plural: str
    scope: str
    repeatable: bool = False


BADGE_DEFINITIONS: Final[tuple[BadgeDefinition, ...]] = (
    BadgeDefinition(
        code="erster_abschluss",
        title="Erster Abschluss",
        description="Die erste Lernaufgabe mit Thema erledigt.",
        target=1,
        unit="Aufgabe",
        unit_plural="Aufgaben",
        scope="global",
    ),
    BadgeDefinition(
        code="serie_von_sieben",
        title="Sieben Tage in Folge",
        description="Sieben Kalendertage in Folge mit abgeschlossener Lernzeit.",
        target=7,
        unit="Tag",
        unit_plural="Tagen",
        scope="global",
    ),
    BadgeDefinition(
        code="wochenrhythmus",
        title="Rhythmus über eine Woche",
        description="An fünf Tagen innerhalb einer Kalenderwoche gelernt, von Montag bis Sonntag.",
        target=5,
        unit="Tag",
        unit_plural="Tagen",
        scope="week",
        repeatable=True,
    ),
    BadgeDefinition(
        code="regelmaessig_wiederholt",
        title="Regelmäßig wiederholt",
        description="An drei verschiedenen Tagen Zeit auf ein Thema verwendet.",
        target=3,
        unit="Tag",
        unit_plural="Tagen",
        scope="topic",
    ),
    BadgeDefinition(
        code="fach_durchgearbeitet",
        title="Fach durchgearbeitet",
        description="Alle angelegten Aufgaben eines Fachs erledigt.",
        target=1,
        unit="Aufgabe",
        unit_plural="Aufgaben",
        scope="subject",
    ),
    BadgeDefinition(
        code="fuenfzig_minuten",
        title="Fünfzig Minuten an einem Fachtag",
        description="An einem einzigen Tag fünfzig abgeschlossene Minuten in einem Fach.",
        target=50,
        unit="Minute",
        unit_plural="Minuten",
        scope="subject",
    ),
)

_BADGES_BY_CODE: Final[dict[str, BadgeDefinition]] = {badge.code: badge for badge in BADGE_DEFINITIONS}


def badge_definitions() -> list[RewardBadgeDefinition]:
    """Der Katalog als Vertrag fuer die Oberflaeche."""
    return [
        RewardBadgeDefinition(
            code=badge.code,
            title=badge.title,
            description=badge.description,
            target=badge.target,
            unit=badge.unit,
            unit_plural=badge.unit_plural,
            scope_kind=badge.scope,  # type: ignore[arg-type]
            repeatable=badge.repeatable,
        )
        for badge in BADGE_DEFINITIONS
    ]


def subject_scope(subject_id: int) -> str:
    """Der Geltungsbereich einer fachbezogenen Auszeichnung."""
    return f"subject:{subject_id}"


def topic_scope(topic_id: int) -> str:
    """Der Geltungsbereich einer themenbezogenen Auszeichnung."""
    return f"topic:{topic_id}"


def week_scope(day: date) -> str:
    """Der Geltungsbereich einer wochenbezogenen Auszeichnung.

    Die ISO-Wochennummer und nicht der Kalender-Sonntag: eine Auszeichnung, die am
    Sonntag geholt wird und am Montag wieder verschwindet, waere keine. Jede Woche
    hat ihre eigene Zeile, deshalb ist die Nummer Teil des Schluessels.
    """
    jahr, woche, _ = day.isocalendar()
    return f"week:{jahr}-W{woche:02d}"


def task_event_key(task_id: int) -> str:
    """Der Ereignisschluessel einer Aufgabe."""
    return f"task:{task_id}"


def subject_day_event_key(subject_id: int, day: date) -> str:
    """Der Ereignisschluessel eines Fachtags."""
    return f"subject-day:{subject_id}:{day.isoformat()}"


# --- Das Ergebnis eines Buchungslaufs --------------------------------------


@dataclass(frozen=True)
class RewardOutcome:
    """Was ein Buchungslauf angelegt oder angefasst hat.

    ``changed`` sagt, ob ueberhaupt etwas geschrieben wurde. Der Aufrufer
    entscheidet damit, ob er ueberhaupt committen muss - ein Endpunkt, der
    idempotent aufgerufen wird, soll nicht jedes Mal eine Transaktion aufmachen.
    """

    entries: tuple[RewardEntry, ...]
    badges: tuple[RewardBadge, ...]
    points: int
    changed: bool


LEER: Final[RewardOutcome] = RewardOutcome(entries=(), badges=(), points=0, changed=False)


# --- Buchen -----------------------------------------------------------------


@dataclass
class _Targets:
    """Fach und Thema der Aufgaben, an denen dieser Lauf haengt."""

    subject_of_topic: dict[int, int | None]
    subject_names: dict[int, str]

    @property
    def topic_ids(self) -> set[int]:
        return set(self.subject_of_topic)

    @property
    def subject_ids(self) -> set[int]:
        return {subject for subject in self.subject_of_topic.values() if subject is not None}


def _targets(db: Session, user_id: int, task_ids: set[int]) -> _Targets:
    """Fach und Thema zu einer Menge Aufgaben - zwei Abfragen, nicht je Aufgabe eine.

    Der Weg von der Lernsession zum Fach geht ueber
    ``StudyPlanTask.topic_id -> Topic.subject_id``. Ein Thema ohne Fach ergibt
    ``None``, und das ist kein Fehler: es ist eine Aufgabe, fuer die es keinen
    Fachtag gibt.
    """
    if not task_ids:
        return _Targets({}, {})
    task_rows = db.execute(
        select(StudyPlanTask.id, StudyPlanTask.topic_id).where(
            StudyPlanTask.user_id == user_id, StudyPlanTask.id.in_(task_ids)
        )
    ).all()
    topic_ids = {int(topic_id) for _id, topic_id in task_rows if topic_id is not None}
    if not topic_ids:
        return _Targets({}, {})
    topic_rows = db.execute(
        select(Topic.id, Topic.subject_id).where(Topic.user_id == user_id, Topic.id.in_(topic_ids))
    ).all()
    subject_of_topic = {int(topic_id): subject_id for topic_id, subject_id in topic_rows}
    subject_ids = {subject_id for subject_id in subject_of_topic.values() if subject_id is not None}
    subject_names: dict[int, str] = {}
    if subject_ids:
        subject_names = {
            int(subject_id): name
            for subject_id, name in db.execute(
                select(Subject.id, Subject.name).where(Subject.user_id == user_id, Subject.id.in_(subject_ids))
            ).all()
        }
    return _Targets(subject_of_topic, subject_names)


def _lock_user(db: Session, user_id: int) -> None:
    """Die Nutzerzeile sperren, bevor gepunkt wird.

    Die Tagesobergrenze ist eine Summe ueber Zeilen, und eine Summe laesst sich nur
    serialisiert korrekt begrenzen. Auf Postgres macht das ``FOR UPDATE``, auf
    SQLite nicht - dort faengt die eindeutige Ereignisgrenze den doppelten Lauf
    immer noch, und der Betrag weicht hoechstens um eine Buchung ab.
    """
    db.scalar(select(User).where(User.id == user_id).with_for_update())


def _day_totals(db: Session, user_id: int) -> dict[date, int]:
    """``Tag -> Punkte`` ueber die gesamte Historie, in **einer** Abfrage.

    Aus denselben Zeilen entstehen ``today_points``, ``week_points`` und
    ``total_points``. Drei Abfragen wuerden drei Moeglichkeiten schaffen, in denen
    die Karte eine Zahl aus dem einen Tag und eine Summe aus einer anderen zeigt -
    und die Tagesobergrenze waere dann gegen eine Zahl geprueft, die es so nicht
    gibt.
    """
    rows = db.execute(
        select(RewardEntry.earned_on, func.coalesce(func.sum(RewardEntry.points), 0)).where(
            RewardEntry.user_id == user_id
        ).group_by(RewardEntry.earned_on)
    ).all()
    return {row[0]: int(row[1] or 0) for row in rows}


def _credited(completed_at: datetime | None, duration_minutes: int | None) -> int:
    """Wie viele Minuten einer Session angerechnet werden.

    Offen zaehlt **null**, genau wie im Lernfortschritt: eine offene Session hat
    noch keine Dauer, und wuerde beim Beenden ein zweites Mal auftauchen.
    """
    if completed_at is None or duration_minutes is None:
        return 0
    return max(0, duration_minutes)


def _minutes_by_day(
    db: Session,
    user_id: int,
    subject_id: int,
    zone: ZoneInfo,
    wanted: set[date],
) -> dict[date, int]:
    """Die angerechneten Minuten eines Fachs, **nur** an den gefragten Tagen.

    Aus der Datenbank und nicht aus den uebergebenen Sessions: ein zweiter Block
    am selben Tag muss auf denselben Tag kommen, sonst stuende am Ende eine Buchung
    je Block, und die eindeutige Ereignisgrenze wuerde den zweiten zur
    Fehlschlag machen, statt ihn zusammenzufassen.

    Die Umrechnung in den Kalendertag passiert in Python, weil sie von der Zeitzone
    des Nutzers abhaengt und SQL dafuer keine kennt. Jede Zeile wird genau einmal
    umgerechnet, danach wird nur noch mit ``date`` gerechnet.
    """
    if not wanted:
        return {}
    rows = db.execute(
        select(StudySession.started_at, StudySession.completed_at, StudySession.duration_minutes)
        .select_from(StudySession)
        .join(StudyPlanTask, StudyPlanTask.id == StudySession.task_id)
        .join(Topic, Topic.id == StudyPlanTask.topic_id)
        .where(
            StudySession.user_id == user_id,
            Topic.subject_id == subject_id,
        )
    ).all()
    tage: dict[date, int] = defaultdict(int)
    for started_at, completed_at, duration in rows:
        minuten = _credited(completed_at, duration)
        if minuten <= 0:
            continue
        tag = local_day(started_at, zone)
        if tag not in wanted:
            continue
        tage[tag] += minuten
    return dict(tage)


def _detail_for_task(title: str) -> str:
    return f"Aufgabe erledigt: {title}"[:DETAIL_LIMIT]


def _detail_for_subject_day(minutes: int, subject_name: str, day: date) -> str:
    return f"{minutes} Min. {subject_name} am {day.strftime('%d.%m.%Y')}"


def _entry(db: Session, user_id: int, kind: str, event_key: str) -> RewardEntry | None:
    return db.scalar(
        select(RewardEntry).where(
            RewardEntry.user_id == user_id, RewardEntry.kind == kind, RewardEntry.event_key == event_key
        )
    )


def _award_task(
    db: Session,
    user_id: int,
    task: StudyPlanTask,
    targets: _Targets,
    day: date,
    budget: int,
) -> tuple[RewardEntry | None, int]:
    """Punkte fuer eine erledigte Aufgabe mit Thema. Einmal je Aufgabe, fuer immer.

    Die Eindeutigkeit liegt auf ``task:{id}``, nicht auf dem Tag: wer eine Aufgabe
    ueber den ``PATCH`` wieder aufgeloest und erneut erledigt, holt sie kein zweites
    Mal. Das ist Absicht und keine Unachtsaemkeit - andernfalls waere "Aufgabe kurz
    auf offen stellen" eine Maschine zum Punktemachen.
    """
    if task.topic_id is None:
        return None, 0
    event_key = task_event_key(task.id)
    if _entry(db, user_id, REWARD_KIND_TASK, event_key) is not None:
        return None, 0
    points = min(TASK_POINTS, budget)
    entry = RewardEntry(
        user_id=user_id,
        kind=REWARD_KIND_TASK,
        event_key=event_key,
        points=points,
        minutes=0,
        subject_id=targets.subject_of_topic.get(task.topic_id),
        topic_id=task.topic_id,
        detail=_detail_for_task(task.title),
        earned_on=day,
    )
    db.add(entry)
    return entry, points


def _award_subject_day(
    db: Session,
    user_id: int,
    subject_id: int,
    tag: date,
    minutes: int,
    subject_name: str,
    day_totals: dict[date, int],
    budget: int,
) -> tuple[RewardEntry, int, int]:
    """Punkte fuer einen Fachtag: eine Buchung je Fach und Tag, Betrag monoton.

    Der Betrag wird **nur nach oben** gefuehrt, nie zurueck: `max` aus dem neuen
    Wert und dem, der schon dasteht. Faellt die Lernzeit eines Tages einmal weg -
    weil jemand eine Aufgabe loescht, und ihre Sessions mitkassiert werden -
    bleibt der vergebene Betrag stehen. Eine Belohnung, die man zuruecknehmen
    kann, ist eine Schuld, und eine Belohnung als Schuld ist keine.
    """
    event_key = subject_day_event_key(subject_id, tag)
    ziel = subject_day_points(minutes)
    bestehend = _entry(db, user_id, REWARD_KIND_SUBJECT_DAY, event_key)
    if bestehend is None:
        punkte = min(ziel, budget)
        entry = RewardEntry(
            user_id=user_id,
            kind=REWARD_KIND_SUBJECT_DAY,
            event_key=event_key,
            points=punkte,
            minutes=minutes,
            subject_id=subject_id,
            topic_id=None,
            detail=_detail_for_subject_day(minutes, subject_name, tag),
            earned_on=tag,
        )
        db.add(entry)
        return entry, punkte, budget - punkte

    # Die Tagesdeckel gilt fuer den ganzen Tag. ``day_totals`` enthaelt schon die
    # Punkte dieser Zeile, deshalb wird sie vorher abgezogen - sonst waere derselbe
    # Betrag zweimal gedeckelt und der Fachtag kaeme unter dem Konto heraus.
    ohne_diese = day_totals.get(tag, 0) - bestehend.points
    neuer = max(bestehend.points, min(ziel, max(0, MAX_DAY_POINTS - ohne_diese)))
    vorher = bestehend.points
    bestehend.points = neuer
    bestehend.minutes = minutes
    bestehend.detail = _detail_for_subject_day(minutes, subject_name, tag)
    return bestehend, neuer - vorher, budget - (neuer - vorher)


def record_progress(
    db: Session,
    user_id: int,
    *,
    zone: ZoneInfo,
    task: StudyPlanTask | None = None,
    sessions: Sequence[StudySession] = (),
    now: datetime | None = None,
) -> RewardOutcome:
    """Punkte und Auszeichnungen fuer ein Ereignis vergeben.

    **Der Aufruf ist idempotent.** Ein zweiter Aufruf mit derselben Aufgabe schreibt
    keine zweiten Punkte, weil das Ereignis schon in ``reward_entries`` steht und die
    eindeutige Grenze es dort auch beweist - der zweifache Aufruf von
    ``POST /complete`` ist damit kein Sonderfall, sondern derselbe Codepfad.

    ``sessions`` sind die **abgeschlossenen** Sessions dieses Ereignisses. Sie
    werden zu Fachtagen gebuendelt; ein zweiter Block am selben Tag erhoeht denselben
    Fachtag, statt eine zweite Buchung anzulegen. Fuer eine Aufgabe, deren Erledigung
    schon gebucht ist, zaehlt ihre Zeit kein zweites Mal - siehe den entsprechenden
    Kommentar unten bei ``rechnungs_sessions``.

    Die Methode committet **nicht**. Sie stellt nur bereit, damit der Aufrufer die
    Buchung in derselben Transaktion schreibt wie die Aenderung, die sie ausgeloest
    hat - ein Abschluss ohne Punkte waere ein halber Zustand.
    """
    moment = as_utc(now or utc_now())
    today = moment.astimezone(zone).date()
    _lock_user(db, user_id)

    # Die gerade geschlossenen oder neu angelegten Sessions muessen vor der
    # Tagesabfrage sichtbar sein: die Sitzung im Test laeuft mit `autoflush=False`,
    # und eine Abfrage wuerde die neue Zeile sonst nicht sehen.
    db.flush()

    task_ids = {session.task_id for session in sessions}
    if task is not None:
        task_ids.add(task.id)
    targets = _targets(db, user_id, task_ids)

    day_totals = _day_totals(db, user_id)
    budget = max(0, MAX_DAY_POINTS - day_totals.get(today, 0))

    entries: list[RewardEntry] = []
    vergeben = 0

    if task is not None and task.status == "completed":
        entry, punkte = _award_task(db, user_id, task, targets, today, budget)
        if entry is not None:
            entries.append(entry)
            budget -= punkte
            vergeben += punkte

    # **Wem die Erledigung schon gutgeschrieben wurde, zaehlt keine zweite Zeit.**
    #
    # Der Abschluss ohne offene Session erfindet eine Lernsession ueber die geplante
    # Dauer der Aufgabe. Ein zweiter Abschluss derselben Aufgabe - nach dem
    # Zurueckstellen auf offen - wuerde damit dieselbe geplante Dauer ein zweites
    # Mal einbringen, und der Aufhaenger waere ein Zaehler. Die Aufgabe ist
    # bereits gebucht, also zaehlt ihre Zeit nicht noch einmal. Lieber zu wenig
    # Punkte als eine Maschine, mit der sich Punkte erzeugen lassen.
    bereits_gebucht = task is not None and _entry(db, user_id, REWARD_KIND_TASK, task_event_key(task.id)) is not None
    rechnungs_sessions = [
        session for session in sessions if not (bereits_gebucht and task is not None and session.task_id == task.id)
    ]

    # Betroffen sind nur die Fachtage, an denen eine der uebergebenen Sessions
    # begonnen hat. Die Historie eines Faches an einem anderen Tag kann sich durch
    # dieses Ereignis nicht aendern, und sie jedes Mal neu zu rechnen waere der
    # Unterschied zwischen einer Karte und einem Server unter Last.
    betroffene_tage = {local_day(session.started_at, zone) for session in rechnungs_sessions}
    for subject_id in sorted(targets.subject_ids):
        fachname = targets.subject_names.get(subject_id, NO_SUBJECT_LABEL)
        for tag, minuten in _minutes_by_day(db, user_id, subject_id, zone, betroffene_tage).items():
            if minuten < MIN_SUBJECT_DAY_MINUTES:
                continue
            entry, punkte, budget = _award_subject_day(
                db, user_id, subject_id, tag, minuten, fachname, day_totals, budget
            )
            entries.append(entry)
            vergeben += punkte

    # Noch einmal spuelen, bevor die Auszeichnungen ausgewertet werden: eine
    # davon misst den groessten Tageswert aus den Buchungen. Ohne dieses Flush
    # wuerde die gerade geschriebene Zeile fehlen und die Auszeichnung erst beim
    # naechsten Ereignis auftauchen - ein Tag spaeter als der, an dem sie
    # eigentlich errungen wurde.
    db.flush()

    badges = _refresh_badges(
        db,
        user_id,
        zone,
        today,
        subjects=targets.subject_ids,
        topics=targets.topic_ids,
    )
    return RewardOutcome(
        entries=tuple(entries),
        badges=tuple(badges),
        points=vergeben,
        changed=bool(entries) or bool(badges),
    )

# --- Auszeichnungen ---------------------------------------------------------


def _completed_topic_tasks(db: Session, user_id: int) -> int:
    """Wie viele Aufgaben mit Thema erledigt sind.

    Nur Aufgaben **mit** Thema: das ist dieselbe Bedingung wie bei ``TASK_POINTS``.
    Eine Auszeichnung, die eine Bedingung zaehlte, die keine Punkte ausloest, waere
    eine Belohnung fuer ein anderes Spiel.
    """
    return int(
        db.scalar(
            select(func.count(StudyPlanTask.id)).where(
                StudyPlanTask.user_id == user_id,
                StudyPlanTask.status == "completed",
                StudyPlanTask.topic_id.is_not(None),
            )
        )
        or 0
    )


def _subject_task_progress(db: Session, user_id: int) -> dict[int, tuple[int, int]]:
    """Je Fach ``(erledigt, gesamt)`` ueber alle nicht stornierten Aufgaben.

    Stornierte Aufgaben zaehlen nicht in den Nenner: ein abgebrochener Plan darf eine
    Auszeichnung nicht zurueck in den Wartestatus schicken. Eine Abfrage fuer alle
    Faecher, weil die Zahl der Abfragen sonst mit der Zahl der Faecher waechse.
    """
    rows = db.execute(
        select(
            Topic.subject_id,
            func.coalesce(func.sum(case((StudyPlanTask.status == "completed", 1), else_=0)), 0),
            func.count(StudyPlanTask.id),
        )
        .select_from(StudyPlanTask)
        .join(Topic, Topic.id == StudyPlanTask.topic_id)
        .where(
            StudyPlanTask.user_id == user_id,
            Topic.subject_id.is_not(None),
            StudyPlanTask.status != "cancelled",
        )
        .group_by(Topic.subject_id)
    ).all()
    return {int(subject_id): (int(erledigt or 0), int(gesamt or 0)) for subject_id, erledigt, gesamt in rows}


def _subject_best_day(db: Session, user_id: int, subject_id: int) -> int:
    """Die meisten Minuten, die an einem Tag in diesem Fach angefallen sind.

    Aus den Buchungen statt aus den Lernsessions, und das ist Absicht: die Buchung
    **ist** der Fachtag. Damit beantwortet sich die Frage mit einer Abfrage ueber eine
    indizierte Tabelle, statt die ganze Session-Historie in Python nach Tagen zu
    sortieren - und sie bleibt auch dann richtig, wenn die Tagesdeckel den Betrag
    gekappt hat: die Minuten stehen unabhaengig davon in der Zeile.
    """
    return int(
        db.scalar(
            select(func.max(RewardEntry.minutes)).where(
                RewardEntry.user_id == user_id,
                RewardEntry.subject_id == subject_id,
                RewardEntry.kind == REWARD_KIND_SUBJECT_DAY,
            )
        )
        or 0
    )


def _topic_studied_days(db: Session, user_id: int, topic_ids: set[int], zone: ZoneInfo) -> dict[int, int]:
    """Je Thema die Zahl der Kalendertage mit abgeschlossener Lernzeit."""
    if not topic_ids:
        return {}
    rows = db.execute(
        select(StudyPlanTask.topic_id, StudySession.started_at, StudySession.completed_at, StudySession.duration_minutes)
        .select_from(StudySession)
        .join(StudyPlanTask, StudyPlanTask.id == StudySession.task_id)
        .where(
            StudySession.user_id == user_id,
            StudyPlanTask.topic_id.in_(topic_ids),
        )
    ).all()
    tage: dict[int, set[date]] = defaultdict(set)
    for topic_id, started_at, completed_at, duration in rows:
        if topic_id is None or _credited(completed_at, duration) <= 0:
            continue
        tage[int(topic_id)].add(local_day(started_at, zone))
    return {topic_id: len(days) for topic_id, days in tage.items()}


def _put_badge(
    db: Session,
    user_id: int,
    definition: BadgeDefinition,
    scope: str,
    progress: int,
    target: int,
    *,
    subject_id: int | None = None,
    topic_id: int | None = None,
) -> RewardBadge | None:
    """Eine Auszeichnung fortschreiben; zurueckgegeben wird nur, was **neu** errungen wurde.

    Eine einmal verdiente Auszeichnung wird danach **nicht** mehr angefasst. Das ist der
    Unterschied zu einer laufenden Anforderung, und er ist beabsichtigt: eine
    Belohnung, die wieder verschwinden kann, erzeugt Druck. Fuer eine wiederholte
    Auszeichnung ist das kein Widerspruch - die naechste Woche hat eine eigene Zeile
    mit eigenem Bereich und bekommt dort ihren eigenen Fortschritt.
    """
    if target < 1:
        return None
    erreicht = progress >= target
    existing = db.scalar(
        select(RewardBadge).where(
            RewardBadge.user_id == user_id, RewardBadge.code == definition.code, RewardBadge.scope == scope
        )
    )
    if existing is not None:
        if existing.awarded_at is not None:
            return None
        if (existing.progress, existing.target, existing.subject_id, existing.topic_id) != (
            progress,
            target,
            subject_id,
            topic_id,
        ):
            existing.progress = progress
            existing.target = target
            existing.subject_id = subject_id
            existing.topic_id = topic_id
        if erreicht:
            existing.awarded_at = utc_now()
        return existing if erreicht else None
    badge = RewardBadge(
        user_id=user_id,
        code=definition.code,
        scope=scope,
        subject_id=subject_id,
        topic_id=topic_id,
        progress=progress,
        target=target,
        awarded_at=utc_now() if erreicht else None,
    )
    db.add(badge)
    return badge if erreicht else None


def _refresh_badges(
    db: Session,
    user_id: int,
    zone: ZoneInfo,
    today: date,
    *,
    subjects: set[int],
    topics: set[int],
) -> list[RewardBadge]:
    """Alle Auszeichnungen nachziehen, die dieses Ereignis betrifft.

    Gerechnet werden die globalen, die der aktuellen Woche und die der betroffenen
    Faecher und Themen. Alles andere kann sich durch dieses Ereignis nicht aendern -
    eine Auszeichnung ueber ein anderes Fach stuende sonst nur auf ihrer alten Basis
    wieder auf.
    """
    vergeben: list[RewardBadge] = []

    tage = studied_days(db, user_id, zone, today)
    badge = _put_badge(db, user_id, _BADGES_BY_CODE["erster_abschluss"], SCOPE_GLOBAL, _completed_topic_tasks(db, user_id), 1)
    if badge is not None:
        vergeben.append(badge)
    badge = _put_badge(db, user_id, _BADGES_BY_CODE["serie_von_sieben"], SCOPE_GLOBAL, longest_streak(tage), 7)
    if badge is not None:
        vergeben.append(badge)
    monday, sunday = week_bounds(today)
    badge = _put_badge(
        db,
        user_id,
        _BADGES_BY_CODE["wochenrhythmus"],
        week_scope(today),
        sum(1 for day in tage if monday <= day <= sunday),
        5,
    )
    if badge is not None:
        vergeben.append(badge)

    if subjects:
        aufgaben = _subject_task_progress(db, user_id)
        for subject_id in sorted(subjects):
            erledigt, gesamt = aufgaben.get(subject_id, (0, 0))
            if gesamt >= 1:
                badge = _put_badge(
                    db,
                    user_id,
                    _BADGES_BY_CODE["fach_durchgearbeitet"],
                    subject_scope(subject_id),
                    erledigt,
                    gesamt,
                    subject_id=subject_id,
                )
                if badge is not None:
                    vergeben.append(badge)
            bester = _subject_best_day(db, user_id, subject_id)
            if bester > 0:
                badge = _put_badge(
                    db,
                    user_id,
                    _BADGES_BY_CODE["fuenfzig_minuten"],
                    subject_scope(subject_id),
                    bester,
                    50,
                    subject_id=subject_id,
                )
                if badge is not None:
                    vergeben.append(badge)

    if topics:
        tage_je_thema = _topic_studied_days(db, user_id, topics, zone)
        for topic_id in sorted(topics):
            anzahl = tage_je_thema.get(topic_id, 0)
            if anzahl <= 0:
                continue
            badge = _put_badge(
                db,
                user_id,
                _BADGES_BY_CODE["regelmaessig_wiederholt"],
                topic_scope(topic_id),
                anzahl,
                3,
                topic_id=topic_id,
            )
            if badge is not None:
                vergeben.append(badge)
    return vergeben


# --- Lesen ------------------------------------------------------------------


@dataclass(frozen=True)
class RewardRead:
    """Die fertige Antwort, ohne den Schritt ueber die Pydantic-Modelle."""

    summary: RewardSummary
    entries: list[RewardEntryRead]
    badges: list[RewardBadgeRead]


def _names(db: Session, user_id: int, subject_ids: set[int], topic_ids: set[int]) -> tuple[dict[int, str], dict[int, str]]:
    """Fach- und Themennamen fuer die Anzeige - je eine Abfrage, keine je Zeile."""
    faecher: dict[int, str] = {}
    themen: dict[int, str] = {}
    if subject_ids:
        faecher = {
            int(subject_id): name
            for subject_id, name in db.execute(
                select(Subject.id, Subject.name).where(Subject.user_id == user_id, Subject.id.in_(subject_ids))
            ).all()
        }
    if topic_ids:
        themen = {
            int(topic_id): name
            for topic_id, name in db.execute(
                select(Topic.id, Topic.name).where(Topic.user_id == user_id, Topic.id.in_(topic_ids))
            ).all()
        }
    return faecher, themen


def _scope_kind(scope: str) -> str:
    """Der Geltungsbereich als eines der vier bekannten Muster.

    Eine unbekannte Form - etwa aus einer kuenftigen Revision - wird nicht geraten,
    sondern als ``global`` gemeldet: die Karte zeigt den Bereich dann als Text und
    laeuft nicht in eine Ausnahme.
    """
    if scope.startswith("subject:"):
        return "subject"
    if scope.startswith("topic:"):
        return "topic"
    if scope.startswith("week:"):
        return "week"
    return "global"


def rewards_read(
    db: Session,
    user_id: int,
    zone: ZoneInfo,
    timezone_name: str,
    now: datetime | None = None,
    entries_limit: int = ENTRY_LIMIT,
) -> RewardRead:
    """Konto, Auszeichnungen und die letzten Buchungen.

    ``entries_limit`` begrenzt die **Anzeige**, nicht die Summe: ``total_points``
    laeuft ueber alle Zeilen. Ohne die Grenze wuerde die Karte irgendwann die ganze
    Historie in den Browser ziehen.
    """
    moment = as_utc(now or utc_now())
    today = moment.astimezone(zone).date()
    monday, sunday = week_bounds(today)

    totals = _day_totals(db, user_id)
    total_points = sum(totals.values())
    today_points = totals.get(today, 0)
    week_points = sum(punkte for tag, punkte in totals.items() if monday <= tag <= sunday)

    begrenzt = max(1, min(entries_limit, ENTRY_LIMIT_MAX))
    rows = db.scalars(
        select(RewardEntry)
        .where(RewardEntry.user_id == user_id)
        .order_by(RewardEntry.earned_on.desc(), RewardEntry.created_at.desc(), RewardEntry.id.desc())
        .limit(begrenzt)
    ).all()
    badge_rows = db.scalars(select(RewardBadge).where(RewardBadge.user_id == user_id).order_by(RewardBadge.id)).all()

    faecher, themen = _names(
        db,
        user_id,
        {int(row.subject_id) for row in rows if row.subject_id is not None}
        | {int(row.subject_id) for row in badge_rows if row.subject_id is not None},
        {int(row.topic_id) for row in rows if row.topic_id is not None}
        | {int(row.topic_id) for row in badge_rows if row.topic_id is not None},
    )

    badges: list[RewardBadgeRead] = []
    for row in badge_rows:
        definition = _BADGES_BY_CODE.get(row.code)
        if definition is None:
            # Eine Auszeichnung aus einer kuenftigen Katalogversion: sie bleibt in der
            # Datenbank, taucht aber nicht als Text ohne Erklaerung auf.
            continue
        badges.append(
            RewardBadgeRead(
                id=row.id,
                code=row.code,
                title=definition.title,
                description=definition.description,
                scope=row.scope,
                scope_kind=_scope_kind(row.scope),  # type: ignore[arg-type]
                subject_id=row.subject_id,
                subject_name=faecher.get(row.subject_id) if row.subject_id is not None else None,
                topic_id=row.topic_id,
                topic_name=themen.get(row.topic_id) if row.topic_id is not None else None,
                progress=row.progress,
                target=row.target,
                awarded=row.awarded_at is not None,
                awarded_at=as_utc(row.awarded_at) if row.awarded_at is not None else None,
                repeatable=definition.repeatable,
                unit=definition.unit,
                unit_plural=definition.unit_plural,
            )
        )
    # Erreichte zuerst, danach die mit dem groessten Fortschritt. Im Python
    # sortiert, weil der Anteil ein Ausdruck waere und die Zeilen sich in der
    # Groessenordnung einer Hand bewegen.
    badges.sort(key=lambda badge: (not badge.awarded, -(badge.progress / badge.target), badge.code, badge.scope))

    entries = [
        RewardEntryRead(
            id=row.id,
            kind=row.kind,  # type: ignore[arg-type]
            points=row.points,
            minutes=row.minutes,
            subject_id=row.subject_id,
            subject_name=faecher.get(row.subject_id) if row.subject_id is not None else None,
            topic_id=row.topic_id,
            topic_name=themen.get(row.topic_id) if row.topic_id is not None else None,
            detail=row.detail,
            earned_on=row.earned_on,
            earned_at=as_utc(row.created_at),
        )
        for row in rows
    ]

    awarded = sum(1 for badge in badges if badge.awarded)
    return RewardRead(
        summary=RewardSummary(
            generated_at=moment,
            timezone=timezone_name,
            today_points=today_points,
            today_cap=MAX_DAY_POINTS,
            week_points=week_points,
            total_points=total_points,
            today=today,
            week_starts_on=monday,
            week_ends_on=sunday,
            awarded_badges=awarded,
            pending_badges=len(badges) - awarded,
        ),
        entries=entries,
        badges=badges,
    )


__all__ = [
    "BADGE_DEFINITIONS",
    "ENTRY_LIMIT",
    "MAX_DAY_POINTS",
    "MAX_SUBJECT_DAY_POINTS",
    "MIN_SUBJECT_DAY_MINUTES",
    "SUBJECT_DAY_STEPS",
    "TASK_POINTS",
    "BadgeDefinition",
    "RewardOutcome",
    "RewardRead",
    "badge_definitions",
    "record_progress",
    "reward_rules",
    "rewards_read",
    "subject_day_event_key",
    "subject_day_points",
    "subject_scope",
    "task_event_key",
    "topic_scope",
    "week_scope",
]
