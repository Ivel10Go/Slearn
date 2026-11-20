"""Welche Modelle passen zu diesem Rechner? Die Empfehlung oben auf der KI-Seite.

Reine Funktion :func:`recommend` ueber gemessenen Werten (getestet ohne echte
Hardware); gemessen wird mit den Helfern aus ``training_hardware``.

Die Regeln sollen in einem Satz erklaerbar sein:

* **Textmodell**: das groesste, dessen RAM-Schaetzung in 40 % des Arbeitsspeichers
  passt - der Rest gehoert Betriebssystem, Browser und den anderen Modellen.
  Passt keines, das kleinste.
* **Bedeutungssuche** (bge-m3) ab 8 GB, **Bildmodell** fuer Fotos und Scans ab 12 GB.
* **Spracherkennung**: ab 12 GB das genaue Whisper, sonst das kleine. Auf macOS
  keins - dort gibt es kein fertiges ``whisper-server`` (siehe AGENTS.md).
* **Stimme**: immer eine in der Sprache der Oberflaeche, sie ist klein.

Die Grafikkarte steht nur in der Beschreibung: ob ``llama-server`` sie nutzt,
haengt vom geladenen Laufzeit-Paket ab, und eine Empfehlung soll nichts versprechen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from app.services.model_catalog import ModelSpec, ram_estimate
from app.services.training_hardware import GpuInfo, nvidia_gpus, total_ram_gb

#: Ohne Messung wird mit einem typischen Schul-Laptop gerechnet.
ASSUMED_RAM_GB: Final[float] = 8.0
TEXT_RAM_SHARE: Final[float] = 0.4

PURPOSES: Final[dict[str, str]] = {
    "text_generation": "Antworten im Assistenten, Lernzettel und Einordnen der Unterlagen",
    "embedding": "Suche nach Bedeutung in deinen Unterlagen",
    "image_to_text": "Fotos und Scans abschreiben",
    "speech_to_text": "Spracheingabe",
    "voice": "Antworten vorlesen",
}


@dataclass(frozen=True, slots=True)
class Pick:
    kind: str  # "model" | "voice"
    id: str
    task: str


def _by_task(specs: Sequence[ModelSpec], task: str) -> list[ModelSpec]:
    return sorted((spec for spec in specs if spec.task == task), key=lambda spec: spec.size_bytes)


def recommend(ram_gb: float | None, system: str, specs: Sequence[ModelSpec], voice_id: str | None) -> list[Pick]:
    ram = ram_gb or ASSUMED_RAM_GB
    picks: list[Pick] = []
    texts = _by_task(specs, "text_generation")
    if texts:
        fitting = [spec for spec in texts if ram_estimate(spec).gib <= ram * TEXT_RAM_SHARE]
        picks.append(Pick("model", (fitting[-1] if fitting else texts[0]).id, "text_generation"))
    embedders = _by_task(specs, "embedding")
    if embedders and ram >= 8:
        picks.append(Pick("model", embedders[0].id, "embedding"))
    visions = _by_task(specs, "image_to_text")
    if visions and ram >= 12:
        picks.append(Pick("model", visions[0].id, "image_to_text"))
    whispers = _by_task(specs, "speech_to_text")
    if whispers and system != "Darwin":
        picks.append(Pick("model", (whispers[-1] if ram >= 12 else whispers[0]).id, "speech_to_text"))
    if voice_id:
        picks.append(Pick("voice", voice_id, "voice"))
    return picks


@lru_cache(maxsize=1)
def measure() -> tuple[float | None, tuple[GpuInfo, ...]]:
    """Einmal je Prozess messen - ``nvidia-smi`` ist ein eigener Prozess, die Hardware aendert sich nicht."""
    return total_ram_gb(), tuple(nvidia_gpus())


def describe_hardware(ram_gb: float | None, gpus: Sequence[GpuInfo]) -> str:
    """"16 GB Arbeitsspeicher · NVIDIA GeForce RTX 4060 (8 GB)" - oder der Hinweis, dass geschaetzt wird."""
    parts = [f"{round(ram_gb)} GB Arbeitsspeicher" if ram_gb else "Arbeitsspeicher unbekannt, gerechnet mit 8 GB"]
    parts.extend(f"{gpu.name} ({round(gpu.memory_gb)} GB)" for gpu in gpus)
    return " · ".join(parts)
