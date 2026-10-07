"""Der Assistent als Agent: Befehle, die er selbst ausfuehrt, statt nur vorzuschlagen.

Zwei Wege fuehren zu einer erledigten Sache:

* **Feste Befehle ohne Modell** (``run_command``): "Plan meine Woche" ruft den
  Wochenplaner auf und traegt ein, "Mathe-Hausaufgabe erledigt" hakt die
  passende Hausaufgabe ab. Beides erkennt eine Regel, nicht das Modell - kleine
  lokale Modelle verrechnen sich schon bei "naechsten Donnerstag", und eine
  Woche mit Fristen und Budget waere Wuerfeln. Die Antwort ist sofort da.
* **Selbst eintragen** (``auto_apply`` im Router): die ``AKTION``-Vorschlaege
  des Modells werden direkt eingetragen, statt auf "Eintragen" zu warten. Das
  Datum hat der Server dann schon aus dem Satz korrigiert (``date_from_text``).
  Jeder Eintrag laesst sich mit ``undo`` zuruecknehmen - das ist der Preis
  dafuer, dass niemand mehr vorher auf einen Knopf drueckt.

Erkennt keine Regel den Satz, geht er wie bisher an das Modell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.time import as_utc, utc_now
from app.db.academic import CalendarEvent, Exam, Homework
from app.schemas.planner import PlannerRequest
from app.services import planner as planner_service

#: Was ein Agent-Schritt war. ``exam``/``homework``/``event`` sind angelegte
#: Eintraege (Rueckgaengig = loeschen), ``homework_done`` eine abgehakte
#: Hausaufgabe (Rueckgaengig = wieder offen), ``week_plan`` ein eingetragener
#: Wochenplan (kein Rueckgaengig - die Bloecke stehen einzeln unter /lernen).
DONE_EXAM: Final = "exam"
DONE_HOMEWORK: Final = "homework"
DONE_EVENT: Final = "event"
DONE_HOMEWORK_COMPLETED: Final = "homework_done"
DONE_WEEK_PLAN: Final = "week_plan"
UNDOABLE: Final[frozenset[str]] = frozenset({DONE_EXAM, DONE_HOMEWORK, DONE_EVENT, DONE_HOMEWORK_COMPLETED})

#: So viele Bloecke nennt die Antwort auf "Plan meine Woche" einzeln.
PLAN_BLOCKS_IN_REPLY: Final = 6

_WEEKDAYS: Final = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

_PLAN_RE: Final = re.compile(
    r"\b(plan\w*|organisier\w*|verteil\w*)\b.*\b(woche|lernzeit|tage)\b"
    r"|\b(woche|lernzeit)\b.*\b(planen|organisieren|verteilen)\b"
    r"|\bwochenplan\b|\blernplan\b.*\b(mach\w*|erstell\w*|bau\w*)\b|\b(mach\w*|erstell\w*)\b.*\blernplan\b",
    re.IGNORECASE,
)
_DONE_RE: Final = re.compile(
    r"\b(erledigt|fertig|gemacht|abgegeben|geschafft|abhaken)\b|\bhak\w*\b.*\bab\b",
    re.IGNORECASE,
)
_QUESTION_RE: Final = re.compile(
    r"\?\s*$|^\s*(was|wie|wann|welche\w*|wo|warum|ist|sind|hab ich|habe ich)\b", re.IGNORECASE
)
_WORD_RE: Final = re.compile(r"[a-zäöüß]{3,}", re.IGNORECASE)
_FILLER: Final = frozenset(
    {
        "die", "der", "das", "den", "dem", "und", "ich", "habe", "hab", "hausaufgabe", "hausaufgaben", "aufgabe",
        "erledigt", "fertig", "gemacht", "abgegeben", "geschafft", "abhaken", "hake", "hak", "bitte", "schon",
        "jetzt", "für", "fuer", "von", "mit", "ist", "sind", "meine", "mein", "auch", "heute", "gestern",
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class AgentStep:
    """Etwas, das der Assistent selbst getan hat."""

    kind: str
    id: int
    title: str
    message: str

    @property
    def undoable(self) -> bool:
        return self.kind in UNDOABLE


@dataclass(frozen=True, slots=True)
class CommandResult:
    reply: str
    steps: list[AgentStep]


class AgentUndoError(ValueError):
    """Ein Schritt, der sich nicht zuruecknehmen laesst - mit deutschem Grund."""


# --- Erkennen ----------------------------------------------------------------


def wants_week_plan(text: str) -> bool:
    """"Plan meine Woche", "Mach mir einen Lernplan", "Verteil meine Lernzeit" - keine Frage."""
    return bool(_PLAN_RE.search(text)) and not text.strip().endswith("?")


def wants_homework_done(text: str) -> bool:
    """"Mathe-Hausaufgabe erledigt", "Hak Bio ab" - keine Frage wie "Was ist noch nicht erledigt?"."""
    return bool(_DONE_RE.search(text)) and not _QUESTION_RE.search(text)


def _words(text: str) -> set[str]:
    return {word.lower() for word in _WORD_RE.findall(text)} - _FILLER


def match_homework(text: str, items: list[Homework]) -> list[Homework]:
    """Die offenen Hausaufgaben, auf die der Satz am besten passt (Titel und Fach).

    Mehrere gleich gute Treffer kommen alle zurueck - der Aufrufer fragt dann
    nach, statt die falsche abzuhaken. Ohne ein einziges passendes Wort gibt es
    nur dann einen Treffer, wenn genau eine Hausaufgabe offen ist.
    """
    wanted = _words(text)
    lowered = text.lower()
    scored: list[tuple[int, Homework]] = []
    for item in items:
        names = _words(item.title)
        if item.subject is not None:
            names |= _words(item.subject.name)
        score = sum(1 for word in wanted if any(name.startswith(word) or word.startswith(name) for name in names))
        # Ein Kuerzel wie "M" oder "D" ist zu kurz fuer die Wortsuche, zaehlt aber als ganzes Wort.
        short = item.subject.short_name if item.subject is not None else None
        if short and re.search(rf"\b{re.escape(short.lower())}\b", lowered):
            score += 1
        scored.append((score, item))
    best = max((score for score, _ in scored), default=0)
    if best == 0:
        return items if len(items) == 1 else []
    return [item for score, item in scored if score == best]


# --- Ausfuehren --------------------------------------------------------------


def _fmt_block(start: datetime, zone: ZoneInfo) -> str:
    local = as_utc(start).astimezone(zone)
    return f"{_WEEKDAYS[local.weekday()]} {local:%H:%M}"


def plan_week(db: Session, user_id: int, zone: ZoneInfo, *, now: datetime | None = None) -> CommandResult:
    """Plant die naechsten sieben Tage mit den Voreinstellungen und traegt sie ein."""
    result = planner_service.build_plan(db, user_id, zone, PlannerRequest(), apply=True, now=now)
    db.commit()
    blocks = sorted((block for day in result.days for block in day.blocks), key=lambda block: block.start)
    unplaced = ", ".join(item.title for item in result.unplaced[:3])
    if not blocks:
        if unplaced:
            return CommandResult(reply=f"Ich habe keinen freien Platz gefunden für: {unplaced}.", steps=[])
        return CommandResult(
            reply="Für die nächsten sieben Tage gibt es nichts zu planen – keine offenen Aufgaben, "
            "Hausaufgaben oder Klausuren.",
            steps=[],
        )
    minutes = sum(block.duration_minutes for block in blocks)
    hours, rest = divmod(minutes, 60)
    dauer = " ".join(part for part in (f"{hours} h" if hours else "", f"{rest} min" if rest else "") if part)
    lines = [f"Erledigt: {len(blocks)} Lernblöcke in den nächsten sieben Tagen eingetragen ({dauer})."]
    for block in blocks[:PLAN_BLOCKS_IN_REPLY]:
        fach = f" ({block.subject_name})" if block.subject_name else ""
        spaet = " – nach der Frist" if block.late else ""
        lines.append(f"• {_fmt_block(block.start, zone)}: {block.title}{fach}{spaet}")
    if len(blocks) > PLAN_BLOCKS_IN_REPLY:
        lines.append(f"… und {len(blocks) - PLAN_BLOCKS_IN_REPLY} weitere unter „Lernplan“.")
    if unplaced:
        lines.append(f"Kein Platz für: {unplaced}.")
    step = AgentStep(
        kind=DONE_WEEK_PLAN,
        id=result.study_plan_id or 0,
        title="Wochenplan",
        message=f"{len(blocks)} Lernblöcke eingetragen",
    )
    return CommandResult(reply="\n".join(lines), steps=[step])


def complete_homework(db: Session, user_id: int, text: str) -> CommandResult | None:
    """Hakt die gemeinte Hausaufgabe ab. ``None``, wenn keine offen ist - dann antwortet das Modell."""
    items = list(
        db.scalars(
            select(Homework)
            .options(joinedload(Homework.subject))
            .where(Homework.user_id == user_id, Homework.completed.is_(False))
            .order_by(Homework.due_date)
        ).all()
    )
    if not items:
        return None
    matches = match_homework(text, items)
    if len(matches) != 1:
        namen = ", ".join(f"„{item.title}“" for item in (matches or items)[:4])
        return CommandResult(reply=f"Welche meinst du? Offen sind: {namen}.", steps=[])
    item = matches[0]
    item.completed = True
    item.completed_at = utc_now()
    db.commit()
    return CommandResult(
        reply=f"Abgehakt: „{item.title}“.",
        steps=[AgentStep(kind=DONE_HOMEWORK_COMPLETED, id=item.id, title=item.title, message="Hausaufgabe erledigt")],
    )


def run_command(
    db: Session, user_id: int, zone: ZoneInfo, text: str, *, now: datetime | None = None
) -> CommandResult | None:
    """Fuehrt einen festen Befehl aus, wenn der Satz einer ist. Sonst ``None``."""
    if wants_week_plan(text):
        return plan_week(db, user_id, zone, now=now)
    if wants_homework_done(text):
        return complete_homework(db, user_id, text)
    return None


# --- Rueckgaengig ------------------------------------------------------------


def undo(db: Session, user_id: int, kind: str, item_id: int) -> str:
    """Nimmt einen Schritt zurueck. Ein fremder Eintrag ist ``LookupError`` (404)."""
    if kind not in UNDOABLE:
        raise AgentUndoError("Das lässt sich hier nicht rückgängig machen.")
    row: Exam | Homework | CalendarEvent | None
    if kind == DONE_EXAM:
        row = db.get(Exam, item_id)
    elif kind in (DONE_HOMEWORK, DONE_HOMEWORK_COMPLETED):
        row = db.get(Homework, item_id)
    else:
        row = db.get(CalendarEvent, item_id)
    if row is None or row.user_id != user_id:
        raise LookupError("Eintrag nicht gefunden")
    if kind == DONE_HOMEWORK_COMPLETED:
        assert isinstance(row, Homework)
        row.completed = False
        row.completed_at = None
        db.commit()
        return "Wieder offen."
    db.delete(row)
    db.commit()
    return "Zurückgenommen."
