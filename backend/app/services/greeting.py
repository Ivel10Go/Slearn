"""Der Willkommenssatz fuer das Dashboard.

Der Satz entsteht aus zwei Wegen: einmal fragen wir die eingetragenen KI-Wege in
der Reihenfolge aus ``app.services.ai_routing`` (lokal vor Online), und wenn keiner
davon antwortet, bauen wir denselben Satz deterministisch aus den Daten. Der zweite
Weg ist kein Notnagel, sondern der Normalfall ohne Provider: Das Dashboard darf an
einem fehlenden oder verweigerenden Modell nie hängen bleiben, und die Daten liegen
ohnehin vor.

``source`` sagt, welcher der beiden Wege geliefert hat: ``local`` für einen Server auf
diesem Rechner, ``provider`` für einen Online-Dienst und ``fallback`` für den Satz aus
den Daten. Die Oberfläche zeigt das als Badge, damit niemand einen Satz für eine
KI-Aussage hält, den kein Modell geschrieben hat.

Der fertige Satz wird pro Nutzer und Tag gehalten. Ein Modellaufruf bei jedem
Öffnen der Seite wäre langsam und teuer, ohne zusätzlichen Nutzen - die Daten
ändern sich innerhalb eines Tages selten genug.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import as_utc, resolve_timezone_name, utc_now, zone_or_utc
from app.schemas.dashboard import DashboardResponse
from app.services import ai_routing

logger = logging.getLogger("school_assistant")

_MAX_SENTENCE_LENGTH: Final = 220
# Zeichen, die ein Modell als Auszeichnung mitliefert und die im Dashboard
# nichts zu suchen haben. Punkt und Komma sind bewusst nicht dabei.
_MARKUP: Final = " \t*#`>•-–—"

_SYSTEM_PROMPT: Final = (
    "Du bist ein Lernraum-Assistent für Schülerinnen und Schüler. "
    "Du schreibst EINE freundliche, deutschsprachige Begrüßung für das Dashboard. "
    "Regeln: höchstens 25 Wörter, ein bis zwei Sätze, kein Emoji, keine Aufzählung, "
    "kein Markdown, keine Anführungszeichen. "
    "Sprich die Schülerin oder den Schüler direkt an. "
    "Nenne nur das, was in den Daten steht, und erfinde nichts. "
    "Nenne Noten nur, wenn ausdrücklich welche mitgeliefert wurden. "
    "Beginne mit einem Begrüßungssatz wie 'Herzlich willkommen'."
)


@dataclass(frozen=True, slots=True)
class GreetingOutcome:
    text: str
    source: str
    model: str | None = None


def greeting_cache_is_fresh(stored_on: date, today: date) -> bool:
    return stored_on == today


def build_fallback_greeting(data: DashboardResponse, now: datetime, zone: ZoneInfo) -> str:
    """Baut den Satz aus den Daten, ohne ein Modell zu befragen."""
    stunden = data.summary.total_lessons
    naechste = _next_lesson(data, now, zone)
    termine = [ereignis for ereignis in data.calendar_events if not ereignis.all_day]
    hausaufgaben = data.summary.open_homework
    lernen = [aufgabe for aufgabe in data.study_tasks if aufgabe.status != "done"]

    if naechste is not None:
        satz = f"Herzlich willkommen. Um {naechste['uhrzeit']} Uhr beginnt {naechste['fach']}."
    elif stunden:
        satz = "Herzlich willkommen. Für heute sind keine Stunden mehr eingetragen."
    elif termine:
        satz = "Herzlich willkommen. Heute warten nur Termine auf dich."
    else:
        satz = "Herzlich willkommen. Für heute ist nichts eingetragen."

    offen: list[str] = []
    if hausaufgaben:
        offen.append(f"{hausaufgaben} {'Hausaufgabe' if hausaufgaben == 1 else 'Hausaufgaben'}")
    if lernen:
        offen.append(f"{len(lernen)} {'Lernaufgabe' if len(lernen) == 1 else 'Lernaufgaben'}")
    if offen:
        satz = f"{satz} Offen sind noch {', '.join(offen)}."

    if termine:
        anzahl = len(termine)
        satz = f"{satz} Dazu {anzahl} {'Termin' if anzahl == 1 else 'Termine'} im Kalender."
    return _hard_limit(satz)


def _next_lesson(data: DashboardResponse, now: datetime, zone: ZoneInfo) -> dict[str, str] | None:
    """Die naechste noch nicht vorbei Stunde, mit Kuerzel, wo vorhanden.

    Die Uhrzeit wird bewusst in ``zone`` umgerechnet und nicht in UTC: Die
    Lektionen liegen als UTC-Zeitpunkte vor, und eine Schulstunde um 07:45
    Europe/Berlin darf nicht als 06:45 im Willkommenssatz stehen.
    """
    aktuell = as_utc(now)
    kandidaten = [lesson for lesson in data.lessons if as_utc(lesson.end_time) > aktuell]
    if not kandidaten:
        return None
    kandidaten.sort(key=lambda lesson: as_utc(lesson.start_time))
    naechste = kandidaten[0]
    return {
        "fach": naechste.subject_short_name or naechste.subject_name,
        "uhrzeit": as_utc(naechste.start_time).astimezone(zone).strftime("%H:%M"),
    }


def build_prompt(data: DashboardResponse, now: datetime, zone: ZoneInfo, grade_lines: Sequence[str] = ()) -> str:
    """Sammelt die Fakten, aus denen der Provider schreiben soll.

    ``grade_lines`` ist der einzige Weg, auf dem eine Note in diesen Prompt
    kommt. Die Liste ist leer, solange die Freigabe nicht gesetzt ist (siehe
    ``app.services.grades.grade_context_lines``), und eine leere Liste
    hinterlaesst hier keine Zeile - kein Platzhalter, kein "keine Noten".
    """
    zeilen = [
        f"Heute: {now.date().isoformat()}",
        f"Stunden heute: {data.summary.total_lessons}",
        f"Davon erledigt: {data.summary.completed_lessons}",
    ]
    naechste = _next_lesson(data, now, zone)
    if naechste:
        zeilen.append(f"Nächste Stunde: {naechste['fach']} um {naechste['uhrzeit']} Uhr")

    termine = [ereignis for ereignis in data.calendar_events if not ereignis.all_day]
    if termine:
        titel = ", ".join(ereignis.title for ereignis in termine[:3])
        zeilen.append(f"Termine heute: {', '.join(titel)}")
    if data.summary.open_homework:
        zeilen.append(f"Offene Hausaufgaben: {data.summary.open_homework}")

    lernen = [aufgabe for aufgabe in data.study_tasks if aufgabe.status != "done"]
    if lernen:
        zeilen.append(f"Offene Lernaufgaben: {len(lernen)}")

    if data.upcoming_exams:
        naechste_klausur = data.upcoming_exams[0]
        zeilen.append(f"Nächste Klausur: {naechste_klausur.subject_name} - {naechste_klausur.title}")
    if data.summary.day_progress_percent:
        zeilen.append(f"Tagesfortschritt: {data.summary.day_progress_percent} Prozent")
    # Die Noten stehen am Ende, damit der Satz sie als Hintergrund sieht und
    # nicht als Tagesauftrag. Erwaehnt werden darf sie nur, wenn die Frage
    # lautet - sonst schreibt das Modell "dein Schnitt ist 4,2" in eine
    # Begruessung, die davon handeln sollte, dass heute etwas ansteht.
    zeilen.extend(grade_lines)
    return "\n".join(zeilen)


def _hard_limit(text: str) -> str:
    """Schneidet überlange Antworten ab, damit die Kachel nicht zerschießt."""
    if len(text) <= _MAX_SENTENCE_LENGTH:
        return text
    return text[: _MAX_SENTENCE_LENGTH - 1].rstrip() + "…"


def _sanitize(text: str) -> str:
    """Entfernt, was ein Modell trotz Anweisung mitliefern kann.

    Aufzählungszeichen und Betonungssterne werden an beiden Enden entfernt:
    Modelle schreiben gern ``- Mathe`` als Liste oder ``**Text**`` als
    Betonung. Die Punktuation im Satz bleibt unangetastet, sonst verlöre der
    Satz seinen Schlusspunkt.
    """
    zeilen = []
    for zeile in text.strip().splitlines():
        sauber = zeile.strip()
        while sauber and (sauber[0] in _MARKUP or sauber[-1] in _MARKUP):
            sauber = sauber.strip(_MARKUP).lstrip()
        if sauber:
            zeilen.append(sauber)
    return " ".join(zeilen)


def render_greeting(
    db: Session,
    user_id: int,
    settings: Settings,
    data: DashboardResponse,
    now: datetime | None = None,
    user_timezone: str | None = None,
    force: bool = False,
    grade_lines: Sequence[str] = (),
) -> GreetingOutcome:
    """Liefert den Satz und sagt, welcher Weg ihn geschrieben hat.

    Die Reihenfolge und die Zeitbudgets stehen in ``app.services.ai_routing``;
    hier steht nur der Satz. ``grade_lines`` wird durchgereicht und nicht selbst
    erzeugt: der Aufrufer entscheidet über die Freigabe, dieser Dienst über den
    Satz. Damit steht die Datenschutzfrage an genau einer Stelle
    (``grade_context_lines``) und nicht in einer Hilfsfunktion, die irgendwann
    einmal jemand aufruft.

    ``AiProviderError`` wird hier **nicht mehr gefangen**, weil es der Weg selbst
    tut und alle Wege der Reihe nach probiert - auch dann, wenn der Fehler gar
    nicht von einem Provider kommt. Was diese Funktion zurückgibt, ist damit
    immer ein fertiger Satz: genau wie vorher.
    """
    moment = as_utc(now or utc_now())
    zone = zone_or_utc(resolve_timezone_name(user_timezone or settings.timezone, settings.timezone))

    def aus_daten() -> str:
        return build_fallback_greeting(data, moment, zone)

    outcome = ai_routing.generate_with_routes(
        ai_routing.resolve_ai_routes(db, user_id, settings),
        _SYSTEM_PROMPT,
        build_prompt(data, moment, zone, grade_lines),
        max_tokens=120,
        local_timeout=settings.ai_local_timeout_seconds,
        local_fast_timeout=settings.ai_local_fast_timeout_seconds,
        provider_timeout=settings.ai_generate_timeout_seconds,
    )
    if outcome is None:
        return GreetingOutcome(text=aus_daten(), source=ai_routing.SOURCE_FALLBACK)

    sauber = _sanitize(outcome.text)
    if not sauber:
        return GreetingOutcome(text=aus_daten(), source=ai_routing.SOURCE_FALLBACK)
    return GreetingOutcome(text=_hard_limit(sauber), source=outcome.source, model=outcome.model)
