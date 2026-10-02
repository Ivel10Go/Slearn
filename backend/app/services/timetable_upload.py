"""Stundenplan aus einem PDF oder Bild uebernehmen.

Zwei Schritte, damit nichts ungeprueft in der Datenbank landet: ``extract_*``
liest den Plan aus und gibt einen **Vorschlag** zurueck, ``apply_timetable``
schreibt erst, nachdem die Nutzerin ihn gesehen (und korrigiert) hat. Ein
Modell liest aus einem Foto auch Faecher ab, die nicht dort stehen.

Gelesen wird ueber den KI-Anbieter des Nutzers (Bild: Bildeingabe, PDF: der
Text der Seiten). Ohne Anbieter gibt es eine klare Meldung und kein
Raten: eine Texterkennung ohne KI wuerde aus einem Foto Muell machen.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import as_utc, zone_or_utc
from app.db.entities import Lesson, Room, Subject
from app.schemas.timetable import TimetableEntryDraft
from app.services.ai_provider import AiProviderError, generate_text, generate_text_from_image
from app.services.documents import _load_provider

SOURCE_NAME: str = "upload"
MAX_UPLOAD_BYTES: int = 8 * 1024 * 1024
MAX_PDF_CHARS: int = 12_000
IMAGE_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_SYSTEM_PROMPT = (
    "Du liest einen Schulstundenplan aus und antwortest ausschliesslich mit JSON, ohne Erklaerung. "
    'Format: {"entries":[{"weekday":0,"start":"07:45","end":"08:30","subject":"Mathematik",'
    '"short_name":"M","room":"322"}]}. weekday: 0=Montag bis 4=Freitag. Zeiten als HH:MM, 24 Stunden. '
    "subject ist der ausgeschriebene Fachname, short_name das Kuerzel, falls eines dasteht. "
    "room nur, wenn er dasteht. Eine Stunde pro Eintrag, auch Doppelstunden getrennt. "
    "Erfinde nichts: was nicht lesbar ist, laesst du weg."
)
_USER_PROMPT = "Lies diesen Stundenplan aus."


class TimetableUploadError(ValueError):
    """Eine Meldung, die so bei der Nutzerin ankommen darf."""


@dataclass(frozen=True, slots=True)
class Extraction:
    entries: list[TimetableEntryDraft]
    via: str


def file_kind(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in IMAGE_TYPES:
        return "image"
    raise TimetableUploadError("Unterstützt werden PDF, PNG, JPG und WebP.")


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages[:5]]
    except (PyPdfError, ValueError, OSError) as exc:
        raise TimetableUploadError("Das PDF lässt sich nicht lesen.") from exc
    text = "\n".join(pages).strip()
    if not text:
        raise TimetableUploadError(
            "Im PDF steht kein Text (vermutlich ein Scan). Lade den Plan stattdessen als Bild hoch."
        )
    return text[:MAX_PDF_CHARS]


def _clock(value: object) -> str:
    return datetime.strptime(str(value).strip(), "%H:%M").strftime("%H:%M")


def parse_entries(raw: str) -> list[TimetableEntryDraft]:
    """Liest die Modellantwort tolerant: Codezaun, Vortext und kaputte Eintraege."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match is None:
        raise TimetableUploadError("Aus dem Plan konnten keine Stunden gelesen werden.")
    try:
        payload = json.loads(match.group(0))
    except ValueError as exc:
        raise TimetableUploadError(
            "Die Antwort der KI war nicht lesbar. Versuche es mit einem schärferen Bild."
        ) from exc
    items = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise TimetableUploadError("Aus dem Plan konnten keine Stunden gelesen werden.")

    entries: list[TimetableEntryDraft] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        short = str(item.get("short_name") or "").strip()[:20] or None
        room = str(item.get("room") or "").strip()[:120] or None
        try:
            entry = TimetableEntryDraft(
                weekday=int(item.get("weekday", -1)),
                start=_clock(item.get("start")),
                end=_clock(item.get("end")),
                subject=str(item.get("subject") or "").strip(),
                short_name=short,
                room=room,
            )
        except (TypeError, ValueError):
            continue
        if entry.end > entry.start and entry.weekday <= 4:
            entries.append(entry)
    if not entries:
        raise TimetableUploadError("Aus dem Plan konnten keine Stunden gelesen werden.")
    return entries


