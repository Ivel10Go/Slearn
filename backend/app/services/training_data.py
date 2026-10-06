"""Aus gesammelten Beispielen einen Trainingsdatensatz machen.

Der Trainer (lokal oder spaeter in der Cloud) bekommt nur Dateien: JSONL rein,
Adapter raus. Was hier entschieden wird:

* **Das Ziel kommt aus den Signalen, nicht aus der Rohantwort.** Bei einem
  ``corrected``-Beispiel lag das Modell daneben; trainiert wird auf die
  ``AKTION``-Zeile mit den Werten, die tatsaechlich eingetragen wurden. Damit
  haengt der Datensatz nicht am Modell, das ihn erzeugt hat - nach einem
  Modellwechsel wird mit denselben Daten neu trainiert.
* **Schlechte Beispiele gehen nicht ins SFT.** Ohne eingetragenen Wert gibt es
  kein richtiges Ziel. Ein korrigiertes Beispiel liefert zusaetzlich ein
  Praeferenzpaar (eingetragen schlaegt roh) fuer DPO.
* **Die Pruefmenge ist fest.** Ob ein Beispiel zur Pruefung gehoert, haengt nur
  an seiner Id. Sonst laege ein Beispiel in der einen Runde in der Pruefung und
  in der naechsten im Training - und die Bewertung waere wertlos.

Mit der Freigabe ``train_on_materials`` kommen Aufschriebe und Arbeitsblaetter
dazu - als **Verhalten**, nicht als Wissen. Die Fakten bleiben in den Dateien
(loeschbar, mit Quelle); gelernt wird, wie man damit umgeht:

* **Stil** (:func:`style_rows`): Eingabe ist der eigene Aufschrieb *ohne* Form
  (keine Ueberschriften, keine Aufzaehlung, keine Hervorhebung), Ziel ist das
  Original. So lernt das Modell den Aufbau des Hefts - Ueberschriften,
  Stichpunkte, "Merke"-Kaesten, Abkuerzungen -, ohne Inhalt zu erfinden: der
  Inhalt steht immer in der Eingabe.
* **Fragen zum Material** (:func:`qa_tasks`, RAFT): je Absatz eine Vorlage im
  echten Prompt-Format des Assistenten, mit dem passenden Auszug und zwei
  unpassenden. Frage und Antwort erzeugt der Trainer auf der Grafikkarte; er
  behaelt nur Antworten, die sich aus dem passenden Auszug belegen lassen.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import as_utc
from app.db.assistant import TrainingExample
from app.db.content import Document, DocumentChunk
from app.db.identity import Subject, User
from app.services import training as training_service
from app.services.assistant import (
    ACTION_EVENT,
    ACTION_EXAM,
    ACTION_HOMEWORK,
    DEFAULT_EVENT_MINUTES,
    DEFAULT_SCHOOL_TIME,
    ROLE_USER,
    SYSTEM_PROMPT,
    ChatTurn,
    build_context,
    build_user_prompt,
    is_action_line,
    material_label,
    parse_reply,
)
from app.services.documents import DocumentSource

#: Jedes zehnte Beispiel (nach Id) ist Pruefung, nie Training.
EVAL_BUCKETS: Final[int] = 10

TRAIN_FILE: Final[str] = "train.jsonl"
QA_FILE: Final[str] = "qa_tasks.jsonl"
#: Platzhalter in der Vorlage: hier setzt der Trainer die erzeugte Frage ein.
QUESTION_PLACEHOLDER: Final[str] = "<<FRAGE>>"
#: Material, das als Verhalten gelernt wird. "sonstiges" (etwa ein Lebenslauf) nicht.
TRAINED_KINDS: Final[tuple[str, ...]] = ("aufschrieb", "arbeitsblatt", "loesung")
#: Hoechstens so viele Absaetze je Runde - die Fragen erzeugt der Trainer einzeln.
MAX_MATERIAL_CHUNKS: Final[int] = 300
STYLE_MIN_CHARS: Final[int] = 200
QA_MIN_CHARS: Final[int] = 150
STYLE_REQUEST: Final[str] = (
    "Mach aus Auszug [1] einen ordentlichen Eintrag für mein Heft – so, wie ich meine Aufschriebe schreibe."
)
EVAL_FILE: Final[str] = "eval.jsonl"
PREFERENCE_FILE: Final[str] = "preference.jsonl"
DATASET_FILE: Final[str] = "dataset.json"

#: Die Woerter, die ``ACTION_PROMPT`` dem Modell vorgibt.
_KIND_WORDS: Final[dict[str, str]] = {
    ACTION_EXAM: "pruefung",
    ACTION_HOMEWORK: "hausaufgabe",
    ACTION_EVENT: "termin",
}


def is_eval_example(example_id: int | str) -> bool:
    """Pruefung oder Training - nur aus der Id. Material traegt ein "m" davor, eigener Namensraum."""
    digest = hashlib.sha256(str(example_id).encode()).digest()
    return int.from_bytes(digest[:4], "big") % EVAL_BUCKETS == 0


# --- Ziel aus den Signalen ---------------------------------------------------


def _norm(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _parse_moment(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def action_line(
    applied: Mapping[str, Any], original: Mapping[str, Any], subjects: Mapping[int, str], zone: ZoneInfo
) -> str | None:
    """Die ``AKTION``-Zeile, die das Modell haette schreiben sollen.

    Grundlage ist die Zeile des Modells; ueberschrieben wird, was eingetragen
    wurde. Eine Uhrzeit, die nur der Standard ist (08:00, eine Stunde Dauer),
    bleibt weg, wenn das Modell keine geschrieben hatte - das Protokoll sagt
    "lass uhrzeit weg, wenn keine genannt wurde".
    """
    kind = applied.get("kind")
    start = _parse_moment(applied.get("starts_at"))
    title = str(applied.get("title") or "").strip()
    if kind not in _KIND_WORDS or start is None or not title:
        return None
    start_local = start.astimezone(zone)
    payload: dict[str, Any] = {"typ": _KIND_WORDS[kind], "titel": title}

    subject_id = applied.get("subject_id")
    fach = subjects.get(subject_id) if isinstance(subject_id, int) else None
    if fach is None and original.get("fach"):
        fach = str(original["fach"])
    if fach:
        payload["fach"] = fach
    payload["datum"] = start_local.date().isoformat()

    had_time = bool(original.get("uhrzeit") or original.get("time"))
    if not applied.get("all_day"):
        clock = start_local.time().replace(second=0, microsecond=0)
        if had_time or clock != DEFAULT_SCHOOL_TIME or kind == ACTION_EVENT:
            payload["uhrzeit"] = f"{clock:%H:%M}"
        end = _parse_moment(applied.get("ends_at"))
        if kind == ACTION_EVENT and end is not None:
            standard_end = end - start == timedelta(minutes=DEFAULT_EVENT_MINUTES)
            if not standard_end or original.get("ende") or original.get("end"):
                payload["ende"] = f"{end.astimezone(zone):%H:%M}"

    beschreibung = applied.get("description") or original.get("beschreibung")
    if beschreibung:
        payload["beschreibung"] = str(beschreibung)
    return "AKTION " + json.dumps(payload, ensure_ascii=False)


def _pair_with_originals(
    applied: Sequence[Mapping[str, Any]], originals: Sequence[Mapping[str, Any]]
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Jedem Eintrag die Modellzeile zuordnen: erst ueber den Titel, sonst der Reihe nach."""
    remaining = list(originals)
    pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for item in applied:
        title = _norm(item.get("title"))
        match = next((o for o in remaining if _norm(o.get("titel") or o.get("title")) == title), None)
        if match is None and remaining:
            match = remaining[0]
        if match is not None:
            remaining.remove(match)
        pairs.append((item, match or {}))
    return pairs


