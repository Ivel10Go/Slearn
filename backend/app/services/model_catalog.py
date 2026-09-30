"""Der kuratierte Katalog der lokalen Modelle: was es gibt, wie groß es ist, was es an RAM braucht.

**Statisch, nicht in der Datenbank.** Für eine Handvoll kuratierter Modelle ist
das die einfachere und hier auch die richtige Antwort: keine Migration, kein
``local_model``-Anhang an der Benutzerverwaltung, und ein Test kann den ganzen
Katalog ohne Dateisystem prüfen. Was dafür bezahlt wird, steht unten bei
*Erweitern* - die Naht ist bewusst gelegt und benannt.

Ein Eintrag trägt **Metadaten**, keine ausgerechneten Zahlen:

* ``parameter_count``, ``quantization``, ``context_length`` und
  ``size_bytes`` stehen so im Katalog, wie der Hersteller sie veröffentlicht.
* Der **RAM-Bedarf wird daraus abgeleitet**, in :func:`ram_estimate`, und nicht
  als Zahl im Katalog gepflegt. Eine Tabelle mit RAM-Werten veraltet genau dann,
  wenn sie niemand mehr pflegt; eine Formel veraltet nur, wenn sich die
  Quantisierungsnamen ändern.

Der Katalog kennt **keine Inference**. Er sagt, welche Datei wie groß ist und wo
sie herkommt - wer sie lädt, entscheidet ``app.services.model_downloads``, und
wer sie öffnet, entscheidet eine spätere Welle.

Sicherheit: Der Endpunkt nimmt ausschließlich ``model_id`` entgegen, niemals eine
URL aus der Anfrage. Ein Downloader, der eine fremde URL abruft, ist eine
SSRF-Quelle, und eine Allowlist im Anfragepfad wäre die schlechtere Variante
derselben Sache. Trotzdem wird **jede** URL - auch jeder Redirect - über
:func:`validate_download_url` geprüft: Das ist die Stelle, an der ein Server uns
auf ``169.254.169.254`` oder in das Docker-Netz umleiten könnte.

Erweitern (bewusst nicht jetzt gemacht):

* **Nutzer-eigene Modelle** brauchen eine Tabelle, weil sie Nutzerdaten sind -
  ein fremdes Modell gibt dann 404 statt 403. Bis dahin gilt: Der Katalog ist
  Installationsbestand, und jeder angemeldete Nutzer sieht denselben.
* **Ein Live-Abgleich** der Größen und Prüfsummen gegen die Hugging-Face-API
  wäre die Alternative zu den fest verdrahteten Werten. Bewusst nicht gebaut:
  Der Katalog soll ohne Netz stehen, und eine still veraltete Prüfsumme ist
  ein harter Fehler mit klarer Meldung statt einer Anzeige, die von einem Lauf
  abhängt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

__all__ = [
    "ALLOWED_DOWNLOAD_HOSTS",
    "MAX_REDIRECTS",
    "MODEL_CATALOG",
    "MODEL_TASKS",
    "ModelCatalogError",
    "ModelFile",
    "ModelSpec",
    "RamEstimate",
    "bits_per_weight",
    "catalog_models",
    "is_allowed_download_host",
    "model_spec",
    "ram_estimate",
    "validate_download_url",
]


class ModelCatalogError(ValueError):
    """Der Katalog oder eine Download-Adresse ist unbrauchbar. Die Meldung ist deutsch."""


#: Aufgaben, für die ein lokales Modell geladen werden kann. Bewusst offen
#: gehalten: `text_generation` braucht llama.cpp, `speech_to_text` whisper.cpp,
#: `text_to_speech` Piper - drei verschiedene Laufzeiten, ein Katalog.
MODEL_TASKS: Final[tuple[str, ...]] = (
    "text_generation",
    "speech_to_text",
    "text_to_speech",
    "embedding",
)

#: Eine `model_id` wird zu einem Verzeichnisnamen. Der Ausdruck ist eine zweite
#: Verteidigungslinie: heute kommen die Ids aus diesem Modul, aber `..` darf
#: auch bei einer späteren Datenbankvariante nicht durchrutschen.
MODEL_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: Dasselbe für den Dateinamen, der aus der Fremdquelle in die Ablage wandert.
MODEL_FILENAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")

_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")

#: Erlaubte Quell-Hosts. Der Katalog zeigt heute auf Hugging Face; eine
#: andere Quelle kommt als zweiter Eintrag dazu, nicht als freie URL.
ALLOWED_DOWNLOAD_HOSTS: Final[frozenset[str]] = frozenset({"huggingface.co"})

#: Hugging Face liefert große Dateien über eigene CDN-Hosts aus
#: (``cdn-lfs.huggingface.co``, ``transfer.xethub.hf.co``, ``cdn-lfs-us-1.hf.co``).
#: Deshalb die Endung statt einer Liste: die CDN-Namen ändern sich, die Domäne
#: nicht. Der Apex `hf.co` ist bewusst **nicht** erlaubt, sondern nur
#: Namensräume darunter.
ALLOWED_DOWNLOAD_SUFFIXES: Final[tuple[str, ...]] = (".huggingface.co", ".hf.co")

#: Wie viele Weiterleitungen ein Download nehmen darf. Drei bis fünf genügen für
#: `resolve -> cdn -> cas-server`; mehr ist kein Download mehr.
MAX_REDIRECTS: Final[int] = 5

#: Zusätzlicher Speicher, den die Laufzeit neben den Gewichten braucht
#: (Arbeitspuffer, Graph, Metadaten). Bewusst klein und als Faktor notiert.
COMPUTE_HEADROOM: Final[float] = 1.10

#: KV-Cache pro Kontexttoken über alle Schichten. Für die hier kuratierten
#: GGUF-Modelle (GQA, 16-36 Schichten) liegt das bei 28-36 KiB; 64 KiB ist eine
#: bewusst grobe Obergrenze, damit die Schätzung nicht zu niedrig ausfällt.
KV_CACHE_BYTES_PER_TOKEN: Final[int] = 64 * 1024

#: Kontextlänge, mit der gerechnet wird, wenn das Modell mehr kann. 8k Token
#: reichen für Lernplan, Dokumentenfrage und Transkription; wer mehr braucht,
#: hebt die Zahl hier an - dann ist es eine bewusste Entscheidung.
DEFAULT_ESTIMATE_CONTEXT: Final[int] = 8192


@dataclass(frozen=True, slots=True)
class ModelFile:
    """Eine Datei, die zu einem Modell gehört.

    ``sha256`` ist der vom Hersteller veröffentlichte Wert des LFS-Objekts
    (Hugging Face nennt ihn ``lfs.sha256``) und **nicht** frei erfunden. Stimmt
    er beim Download nicht überein, wird die Datei verworfen und der Download
    als gescheitert gemeldet - ein Modell mit falscher Prüfsumme ist ein
    Zustand, aus dem ein späterer Start ein kaputtes Modell lädt.
    """

    name: str
    url: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Ein kuratiertes Modell mit seinen Metadaten und seinen Dateien."""

    id: str
    label: str
    task: str
    family: str
    description: str
    license: str
    source_repository: str
    source_page: str
    parameter_count: int | None
    quantization: str | None
    context_length: int | None
    files: tuple[ModelFile, ...]

    @property
    def size_bytes(self) -> int:
        """Die Downloadgröße, aufsummiert aus den Metadaten der Dateien."""
        return sum(item.size_bytes for item in self.files)


