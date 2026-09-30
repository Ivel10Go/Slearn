"""Noten: Schnitt, Verlauf, Hochrechnung, Freigabe und Import aus Dokumenten.

Vier Dinge wohnen hier, weil sie dieselbe Tabelle lesen und dieselbe Rechnung
benutzen:

* **Schnitt und Verlauf** (``grade_summary``): gewichteter Mittelwert je Fach und
  ueber alle Faecher, dazu der Verlauf als kumulierter Schnitt je Tag. Der
  Verlauf ist eine Liste von Zahlen, keine Grafik - die Linie im Dashboard baut
  die Oberflaeche daraus, ohne eine Diagrammbibliothek.
* **Hochrechnung** (``project_average``): was ab jetzt noch kommen muss, damit
  der Schnitt die Bestehensgrenze erreicht. Ohne diesen Schritt waere die Karte
  ein Rueckblick, und ein Rueckblick sagt nichts darueber, was noch zu retten ist.
* **Freigabe** (``grades_sharing_enabled``): ob Noten den KI-Dienst ueberhaupt
  erreichen duerfen. Siehe unten, warum das nicht eine Nebensache ist.
* **Import aus einem Dokument** (``parse_grade_document``): eine abgegebene
  Notenliste wird Zeile fuer Zeile gelesen, ohne ein Modell zu fragen.

Die gewichtete Rechnung
-----------------------

Eine Note zaehlt so oft, wie ihr Gewicht sagt. Ein Test, der die halbe Note
verspricht, geht mit Gewicht 2 in den Schnitt ein, eine muendliche Leistung mit
Gewicht 1. Der Schnitt ist deshalb **immer** ``summe(weight)``-gewichtet, nie ein
einfaches Mittel - sonst wuerde eine muendliche Note eine Klassenarbeit
ausgleichen koennen, und genau das will die Gewichtung verhindern.

Alle Mittelwerte laufen ueber ``weighted_average``; es gibt keinen zweiten Weg,
sonst koennten Karte und Hochrechnung verschiedene Zahlen zeigen.

Die Bestehensgrenze
-------------------

``PASSING_GRADE = 4.0`` ist die deutsche Grenze ("ausreichend") und steht als
eine Konstante hier, nicht als Zahl in der Oberflaeche. Sie gehoert zur
Rechnung: die Oberflaeche bekommt ``passed`` und ``still_possible`` als
Wahrheitswerte und uebersetzt sie in Worte.

Notenstand und Prioritaet
-------------------------

``grade_priority`` macht aus einem Fachschnitt eine Prioritaetsstufe. Sie geht
als **Mindestwert** in die Prioritaet einer Lernaufgabe ein, nie als eigener
Sortierschluessel - die Begruendung steht in ``app/services/study.py`` bei
``generate_exam_tasks``, weil die Aufloesung dort faellt.

Die Freigabe
------------

Noten sind die sensibelste Zahl, die diese Anwendung kennt, und sie ist die
einzige, die **nicht** aus einem Dokument stammt: ein Stundenplan, eine Hausaufgabe
und ein Klausurtermin liegen ohnehin im Schulportal, in das der KI-Dienst
ohnehin keinen Schreibzugriff hat. Eine Note ist eine Leistungsbeurteilung.

Deshalb ist der Weg zum Provider eine ausdrueckliche Entscheidung:

* **Aus ist der Standard.** Ohne gesetzte Freigabe verlassen keine Noten den
  Prozess - das gilt fuer den Willkommenssatz genauso wie fuer
  ``documents/ask``.
* **Die Freigabe steht in ``settings.preferences``**, unter
  ``SHARING_PREFERENCE_KEY``. Bewusst dort und nicht in einer eigenen Spalte:
  die Spalte gibt es schon, und ein zweiter Ort fuer Nutzereinstellungen waere
  eine dritte Wahrheit ueber dieselbe Sache - dasselbe Argument wie beim
  Wochenziel in ``app/services/progress.py``.
* **Jeder unlesbare Zustand heisst "aus".** Kein Schluessel, ``false``, ein
  falscher Typ, kaputtes JSON: ``grades_sharing_enabled`` gibt ``False``
  zurueck. Ein Schalter, der durch einen kaputten Eintrag von selbst aufginge,
  waere kein Schalter.
* **Zuruecknehmen loescht den heutigen Willkommenssatz.** Der Satz ist
  zwischengespeichert und kann eine Note genannt haben. Nach dem Widerruf darf
  das nicht weiter angezeigt werden; neu erzeugt wird er beim naechsten Abruf.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.time import utc_now
from app.db.entities import Document, Grade, GreetingCache, Subject, UserSettings
from app.schemas.grades import (
    GradeAverageRead,
    GradeImportCandidateRead,
    GradeImportPreview,
    GradeImportResult,
    GradeKind,
    GradeProjectionRead,
    GradeSummary,
    GradeTrendPointRead,
)
from app.services.documents import read_document_text

logger = logging.getLogger("school_assistant")

# Die drei Arten, die ein Notenspiegel kennt. Die Liste ist geschlossen: eine
# vierte Art waere eine Aenderung an der Skala, und die steht in
# ``app/schemas/grades.py`` als Literal plus als Check-Constraint in der
# Migration - nicht in einer Liste, die man beim Lesen uebersieht.
GRADE_KINDS: Final[tuple[str, ...]] = ("written_exam", "test", "oral")

# Die deutschen Bestehensgrenze. Ab 4,0 ist bestanden; 4,0 selbst ist es noch.
PASSING_GRADE: Final[float] = 4.0

# Die beste moegliche Note. Sie ist der Anker der Hochrechnung: alles, was der
# beste noch moegliche Rest bringt.
BEST_GRADE: Final[float] = 1.0

# Schluessel der Freigabe in `settings.preferences`. Fehlt er, ist die Freigabe
# aus - siehe Modul-Docstring.
SHARING_PREFERENCE_KEY: Final[str] = "share_grades_with_ai"

# Wie viele Tagepunkte der Verlauf liefert. Eine Linie aus zweihundert Punkten
# ist kein Verlauf mehr, sondern ein Rauschen, und die Antwort wird sonst
# grösser als die Karte, die sie zeigt.
TREND_LIMIT: Final[int] = 30

# Prioritaetsstufen aus einem Fachschnitt. **Steigend mit der Note**, weil auf
# der deutschen Skala eine kleine Zahl gut ist: 1,0 heisst sehr gut. Wer hier
# denkt "kleine Zahl = wenig wichtig", baut die Prioritaet genau umgekehrt herum.
#
# Die Stufe ist die **Dringlichkeit**, nicht die Leistung. 0 bis 4, wie bei der
# Konfidenz, damit `max()` aus beiden Quellen eine Skala mit einem Ende ergibt.
# Oberhalb der Bestehensgrenze gibt es keine feinere Abstufung mehr: es gibt
# keine Note schlimmer als "nicht genuegend", also ist die maximale Stufe dort
# erreicht und eine feinere Teilung waere eine erfundene.
GRADE_PRIORITY_STEPS: Final[tuple[tuple[float, int], ...]] = (
    (2.0, 0),
    (3.0, 1),
    (3.5, 2),
    (PASSING_GRADE, 3),
)
# Alles, was schlechter ist als die Bestehensgrenze.
MAX_GRADE_PRIORITY: Final[int] = 4

NO_SUBJECT_LABEL: Final[str] = "Ohne Fach"
OVERALL_SUBJECT_LABEL: Final[str] = "Alle Fächer"

# --- Zeilen aus einem Dokument lesen ----------------------------------------

# Ein Datum in deutscher Schreibweise, etwa "12.09.2026" oder "4.9.26".
_DT_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\b")
# Ein Datum in ISO-Schreibweise.
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
# Eine Notenzahl, die in ihrer eigenen Spalte steht. Die negative Umgebung
# schuetzt Zahlen mit mehreren Stellen ("2026") und Datumsfragmente ("12.09"),
# sonst wuerde aus jeder Zeile mit einer Jahreszahl eine Note.
_GRADE_NUMBER = re.compile(r"(?<![\d.,])(\d)(?:[.,](\d))?(?![\d])")
# Stueckelung einer Zeile fuer die Schluesselbildung: nur der Leerraum wird
# vereinheitlicht, damit derselbe Text denselben Schluessel ergibt.
_WHITESPACE = re.compile(r"\s+")
# Woerter, die die Art der Leistung verraten.
_KIND_WORDS: Final[tuple[tuple[str, str], ...]] = (
    ("klassarbeit", "written_exam"),
    ("klausur", "written_exam"),
    ("schulaufgabe", "written_exam"),
    ("arbeit", "written_exam"),
    ("test", "test"),
    ("quiz", "test"),
    ("kleinere", "test"),
    ("mündlich", "oral"),
    ("muendlich", "oral"),
    ("oral", "oral"),
)
# Zeichen, die nach dem Herausrechnen der Zahl nur noch Fuellung sind.
_TRIM_CHARS = " \t|;:,()[]-–—·. "
# Unter dieser Laenge ist der Rest einer Zeile kein Kommentar, sondern Muell.
_MIN_COMMENT = 3


class GradeImportError(Exception):
    """Ein Fehler, der sich nicht in einer Datenbankzeile ausdruecken laesst."""


class GradeImportNotFound(GradeImportError):
    """Das Dokument gehoert nicht dem Nutzer.

    Eigene Ausnahme, weil daraus ein **404** wird und nicht ein 422: eine fremde
    Id soll nicht verraten, dass es sie gibt - dieselbe Regel wie bei
    ``_owned_topic`` im Lernfortschritt.
    """


@dataclass(frozen=True, slots=True)
class WeightedGrade:
    """Eine Note fuer die Rechnung: Wert und Gewicht, mehr nicht."""

    value: float
    weight: int


@dataclass(frozen=True, slots=True)
class GradeCandidate:
    """Ein Fund aus einem Dokument, noch ohne Datenbank-Id."""

    key: str
    line_number: int
    line: str
    value: float
    kind: GradeKind
    graded_on: date
    weight: int
    comment: str | None
    subject_id: int | None
    subject_name: str | None
    subject_recognized: bool


# --- Rechnen ----------------------------------------------------------------


def weighted_average(entries: list[WeightedGrade]) -> float | None:
    """Der gewichtete Mittelwert, oder ``None`` ohne einzige Note.

    ``None`` und ``0,0`` sind zwei verschiedene Antworten: ohne Noten gibt es
    keinen Schnitt, und 0,0 waere eine Note, die es in dieser Skala nicht gibt.
    """
    total_weight = sum(entry.weight for entry in entries)
    if total_weight <= 0:
        return None
    total = sum(entry.value * entry.weight for entry in entries)
    return round(total / total_weight, 2)


def is_passing(average: float | None) -> bool | None:
    """Hoechstens ab ``PASSING_GRADE`` ist bestanden; ohne Schnitt bleibt es offen."""
    if average is None:
        return None
    return average <= PASSING_GRADE


def grade_priority(average: float | None) -> int:
    """Dringlichkeitsstufe aus einem Fachschnitt, 0 bis 4.

    **Ohne Schnitt 0 - nicht 4.** Das ist der einzige Unterschied zur
    Konfidenz im Lernfortschritt, und er ist Absicht: dort heisst "kein Wert"
    "ich fuehle mich unsicher", weil das eine Aussage der Lernenden ist. Hier
    heisst "kein Wert" nur, dass es keine Noten gibt. Eine fehlende Angabe ist
    keine schlechte Note, und sie darf keine Aufgabe nach vorn schieben, die
    aus einem anderen Grund hinten gehoert.
    """
    if average is None:
        return 0
    for grenze, stufe in GRADE_PRIORITY_STEPS:
        if average <= grenze:
            return stufe
    return MAX_GRADE_PRIORITY


def project_average(
    entries: list[WeightedGrade],
    projected_weight: int,
    target: float = PASSING_GRADE,
) -> tuple[float | None, float | None, bool]:
    """Was ab jetzt noch kommen muss: ``(needed, best_possible, still_possible)``.

    * ``needed`` ist der Schnitt, den der **offene** Teil der Gewichtung bringen
      muss, damit der Gesamtschnitt ``target`` erreicht. Liegt die noetige Note
      ausserhalb der Skala, ist der Wert ``None`` und nicht eine erfundene Zahl:
      "0,4 noetig" gibt es nicht, und eine Anzeige, die so etwas behauptet, waere
      schlimmer als keine. Dann bleibt nur ``still_possible = False`` - und das
      ist die richtige Antwort, denn ausserhalb der Skala heisst: nicht erreichbar.
    * ``best_possible`` ist der Schnitt, der herauskommt, wenn der offene Teil
      ausschliesslich aus Bestnoten besteht. Ohne jede bestehende Note **und**
      ohne offene Gewichtung ist er ``None``: es gibt dann nichts zu rechnen.
    * ``still_possible`` ist die Frage, die die Karte beantwortet.
    """
    if projected_weight < 0:
        raise GradeImportError("projected_weight must not be negative")
    current_weight = sum(entry.weight for entry in entries)
    current_total = sum(entry.value * entry.weight for entry in entries)
    if current_weight <= 0 and projected_weight <= 0:
        return None, None, False

    needed: float | None = None
    if projected_weight > 0:
        raw = (target * (current_weight + projected_weight) - current_total) / projected_weight
        if BEST_GRADE <= raw <= 6.0:
            needed = round(raw, 2)
    best = round((current_total + projected_weight * BEST_GRADE) / (current_weight + projected_weight), 2)
    # Die Epsilon-Grenze faengt den Fall, in dem die Rechnung 4,0000001 ergibt,
    # obwohl genau 4,0 herauskommt. Eine Anzeige, die wegen einer Rundung
    # "nicht mehr erreichbar" behauptet, waere schlimmer als eine zu optimistische.
    return needed, best, best <= target + 1e-9


# --- Datenbank lesen --------------------------------------------------------


def subject_averages(db: Session, user_id: int, subject_ids: set[int]) -> dict[int, float]:
    """Fach -> gewichteter Schnitt, in **einer** Abfrage.

    Die Aufgabenliste liest das fuer jede Aufgabe, und ein Lauf je Fach waere der
    klassische N+1-Fall. Noten **ohne** Fach werden hier uebersprungen: ein
    Fachschnitt ohne Fach waere ein Schnitt, den niemand zuordnen kann. Sie
    zaehlen aber im Gesamtschnitt mit (``_all_grades``), weil "alles" alles
    heisst.
    """
    if not subject_ids:
        return {}
    rows = db.execute(
        select(
            Grade.subject_id,
            func.sum(Grade.grade_value * Grade.weight),
            func.sum(Grade.weight),
        )
        .where(Grade.user_id == user_id, Grade.subject_id.in_(subject_ids))
        .group_by(Grade.subject_id)
    ).all()
    durchschnitt: dict[int, float] = {}
    for subject_id, total, weight in rows:
        if subject_id is None or weight is None or not weight:
            continue
        durchschnitt[int(subject_id)] = round(float(total or 0.0) / float(weight), 2)
    return durchschnitt


def list_grades(
    db: Session,
    user_id: int,
    subject_id: int | None = None,
    limit: int = 50,
) -> list[Grade]:
    """Die Noten des Nutzers, neueste zuerst.

    Der Verlauf im Dashboard will aufsteigend, die Liste in der Karte absteigend;
    die Oberflaeche dreht die Liste fuer ihre Zwecke - eine zweite Sortierung im
    Backend waere eine zweite Wahrheit ueber dieselbe Frage.
    """
    query = (
        select(Grade)
        .options(joinedload(Grade.subject))
        .where(Grade.user_id == user_id)
        .order_by(Grade.graded_on.desc(), Grade.id.desc())
        .limit(limit)
    )
    if subject_id is not None:
        query = query.where(Grade.subject_id == subject_id)
    return list(db.scalars(query).all())


def _grades_for_subject(db: Session, user_id: int, subject_id: int | None) -> list[WeightedGrade]:
    query = select(Grade.grade_value, Grade.weight).where(Grade.user_id == user_id)
    if subject_id is None:
        query = query.where(Grade.subject_id.is_(None))
    else:
        query = query.where(Grade.subject_id == subject_id)
    return [WeightedGrade(value=float(value), weight=int(weight)) for value, weight in db.execute(query).all()]


def _all_grades(db: Session, user_id: int) -> list[tuple[Grade, int]]:
    """Alle Noten mit ihrem Gewicht, aufsteigend nach Datum.

    Aufsteigend, weil der Verlauf eine Zeitreihe ist und nicht eine Liste: der
    kumulierte Schnitt waere sonst rueckwaerts gerechnet.
    """
    rows = db.execute(
        select(Grade, Grade.weight)
        .where(Grade.user_id == user_id)
        .order_by(Grade.graded_on, Grade.id)
    ).all()
    return [(grade, int(weight)) for grade, weight in rows]


def _trend(points: list[GradeTrendPointRead]) -> list[GradeTrendPointRead]:
    """Die letzten ``TREND_LIMIT`` Punkte - mehr liest niemand auf einer Karte."""
    return points[-TREND_LIMIT:]


def grade_summary(
    db: Session,
    user_id: int,
    subject_id: int | None = None,
    projected_weight: int = 0,
    now: datetime | None = None,
) -> GradeSummary:
    """Schnitt, Verlauf und Hochrechnung fuer ein Fach oder fuer alles.

    ``subject_id`` schraenkt **beides** ein: Schnitt und Verlauf. Ein Verlauf,
    der die Faecher mischt, ist eine Linie, auf der niemand etwas ablesen kann -
    ein Sprung zwischen 2,0 und 5,0 ist dann ein Fachwechsel und kein
    Leistungsverlauf.
    """
    if subject_id is not None:
        eintraege = _grades_for_subject(db, user_id, subject_id)
        average = weighted_average(eintraege)
        name = db.scalar(select(Subject.name).where(Subject.user_id == user_id, Subject.id == subject_id))
        overall = GradeAverageRead(
            subject_id=subject_id,
            subject_name=name or NO_SUBJECT_LABEL,
            average=average if average is not None else 0.0,
            count=len(eintraege),
            weight=sum(entry.weight for entry in eintraege),
            passed=bool(is_passing(average)),
        )
        by_subject = [overall]
        trend = _subject_trend(db, user_id, subject_id)
    else:
        eintraege = [WeightedGrade(value=float(grade.grade_value), weight=weight) for grade, weight in _all_grades(db, user_id)]
        average = weighted_average(eintraege)
        overall = GradeAverageRead(
            subject_id=None,
            subject_name=OVERALL_SUBJECT_LABEL,
            average=average if average is not None else 0.0,
            count=len(eintraege),
            weight=sum(entry.weight for entry in eintraege),
            passed=bool(is_passing(average)),
        )
        by_subject = _subject_averages(db, user_id)
        trend = _overall_trend(db, user_id)
    needed, best, moeglich = project_average(eintraege, projected_weight)

    return GradeSummary(
        passing_grade=PASSING_GRADE,
        generated_at=now or utc_now(),
        overall=overall,
        by_subject=by_subject,
        trend=_trend(trend),
        total_count=overall.count,
        projection=GradeProjectionRead(
            current_average=overall.average if overall.count else None,
            passing_grade=PASSING_GRADE,
            projected_weight=projected_weight,
            needed_average=needed,
            best_possible_average=best,
            still_possible=bool(moeglich),
        ),
    )


def _subject_averages(db: Session, user_id: int) -> list[GradeAverageRead]:
    """Schnitt je Fach, absteigend nach Schnitt - die schlechtesten zuerst."""
    rows = db.execute(
        select(
            Grade.subject_id,
            Subject.name,
            func.sum(Grade.grade_value * Grade.weight),
            func.sum(Grade.weight),
            func.count(Grade.id),
        )
        .select_from(Grade)
        .outerjoin(Subject, Subject.id == Grade.subject_id)
        .where(Grade.user_id == user_id)
        .group_by(Grade.subject_id, Subject.name)
    ).all()
    eintraege: list[GradeAverageRead] = []
    for fach_id, name, total, weight, anzahl in rows:
        gesamt_gewicht = int(weight or 0)
        if gesamt_gewicht <= 0:
            continue
        average = round(float(total or 0.0) / gesamt_gewicht, 2)
        eintraege.append(
            GradeAverageRead(
                subject_id=int(fach_id) if fach_id is not None else None,
                subject_name=str(name) if name else NO_SUBJECT_LABEL,
                average=average,
                count=int(anzahl or 0),
                weight=gesamt_gewicht,
                passed=is_passing(average) or False,
            )
        )
    return sorted(eintraege, key=lambda eintrag: (-eintrag.average, eintrag.subject_name))


def _subject_trend(db: Session, user_id: int, subject_id: int) -> list[GradeTrendPointRead]:
    rows = [
        (row[0], float(row[1]), int(row[2]))
        for row in db.execute(
            select(Grade.graded_on, Grade.grade_value, Grade.weight)
            .where(Grade.user_id == user_id, Grade.subject_id == subject_id)
            .order_by(Grade.graded_on, Grade.id)
        ).all()
    ]
    return _cumulative(rows)


def _overall_trend(db: Session, user_id: int) -> list[GradeTrendPointRead]:
    rows = [
        (row[0], float(row[1]), int(row[2]))
        for row in db.execute(
            select(Grade.graded_on, Grade.grade_value, Grade.weight)
            .where(Grade.user_id == user_id)
            .order_by(Grade.graded_on, Grade.id)
        ).all()
    ]
    return _cumulative(rows)


def _cumulative(rows: list[tuple[date, float, int]]) -> list[GradeTrendPointRead]:
    """Der Schnitt nach jedem Tag, aufsteigend.

    Ein Punkt je **Tag**, nicht je Note: zwei Noten an einem Tag sind fuer den
    Verlauf eine Information, und zwei Punkte an derselben Stelle wuerden die
    Linie nur zittern lassen.

    ``count`` und ``weight`` sind **kumuliert**. Die Linie zeigt einen
    Durchschnitt, und ein Durchschnitt ohne die Zahl, auf wie vielen Noten er
    beruht, ist eine Behauptung: dieselbe Linie sieht bei einer Note und bei
    zwanzig gleich aus.
    """
    tage: dict[date, tuple[float, int, int]] = {}
    for graded_on, value, weight in rows:
        total, gesamt_gewicht, anzahl = tage.get(graded_on, (0.0, 0, 0))
        tage[graded_on] = (total + float(value) * weight, gesamt_gewicht + weight, anzahl + 1)
    punkte: list[GradeTrendPointRead] = []
    laufende_summe = 0.0
    laufendes_gewicht = 0
    laufende_anzahl = 0
    for graded_on in sorted(tage):
        total, gesamt_gewicht, anzahl = tage[graded_on]
        laufende_summe += total
        laufendes_gewicht += gesamt_gewicht
        laufende_anzahl += anzahl
        punkte.append(
            GradeTrendPointRead(
                graded_on=graded_on,
                average=round(laufende_summe / laufendes_gewicht, 2),
                count=laufende_anzahl,
                weight=laufendes_gewicht,
            )
        )
    return punkte


def _zahl(wert: float) -> str:
    """Eine Zahl mit deutschem Komma - wie sie in der Oberflaeche steht.

    Der Prompt geht an ein deutschsprachiges Modell, das die Noten sonst als
    amerikanische Dezimalzahl liest: aus 4.4 wuerde im Satz "4.4" stehen, und
    das ist in einer Notenliste eine andere Zahl als 4,4.
    """
    return f"{wert:.1f}".replace(".", ",")


def grade_context_lines(db: Session, user_id: int) -> list[str]:
    """Die Notenzeilen fuer einen KI-Prompt - **leer ohne Freigabe**.

    Leer heisst nicht "nichts gefunden", sondern "darf nicht": der Aufrufer
    uebergibt das Ergebnis unveraendert an den Prompt, und ein leerer Block wird
    gar nicht erst angehaengt. So gibt es keinen Pfad, auf dem Noten in einen
    Provider gelangen, ohne dass vorher ``grades_sharing_enabled`` True gesagt
    hat.
    """
    if not grades_sharing_enabled(db, user_id):
        return []
    zusammenfassung = grade_summary(db, user_id)
    if zusammenfassung.total_count == 0:
        return []
    zeilen = [
        f"Noten gesamt: {_zahl(zusammenfassung.overall.average)} "
        f"({'bestanden' if zusammenfassung.overall.passed else 'nicht bestanden'}, "
        f"{zusammenfassung.total_count} {'Note' if zusammenfassung.total_count == 1 else 'Noten'})"
    ]
    for eintrag in zusammenfassung.by_subject[:5]:
        zeilen.append(
            f"{eintrag.subject_name}: Schnitt {_zahl(eintrag.average)} aus {eintrag.count} "
            f"{'Note' if eintrag.count == 1 else 'Noten'}"
        )
    return zeilen


# --- Die Freigabe ------------------------------------------------------------


def grades_sharing_enabled(db: Session, user_id: int) -> bool:
    """Duerfen Noten den KI-Dienst erreichen? Standard und Fehlerfall: nein."""
    settings = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if settings is None:
        return False
    einstellungen: Any = settings.preferences
    if not isinstance(einstellungen, dict):
        return False
    wert = einstellungen.get(SHARING_PREFERENCE_KEY)
    if isinstance(wert, bool):
        return wert
    return False


def set_grades_sharing(db: Session, user_id: int, enabled: bool) -> bool:
    """Die Freigabe setzen und die gespeicherte Entscheidung zurueckgeben.

    Liest und schreibt ``preferences`` als Ganzes, weil die anderen Schluessel
    darin stehen bleiben: das ist ein fremdes Verzeichnis, und eine Noteneinstellung
    hat dort nichts zu suchen. Fehlt die ``settings``-Zeile, wird sie angelegt -
    ein Testkonto hat sie nicht.
    """
    settings = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if settings is None:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
    einstellungen: dict[str, Any] = dict(settings.preferences) if isinstance(settings.preferences, dict) else {}
    einstellungen[SHARING_PREFERENCE_KEY] = enabled
    settings.preferences = einstellungen
    db.flush()
    return enabled


# --- Aus einem Dokument uebernehmen -----------------------------------------


def _normalize_line(line: str) -> str:
    return _WHITESPACE.sub(" ", line).strip()


def candidate_key(line: str) -> str:
    """Der Schluessel einer Zeile - stabil, ohne Zeilennummer.

    Bewusst der **Inhalt** und nicht die Zeilennummer oder die Dokument-Id: wird
    dieselbe Liste ein zweites Mal hochgeladen, ist es ein neues Dokument mit
    neuen Zeilennummern, und an der Zeilennummer haengende Schluessel wuerden
    jede Note verdoppeln. Mit dem Inhalt ist der zweite Import ein No-Op -
    dafuer gibt es die eindeutige Kombination aus Nutzer, Quelle und Schluessel.

    Zwei woertlich gleiche Zeilen in einer Liste ergeben denselben Schluessel;
    das ist in einem Notenspiegel ein Duplikat und keine zweite Leistung.
    """
    return hashlib.sha256(_normalize_line(line).encode("utf-8")).hexdigest()[:16]


def _find_date(line: str) -> tuple[date | None, str]:
    """Ein Datum in der Zeile und die Zeile ohne dieses Datum."""
    treffer = _DT_DATE.search(line) or _ISO_DATE.search(line)
    if treffer is None:
        return None, line
    tag, monat, jahr = (int(teil) for teil in treffer.groups())
    if jahr < 100:
        jahr += 2000 if jahr < 70 else 1900
    try:
        datum = date(jahr, monat, tag)
    except ValueError:
        # "31.02.2026" ist keine Note mit kaputtem Datum, sondern Muell.
        return None, line
    return datum, _DT_DATE.sub(" ", _ISO_DATE.sub(" ", line))


def _find_value(line: str) -> tuple[float | None, str, str]:
    """Die Notenzahl in der Zeile, davor der Text, danach der Rest."""
    for treffer in _GRADE_NUMBER.finditer(line):
        zahl = treffer.group(1)
        nachkomma = treffer.group(2)
        wert = float(f"{zahl}.{nachkomma}") if nachkomma is not None else float(zahl)
        if not BEST_GRADE <= wert <= 6.0:
            continue
        return wert, line[: treffer.start()], line[treffer.end() :]
    return None, line, ""


def _find_kind(text: str, default_kind: GradeKind) -> tuple[GradeKind, str]:
    """Die Art aus der Zeile, sonst die Vorgabe - und die Zeile ohne das Stueck."""
    klein = text.lower()
    for wort, art in _KIND_WORDS:
        position = klein.find(wort)
        if position >= 0:
            return cast(GradeKind, art), text[:position] + " " + text[position + len(wort) :]
    return default_kind, text


def _find_comment(rest: str) -> str | None:
    """Was von der Zeile uebrig bleibt, ist der Kommentar - oder nichts."""
    text = _normalize_line(rest)
    for zeichen in _TRIM_CHARS:
        text = text.replace(zeichen, " ")
    text = _normalize_line(text)
    if len(text) < _MIN_COMMENT:
        return None
    return text[:500]


def _match_subject(text: str, faecher: list[Subject]) -> Subject | None:
    """Das Fach, dessen Name in der Zeile steht. Der laengste Treffer gewinnt.

    Das laengste zuerst, weil "Deutsch" sonst "Deutsch Geschichte" verschlucken
    wuerde, sobald jemand ein Fach mit aehnlichem Anfang anlegt.
    """
    klein = text.lower()
    treffer = [fach for fach in faecher if fach.name and fach.name.lower() in klein]
    treffer += [fach for fach in faecher if fach.short_name and fach.short_name.lower() in klein]
    if not treffer:
        return None
    return max(treffer, key=lambda fach: len(fach.name or ""))


def document_text(document: Document) -> str:
    """Der Klartext der Datei, **nicht** der indexierte Absatz.

    Bewusst von der Datei und nicht von ``DocumentChunk``: der Chunker zerlegt
    nach Woertern und setzt den Text dabei mit Leerzeichen zusammen - die
    Zeilenumbruche eines Notenspiegels sind danach weg. Ein Notenspiegel wird
    aber Zeile fuer Zeile gelesen, und eine Zeile ist genau das, was der
    Chunker nicht liefert.

    Die Absaetze bleiben fuer die Suche das Richtige; fuer eine zeilenweise
    Auswertung nicht.
    """
    return read_document_text(Path(document.storage_path))


def parse_grade_document(
    db: Session,
    user_id: int,
    document_id: int,
    fallback_subject_id: int | None,
    default_kind: GradeKind = "written_exam",
    default_weight: int = 1,
    default_date: date | None = None,
) -> list[GradeCandidate]:
    """Alle Noten finden, die in einem abgegebenen Dokument stehen.

    **Ohne Modell.** Das ist Absicht: eine Zeile, aus der eine Zahl wird, ist
    eine Sache fuer eine Regex, und ein Modell wuerde hier nur teurer und
    unzuverfaelliger. Was nicht erkannt wird, wird nicht geraten - die Zeile
    fehlt in der Vorschau, statt als Note mit erfundenem Fach zu landen.

    Drei Regeln, mehr nicht:

    1. **Der Wert** ist die erste Zahl der Zeile, die zwischen 1,0 und 6,0 liegt
       und nicht Teil einer groesseren Zahl ist. Alles andere - Jahreszahlen,
       Datumsfragmente, Klassennummern - faellt durch diese Regel heraus.
    2. **Das Fach** ist das, dessen Name in der Zeile steht. Steht keiner drin,
       greift das Fach, das der Dialog als Vorgabe mitgegeben hat, und der Fund
       wird als "nicht erkannt" markiert, damit die Oberflaeche das zeigen kann.
    3. **Das Datum** kommt aus der Zeile, sonst aus der Vorgabe. Eine Note ohne
       Datum waere nicht im Verlauf und damit halb erfasst.

    Die Zeile bleibt der Schluessel, nicht die Position: siehe ``candidate_key``.
    """
    dokument = db.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    if dokument is None:
        raise GradeImportNotFound("Dokument nicht gefunden")
    if dokument.status not in {"indexed", "pending", "processed"}:
        raise GradeImportError(
            "Dieses Dokument ist noch nicht lesbar. Lade es als Textdatei hoch, damit es indexiert wird."
        )
    try:
        text = document_text(dokument)
    except (OSError, UnicodeError, ValueError) as exc:
        # Kein Textformat, oder die Datei liegt nicht mehr auf dem Server. Beides
        # ist ein Problem mit **diesem** Dokument und kein Fehler der Lernenden.
        raise GradeImportError(f"Die Datei konnte nicht gelesen werden: {exc}") from exc

    faecher = list(db.scalars(select(Subject).where(Subject.user_id == user_id).order_by(Subject.id)).all())
    vortrag = default_date or utc_now().date()
    kandidaten: list[GradeCandidate] = []
    for nummer, roh in enumerate(text.splitlines(), start=1):
        zeile = _normalize_line(roh)
        if not zeile:
            continue
        datum, ohne_datum = _find_date(zeile)
        wert, davor, danach = _find_value(ohne_datum)
        if wert is None:
            continue
        art, _ohne_art = _find_kind(zeile, default_kind)
        fach = _match_subject(f"{davor} {zeile}", faecher)
        erkannt = fach is not None
        kandidaten.append(
            GradeCandidate(
                key=candidate_key(zeile),
                line_number=nummer,
                line=zeile,
                value=wert,
                kind=art,
                graded_on=datum or vortrag,
                weight=default_weight,
                comment=_find_comment(danach),
                subject_id=fach.id if fach is not None else fallback_subject_id,
                subject_name=fach.name if fach is not None else None,
                subject_recognized=erkannt,
            )
        )
    return kandidaten


def _imported_keys(db: Session, user_id: int, keys: list[str]) -> set[str]:
    if not keys:
        return set()
    vorhanden = db.execute(
        select(Grade.external_id).where(
            Grade.user_id == user_id,
            Grade.source == "document",
            Grade.external_id.in_(keys),
        )
    ).all()
    return {str(external_id) for (external_id,) in vorhanden if external_id is not None}


def preview_grade_import(
    db: Session,
    user_id: int,
    document_id: int,
    fallback_subject_id: int | None,
    default_kind: GradeKind = "written_exam",
    default_weight: int = 1,
    default_date: date | None = None,
) -> GradeImportPreview:
    """Die Fundstellen des Dokuments, mit dem Vermerk, ob sie schon importiert sind.

    Die Vorschau ist der Grund, warum es zwei Routen gibt: eine Notenliste zu
    uebernehmen ist nicht reversible, wenn sie falsch gelesen wurde. Hier sieht
    die Lernende jede Zeile, klickt die zweifelhaften ab, und **erst dann** legt
    der Server etwas an - und zwar nur die Schluessel, die bestaetigt wurden.
    """
    kandidaten = parse_grade_document(
        db,
        user_id,
        document_id,
        fallback_subject_id,
        default_kind=default_kind,
        default_weight=default_weight,
        default_date=default_date,
    )
    dokument = db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user_id))
    uebernommen = _imported_keys(db, user_id, [kandidat.key for kandidat in kandidaten])
    return GradeImportPreview(
        document_id=document_id,
        filename=dokument.filename if dokument is not None else "",
        subject_id=fallback_subject_id,
        default_kind=default_kind,
        default_weight=default_weight,
        default_date=default_date,
        entries=[
            GradeImportCandidateRead(
                key=kandidat.key,
                line_number=kandidat.line_number,
                line=kandidat.line,
                value=kandidat.value,
                kind=kandidat.kind,
                graded_on=kandidat.graded_on,
                weight=kandidat.weight,
                comment=kandidat.comment,
                subject_id=kandidat.subject_id,
                subject_name=kandidat.subject_name,
                subject_recognized=kandidat.subject_recognized,
                already_imported=kandidat.key in uebernommen,
            )
            for kandidat in kandidaten
        ],
    )


def import_grades(
    db: Session,
    user_id: int,
    document_id: int,
    keys: list[str],
    fallback_subject_id: int | None,
    default_kind: GradeKind = "written_exam",
    default_weight: int = 1,
    default_date: date | None = None,
) -> GradeImportResult:
    """Die bestaetigten Zeilen als Noten anlegen.

    Der Server sucht die Zeilen **selbst** und uebernimmt nur die Schluessel aus
    dem Request. Werte, Art, Datum und Fach kommen aus dem Dokument: eine
    Oberflaeche, die hier Zahlen schicken koennte, koennte Noten erfinden, die
    in keiner Zeile stehen.

    Schluessel, die schon importiert sind, zaehlen als uebersprungen - deshalb
    ist ein zweiter Klick auf "uebernehmen" ein No-Op und kein Duplikat.
    """
    if not keys:
        raise GradeImportError("Es wurde keine Zeile bestaetigt")
    kandidaten = parse_grade_document(
        db,
        user_id,
        document_id,
        fallback_subject_id,
        default_kind=default_kind,
        default_weight=default_weight,
        default_date=default_date,
    )
    gewaehlt = [kandidat for kandidat in kandidaten if kandidat.key in set(keys)]
    if not gewaehlt:
        raise GradeImportError("Keine der bestaetigten Zeilen steht in diesem Dokument")

    uebernommen = _imported_keys(db, user_id, [kandidat.key for kandidat in gewaehlt])
    angelegt: list[Grade] = []
    uebersprungen = 0
    for kandidat in gewaehlt:
        if kandidat.key in uebernommen:
            uebersprungen += 1
            continue
        note = Grade(
            user_id=user_id,
            subject_id=kandidat.subject_id,
            document_id=document_id,
            grade_value=kandidat.value,
            kind=kandidat.kind,
            weight=kandidat.weight,
            graded_on=kandidat.graded_on,
            comment=kandidat.comment,
            source="document",
            external_id=kandidat.key,
        )
        db.add(note)
        angelegt.append(note)
    db.flush()
    logger.info("Noten aus Dokument %s uebernommen: %s neu, %s uebersprungen", document_id, len(angelegt), uebersprungen)
    return GradeImportResult(
        document_id=document_id,
        created=len(angelegt),
        skipped=uebersprungen,
        created_ids=[note.id for note in angelegt],
    )


def invalidate_today_greeting(db: Session, user_id: int, zone_today: date) -> None:
    """Den heutigen Willkommenssatz loeschen.

    Wird aufgerufen, wenn die Freigabe zurueckgenommen wird. Der Satz ist ein
    Cache aus Modellausgabe und kann eine Note genannt haben - nach dem Widerruf
    darf er nicht weiter stehen. Beim naechsten Abruf entsteht er neu, es geht
    also nichts verloren, was nicht aus einer Note besteht.

    Der Import von ``GreetingCache`` steht oben bei den uebrigen Modellen und
    nicht hier: es gibt keinen Zirkel, und ein Import in einer Hilfsfunktion ist
    eine Lesebarkeitsfrage, keine Notwendigkeit.
    """
    geloescht = db.query(GreetingCache).filter(
        GreetingCache.user_id == user_id,
        GreetingCache.generated_on == zone_today,
    ).delete(synchronize_session=False)
    if geloescht:
        logger.info("Heutiger Willkommenssatz geloescht, weil die Notenfreigabe zurueckgenommen wurde")