def corrected_completion(example: TrainingExample, subjects: Mapping[int, str], zone: ZoneInfo) -> str:
    """Die Rohantwort, deren ``AKTION``-Zeilen durch die eingetragenen ersetzt sind.

    Verworfene Vorschlaege fallen dabei weg. Die neuen Zeilen stehen dort, wo
    die erste alte stand; Text und ``MERKE`` bleiben, wie sie waren.
    """
    if not example.applied_actions:
        return example.completion
    originals = parse_reply(example.completion).actions
    new_lines = [
        line
        for applied, original in _pair_with_originals(example.applied_actions, originals)
        if (line := action_line(applied, original, subjects, zone)) is not None
    ]
    out: list[str] = []
    inserted = False
    for line in example.completion.splitlines():
        if is_action_line(line):
            if not inserted:
                out.extend(new_lines)
                inserted = True
            continue
        out.append(line)
    if not inserted:
        out.extend(new_lines)
    return "\n".join(out).strip()


# --- Datensatz ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrainingDataset:
    train: list[dict[str, Any]]
    eval: list[dict[str, Any]]
    preference: list[dict[str, Any]]
    #: Alle verwendeten Beispiele - eine Runde friert genau diese Liste ein.
    example_ids: list[int] = field(default_factory=list)
    #: Vorlagen fuer Fragen zum Material; der Trainer erzeugt Frage und Antwort.
    qa_tasks: list[dict[str, Any]] = field(default_factory=list)
    #: Alle verwendeten Materialabsaetze - daran erkennt die Automatik neues Material.
    material_chunk_ids: list[int] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        """Gleiche Daten, gleicher Wert - unabhaengig davon, wann gebaut wurde."""
        digest = hashlib.sha256()
        parts = (("train", self.train), ("eval", self.eval), ("preference", self.preference), ("qa", self.qa_tasks))
        for name, rows in parts:
            digest.update(name.encode())
            for row in rows:
                digest.update(json.dumps(row, ensure_ascii=False, sort_keys=True).encode())
        return digest.hexdigest()


