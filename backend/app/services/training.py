"""Trainingsbeispiele sammeln - die Vorstufe zum Feintuning je Nutzer.

Was hier passiert, und was (noch) nicht:

* **Nur mit Freigabe.** ``settings.preferences["train_on_conversations"]``,
  Standard aus, jeder unlesbare Zustand bedeutet aus - dasselbe Muster wie die
  Notenfreigabe. Ohne Freigabe wird kein einziges Beispiel geschrieben.
* **Ein Beispiel ist genau das, was das Modell gesehen und geantwortet hat**:
  Systemprompt, Nutzerprompt, Rohantwort. Daraus wird spaeter ein
  Chat-Datensatz fuer LoRA, ohne den Prompt nachbauen zu muessen.
* **Signale sind Tatsachen, das Urteil wird gerechnet** (:func:`example_label`).
  Ein eingetragener Vorschlag, ein verworfener, eine geloeschte Notiz und ein
  Daumen stehen nebeneinander; keines ueberschreibt das andere.
* **Kein Training.** Das kommt als eigener Schritt (lokal auf starken
  Rechnern, siehe ``training_hardware.py``, spaeter optional in der Cloud) und
  liest nur den Datensatz aus ``training_data.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.assistant import MEMORY_SOURCE_ASSISTANT, RATING_BAD, RATING_GOOD, AssistantMemory, TrainingExample
from app.db.entities import UserSettings

PREFERENCE_KEY: Final[str] = "train_on_conversations"
AUTO_PREFERENCE_KEY: Final[str] = "train_automatically"
MATERIALS_PREFERENCE_KEY: Final[str] = "train_on_materials"

#: Obergrenze je Nutzer. Ein Beispiel traegt den ganzen Prompt (bis ~20 KB mit
#: Dokumentauszuegen); 2000 sind rund 40 MB - genug fuer LoRA, und die Platte
#: der Lernenden ist kein Archiv. Die aeltesten fallen zuerst heraus.
MAX_EXAMPLES_PER_USER: Final[int] = 2000

LABEL_GOOD: Final[str] = "good"
LABEL_BAD: Final[str] = "bad"
#: Eingetragen, aber erst nachdem das Backend das Datum korrigiert hat: das
#: Ziel steht in ``applied_actions``, die Rohantwort selbst war falsch.
LABEL_CORRECTED: Final[str] = "corrected"
LABELS: Final[tuple[str, ...]] = (LABEL_GOOD, LABEL_BAD, LABEL_CORRECTED)

_QUESTION_RE = re.compile(r"^Person:\s*(.*)$", re.MULTILINE)


# --- Freigabe ----------------------------------------------------------------


def _flag(db: Session, user_id: int, key: str) -> bool:
    settings = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if settings is None or not isinstance(settings.preferences, dict):
        return False
    wert = settings.preferences.get(key)
    return wert if isinstance(wert, bool) else False


def _set_flag(db: Session, user_id: int, key: str, enabled: bool) -> bool:
    settings = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if settings is None:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
    einstellungen: dict[str, Any] = dict(settings.preferences) if isinstance(settings.preferences, dict) else {}
    einstellungen[key] = enabled
    settings.preferences = einstellungen
    db.flush()
    return enabled


def training_enabled(db: Session, user_id: int) -> bool:
    """Darf gesammelt werden? Standard und Fehlerfall: nein."""
    return _flag(db, user_id, PREFERENCE_KEY)


def set_training_enabled(db: Session, user_id: int, enabled: bool) -> bool:
    """Setzt die Freigabe; die anderen Schluessel in ``preferences`` bleiben stehen.

    Ein Widerruf loescht die gesammelten Beispiele **nicht** - ein versehentlicher
    Klick soll nicht Wochen an Daten kosten. Dafuer gibt es "Alle loeschen".
    """
    return _set_flag(db, user_id, PREFERENCE_KEY, enabled)


def auto_training_enabled(db: Session, user_id: int) -> bool:
    """Darf im Hintergrund trainiert werden? Eigene Freigabe, Standard aus.

    Sammeln und Trainieren sind zwei Entscheidungen: wer sammelt, will nicht
    zwingend, dass die Grafikkarte nachts anlaeuft.
    """
    return _flag(db, user_id, AUTO_PREFERENCE_KEY)


def set_auto_training(db: Session, user_id: int, enabled: bool) -> bool:
    return _set_flag(db, user_id, AUTO_PREFERENCE_KEY, enabled)


def materials_training_enabled(db: Session, user_id: int) -> bool:
    """Duerfen Aufschriebe und Arbeitsblaetter ins Training? Eigene Freigabe, Standard aus.

    Gespraeche und Unterrichtsmaterial sind verschiedene Inhalte: wer seine
    Antworten sammeln laesst, hat damit nicht seine Hefte freigegeben.
    """
    return _flag(db, user_id, MATERIALS_PREFERENCE_KEY)


def set_materials_training(db: Session, user_id: int, enabled: bool) -> bool:
    return _set_flag(db, user_id, MATERIALS_PREFERENCE_KEY, enabled)


# --- Sammeln -----------------------------------------------------------------


def record_example(
    db: Session,
    user_id: int,
    *,
    system_prompt: str,
    prompt: str,
    completion: str,
    source: str,
    model: str | None,
    server_adjusted: bool = False,
) -> TrainingExample:
    """Legt ein Beispiel an (``flush``, kein ``commit``) und kuerzt auf die Obergrenze."""
    example = TrainingExample(
        user_id=user_id,
        system_prompt=system_prompt,
        prompt=prompt,
        completion=completion,
        source=source,
        model=model[:160] if model else None,
        server_adjusted=server_adjusted,
        applied_actions=[],
    )
    db.add(example)
    db.flush()
    _prune(db, user_id)
    return example


def _prune(db: Session, user_id: int) -> None:
    overflow = (
        db.scalar(select(func.count()).select_from(TrainingExample).where(TrainingExample.user_id == user_id)) or 0
    ) - MAX_EXAMPLES_PER_USER
    if overflow <= 0:
        return
    oldest = db.scalars(
        select(TrainingExample.id)
        .where(TrainingExample.user_id == user_id)
        .order_by(TrainingExample.created_at.asc(), TrainingExample.id.asc())
        .limit(overflow)
    ).all()
    db.execute(delete(TrainingExample).where(TrainingExample.id.in_(oldest)))


def owned_example(db: Session, user_id: int, example_id: int | None) -> TrainingExample | None:
    """Ein fremdes und ein unbekanntes Beispiel sind dasselbe: ``None``."""
    if example_id is None:
        return None
    example = db.get(TrainingExample, example_id)
    if example is None or example.user_id != user_id:
        return None
    return example


def record_applied(example: TrainingExample, action: dict[str, Any]) -> None:
    # Neue Liste statt append: JSON-Spalten erkennen eine Mutation an Ort und Stelle nicht.
    example.applied_actions = [*example.applied_actions, action]


def record_rejected(example: TrainingExample) -> None:
    example.rejected_actions += 1


def set_rating(example: TrainingExample, rating: str | None) -> None:
    if rating not in (None, RATING_GOOD, RATING_BAD):
        raise ValueError(f"Unbekannte Bewertung: {rating}")
    example.user_rating = rating


def record_memory_deleted(db: Session, memory: AssistantMemory) -> None:
    """Eine geloeschte, vom Modell gemerkte Notiz: die Antwort lag daneben.

    Von Hand eingetragene Notizen sagen nichts ueber das Modell.
    """
    if memory.source != MEMORY_SOURCE_ASSISTANT or memory.training_example_id is None:
        return
    example = owned_example(db, memory.user_id, memory.training_example_id)
    if example is not None:
        example.memory_deleted = True


# --- Auswerten ---------------------------------------------------------------


def example_label(example: TrainingExample) -> str | None:
    """Das Urteil ueber ein Beispiel. ``None`` heisst: kein Signal, nicht "gut".

    Reihenfolge: ein ausdruecklicher Daumen schlaegt alles; dann wiegt jeder
    Hinweis auf einen Fehler schwerer als ein Eintrag, weil ein eingetragener
    Vorschlag in derselben Antwort eine falsche Notiz nicht gut macht.
    """
    if example.user_rating == RATING_GOOD:
        return LABEL_GOOD
    if example.user_rating == RATING_BAD or example.memory_deleted:
        return LABEL_BAD
    applied = bool(example.applied_actions)
    if example.rejected_actions and not applied:
        return LABEL_BAD
    if applied and (example.server_adjusted or example.rejected_actions):
        return LABEL_CORRECTED
    if applied:
        return LABEL_GOOD
    return None


def question_of(prompt: str) -> str:
    """Die letzte Frage aus dem Prompt - fuer die Liste in der Oberflaeche."""
    fragen = _QUESTION_RE.findall(prompt)
    return fragen[-1].strip() if fragen else ""


@dataclass(frozen=True, slots=True)
class TrainingStats:
    total: int
    good: int
    bad: int
    corrected: int
    unlabeled: int


def training_stats(db: Session, user_id: int) -> TrainingStats:
    counts = dict.fromkeys(LABELS, 0)
    unlabeled = 0
    examples = db.scalars(select(TrainingExample).where(TrainingExample.user_id == user_id)).all()
    for example in examples:
        label = example_label(example)
        if label is None:
            unlabeled += 1
        else:
            counts[label] += 1
    return TrainingStats(
        total=len(examples),
        good=counts[LABEL_GOOD],
        bad=counts[LABEL_BAD],
        corrected=counts[LABEL_CORRECTED],
        unlabeled=unlabeled,
    )


def list_examples(db: Session, user_id: int, *, limit: int = 50, offset: int = 0) -> list[TrainingExample]:
    return list(
        db.scalars(
            select(TrainingExample)
            .where(TrainingExample.user_id == user_id)
            .order_by(TrainingExample.created_at.desc(), TrainingExample.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


def delete_all_examples(db: Session, user_id: int) -> int:
    ids = db.scalars(select(TrainingExample.id).where(TrainingExample.user_id == user_id)).all()
    if ids:
        db.execute(delete(TrainingExample).where(TrainingExample.id.in_(ids)))
    return len(ids)