@dataclass(frozen=True, slots=True)
class RamEstimate:
    """Der RAM-Bedarf zur Laufzeit - ausdrücklich eine Schätzung.

    ``derived_from`` sagt in einem deutschen Satz, woraus gerechnet wurde. Die
    Oberfläche zeigt diesen Text mit an, damit niemand eine Größenordnung als
    eine Messung liest.
    """

    ram_bytes: int
    weight_bytes: int
    derived_from: str

    @property
    def is_estimate(self) -> bool:
        return True

    @property
    def gib(self) -> float:
        return round(self.ram_bytes / (1024**3), 1)


#: K-Quanten mischen Tensoren unterschiedlicher Genauigkeit: `Q4_K_M` liegt
#: real bei rund 4,8 Bit pro Gewicht, nicht bei 4. Der Aufschlag von 0,8 ist
#: deshalb eine Annahme und keine Tabelle, die veralten kann.
_KV_QUANT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^i?q(\d+)(?:_k)?_(?:m|s|t|l|xs|xl)$")
_PLAIN_QUANT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^i?q(\d+)(?:_[a-z0-9]+)*$")
_EXACT_BITS: Final[dict[str, float]] = {
    "f16": 16.0,
    "fp16": 16.0,
    "bf16": 16.0,
    "f32": 32.0,
    "fp32": 32.0,
}
_K_QUANT_EXTRA_BITS: Final[float] = 0.8


