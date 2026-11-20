"""Klassenarbeit als Arbeitsbereich (wie NotebookLM): Material, Lernplan, Studio.

- **Material**: die ausgewaehlten Dokumente einer Klausur (``exam_documents``).
  Ist noch nichts gewaehlt, gelten alle Dokumente des Klausurfachs.
- **Lernplan**: die Themen kommen aus den erkannten Themen des Materials
  (``Document.material_topic``), danach erzeugt der vorhandene Klausur-Generator
  die Aufgaben. Vorher brauchte er von Hand angelegte Themen.
- **Studio**: ein Modellaufruf je Erstellung, das Ergebnis wird gespeichert.
  Strukturiertes (Karteikarten, Glossar, Probeklausur) als Zeilenprotokoll
  statt JSON - kleine lokale Modelle schreiben kaputtes JSON, aber "F:"/"A:" halten sie durch.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.core.language import Language, reply_instruction
from app.db.entities import Document, DocumentChunk, Exam, ExamCreation, ExamDocument, Topic, TopicDocument
from app.services.assistant import EXCERPT_CHAR_BUDGET, material_label
from app.services.learner import Generator


class NotebookError(ValueError):
    """Erstellen geht gerade nicht. Deutsche Meldung, wird zum 422."""


@dataclass(frozen=True, slots=True)
class Kind:
    task: str
    max_tokens: int
    # Zwei Markergruppen fuer Paare (Vorderseite/Rueckseite), sonst Fliesstext.
    pair: tuple[tuple[str, ...], tuple[str, ...], str, str] | None = None


KINDS: dict[str, Kind] = {
    "summary": Kind(
        "Schreibe eine verständliche Zusammenfassung des Materials in Absätzen, geordnet nach Themen.", 900
    ),
    "cheatsheet": Kind(
        "Schreibe einen Lernzettel für die Klassenarbeit: die wichtigsten Regeln, Formeln, Definitionen und "
        "typische Fehler als kurze Stichpunkte, die mit '- ' beginnen. Überschriften als eigene Zeile.",
        900,
    ),
    "mindmap": Kind(
        "Erstelle eine Mindmap als eingerückte Liste: erste Zeile das Hauptthema, darunter Unterthemen mit '- ', "
        "Details zwei Leerzeichen tiefer eingerückt mit '- '. Nur die Liste, kein weiterer Text.",
        700,
    ),
    "flashcards": Kind(
        "Erstelle 10 bis 15 Karteikarten. Jede Karte genau so:\nF: <Frage>\nA: <kurze Antwort>",
        1000,
        (("F", "Q", "FRAGE", "QUESTION"), ("A", "ANTWORT", "ANSWER"), "front", "back"),
    ),
    "glossary": Kind(
        "Erstelle ein Glossar der wichtigen Fachbegriffe. Jeder Eintrag genau so:\n"
        "BEGRIFF: <Begriff>\nERKLÄRUNG: <ein bis zwei Sätze>",
        1000,
        (("BEGRIFF", "TERM"), ("ERKLÄRUNG", "ERKLAERUNG", "DEFINITION"), "term", "definition"),
    ),
    "practice_exam": Kind(
        "Erstelle eine Probeklausur mit 6 bis 8 Aufgaben wie in einer echten Klassenarbeit, von leicht nach schwer. "
        "Jede Aufgabe genau so:\nAUFGABE: <Aufgabenstellung>\nLÖSUNG: <Musterlösung>",
        1400,
        (("AUFGABE", "TASK", "QUESTION"), ("LÖSUNG", "LOESUNG", "SOLUTION", "ANSWER"), "question", "answer"),
    ),
}

SYSTEM_PROMPT = (
    "Du hilfst einer Schülerin oder einem Schüler, sich auf eine Klassenarbeit vorzubereiten. "
    "Nutze nur das mitgegebene Material, erfinde keine Inhalte. Halte dich genau an das verlangte Format."
)

NO_MODEL = "Für das Erstellen braucht es ein KI-Modell. Lade eines unter „KI & Modelle“."

MODE_FULL = "full"
MODE_SUMMARY = "summary"
SUMMARY_KEY = "notebook_summary"
SUMMARY_CHAR_LIMIT = 1500
SUMMARY_PROMPT = (
    "Fasse das Unterrichtsmaterial in höchstens 8 knappen Stichpunkten zusammen: Themen, Regeln, Formeln, "
    "Fachbegriffe. Nur was im Material steht, nichts erfinden. Antworte auf Deutsch."
)


def selected_documents(db: Session, user_id: int, exam: Exam) -> list[Document]:
    """Gewaehltes Material; ohne Auswahl alle Dokumente des Klausurfachs.

    shortcut: "nichts gewaehlt" und "alles abgewaehlt" sind dasselbe (beides Fach-Vorauswahl);
    ein eigenes Flag erst, wenn jemand bewusst ohne Material arbeiten will.
    """
    linked = db.scalars(
        select(Document)
        .join(ExamDocument, ExamDocument.document_id == Document.id)
        .options(joinedload(Document.subject))
        .where(ExamDocument.exam_id == exam.id, Document.user_id == user_id)
        .order_by(Document.material_date, Document.id)
    ).all()
    if linked or exam.subject_id is None:
        return list(linked)
    return list(
        db.scalars(
            select(Document)
            .options(joinedload(Document.subject))
            .where(Document.user_id == user_id, Document.subject_id == exam.subject_id)
            .order_by(Document.material_date, Document.id)
        ).all()
    )


def set_documents(
    db: Session, user_id: int, exam: Exam, document_ids: Sequence[int], summary_ids: Sequence[int] = ()
) -> None:
    """Ersetzt die Auswahl. Ein fremdes Dokument ist ``LookupError`` (404, nicht 403).

    ``summary_ids`` ausserhalb von ``document_ids`` werden ignoriert: abgewaehlt schlaegt Kurzfassung.
    """
    wanted = set(document_ids)
    owned = set(db.scalars(select(Document.id).where(Document.user_id == user_id, Document.id.in_(wanted))).all())
    if owned != wanted:
        raise LookupError("document")
    short = set(summary_ids)
    db.execute(delete(ExamDocument).where(ExamDocument.exam_id == exam.id))
    db.add_all(
        ExamDocument(
            exam_id=exam.id, document_id=doc_id, user_id=user_id, mode=MODE_SUMMARY if doc_id in short else MODE_FULL
        )
        for doc_id in sorted(wanted)
    )


def summary_ids(db: Session, exam: Exam) -> set[int]:
    """Dokumente dieser Klassenarbeit, die nur als Kurzfassung mitgehen. Die Fach-Vorauswahl hat keine."""
    return set(
        db.scalars(
            select(ExamDocument.document_id).where(ExamDocument.exam_id == exam.id, ExamDocument.mode == MODE_SUMMARY)
        ).all()
    )


def _full_text(db: Session, document: Document) -> str:
    chunks = db.scalars(
        select(DocumentChunk.content).where(DocumentChunk.document_id == document.id).order_by(DocumentChunk.chunk_index)
    ).all()
    return "\n".join(chunks).strip()


def document_summary(db: Session, document: Document, generate: Generator) -> str | None:
    """Kurzfassung eines Dokuments, einmal erzeugt und am Dokument gemerkt.

    Gemerkt mit einer Pruefsumme des Texts: aendert ein erneuter Abruf (WebDAV,
    Ordner) den Inhalt, entsteht die Kurzfassung neu. ``None`` = kein Modell.
    """
    text = _full_text(db, document)
    if not text:
        return ""
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    cached = (document.extra_data or {}).get(SUMMARY_KEY)
    if isinstance(cached, dict) and cached.get("hash") == digest and isinstance(cached.get("text"), str):
        return str(cached["text"])
    outcome = generate(SUMMARY_PROMPT, f"=== {document.filename} ===\n{text[:EXCERPT_CHAR_BUDGET]}", 300)
    if outcome is None:
        return None
    summary = outcome[0].strip()[:SUMMARY_CHAR_LIMIT]
    # Neues dict zuweisen, sonst merkt SQLAlchemy die Aenderung an der JSON-Spalte nicht.
    document.extra_data = {**(document.extra_data or {}), SUMMARY_KEY: {"hash": digest, "text": summary}}
    return summary


def material_text(
    db: Session,
    documents: Sequence[Document],
    budget: int = EXCERPT_CHAR_BUDGET,
    summaries: Mapping[int, str] | None = None,
) -> str:
    """Das Material fuer den Prompt. Kurzfassungen gehen ganz mit, der Rest teilt sich das uebrige Budget."""
    if not documents:
        return ""
    summaries = summaries or {}
    full = [document for document in documents if document.id not in summaries]
    rest = budget - sum(len(summaries[document.id]) for document in documents if document.id in summaries)
    share = max(rest, 0) // len(full) if full else 0
    blocks: list[str] = []
    for document in documents:
        short = document.id in summaries
        text = summaries[document.id] if short else _full_text(db, document)[:share]
        if text:
            label = material_label(document)
            suffix = " (Kurzfassung)" if short else ""
            blocks.append(f"--- {document.filename}{f' [{label}]' if label else ''}{suffix} ---\n{text}")
    return "\n\n".join(blocks)


_MARKER = re.compile(r"^[\s\-*\d.)]*\**([A-Za-zÄÖÜäöü]+)\**\s*[:：]\s*(.*)$")


def parse_pairs(raw: str, first: Sequence[str], second: Sequence[str], keys: tuple[str, str]) -> list[dict[str, str]]:
    """Zeilenprotokoll -> Paare. Folgezeilen gehoeren zum letzten Feld; unvollstaendige Paare fallen raus."""
    items: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    field: str | None = None
    for line in raw.splitlines():
        match = _MARKER.match(line)
        marker = match.group(1).upper() if match else ""
        if match and marker in first:
            current = {keys[0]: match.group(2).strip()}
            items.append(current)
            field = keys[0]
        elif match and marker in second and current is not None:
            current[keys[1]] = match.group(2).strip()
            field = keys[1]
        elif current is not None and field is not None and line.strip():
            current[field] = f"{current[field]}\n{line.strip()}".strip()
    return [item for item in items if item.get(keys[0]) and item.get(keys[1])]


def create(
    db: Session,
    user_id: int,
    exam: Exam,
    kind: str,
    generate: Generator,
    language: Language,
) -> ExamCreation:
    spec = KINDS[kind]
    documents = selected_documents(db, user_id, exam)
    short = summary_ids(db, exam)
    summaries: dict[int, str] = {}
    for document in documents:
        if document.id in short:
            summary = document_summary(db, document, generate)
            if summary is None:
                raise NotebookError(NO_MODEL)
            summaries[document.id] = summary
    material = material_text(db, documents, summaries=summaries)
    if not material:
        raise NotebookError("Zu dieser Klassenarbeit gibt es noch kein lesbares Material")
    prompt = f"Klassenarbeit: {exam.title}\n\n{spec.task}\n\n=== Material ===\n{material}"
    outcome = generate(f"{SYSTEM_PROMPT} {reply_instruction(language)}", prompt, spec.max_tokens)
    if outcome is None:
        raise NotebookError(NO_MODEL)
    raw, source = outcome
    items = None
    if spec.pair is not None:
        items = parse_pairs(raw, spec.pair[0], spec.pair[1], (spec.pair[2], spec.pair[3]))
        if not items:
            raise NotebookError("Das Modell hat kein brauchbares Ergebnis geliefert. Bitte noch einmal versuchen.")
    creation = ExamCreation(user_id=user_id, exam_id=exam.id, kind=kind, body=raw.strip(), items=items, source=source)
    db.add(creation)
    db.flush()
    return creation


def ensure_topics(db: Session, user_id: int, exam: Exam) -> int:
    """Legt fuer jedes erkannte Thema des Materials ein Thema im Klausurfach an. Liefert die Zahl neuer Themen."""
    if exam.subject_id is None:
        raise NotebookError("Die Klassenarbeit braucht ein Fach, damit ein Lernplan entstehen kann")
    created = 0
    for document in selected_documents(db, user_id, exam):
        name = (document.material_topic or "").strip()[:200]
        if not name:
            continue
        topic = db.scalars(
            select(Topic).where(Topic.user_id == user_id, Topic.subject_id == exam.subject_id, Topic.name == name)
        ).first()
        if topic is None:
            topic = Topic(user_id=user_id, subject_id=exam.subject_id, name=name)
            db.add(topic)
            db.flush()
            created += 1
        if db.get(TopicDocument, (user_id, topic.id, document.id)) is None:
            db.add(TopicDocument(user_id=user_id, topic_id=topic.id, document_id=document.id))
    db.flush()
    # Ohne erkanntes Thema waere der Generator ein 422; die Klassenarbeit selbst ist dann das eine Thema.
    if db.scalars(select(Topic.id).where(Topic.user_id == user_id, Topic.subject_id == exam.subject_id)).first() is None:
        db.add(Topic(user_id=user_id, subject_id=exam.subject_id, name=exam.title[:200]))
        db.flush()
        created += 1
    return created
