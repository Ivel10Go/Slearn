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
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import as_utc
from app.db.assistant import TrainingExample
from app.db.identity import Subject
from app.services import training as training_service
from app.services.assistant import (
    ACTION_EVENT,
    ACTION_EXAM,
    ACTION_HOMEWORK,
    DEFAULT_EVENT_MINUTES,
    DEFAULT_SCHOOL_TIME,
    is_action_line,
    parse_reply,
)

#: Jedes zehnte Beispiel (nach Id) ist Pruefung, nie Training.
EVAL_BUCKETS: Final[int] = 10

TRAIN_FILE: Final[str] = "train.jsonl"
EVAL_FILE: Final[str] = "eval.jsonl"
PREFERENCE_FILE: Final[str] = "preference.jsonl"
DATASET_FILE: Final[str] = "dataset.json"

#: Die Woerter, die ``ACTION_PROMPT`` dem Modell vorgibt.
_KIND_WORDS: Final[dict[str, str]] = {
    ACTION_EXAM: "pruefung",
    ACTION_HOMEWORK: "hausaufgabe",
    ACTION_EVENT: "termin",
}


def is_eval_example(example_id: int) -> bool:
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

    @property
    def fingerprint(self) -> str:
        """Gleiche Daten, gleicher Wert - unabhaengig davon, wann gebaut wurde."""
        digest = hashlib.sha256()
        for name, rows in (("train", self.train), ("eval", self.eval), ("preference", self.preference)):
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


def load_dataset(db: Session, user_id: int, zone: ZoneInfo) -> TrainingDataset:
    examples = db.scalars(select(TrainingExample).where(TrainingExample.user_id == user_id)).all()
    subjects = {
        subject.id: subject.name for subject in db.scalars(select(Subject).where(Subject.user_id == user_id)).all()
    }
    return build_dataset(examples, subjects, zone)


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
    summary = {
        "fingerprint": dataset.fingerprint,
        "example_ids": dataset.example_ids,
        "counts": {"train": len(dataset.train), "eval": len(dataset.eval), "preference": len(dataset.preference)},
    }
    _write_atomic(directory / DATASET_FILE, json.dumps(summary, ensure_ascii=False, indent=2))
    return directory