def bits_per_weight(quantization: str | None) -> float | None:
    """Wie viele Bit ein Gewicht im Mittel hat - abgeleitet aus dem Namen.

    ``None`` heißt: die Schicht ist zu exotisch, um sie zu raten. Dann rechnet
    :func:`ram_estimate` mit der Dateigröße statt mit der Parameterzahl, und
    sagt das auch.
    """
    if not quantization:
        return None
    token = quantization.strip().lower()
    if token in _EXACT_BITS:
        return _EXACT_BITS[token]
    k_quant = _KV_QUANT_PATTERN.match(token)
    if k_quant is not None:
        return int(k_quant.group(1)) + _K_QUANT_EXTRA_BITS
    plain = _PLAIN_QUANT_PATTERN.match(token)
    if plain is not None:
        return float(int(plain.group(1)))
    return None


def ram_estimate(spec: ModelSpec) -> RamEstimate:
    """Rechnet den RAM-Bedarf zur Laufzeit aus den Metadaten.

    Die Formel ist absichtlich simpel und in drei Schritten lesbar:

    1. **Gewichte** - ``Parameter × Bit pro Gewicht / 8``. Wo beides bekannt
       ist, kommt das der Dateigröße auf wenige Prozent nahe.
    2. **Arbeitsbereich** - ``Gewichte × COMPUTE_HEADROOM`` für den Puffer
       neben den Gewichten.
    3. **Kontext** - ``Kontext × KV_CACHE_BYTES_PER_TOKEN``. Gerechnet wird mit
       ``min(context_length, DEFAULT_ESTIMATE_CONTEXT)``, weil ein Modell mit
       128k Kontext hier sonst unbenutzbar erscheint.

    Fehlt die Parameterzahl (etwa bei whisper.cpp), werden die Gewichte mit der
    Dateigröße angesetzt, und der Unterschied steht im Satz dazu.
    """
    bits = bits_per_weight(spec.quantization)
    if spec.parameter_count and bits:
        weight_bytes = round(spec.parameter_count * bits / 8)
        derived = (
            f"{spec.parameter_count / 1e9:.2f} Milliarden Parameter mit {bits:.1f} Bit pro Gewicht "
            f"({spec.quantization})"
        )
    else:
        weight_bytes = spec.size_bytes
        derived = f"Dateigröße von {spec.size_bytes} Byte, weil Parameterzahl oder Quantisierung fehlen"

    ram_bytes = round(weight_bytes * COMPUTE_HEADROOM)
    context = _estimate_context(spec)
    if context:
        ram_bytes += context * KV_CACHE_BYTES_PER_TOKEN
        derived += f", plus {context} Kontexttoken"
    return RamEstimate(ram_bytes=ram_bytes, weight_bytes=weight_bytes, derived_from=derived)


def _estimate_context(spec: ModelSpec) -> int:
    if not spec.context_length:
        return 0
    return min(spec.context_length, DEFAULT_ESTIMATE_CONTEXT)


def _hugging_face_url(repository: str, file_name: str) -> str:
    return f"https://huggingface.co/{repository}/resolve/main/{file_name}"


def _hugging_face_file(repository: str, file_name: str, sha256: str, size_bytes: int) -> ModelFile:
    return ModelFile(
        name=file_name,
        url=_hugging_face_url(repository, file_name),
        sha256=sha256,
        size_bytes=size_bytes,
    )


