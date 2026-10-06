"""Der Lern-Assistent: Chat mit Zugriff auf Unterlagen, Plan und Gedaechtnis.

Der Assistent ist die erweiterte Dokumentenfrage. Was er zusaetzlich kann:

* **Ein Gespraech fuehren.** Der Verlauf kommt vom Client und wird hier nur in
  den Prompt gefaltet. Gespeichert wird er nicht - was ueber das Gespraech hinaus
  bleiben soll, ist das Gedaechtnis.
* **Den ganzen Schulstand sehen.** Faecher, Stundenplan heute, anstehende
  Pruefungen, offene Hausaufgaben, Termine, passende Dokumentauszuege und - nur
  mit Freigabe - Noten. Alles kompakt als Text, weil ein kleines lokales Modell
  mit einem langen Kontext schlechter wird, nicht besser.
* **An der Lernenden lernen.** Antwortet das Modell mit einer Zeile ``MERKE:``,
  wird der Satz in ``assistant_memories`` abgelegt und steht ab der naechsten
  Anfrage im Prompt. Sichtbar und loeschbar, siehe ``app/db/assistant.py``.
* **Eintraege vorschlagen.** Eine Zeile ``AKTION {json}`` wird zu einem
  *Vorschlag* (Pruefung, Hausaufgabe, Termin). Eingetragen wird erst, wenn die
  Lernende ihn bestaetigt (``apply_action``). Der Grund ist das Modell: ein
  kleines lokales Modell verrechnet sich bei "naechsten Donnerstag" oft genug,
  und ein falsch eingetragener Klausurtermin ist schlimmer als ein Klick.

**Warum ein Textprotokoll und kein Function-Calling.** ``tools`` im
OpenAI-Format beherrschen Ollama, LM Studio und llama-server nur fuer manche
Modelle, und ein Modell ohne Unterstuetzung ignoriert das Feld stillschweigend.
Zwei Zeilenmarken versteht jedes Modell, das Anweisungen folgt - und was nicht
parst, bleibt einfach Text.

Die Reihenfolge der KI-Wege kommt wie ueberall aus ``app.services.ai_routing``.
Mit ``integration_id`` laesst sich genau eine Verbindung waehlen; dann wird nur
sie versucht, damit die Antwort wirklich vom gewaehlten Modell stammt.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings
from app.core.time import as_utc, day_window, resolve_timezone_name, utc_now, zone_or_utc
from app.db.assistant import MEMORY_MAX_CHARS, MEMORY_SOURCE_ASSISTANT, AssistantMemory
from app.db.entities import (
    SYNC_STATE_DELETED,
    CalendarEvent,
    Document,
    DocumentChunk,
    Exam,
    Homework,
    Lesson,
    Subject,
    User,
)
from app.services import ai_routing
from app.services import training as training_service
from app.services.documents import DocumentSource, search_chunks
from app.services.local_runtimes import active_manager

logger = logging.getLogger("school_assistant")

ACTION_EXAM: Final = "exam"
ACTION_HOMEWORK: Final = "homework"
ACTION_EVENT: Final = "event"
ACTION_KINDS: Final[tuple[str, ...]] = (ACTION_EXAM, ACTION_HOMEWORK, ACTION_EVENT)

#: Die deutschen Woerter, die das Modell im Protokoll benutzt, auf die Arten.
_KIND_ALIASES: Final[dict[str, str]] = {
    "pruefung": ACTION_EXAM,
    "prüfung": ACTION_EXAM,
    "klausur": ACTION_EXAM,
    "test": ACTION_EXAM,
    "exam": ACTION_EXAM,
    "klassenarbeit": ACTION_EXAM,
    "schulaufgabe": ACTION_EXAM,
    "hausaufgabe": ACTION_HOMEWORK,
    "hausaufgaben": ACTION_HOMEWORK,
    "homework": ACTION_HOMEWORK,
    "termin": ACTION_EVENT,
    "event": ACTION_EVENT,
    "kalender": ACTION_EVENT,
}

#: Ohne Uhrzeit: Pruefung und Hausaufgabe zum Schulbeginn, nicht um Mitternacht -
#: eine Hausaufgabe "fuer Donnerstag" ist Donnerstagmorgen faellig.
DEFAULT_SCHOOL_TIME: Final = time(8, 0)
DEFAULT_EVENT_MINUTES: Final = 60

MAX_HISTORY_TURNS: Final = 12
MAX_TURN_CHARS: Final = 2000
MAX_MEMORIES_IN_PROMPT: Final = 40
MAX_MEMORIES_PER_REPLY: Final = 3
MAX_ACTIONS_PER_REPLY: Final = 5
DOCUMENT_HITS: Final = 4
#: So viele Dateinamen stehen im Prompt - der Assistent soll wissen, was es gibt.
DOCUMENT_LIST_LIMIT: Final = 40
#: Zeichenbudget fuer Auszuege. ``llama-server`` laeuft mit 8192 Tokens Kontext;
#: rund 12 000 Zeichen deutscher Text sind etwa 3500 Tokens und lassen Platz
#: fuer Schulstand, Verlauf und Antwort.
EXCERPT_CHAR_BUDGET: Final = 12_000
#: Woerter in Dateinamen, die nichts ueber den Inhalt sagen ("Scan_final.pdf").
_GENERIC_NAME_WORDS: Final = frozenset(
    {"scan", "final", "kopie", "copy", "datei", "dokument", "version", "neu", "seite", "page", "img", "image", "pdf"}
)
_NAME_WORD_RE = re.compile(r"[a-zäöüß]+")
PLAN_HORIZON_DAYS: Final = 21
_MAX_REPLY_TOKENS: Final = 700
#: Im Gespraechsmodus wird vorgelesen: kurze Antworten sind schneller erzeugt und angenehmer zu hoeren.
_MAX_VOICE_REPLY_TOKENS: Final = 260
VOICE_PROMPT: Final = (
    "\n\nDie Antwort wird laut vorgelesen, und die Person spricht mit dir. Antworte deshalb in hoechstens "
    "drei kurzen, natuerlichen Saetzen, ohne Listen, Ueberschriften, Sternchen oder Emojis. Stelle bei Bedarf "
    "eine kurze Rueckfrage."
)

# Nur in Grossbuchstaben: "Merke dir die Formel" ist ein normaler Satz und
# darf nicht als Notiz verschwinden. Qwen schreibt neben "MERKE: ..." auch
# 'MERKE ("...")' - beides wird erkannt.
_ACTION_RE = re.compile(r"^\s*[*`>-]*\s*AKTION\b\s*:?\s*(.*?)\s*`*\s*$")
_MEMORY_RE = re.compile(r"^\s*[*`>-]*\s*MERKE\b\s*[:(]?\s*[\"„“']?(.+?)[\"“”']?\s*\)?\s*`*\s*$")
#: Kleine Modelle dichten das Gespraech gern selbst weiter ("Person: ...").
_ROLE_LINE_RE = re.compile(r"^\s*(Person|Nutzer|Nutzerin|Schüler|Schülerin|User)\s*:", re.IGNORECASE)
_ASSISTANT_PREFIX_RE = re.compile(r"^\s*(Assistent|Assistant)\s*:\s*", re.IGNORECASE)
_INLINE_MEMORY_RE = re.compile(r"\s*\bMerke\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DE_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})?$")
_TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})")

_WEEKDAYS: Final = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

ROLE_USER: Final = "user"
ROLE_ASSISTANT: Final = "assistant"


class AssistantActionError(ValueError):
    """Ein Vorschlag, der sich nicht eintragen laesst - mit deutschem Grund."""


@dataclass(frozen=True, slots=True)
class ChatTurn:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """Ein Eintrag, den der Assistent vorschlaegt - noch nicht gespeichert.

    ``starts_at``/``ends_at`` sind bereits UTC. ``subject_name`` ist der Name
    aus der Antwort, auch wenn kein Fach dazu gefunden wurde: die Oberflaeche
    zeigt dann "Fach nicht gefunden", statt den Hinweis zu verschlucken.
    """

    kind: str
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    all_day: bool = False
    subject_id: int | None = None
    subject_name: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class AppliedAction:
    kind: str
    id: int
    title: str
    message: str


@dataclass(frozen=True, slots=True)
class AssistantRoute:
    """Eine waehlbare KI-Verbindung, fuer das Auswahlfeld der Oberflaeche.

    Entweder eine eingetragene Verbindung (``integration_id``) oder ein im
    Lernraum geladenes Modell (``local_model_id``), das eingebaut laeuft.
    """

    integration_id: int | None
    name: str
    model: str
    source: str
    local_model_id: str | None = None
    selected: bool = False


@dataclass(slots=True)
class AssistantReply:
    reply: str
    source: str
    model: str | None = None
    sources: list[DocumentSource] = field(default_factory=list)
    proposals: list[ActionProposal] = field(default_factory=list)
    memories_added: list[AssistantMemory] = field(default_factory=list)
    grades_shared: bool = False
    #: Nur mit Trainingsfreigabe: das abgelegte Beispiel, damit Eintragen,
    #: Verwerfen und Daumen spaeter darauf zeigen koennen.
    training_example_id: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedReply:
    text: str
    actions: list[dict[str, object]]
    memories: list[str]


# --- Protokoll ---------------------------------------------------------------


def is_action_line(line: str) -> bool:
    """Ob die Zeile Protokoll ist (``AKTION {...}``) - mit gueltigem JSON oder nicht."""
    match = _ACTION_RE.match(line)
    return bool(match and "{" in match.group(1))


def parse_reply(raw: str) -> ParsedReply:
    """Trennt den sichtbaren Text von den Zeilen ``AKTION`` und ``MERKE``.

    Eine ``AKTION``-Zeile, deren JSON nicht parst, bleibt **nicht** als Text
    stehen: sie ist Protokoll, und ein halber JSON-Brocken in der Antwort hilft
    niemandem. Gezaehlt wird sie im Log.
    """
    text_lines: list[str] = []
    actions: list[dict[str, object]] = []
    memories: list[str] = []
    for line in raw.splitlines():
        action_match = _ACTION_RE.match(line)
        # Ohne "{" ist es ein Satz, der zufaellig mit "Aktion" beginnt.
        if action_match and "{" in action_match.group(1):
            payload = action_match.group(1)
            try:
                value = json.loads(payload[payload.index("{") : payload.rindex("}") + 1])
            except (ValueError, json.JSONDecodeError):
                logger.warning("Assistent: AKTION ohne gueltiges JSON verworfen.")
                continue
            if isinstance(value, dict):
                actions.append(value)
            continue
        memory_match = _MEMORY_RE.match(line)
        if memory_match:
            memories.append(memory_match.group(1).strip())
            continue
        if _ROLE_LINE_RE.match(line):
            continue
        text_lines.append(_ASSISTANT_PREFIX_RE.sub("", line))
    text = "\n".join(text_lines).strip()
    # Leere Codezaune, die nach dem Herausnehmen einer AKTION uebrig bleiben.
    text = re.sub(r"```\w*\s*```", "", text).strip()
    return ParsedReply(text=text, actions=actions, memories=memories)


_WEEKDAY_INDEX: Final[dict[str, int]] = {
    "montag": 0,
    "dienstag": 1,
    "mittwoch": 2,
    "donnerstag": 3,
    "freitag": 4,
    "samstag": 5,
    "sonntag": 6,
}
_TEXT_DATE_RE = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.(\d{2,4})?")
_TEXT_WEEKDAY_RE = re.compile(
    r"\b(?:(übernächste[nmr]?|uebernaechste[nmr]?)|(nächste[nmr]?|naechste[nmr]?|kommende[nmr]?|diese[nmr]?|am))?\s*"
    r"(montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)\b",
    re.IGNORECASE,
)


def date_from_text(text: str, today: date) -> date | None:
    """Das Datum, das die Person **selbst** genannt hat - oder ``None``.

    Kleine Modelle verrechnen sich bei "uebermorgen" oder "naechsten Freitag"
    (Qwen 3B machte aus "uebermorgen" an einem Freitag den naechsten Freitag).
    Steht ein eindeutiges Datumswort in der Nachricht, gilt es vor der
    Angabe des Modells. "naechster Freitag" ist das erste Vorkommen nach heute,
    "uebernaechster" eine Woche spaeter - dieselbe Regel wie die Datumshilfe.
    """
    lowered = text.lower()
    if "übermorgen" in lowered or "uebermorgen" in lowered:
        return today + timedelta(days=2)
    if re.search(r"\bmorgen\b", lowered) and "morgens" not in lowered:
        return today + timedelta(days=1)
    if re.search(r"\bheute\b", lowered):
        return today
    match = _TEXT_DATE_RE.search(text)
    if match:
        return _parse_day(f"{match.group(1)}.{match.group(2)}.{match.group(3) or ''}", today)
    match = _TEXT_WEEKDAY_RE.search(text)
    if match:
        offset = (_WEEKDAY_INDEX[match.group(3).lower()] - today.weekday()) % 7 or 7
        return today + timedelta(days=offset + (7 if match.group(1) else 0))
    return None


_TEXT_EXAM_RE = re.compile(
    r"klassenarbeit|klausur|schulaufgabe|pr[uü]fung|lernkontrolle|vokabeltest|(?<![a-zäöüß])(?:ka|test)(?![a-zäöüß])",
    re.IGNORECASE,
)


def kind_from_text(text: str) -> str | None:
    """Die Art, die die Person **selbst** genannt hat - sonst ``None``.

    Qwen 1.5B macht aus "Trage die Englisch-Klassenarbeit ein" eine Hausaufgabe.
    "Klassenarbeit", "Klausur", "KA" und "Test" sind eindeutig eine Pruefung.
    """
    return ACTION_EXAM if _TEXT_EXAM_RE.search(text) else None


def with_kind(proposal: ActionProposal, kind: str) -> ActionProposal:
    """Derselbe Vorschlag als andere Art; ein Termin wird nicht zur Pruefung umgedeutet."""
    if proposal.kind == kind or proposal.kind == ACTION_EVENT:
        return proposal
    return replace(proposal, kind=kind)


def with_day(proposal: ActionProposal, day: date, zone: ZoneInfo) -> ActionProposal:
    """Derselbe Vorschlag, auf einen anderen Tag verschoben (Uhrzeit und Dauer bleiben)."""
    start_local = as_utc(proposal.starts_at).astimezone(zone)
    new_start = as_utc(datetime.combine(day, start_local.time(), tzinfo=zone))
    shift = new_start - as_utc(proposal.starts_at)
    return ActionProposal(
        kind=proposal.kind,
        title=proposal.title,
        starts_at=new_start,
        ends_at=as_utc(proposal.ends_at) + shift if proposal.ends_at else None,
        all_day=proposal.all_day,
        subject_id=proposal.subject_id,
        subject_name=proposal.subject_name,
        description=proposal.description,
    )


_KIND_WORDS: Final[dict[str, str]] = {
    ACTION_EXAM: "die Prüfung",
    ACTION_HOMEWORK: "die Hausaufgabe",
    ACTION_EVENT: "den Termin",
}


def describe_proposals(proposals: Sequence[ActionProposal], zone: ZoneInfo) -> str:
    """Ein Satz, der die Vorschlaege nennt - fuer den Text und zum Vorlesen."""
    teile = []
    for proposal in proposals:
        local = as_utc(proposal.starts_at).astimezone(zone)
        tag = f"{_WEEKDAYS[local.weekday()]}, den {local.day}.{local.month}."
        uhr = "" if proposal.all_day or proposal.kind == ACTION_HOMEWORK else f" um {local:%H:%M} Uhr"
        teile.append(f"{_KIND_WORDS.get(proposal.kind, 'den Eintrag')} „{proposal.title}“ am {tag}{uhr}")
    return "Soll ich " + " und ".join(teile) + " eintragen?"


def _parse_day(value: object, today: date) -> date | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw in {"heute", "today"}:
        return today
    if raw in {"morgen", "tomorrow"}:
        return today + timedelta(days=1)
    if raw in {"übermorgen", "uebermorgen"}:
        return today + timedelta(days=2)
    match = _DATE_RE.match(raw)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = _DE_DATE_RE.match(raw)
    if match:
        year_raw = match.group(3)
        year = today.year if not year_raw else int(year_raw) + (2000 if len(year_raw) == 2 else 0)
        try:
            parsed = date(year, int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None
        # "3.2." im Oktober meint den kommenden Februar.
        if not year_raw and parsed < today:
            parsed = parsed.replace(year=parsed.year + 1)
        return parsed
    return None


def _parse_time(value: object) -> time | None:
    match = _TIME_RE.match(str(value or "").strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def match_subject(subjects: Sequence[Subject], name: str | None) -> Subject | None:
    """Findet das Fach zu einem Namen aus der Antwort: exakt, Kuerzel, dann Anfang.

    Kein unscharfer Abgleich darueber hinaus: "Deutsch" darf nicht bei
    "Deutsch als Fremdsprache" *und* "Deutsch" landen, und im Zweifel ist kein
    Fach besser als das falsche.
    """
    wanted = _normalize(name or "")
    if not wanted:
        return None
    for subject in subjects:
        if _normalize(subject.name) == wanted:
            return subject
    for subject in subjects:
        if subject.short_name and _normalize(subject.short_name) == wanted:
            return subject
    prefixed = [subject for subject in subjects if _normalize(subject.name).startswith(wanted)]
    if len(prefixed) == 1:
        return prefixed[0]
    contained = [subject for subject in subjects if wanted.startswith(_normalize(subject.name))]
    return contained[0] if len(contained) == 1 else None


def build_proposal(
    raw: dict[str, object], subjects: Sequence[Subject], zone: ZoneInfo, today: date
) -> ActionProposal | None:
    """Macht aus einer ``AKTION``-Zeile einen Vorschlag - oder verwirft sie.

    Verworfen wird, was keine Art, keinen Titel oder kein Datum hat. Alles
    andere hat einen Standard (Uhrzeit, Dauer), weil ein Vorschlag mit
    sinnvollem Standard besser ist als gar keiner - die Lernende sieht ihn vor
    dem Eintragen.
    """
    kind = _KIND_ALIASES.get(str(raw.get("typ") or raw.get("type") or "").strip().lower())
    title = str(raw.get("titel") or raw.get("title") or "").strip()[:240]
    day = _parse_day(raw.get("datum") or raw.get("date"), today)
    if kind is None or not title or day is None:
        return None
    start_clock = _parse_time(raw.get("uhrzeit") or raw.get("time"))
    subject_name = str(raw.get("fach") or raw.get("subject") or "").strip() or None
    subject = match_subject(subjects, subject_name)
    description = str(raw.get("beschreibung") or raw.get("description") or "").strip() or None

    if kind == ACTION_EVENT:
        if start_clock is None:
            start_local = datetime.combine(day, time(0, 0), tzinfo=zone)
            return ActionProposal(
                kind=kind,
                title=title,
                starts_at=as_utc(start_local),
                ends_at=as_utc(start_local + timedelta(days=1)),
                all_day=True,
                subject_id=subject.id if subject else None,
                subject_name=subject.name if subject else subject_name,
                description=description,
            )
        start_local = datetime.combine(day, start_clock, tzinfo=zone)
        end_clock = _parse_time(raw.get("ende") or raw.get("end"))
        end_local = datetime.combine(day, end_clock, tzinfo=zone) if end_clock else None
        if end_local is None or end_local <= start_local:
            end_local = start_local + timedelta(minutes=DEFAULT_EVENT_MINUTES)
        return ActionProposal(
            kind=kind,
            title=title,
            starts_at=as_utc(start_local),
            ends_at=as_utc(end_local),
            subject_id=subject.id if subject else None,
            subject_name=subject.name if subject else subject_name,
            description=description,
        )

    start_local = datetime.combine(day, start_clock or DEFAULT_SCHOOL_TIME, tzinfo=zone)
    return ActionProposal(
        kind=kind,
        title=title,
        starts_at=as_utc(start_local),
        subject_id=subject.id if subject else None,
        subject_name=subject.name if subject else subject_name,
        description=description,
    )


# --- Kontext -----------------------------------------------------------------


def user_zone(user: User, settings: Settings) -> ZoneInfo:
    return zone_or_utc(resolve_timezone_name(user.timezone, settings.timezone))


def _local(value: datetime, zone: ZoneInfo) -> datetime:
    return as_utc(value).astimezone(zone)


def _fmt_day(value: datetime, zone: ZoneInfo) -> str:
    local = _local(value, zone)
    return f"{_WEEKDAYS[local.weekday()]}, {local:%d.%m.%Y}"


def _fmt_moment(value: datetime, zone: ZoneInfo) -> str:
    local = _local(value, zone)
    return f"{_WEEKDAYS[local.weekday()]}, {local:%d.%m.%Y %H:%M}"


def list_memories(db: Session, user_id: int) -> list[AssistantMemory]:
    return list(
        db.scalars(
            select(AssistantMemory)
            .where(AssistantMemory.user_id == user_id)
            .order_by(AssistantMemory.created_at.desc(), AssistantMemory.id.desc())
        ).all()
    )


def add_memory(db: Session, user_id: int, content: str, source: str = MEMORY_SOURCE_ASSISTANT) -> AssistantMemory | None:
    """Legt einen Satz ab - ausser er steht schon da (Gross/Klein egal).

    ``None`` heisst "war schon bekannt" und ist kein Fehler: ein Modell, das
    dieselbe Vorliebe in jeder Antwort wiederholt, soll keine Liste fuellen.
    """
    cleaned = re.sub(r"\s+", " ", content).strip().strip("{}[]()\"'„“” ")[:MEMORY_MAX_CHARS]
    if not cleaned:
        return None
    existing = db.scalar(
        select(AssistantMemory.id).where(
            AssistantMemory.user_id == user_id,
            func.lower(AssistantMemory.content) == cleaned.lower(),
        )
    )
    if existing is not None:
        return None
    memory = AssistantMemory(user_id=user_id, content=cleaned, source=source)
    db.add(memory)
    db.flush()
    return memory


def relative_day_table(today: date) -> list[str]:
    """Woerter wie "naechster Freitag" mit fertigem Datum.

    Kleine Modelle rechnen Wochentage unzuverlaessig. Eine Liste der naechsten
    14 Tage reichte nicht: bei "naechsten Freitag" an einem Freitag nahm Qwen 3B
    den zweiten Freitag der Liste. Deshalb steht die Zuordnung hier eindeutig -
    "naechster" ist das erste Vorkommen nach heute, "uebernaechster" eine Woche
    spaeter.
    """
    eintraege = [
        f"heute = {today.isoformat()}",
        f"morgen = {(today + timedelta(days=1)).isoformat()}",
        f"übermorgen = {(today + timedelta(days=2)).isoformat()}",
    ]
    for offset in range(1, 8):
        tag = today + timedelta(days=offset)
        eintraege.append(f"nächster {_WEEKDAYS[tag.weekday()]} = {tag.isoformat()}")
    for offset in range(8, 15):
        tag = today + timedelta(days=offset)
        eintraege.append(f"übernächster {_WEEKDAYS[tag.weekday()]} = {tag.isoformat()}")
    return eintraege


def build_context(db: Session, user: User, zone: ZoneInfo, now: datetime) -> list[str]:
    """Der Schulstand als kurze Zeilen. Jede Abfrage ist auf den Nutzer begrenzt."""
    local_now = _local(now, zone)
    today = local_now.date()
    horizon = now + timedelta(days=PLAN_HORIZON_DAYS)
    lines: list[str] = [
        f"Jetzt: {_WEEKDAYS[today.weekday()]}, {local_now:%d.%m.%Y %H:%M} (Zeitzone {zone.key}).",
        "Datumshilfe: " + "; ".join(relative_day_table(today)),
    ]

    subjects = db.scalars(select(Subject).where(Subject.user_id == user.id).order_by(Subject.name)).all()
    if subjects:
        lines.append(
            "Faecher: "
            + ", ".join(f"{s.name} ({s.short_name})" if s.short_name else s.name for s in subjects)
        )

    start, end = day_window(today, zone)
    lessons = db.scalars(
        select(Lesson)
        .options(joinedload(Lesson.subject))
        .where(Lesson.user_id == user.id, Lesson.start_time >= start, Lesson.start_time < end)
        .order_by(Lesson.start_time)
    ).all()
    if lessons:
        lines.append(
            "Stundenplan heute: "
            + "; ".join(
                f"{_local(lesson.start_time, zone):%H:%M} {lesson.subject.name if lesson.subject else lesson.title or 'Stunde'}"
                + (" (faellt aus)" if lesson.is_cancelled else "")
                for lesson in lessons
            )
        )

    exams = db.scalars(
        select(Exam)
        .options(joinedload(Exam.subject))
        .where(Exam.user_id == user.id, Exam.date >= now, Exam.date <= horizon + timedelta(days=30))
        .order_by(Exam.date)
        .limit(8)
    ).all()
    lines.append(
        "Anstehende Pruefungen: "
        + (
            "; ".join(
                f"{_fmt_day(exam.date, zone)} {exam.title}" + (f" ({exam.subject.name})" if exam.subject else "")
                for exam in exams
            )
            if exams
            else "keine eingetragen"
        )
    )

    homework = db.scalars(
        select(Homework)
        .options(joinedload(Homework.subject))
        .where(Homework.user_id == user.id, Homework.completed.is_(False))
        .order_by(Homework.due_date)
        .limit(10)
    ).all()
    lines.append(
        "Offene Hausaufgaben: "
        + (
            "; ".join(
                f"bis {_fmt_day(item.due_date, zone)} {item.title}" + (f" ({item.subject.name})" if item.subject else "")
                for item in homework
            )
            if homework
            else "keine"
        )
    )

    events = db.scalars(
        select(CalendarEvent)
        .where(
            CalendarEvent.user_id == user.id,
            CalendarEvent.sync_state != SYNC_STATE_DELETED,
            CalendarEvent.end_time >= now,
            CalendarEvent.start_time <= horizon,
        )
        .order_by(CalendarEvent.start_time)
        .limit(8)
    ).all()
    if events:
        lines.append(
            "Termine: "
            + "; ".join(
                f"{_fmt_day(event.start_time, zone) if event.all_day else _fmt_moment(event.start_time, zone)} {event.title}"
                for event in events
            )
        )
    return lines


# --- Dokumente ---------------------------------------------------------------


def list_documents(db: Session, user_id: int) -> list[Document]:
    return list(
        db.scalars(
            select(Document)
            .where(Document.user_id == user_id)
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(DOCUMENT_LIST_LIMIT)
        ).all()
    )


def _name_words(text: str) -> set[str]:
    """Inhaltswoerter: klein, ohne Endung, ab vier Buchstaben, ohne Allerweltswoerter."""
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", text)
    return {
        word
        for word in _NAME_WORD_RE.findall(stem.lower())
        if len(word) >= 4 and word not in _GENERIC_NAME_WORDS
    }


def named_documents(question: str, documents: Sequence[Document]) -> list[Document]:
    """Dokumente, die die Frage beim Namen nennt ("Was steht in meinem Lebenslauf?").

    Die Stichwortsuche sucht nur im Text. Ein Lebenslauf enthaelt das Wort
    "Lebenslauf" oft gar nicht - ohne diesen Abgleich bekam das Modell nichts
    und erfand eine Antwort aus den Notizen. Verglichen wird tolerant:
    "Zeugnis" findet "Mathe_Zeugnis_2026.pdf", "Mathezeugnis" und "Zeugnisse".
    """
    asked = _name_words(question)
    if not asked:
        return []
    found: list[Document] = []
    for document in documents:
        words = _name_words(document.filename)
        if any(
            a == w or (min(len(a), len(w)) >= 5 and (a in w or w in a))
            for a in asked
            for w in words
        ):
            found.append(document)
    return found


def document_excerpts(
    db: Session,
    user_id: int,
    named: Sequence[Document],
    hits: Sequence[DocumentSource],
    budget: int = EXCERPT_CHAR_BUDGET,
) -> list[DocumentSource]:
    """Erst die genannten Dokumente vom Anfang an, dann die Suchtreffer - bis zum Budget.

    Ein genanntes Dokument teilt sich das Budget mit den anderen genannten, damit
    bei "Lebenslauf und Zeugnis" nicht der Lebenslauf alles verbraucht.
    """
    result: list[DocumentSource] = []
    seen: set[tuple[int, int]] = set()
    remaining = budget
    if named:
        share = max(budget // len(named), 1500)
        for document in named:
            chunks = db.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.user_id == user_id, DocumentChunk.document_id == document.id)
                .order_by(DocumentChunk.chunk_index)
            ).all()
            used = 0
            for chunk in chunks:
                if used >= share or remaining <= 0:
                    break
                content = chunk.content[: min(share - used, remaining)]
                result.append(
                    DocumentSource(
                        document_id=document.id,
                        filename=document.filename,
                        chunk_index=chunk.chunk_index,
                        content=content,
                    )
                )
                seen.add((document.id, chunk.chunk_index))
                used += len(content)
                remaining -= len(content)
    for hit in hits:
        if remaining <= 0:
            break
        if (hit.document_id, hit.chunk_index) in seen:
            continue
        content = hit.content[:remaining]
        result.append(
            DocumentSource(
                document_id=hit.document_id, filename=hit.filename, chunk_index=hit.chunk_index, content=content
            )
        )
        seen.add((hit.document_id, hit.chunk_index))
        remaining -= len(content)
    return result


def document_overview_lines(
    db: Session, documents: Sequence[Document], named: Sequence[Document]
) -> list[str]:
    """Welche Dokumente es gibt - und ob ein genanntes ueberhaupt lesbaren Text hat."""
    if not documents:
        return ["Hochgeladene Dokumente: keine."]
    counts = dict(
        db.execute(
            select(DocumentChunk.document_id, func.count())
            .where(DocumentChunk.document_id.in_([d.id for d in documents]))
            .group_by(DocumentChunk.document_id)
        )
        .tuples()
        .all()
    )
    lines = [
        "Hochgeladene Dokumente: "
        + "; ".join(
            document.filename + ("" if counts.get(document.id) else " (kein lesbarer Text)") for document in documents
        )
    ]
    for document in named:
        if not counts.get(document.id):
            lines.append(
                f"Hinweis: Aus {document.filename} konnte kein Text gelesen werden (z. B. ein Foto oder Scan ohne "
                "Texterkennung). Sag das der Person, statt den Inhalt zu raten."
            )
    return lines


SYSTEM_PROMPT: Final = (
    "Du bist der persoenliche Lern-Assistent einer Schuelerin oder eines Schuelers. "
    "Antworte auf Deutsch, freundlich, knapp und konkret, und sprich die Person immer mit einem "
    "normalen Satz direkt an.\n\n"
    "Du hast Zugriff auf alles, was unten mitgeliefert wird: Schulstand, Liste der Dokumente, "
    "Dokumentauszuege, Noten und Notizen ueber die Person. Halte dich streng daran:\n"
    "- Nenne nur Fakten, die woertlich oder eindeutig in diesen Bloecken stehen. Erfinde nichts, "
    "auch keine Noten, Daten oder Inhalte.\n"
    "- Die Notizen ueber die Person sind KEIN Dokumentinhalt. Gib sie nie als Inhalt eines Dokuments aus.\n"
    "- Fragt die Person nach einem Dokument und es gibt keinen Auszug daraus, sag ehrlich, dass du den "
    "Inhalt nicht siehst, und nenne die Dokumente, die du hast.\n"
    "- Fehlt eine Information, sag genau, was fehlt, statt zu raten.\n\n"
    "Wenn du etwas Dauerhaftes und NEUES ueber die Person erfaehrst, weil sie es dir gerade selbst "
    "erzaehlt (Vorlieben, Ziele, Schwaechen, wie sie gern lernt), schreibe am Ende zusaetzlich eine Zeile:\n"
    "MERKE: <ein kurzer Satz ohne Subjekt, z. B. 'Lernt am liebsten abends.'>\n"
    "Wiederhole keine Notiz, die schon da ist, und schreibe MERKE nicht bei einer blossen Frage."
)

#: Nur wenn die Person etwas eintragen lassen will, steht die AKTION-Anweisung im
#: Prompt. Sonst springt ein kleines Modell darauf an und erfindet bei "Was soll
#: ich heute lernen?" eine Hausaufgabe aus dem Schulstand.
ACTION_PROMPT: Final = (
    "\n\nDie Person moechte etwas eintragen lassen. Schreibe pro Eintrag am Ende eine eigene Zeile "
    "genau in dieser Form:\n"
    'AKTION {"typ": "pruefung|hausaufgabe|termin", "titel": "...", "fach": "...", '
    '"datum": "JJJJ-MM-TT", "uhrzeit": "HH:MM", "ende": "HH:MM", "beschreibung": "..."}\n'
    "Nur typ, titel und datum sind Pflicht. Eine Klassenarbeit, Klausur oder ein Test ist typ 'pruefung', nie 'hausaufgabe'. Uebernimm das Datum fuer Angaben wie 'naechsten Freitag' "
    "oder 'morgen' genau aus der Datumshilfe, rechne nicht selbst. Lass uhrzeit weg, wenn keine genannt "
    "wurde. Schreibe davor kurz in einem Satz, was du vorschlaegst; die Person bestaetigt den Eintrag selbst."
)

_ENTRY_WORDS: Final = re.compile(
    r"\b(trag\w*|eintrag\w*|notier\w*|speicher\w*|vormerk\w*|erinner\w*|anleg\w*|leg\w*\b.*\ban|"
    r"f[uü]g\w*\b.*\bhinzu|hinzuf[uü]g\w*|setz\w*\b.*\b(kalender|plan)|kalender)\b",
    re.IGNORECASE,
)


def wants_entry(text: str) -> bool:
    """Ob die Nachricht etwas eintragen lassen will ("Trag ... ein", "Notier ...", "Erinnere mich ...")."""
    return bool(_ENTRY_WORDS.search(text))


def _letters(text: str) -> str:
    return re.sub(r"[^a-zäöüß]", "", text.lower())


def _is_known(content: str, known: Sequence[str]) -> bool:
    """Ob eine Notiz einer vorhandenen so aehnlich ist, dass sie nichts Neues sagt."""
    candidate = _letters(content)
    return any(SequenceMatcher(None, candidate, _letters(other)).ratio() >= 0.8 for other in known)


def build_user_prompt(
    history: Sequence[ChatTurn],
    context: Sequence[str],
    memories: Sequence[AssistantMemory],
    hits: Sequence[DocumentSource],
    grade_context: Sequence[str],
) -> str:
    parts: list[str] = ["--- Schulstand ---", *context]
    if memories:
        parts.append("")
        parts.append("--- Notizen ueber die Person ---")
        parts.extend(f"- {memory.content}" for memory in memories)
    parts.append("")
    parts.append("--- Dokumentauszuege ---")
    if hits:
        for index, hit in enumerate(hits, start=1):
            parts.append(f"[{index}] {hit.filename} (Abschnitt {hit.chunk_index + 1}): {hit.content}")
    else:
        parts.append("Zu dieser Frage wurde in den Dokumenten nichts gefunden.")
    if grade_context:
        parts.append("")
        parts.append("--- Noten ---")
        parts.extend(grade_context)
    parts.append("")
    parts.append("--- Gespraech ---")
    for turn in history:
        speaker = "Person" if turn.role == ROLE_USER else "Assistent"
        parts.append(f"{speaker}: {turn.content}")
    parts.append("Assistent:")
    return "\n".join(parts)


# --- Wege --------------------------------------------------------------------


def available_routes(db: Session, user_id: int, settings: Settings) -> list[AssistantRoute]:
    """Erst die geladenen Modelle (eingebaut), dann die eingetragenen Verbindungen."""
    result: list[AssistantRoute] = []
    manager = active_manager()
    if manager is not None:
        preferred = manager.preferred_text_model()
        for spec in manager.installed_text_models():
            result.append(
                AssistantRoute(
                    integration_id=None,
                    name=spec.label,
                    model=spec.id,
                    source=ai_routing.SOURCE_LOCAL,
                    local_model_id=spec.id,
                    selected=preferred is not None and spec.id == preferred.id,
                )
            )
    result.extend(
        AssistantRoute(integration_id=route.integration_id, name=route.name, model=route.model, source=route.source)
        for route in ai_routing.resolve_ai_routes(db, user_id, settings)
        if not route.is_builtin
    )
    return result


def _select_routes(
    routes: tuple[ai_routing.AiRoute, ...], integration_id: int | None, local_model_id: str | None
) -> tuple[ai_routing.AiRoute, ...]:
    if local_model_id:
        route = ai_routing.builtin_route(local_model_id)
        manager = active_manager()
        if route is not None and manager is not None:
            # Die Wahl gilt auch fuer Begruessung und Dokumentenfrage.
            manager.remember_text_model(local_model_id)
        return (route,) if route else ()
    if integration_id is None:
        return routes
    return tuple(route for route in routes if route.integration_id == integration_id and not route.is_builtin)


# --- Chat --------------------------------------------------------------------


def _clean_history(messages: Sequence[ChatTurn]) -> list[ChatTurn]:
    cleaned = [
        ChatTurn(role=turn.role, content=turn.content.strip()[:MAX_TURN_CHARS])
        for turn in messages
        if turn.role in (ROLE_USER, ROLE_ASSISTANT) and turn.content.strip()
    ]
    return cleaned[-MAX_HISTORY_TURNS:]


def _fallback_reply(hits: Sequence[DocumentSource], context: Sequence[str]) -> str:
    teile = ["Gerade antwortet kein KI-Modell, deshalb nur, was in deinen Daten steht."]
    if hits:
        teile.append("Passende Stellen findest du in: " + ", ".join(sorted({hit.filename for hit in hits})) + ".")
    for zeile in context:
        if zeile.startswith(("Anstehende Pruefungen", "Offene Hausaufgaben")):
            teile.append(zeile.replace("Pruefungen", "Prüfungen") + ".")
    teile.append("Ein Modell richtest du unter „KI & Modelle“ ein.")
    return " ".join(teile)


def chat(
    db: Session,
    user: User,
    settings: Settings,
    messages: Sequence[ChatTurn],
    *,
    integration_id: int | None = None,
    local_model_id: str | None = None,
    grade_context: Sequence[str] = (),
    grades_shared_online: bool = True,
    voice: bool = False,
    record_training: bool = False,
    now: datetime | None = None,
) -> AssistantReply:
    """Eine Antwort des Assistenten auf den bisherigen Verlauf.

    Gemerkte Saetze werden hier schon gespeichert (``commit``), Vorschlaege
    nicht - die gehen erst ueber ``apply_action`` in die Datenbank.

    ``grade_context`` sind alle Notenzeilen. Sie gehen in den Prompt, wenn die
    Freigabe erteilt ist (``grades_shared_online``) **oder** jeder moegliche Weg
    ein lokales Modell ist - dann verlassen die Noten den Rechner nicht, und
    eine Freigabe fuer einen Online-Dienst waere eine Frage ohne Gegenstand.

    ``record_training`` (die Trainingsfreigabe) legt jede echte Modellantwort
    mit Prompt als Trainingsbeispiel ab - die Ersatzantwort ohne Modell nicht,
    an ihr gibt es nichts zu lernen.
    """
    history = _clean_history(messages)
    if not history or history[-1].role != ROLE_USER:
        raise AssistantActionError("Die letzte Nachricht muss von dir kommen.")
    moment = now or utc_now()
    zone = user_zone(user, settings)
    today = _local(moment, zone).date()

    question = history[-1].content
    documents = list_documents(db, user.id)
    named = named_documents(question, documents)
    search = [hit.source() for hit in search_chunks(db, user.id, settings, question, limit=DOCUMENT_HITS)]
    hits = document_excerpts(db, user.id, named, search)
    context = build_context(db, user, zone, moment) + document_overview_lines(db, documents, named)
    memories = list_memories(db, user.id)[:MAX_MEMORIES_IN_PROMPT]

    routes = _select_routes(ai_routing.resolve_ai_routes(db, user.id, settings), integration_id, local_model_id)
    grades_allowed = grades_shared_online or (bool(routes) and all(route.is_local for route in routes))
    if not grade_context:
        context.append("Noten: keine eingetragen.")
    elif not grades_allowed:
        context.append(
            "Noten: vorhanden, aber fuer den Online-KI-Dienst nicht freigegeben (Freigabe auf der Seite "
            "„Fortschritt“ bei den Noten, oder ein lokales Modell waehlen)."
        )
    grade_context = list(grade_context) if grades_allowed else []
    entry_wanted = wants_entry(question)
    system_prompt = SYSTEM_PROMPT + (ACTION_PROMPT if entry_wanted else "") + (VOICE_PROMPT if voice else "")
    user_prompt = build_user_prompt(history, context, memories, hits, grade_context)
    outcome = (
        ai_routing.generate_with_routes(
            routes,
            system_prompt,
            user_prompt,
            max_tokens=_MAX_VOICE_REPLY_TOKENS if voice else _MAX_REPLY_TOKENS,
            local_timeout=settings.ai_local_timeout_seconds,
            local_fast_timeout=settings.ai_local_fast_timeout_seconds,
            provider_timeout=settings.ai_generate_timeout_seconds,
        )
        if routes
        else None
    )
    if outcome is None:
        return AssistantReply(
            reply=_fallback_reply(hits, context),
            source=ai_routing.SOURCE_FALLBACK,
            sources=hits,
            grades_shared=bool(grade_context),
        )

    parsed = parse_reply(outcome.text)
    subjects = db.scalars(select(Subject).where(Subject.user_id == user.id)).all()
    # Ohne Eintragswunsch gibt es keine Vorschlaege - auch wenn das Modell trotzdem AKTION schreibt.
    actions = parsed.actions[:MAX_ACTIONS_PER_REPLY] if entry_wanted else []
    proposals = [
        proposal for proposal in (build_proposal(raw, subjects, zone, today) for raw in actions) if proposal is not None
    ]
    genannt = date_from_text(question, today) if proposals else None
    adjusted = False
    if genannt is not None:
        korrigiert = [with_day(proposal, genannt, zone) for proposal in proposals]
        adjusted = korrigiert != proposals
        proposals = korrigiert
    genannte_art = kind_from_text(question) if proposals else None
    if genannte_art is not None:
        korrigiert = [with_kind(proposal, genannte_art) for proposal in proposals]
        adjusted = adjusted or korrigiert != proposals
        proposals = korrigiert
    example = (
        training_service.record_example(
            db,
            user.id,
            system_prompt=system_prompt,
            prompt=user_prompt,
            completion=outcome.text,
            source=outcome.source,
            model=outcome.model,
            server_adjusted=adjusted,
        )
        if record_training
        else None
    )
    known = [memory.content for memory in memories]
    added: list[AssistantMemory] = []
    for text in parsed.memories[:MAX_MEMORIES_PER_REPLY]:
        if _is_known(text, known):
            continue
        memory = add_memory(db, user.id, text)
        if memory is not None:
            if example is not None:
                memory.training_example_id = example.id
            added.append(memory)
            known.append(memory.content)
    if added or example is not None:
        db.commit()

    # Qwen haengt bekannte Notizen gern als "Merke: ..." mitten in den Satz.
    reply_text = _INLINE_MEMORY_RE.sub(
        lambda match: "" if _is_known(match.group(1), known) else match.group(0), parsed.text
    ).strip()
    if not reply_text:
        if proposals:
            reply_text = describe_proposals(proposals, zone)
        elif added:
            reply_text = "Alles klar, das merke ich mir."
        else:
            logger.warning("Assistent: leere Antwort nach dem Bereinigen, Rohtext: %r", outcome.text[:500])
            reply_text = "Entschuldige, darauf habe ich gerade keine gute Antwort. Frag mich bitte noch einmal anders."
    return AssistantReply(
        reply=reply_text,
        source=outcome.source,
        model=outcome.model,
        sources=hits,
        proposals=proposals,
        memories_added=added,
        grades_shared=bool(grade_context),
        training_example_id=example.id if example is not None else None,
    )


# --- Eintragen ---------------------------------------------------------------


def apply_action(db: Session, user_id: int, proposal: ActionProposal) -> AppliedAction:
    """Traegt einen bestaetigten Vorschlag ein. Das Fach muss dem Nutzer gehoeren.

    Ein fremdes Fach ist dieselbe Antwort wie ein unbekanntes - der Aufrufer
    macht daraus 404, nicht 403.
    """
    if proposal.kind not in ACTION_KINDS:
        raise AssistantActionError(f"Unbekannte Art: {proposal.kind}")
    title = proposal.title.strip()
    if not title:
        raise AssistantActionError("Ein Eintrag braucht einen Titel.")
    subject_id = proposal.subject_id
    if subject_id is not None:
        subject = db.get(Subject, subject_id)
        if subject is None or subject.user_id != user_id:
            raise LookupError("Fach nicht gefunden")

    starts_at = as_utc(proposal.starts_at)
    if proposal.kind == ACTION_EXAM:
        exam = Exam(user_id=user_id, subject_id=subject_id, title=title, description=proposal.description, date=starts_at)
        db.add(exam)
        db.commit()
        return AppliedAction(kind=proposal.kind, id=exam.id, title=title, message="Prüfung eingetragen.")
    if proposal.kind == ACTION_HOMEWORK:
        item = Homework(
            user_id=user_id, subject_id=subject_id, title=title, description=proposal.description, due_date=starts_at
        )
        db.add(item)
        db.commit()
        return AppliedAction(kind=proposal.kind, id=item.id, title=title, message="Hausaufgabe eingetragen.")

    ends_at = as_utc(proposal.ends_at) if proposal.ends_at else starts_at + timedelta(minutes=DEFAULT_EVENT_MINUTES)
    if ends_at < starts_at:
        raise AssistantActionError("Das Ende liegt vor dem Beginn.")
    event = CalendarEvent(
        user_id=user_id,
        title=title,
        description=proposal.description,
        start_time=starts_at,
        end_time=ends_at,
        all_day=proposal.all_day,
    )
    db.add(event)
    db.commit()
    return AppliedAction(kind=proposal.kind, id=event.id, title=title, message="Termin eingetragen.")
