"""Die Antworten von ``/api/v1/models``: Katalog, Größe, RAM-Schätzung, Downloadstand.

Drei Regeln prägen die Felder:

* **Nichts wird erfunden.** Fehlt eine Angabe, ist sie ``null`` und die
  Oberfläche sagt "unbekannt". Es gibt keinen geratenen Wert, der wie eine
  Tatsache aussieht.
* **Größe und RAM-Bedarf sind nicht dasselbe.** ``size_bytes`` ist die
  Downloadgröße aus den Metadaten des Herstellers; ``ram_estimate_bytes`` ist
  eine daraus abgeleitete Größenordnung, und ``ram_is_estimate`` steht
  konstant auf ``true``. Wer die Zahl liest, soll wissen, dass sie eine Rechnung
  ist - deshalb steht ``ram_basis`` als deutscher Satz daneben.
* **Der Ablagepfad kommt nicht in die Antwort.** Ein Container-Pfad sagt dem
  Browser nichts und legt die Verzeichnisstruktur offen; siehe
  ``app.services.backups`` mit derselben Begründung für den Programmnamen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import UTCResponseModel

ModelTask = Literal["text_generation", "speech_to_text", "text_to_speech", "embedding", "image_to_text"]
ModelStatus = Literal["not_downloaded", "downloading", "installed", "interrupted", "failed"]


class ModelFileRead(UTCResponseModel):
    """Eine Datei des Modells: Name, Größe und die erwartete Prüfsumme.

    ``url`` wird bewusst **nicht** ausgeliefert. Es ist eine Adresse im
    Internet, und sie hat in einer Nutzeroberfläche nichts verloren - der
    Downloader holt sie aus dem Katalog, und nur der Katalog darf das.
    """

    name: str
    size_bytes: int
    sha256: str


class ModelStatusRead(UTCResponseModel):
    """Der Downloadstand, wie er aus der Ablage und dem laufenden Thread kommt."""

    status: ModelStatus
    bytes_downloaded: int
    bytes_total: int
    progress_percent: float
    speed_bytes_per_second: float | None = None
    current_file: str | None = None
    verified: bool = False
    message: str
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ModelRead(UTCResponseModel):
    """Ein Katalogeintrag mit seinen Metadaten, seiner Größe und seinem Stand."""

    id: str
    label: str
    task: ModelTask
    family: str
    description: str
    license: str
    source_repository: str
    source_page: str
    parameter_count: int | None = None
    quantization: str | None = None
    context_length: int | None = None
    #: Downloadgröße aus den Metadaten, aufsummiert über alle Dateien.
    size_bytes: int
    size_is_estimate: bool = False
    #: Aus den Metadaten abgeleiteter RAM-Bedarf zur Laufzeit. Immer eine
    #: Schätzung, und ``ram_is_estimate`` sagt das dem Leser auch.
    ram_estimate_bytes: int
    ram_estimate_gib: float
    ram_is_estimate: bool = True
    ram_basis: str
    files: list[ModelFileRead] = Field(default_factory=list)
    status: ModelStatusRead


class ModelStorageRead(UTCResponseModel):
    """Ob die Ablage benutzbar ist und wie viel Platz übrig ist."""

    available: bool
    writable: bool
    free_bytes: int | None = None
    message: str


class ModelCatalogRead(UTCResponseModel):
    """Antwort von ``GET /api/v1/models``: der Katalog und die Ablage.

    ``shared_installation`` ist bewusst ``true`` und steht hier, damit die
    Oberfläche die Lage nicht errät: Modelle sind Installationsbestand, kein
    Nutzerdatum. Wer eigene Modelle je Nutzer braucht, bekommt eine Tabelle -
    siehe ``app.services.model_catalog``.
    """

    models: list[ModelRead] = Field(default_factory=list)
    storage: ModelStorageRead
    shared_installation: bool = True