MODEL_CATALOG: Final[dict[str, ModelSpec]] = {
    "qwen2.5-1.5b-instruct-q4km": ModelSpec(
        id="qwen2.5-1.5b-instruct-q4km",
        label="Qwen2.5 1.5B Instruct (Q4_K_M)",
        task="text_generation",
        family="GGUF für llama.cpp",
        description=(
            "Mehrsprachiges Anweisungsmodell mit guten Deutschkenntnissen. "
            "Die kleinste sinnvolle Wahl für Lernplan und Dokumentenfragen."
        ),
        license="Apache-2.0",
        source_repository="bartowski/Qwen2.5-1.5B-Instruct-GGUF",
        source_page="https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF",
        parameter_count=1543714304,
        quantization="Q4_K_M",
        context_length=32768,
        files=(
            _hugging_face_file(
                "bartowski/Qwen2.5-1.5B-Instruct-GGUF",
                "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
                "1adf0b11065d8ad2e8123ea110d1ec956dab4ab038eab665614adba04b6c3370",
                986048768,
            ),
        ),
    ),
    "llama-3.2-1b-instruct-q4km": ModelSpec(
        id="llama-3.2-1b-instruct-q4km",
        label="Llama 3.2 1B Instruct (Q4_K_M)",
        task="text_generation",
        family="GGUF für llama.cpp",
        description="Das kleinste Textmodell im Katalog, wenn der Speicher knapp ist.",
        license="Llama 3.2 Community License",
        source_repository="bartowski/Llama-3.2-1B-Instruct-GGUF",
        source_page="https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF",
        parameter_count=1235814432,
        quantization="Q4_K_M",
        context_length=131072,
        files=(
            _hugging_face_file(
                "bartowski/Llama-3.2-1B-Instruct-GGUF",
                "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
                "6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83",
                807694464,
            ),
        ),
    ),
    "qwen2.5-3b-instruct-q4km": ModelSpec(
        id="qwen2.5-3b-instruct-q4km",
        label="Qwen2.5 3B Instruct (Q4_K_M)",
        task="text_generation",
        family="GGUF für llama.cpp",
        description="Deutlich bessere Antworten als die 1.5B-Variante, wenn der Server mehr RAM hat.",
        license="Apache-2.0",
        source_repository="bartowski/Qwen2.5-3B-Instruct-GGUF",
        source_page="https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF",
        parameter_count=3085938688,
        quantization="Q4_K_M",
        context_length=32768,
        files=(
            _hugging_face_file(
                "bartowski/Qwen2.5-3B-Instruct-GGUF",
                "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
                "9c9f56a391a3abbd5b89d0245bf6106081bcc3173119d4229235dd9d23253f94",
                1929903264,
            ),
        ),
    ),
    # Zwei Sprachmodelle, damit das Format nicht als GGUF-Flkost lesbar ist:
    # whisper.cpp laedt Binaerdateien ohne Quantisierungssuffix und kennt keinen
    # Kontextpuffer im Sinne von llama.cpp.
    "whisper-tiny-ggml": ModelSpec(
        id="whisper-tiny-ggml",
        label="Whisper tiny (GGML)",
        task="speech_to_text",
        family="GGML für whisper.cpp",
        description="Kleinstes Spracherkennungsmodell, mehrsprachig. Reicht für kurze Aufgaben.",
        license="MIT",
        source_repository="ggerganov/whisper.cpp",
        source_page="https://huggingface.co/ggerganov/whisper.cpp",
        parameter_count=None,
        quantization="f16",
        context_length=None,
        files=(
            _hugging_face_file(
                "ggerganov/whisper.cpp",
                "ggml-tiny.bin",
                "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21",
                77691713,
            ),
        ),
    ),
    "whisper-base-ggml": ModelSpec(
        id="whisper-base-ggml",
        label="Whisper base (GGML)",
        task="speech_to_text",
        family="GGML für whisper.cpp",
        description="Spracherkennung mit weniger Fehlern als tiny, auf Kosten von RAM und Zeit.",
        license="MIT",
        source_repository="ggerganov/whisper.cpp",
        source_page="https://huggingface.co/ggerganov/whisper.cpp",
        parameter_count=None,
        quantization="f16",
        context_length=None,
        files=(
            _hugging_face_file(
                "ggerganov/whisper.cpp",
                "ggml-base.bin",
                "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
                147951465,
            ),
        ),
    ),
}


def catalog_models() -> tuple[ModelSpec, ...]:
    """Alle Modelle in stabiler Reihenfolge."""
    return tuple(sorted(MODEL_CATALOG.values(), key=lambda spec: (spec.task, spec.size_bytes, spec.id)))


def model_spec(model_id: str) -> ModelSpec:
    """Das Modell mit dieser Id, sonst ein deutscher Fehler.

    Der Aufrufer entscheidet, ob daraus 404 oder 422 wird - hier ist nur der
    Text festgelegt.
    """
    spec = MODEL_CATALOG.get(model_id)
    if spec is None:
        raise ModelCatalogError(
            f"Unbekanntes Modell: {model_id!r}. Der Katalog kennt {len(MODEL_CATALOG)} Modelle."
        )
    return spec