def _messages(example: TrainingExample, completion: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": example.system_prompt},
        {"role": "user", "content": example.prompt},
        {"role": "assistant", "content": completion},
    ]


def build_dataset(
    examples: Sequence[TrainingExample], subjects: Mapping[int, str], zone: ZoneInfo
) -> TrainingDataset:
    train: list[dict[str, Any]] = []
    evaluation: list[dict[str, Any]] = []
    preference: list[dict[str, Any]] = []
    used: list[int] = []
    for example in sorted(examples, key=lambda e: e.id):
        label = training_service.example_label(example)
        if label not in (training_service.LABEL_GOOD, training_service.LABEL_CORRECTED):
            continue
        target = corrected_completion(example, subjects, zone)
        row = {"example_id": example.id, "label": label, "messages": _messages(example, target)}
        held_out = is_eval_example(example.id)
        (evaluation if held_out else train).append(row)
        used.append(example.id)
        if not held_out and target.strip() != example.completion.strip():
            preference.append(
                {
                    "example_id": example.id,
                    "prompt": _messages(example, "")[:2],
                    "chosen": target,
                    "rejected": example.completion,
                }
            )
    return TrainingDataset(train=train, eval=evaluation, preference=preference, example_ids=used)


# --- Material: Stil und Fragen ------------------------------------------------

_MARKER_RE = re.compile(r"^\s*(#{1,6}\s+|[-*•]\s+|\d+[.)]\s+|[a-z][.)]\s+)", re.IGNORECASE)


def has_structure(text: str) -> bool:
    """Hat der Absatz eine Form, die man lernen kann - Ueberschrift, Aufzaehlung, Hervorhebung?"""
    lines = [line for line in text.splitlines() if line.strip()]
    marked = sum(
        1 for line in lines if _MARKER_RE.match(line) or "**" in line or line.strip().lower().startswith("merke")
    )
    return len(lines) >= 3 and marked >= 2


def flatten(text: str) -> str:
    """Derselbe Inhalt ohne Form: keine Ueberschriften, Aufzaehlungen, Hervorhebungen, Zeilen."""
    words: list[str] = []
    for line in text.splitlines():
        line = _MARKER_RE.sub("", line).replace("**", "").replace("__", "").strip()
        if line:
            words.append(line)
    return " ".join(" ".join(words).split())


@dataclass(frozen=True, slots=True)
class MaterialChunk:
    chunk_id: int
    document_id: int
    filename: str
    chunk_index: int
    content: str
    kind: str
    label: str


def material_chunks(db: Session, user_id: int, limit: int = MAX_MATERIAL_CHUNKS) -> list[MaterialChunk]:
    """Die Absaetze aus Aufschrieben, Arbeitsblaettern und Loesungen - nur die eigenen."""
    rows = db.execute(
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.user_id == user_id,
            Document.user_id == user_id,
            Document.status == "indexed",
            Document.material_kind.in_(TRAINED_KINDS),
        )
        .order_by(DocumentChunk.id)
        .limit(limit)
    ).tuples()
    return [
        MaterialChunk(
            chunk_id=chunk.id,
            document_id=document.id,
            filename=document.filename,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            kind=document.material_kind or "",
            label=material_label(document),
        )
        for chunk, document in rows
    ]


def _prompt(
    context: Sequence[str],
    question: str,
    hits: Sequence[MaterialChunk],
    content_of: Callable[[MaterialChunk], str] | None = None,
) -> str:
    sources = [
        DocumentSource(
            document_id=hit.document_id,
            filename=hit.filename,
            chunk_index=hit.chunk_index,
            content=content_of(hit) if content_of else hit.content,
        )
        for hit in hits
    ]
    labels = {hit.document_id: hit.label for hit in hits}
    return build_user_prompt([ChatTurn(ROLE_USER, question)], context, [], sources, [], labels)