def extract_timetable(db: Session, user_id: int, settings: Settings, filename: str, data: bytes) -> Extraction:
    kind = file_kind(filename)
    if len(data) > MAX_UPLOAD_BYTES:
        raise TimetableUploadError("Die Datei ist größer als 8 MB.")
    if not data:
        raise TimetableUploadError("Die Datei ist leer.")

    provider = _load_provider(db, user_id, settings)
    if provider is None:
        raise TimetableUploadError(
            "Zum Auslesen braucht es einen KI-Anbieter. Richte ihn unter „KI & Modelle“ ein, "
            "oder lege die Fächer von Hand an."
        )
    try:
        if kind == "pdf":
            text = pdf_text(data)
            raw = generate_text(
                provider.base_url,
                provider.model,
                _SYSTEM_PROMPT,
                f"{_USER_PROMPT}\n\n{text}",
                api_key=provider.api_key,
                timeout_seconds=90.0,
                max_tokens=1500,
            )
            via = "pdf_text"
        else:
            raw = generate_text_from_image(
                provider.base_url,
                provider.model,
                _SYSTEM_PROMPT,
                _USER_PROMPT,
                data,
                IMAGE_TYPES[Path(filename).suffix.lower()],
                api_key=provider.api_key,
            )
            via = "image"
    except AiProviderError as exc:
        raise TimetableUploadError(
            f"Der KI-Anbieter konnte den Plan nicht lesen: {exc} Für Bilder braucht das Modell Bildeingabe."
        ) from exc
    return Extraction(entries=parse_entries(raw), via=via)


def existing_subject_names(db: Session, user_id: int) -> set[str]:
    rows = db.scalars(select(Subject.name).where(Subject.user_id == user_id)).all()
    return {name.casefold() for name in rows}


def apply_timetable(
    db: Session,
    user_id: int,
    entries: list[TimetableEntryDraft],
    timezone_name: str,
    weeks: int,
    today: date | None = None,
) -> tuple[int, int, date, date]:
    """Legt fehlende Faecher an und schreibt die Woche fuer ``weeks`` Wochen.

    Der Upload ersetzt nur frueher hochgeladene Stunden im Fenster; WebUntis-
    und von Hand angelegte Stunden bleiben unberuehrt.
    """
    zone = zone_or_utc(timezone_name)
    start_day = today or datetime.now(zone).date()
    start_day -= timedelta(days=start_day.weekday())
    end_day = start_day + timedelta(weeks=weeks) - timedelta(days=1)

    subjects: dict[str, Subject] = {
        s.name.casefold(): s for s in db.scalars(select(Subject).where(Subject.user_id == user_id)).all()
    }
    rooms: dict[str, Room] = {
        r.name.casefold(): r for r in db.scalars(select(Room).where(Room.user_id == user_id)).all()
    }
    created = 0
    for entry in entries:
        key = entry.subject.strip().casefold()
        found = subjects.get(key)
        if found is None:
            found = Subject(
                user_id=user_id, name=entry.subject.strip(), short_name=entry.short_name, source=SOURCE_NAME
            )
            db.add(found)
            subjects[key] = found
            created += 1
        elif not found.short_name and entry.short_name:
            found.short_name = entry.short_name
        if entry.room and entry.room.strip().casefold() not in rooms:
            room = Room(user_id=user_id, name=entry.room.strip(), source=SOURCE_NAME)
            db.add(room)
            rooms[entry.room.strip().casefold()] = room
    # Erst der Flush vergibt die Primaerschluessel, die die Lessons brauchen.
    db.flush()

    scope_start = as_utc(datetime.combine(start_day, time.min, tzinfo=zone))
    scope_end = as_utc(datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=zone))
    db.execute(
        delete(Lesson).where(
            Lesson.user_id == user_id,
            Lesson.source == SOURCE_NAME,
            Lesson.start_time >= scope_start,
            Lesson.start_time < scope_end,
        )
    )

    imported = 0
    for week in range(weeks):
        for index, entry in enumerate(entries):
            day = start_day + timedelta(weeks=week, days=entry.weekday)
            subject = subjects[entry.subject.strip().casefold()]
            room_row = rooms.get(entry.room.strip().casefold()) if entry.room else None
            db.add(
                Lesson(
                    user_id=user_id,
                    subject_id=subject.id,
                    room_id=room_row.id if room_row else None,
                    start_time=as_utc(datetime.combine(day, time.fromisoformat(entry.start), tzinfo=zone)),
                    end_time=as_utc(datetime.combine(day, time.fromisoformat(entry.end), tzinfo=zone)),
                    source=SOURCE_NAME,
                    external_id=f"{SOURCE_NAME}:{day.isoformat()}:{index}",
                )
            )
            imported += 1
    db.commit()
    return created, imported, start_day, end_day