def validate_download_url(url: str) -> str:
    """Prüft eine Download-Adresse gegen Schema, Port und Host-Allowlist.

    Drei Regeln, alle aus demselben Grund - ein Downloader, der eine Adresse
    aus einer Anfrage zieht, ist eine SSRF-Quelle:

    * **Nur HTTPS.** Kein Klartext, kein ``file:``, kein ``gopher:``.
    * **Nur Standardport.** Ein Ziel auf einem anderen Port ist keine
      Modellquelle, sondern ein Dienst im eigenen Netz.
    * **Nur bekannte Hosts.** Keine Benutzerangaben in der URL.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ModelCatalogError(f"Eine Modellquelle muss HTTPS sein, nicht {parts.scheme or 'ohne Schema'}: {url}")
    if parts.username or parts.password:
        raise ModelCatalogError(f"Eine Modellquelle darf keine Zugangsdaten enthalten: {url}")
    if parts.port is not None and parts.port != 443:
        raise ModelCatalogError(f"Eine Modellquelle muss auf Port 443 liegen: {url}")
    host = (parts.hostname or "").lower()
    if not host:
        raise ModelCatalogError(f"Eine Modellquelle braucht einen Hostnamen: {url}")
    if not is_allowed_download_host(host):
        erlaubt = ", ".join(sorted(ALLOWED_DOWNLOAD_HOSTS))
        raise ModelCatalogError(
            f"Die Modellquelle {host!r} steht nicht auf der Liste der erlaubten Quellen ({erlaubt} "
            "und deren CDN-Namensräume). Der Downloader holt nur aus diesem Katalog."
        )
    return url


def is_allowed_download_host(host: str) -> bool:
    """Nimmt die aus :func:`validate_download_url` gesinnte Host-Prüfung vor."""
    candidate = host.strip().lower().rstrip(".")
    if candidate in ALLOWED_DOWNLOAD_HOSTS:
        return True
    return any(candidate.endswith(suffix) for suffix in ALLOWED_DOWNLOAD_SUFFIXES)


def _assert_model_file_is_usable(spec: ModelSpec, item: ModelFile) -> None:
    if not MODEL_FILENAME_PATTERN.match(item.name):
        raise ModelCatalogError(f"Der Dateiname {item.name!r} von {spec.id!r} ist nicht zulaessig.")
    if not _SHA256_PATTERN.match(item.sha256):
        raise ModelCatalogError(f"Die Pruefsumme von {spec.id!r}/{item.name} ist keine SHA256-Hexadezimalzahl.")
    if item.size_bytes <= 0:
        raise ModelCatalogError(f"Die Dateigroesse von {spec.id!r}/{item.name} ist nicht plausibel.")
    validate_download_url(item.url)


def _assert_catalog_is_valid() -> None:
    """Bricht beim Import, wenn ein Katalogeintrag kaputt ist.

    Bewusst beim Import und nicht beim ersten Download: ein schlechter Eintrag
    ist ein Programmierfehler, und ``import app.main`` ist die früheste Stelle,
    an der er auffallen kann.
    """
    if not MODEL_CATALOG:
        raise ModelCatalogError("Der Modellkatalog ist leer.")
    for spec in MODEL_CATALOG.values():
        if not MODEL_ID_PATTERN.match(spec.id):
            raise ModelCatalogError(f"Die model_id {spec.id!r} ist kein zulaessiger Verzeichnisname.")
        if spec.task not in MODEL_TASKS:
            raise ModelCatalogError(f"Das Modell {spec.id!r} hat die unbekannte Aufgabe {spec.task!r}.")
        if not spec.files:
            raise ModelCatalogError(f"Das Modell {spec.id!r} hat keine Datei; es gaebe nichts zu laden.")
        if spec.parameter_count is not None and spec.parameter_count <= 0:
            raise ModelCatalogError(f"Das Modell {spec.id!r} hat eine unplausible Parameterzahl.")
        names = [item.name for item in spec.files]
        if len(set(names)) != len(names):
            raise ModelCatalogError(f"Das Modell {spec.id!r} nennt dieselbe Datei zweimal.")
        for item in spec.files:
            _assert_model_file_is_usable(spec, item)


_assert_catalog_is_valid()