def style_rows(chunks: Sequence[MaterialChunk], context: Sequence[str]) -> list[dict[str, Any]]:
    """Aufschrieb ohne Form rein, Original raus - der Aufbau des Hefts als Lernziel."""
    rows: list[dict[str, Any]] = []
    for chunk in chunks:
        if chunk.kind != "aufschrieb" or len(chunk.content) < STYLE_MIN_CHARS or not has_structure(chunk.content):
            continue
        user = _prompt(context, STYLE_REQUEST, [chunk], content_of=lambda hit: flatten(hit.content))
        rows.append(
            {
                "example_id": None,
                "material_chunk_id": chunk.chunk_id,
                "label": "style",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": chunk.content},
                ],
            }
        )
    return rows


def qa_tasks(chunks: Sequence[MaterialChunk], context: Sequence[str]) -> list[dict[str, Any]]:
    """Je Absatz eine Vorlage: passender Auszug und zwei aus anderen Dokumenten, fest gemischt."""
    tasks: list[dict[str, Any]] = []
    for chunk in chunks:
        if len(chunk.content) < QA_MIN_CHARS:
            continue
        others = [other for other in chunks if other.document_id != chunk.document_id]
        rng = random.Random(chunk.chunk_id)
        distractors = rng.sample(others, k=min(2, len(others)))
        hits = [chunk, *distractors]
        rng.shuffle(hits)
        tasks.append(
            {
                "task_id": f"q{chunk.chunk_id}",
                "chunk_id": chunk.chunk_id,
                "label": chunk.label,
                "content": chunk.content,
                "distractors": [other.content for other in distractors],
                "system": SYSTEM_PROMPT,
                "user_template": _prompt(context, QUESTION_PLACEHOLDER, hits),
                "eval": is_eval_example(f"m{chunk.chunk_id}"),
            }
        )
    return tasks


def with_materials(
    dataset: TrainingDataset, chunks: Sequence[MaterialChunk], context: Sequence[str]
) -> TrainingDataset:
    train, evaluation = list(dataset.train), list(dataset.eval)
    for row in style_rows(chunks, context):
        (evaluation if is_eval_example(f"m{row['material_chunk_id']}") else train).append(row)
    return TrainingDataset(
        train=train,
        eval=evaluation,
        preference=dataset.preference,
        example_ids=dataset.example_ids,
        qa_tasks=qa_tasks(chunks, context),
        material_chunk_ids=[chunk.chunk_id for chunk in chunks],
    )


def load_dataset(
    db: Session, user_id: int, zone: ZoneInfo, *, materials: bool = False, now: datetime | None = None
) -> TrainingDataset:
    examples = db.scalars(select(TrainingExample).where(TrainingExample.user_id == user_id)).all()
    subjects = {
        subject.id: subject.name for subject in db.scalars(select(Subject).where(Subject.user_id == user_id)).all()
    }
    dataset = build_dataset(examples, subjects, zone)
    user = db.get(User, user_id)
    if not materials or user is None:
        return dataset
    # Derselbe Schulstand-Block wie im echten Gespraech - das Modell soll nicht
    # an einer Prompt-Form lernen, die es spaeter nie sieht.
    context = build_context(db, user, zone, now or datetime.now(UTC))
    return with_materials(dataset, material_chunks(db, user_id), context)


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def write_dataset(dataset: TrainingDataset, directory: Path) -> Path:
    """Schreibt die Dateien fuer den Trainer; ``dataset.json`` zuletzt.

    Ein Trainer liest nur einen Ordner mit ``dataset.json`` - fehlt die Datei,
    ist der Ordner unfertig, egal welche JSONL schon daliegen.
    """
    directory.mkdir(parents=True, exist_ok=True)
    _write_atomic(directory / TRAIN_FILE, _jsonl(dataset.train))
    _write_atomic(directory / EVAL_FILE, _jsonl(dataset.eval))
    _write_atomic(directory / PREFERENCE_FILE, _jsonl(dataset.preference))
    _write_atomic(directory / QA_FILE, _jsonl(dataset.qa_tasks))
    summary = {
        "fingerprint": dataset.fingerprint,
        "example_ids": dataset.example_ids,
        "material_chunk_ids": dataset.material_chunk_ids,
        "counts": {
            "train": len(dataset.train),
            "eval": len(dataset.eval),
            "preference": len(dataset.preference),
            "qa": len(dataset.qa_tasks),
        },
    }
    _write_atomic(directory / DATASET_FILE, json.dumps(summary, ensure_ascii=False, indent=2))
    return directory
