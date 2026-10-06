"""Unterrichtsmaterial lesen und verstehen: Text, PDF, Foto, Scan - und Bedeutungssuche.

Aufschriebe und Arbeitsblaetter sind selten Textdateien. Meist sind es Fotos
oder Scans, oft handschriftlich. Dieses Modul macht daraus Text, erkennt, was
es ist, und legt Einbettungen fuer die Suche nach Bedeutung an.

* **Zwei Geschwindigkeiten.** Text und PDF mit Textebene werden beim Hochladen
  sofort gelesen. Fotos und Scanseiten bekommen ``status="queued"`` und werden
  vom :class:`MaterialIndexer` im Hintergrund abgeschrieben - ein Foto dauert
  auf dem Prozessor etwa eine Minute je Seite, das darf keinen Upload blockieren. Nach
  einem Neustart macht der Indexer einfach weiter: der Zustand steht in der
  Datenbank, nicht im Speicher.
* **Abschreiben, nicht deuten.** Das Bildmodell soll wortgetreu abschreiben,
  mit Ueberschriften und Aufzaehlungen. Genau diese Form ist spaeter das, woran
  das persoenliche Training den Stil der Aufschriebe lernt.
* **Ohne Bildmodell kein Raten.** Ist weder ein lokales Bildmodell geladen noch
  ein Online-Dienst eingetragen, steht das Dokument auf ``needs_model``. Sobald
  ein Modell da ist, nimmt der Indexer es wieder auf.
* **Art, Fach, Thema, Datum** erkennt :func:`classify` mit festen Regeln: ein
  Dateiname "Mathe Aufschrieb 12.03." ist eindeutiger als jedes Modell. Von
  Hand Korrigiertes steht in ``extra_data["manual_fields"]`` und wird nie
  ueberschrieben.
* **Einbettungen lokal** (bge-m3 ueber ``llama-server --embedding``) in
  ``chunk_embeddings``. Die Suche rechnet den Kosinus in Python: ein Schuljahr
  sind wenige tausend Absaetze, das ist schneller als jede Indexstruktur.
"""

from __future__ import annotations

import base64
import logging
import math
import re
import threading
from array import array
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.entities import ChunkEmbedding, Document, DocumentChunk, Subject

logger = logging.getLogger("school_assistant")

KIND_NOTES: Final[str] = "aufschrieb"
KIND_WORKSHEET: Final[str] = "arbeitsblatt"
KIND_SOLUTION: Final[str] = "loesung"
KIND_OTHER: Final[str] = "sonstiges"
MATERIAL_KINDS: Final[tuple[str, ...]] = (KIND_NOTES, KIND_WORKSHEET, KIND_SOLUTION, KIND_OTHER)
KIND_LABELS: Final[dict[str, str]] = {
    KIND_NOTES: "Aufschrieb",
    KIND_WORKSHEET: "Arbeitsblatt",
    KIND_SOLUTION: "Lösung",
    KIND_OTHER: "Sonstiges",
}

SOURCE_TEXT: Final[str] = "text"
SOURCE_PDF: Final[str] = "pdf"
SOURCE_OCR_LOCAL: Final[str] = "ocr_local"
SOURCE_OCR_ONLINE: Final[str] = "ocr_online"

STATUS_QUEUED: Final[str] = "queued"
STATUS_NEEDS_MODEL: Final[str] = "needs_model"

TEXT_SUFFIXES: Final[frozenset[str]] = frozenset({".txt", ".md", ".markdown"})
IMAGE_TYPES: Final[dict[str, str]] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
#: Eine PDF-Seite mit weniger Text ist ein Scan (oder ein Deckblatt) und wird abgeschrieben.
MIN_PAGE_CHARS: Final[int] = 40
MAX_PDF_PAGES: Final[int] = 60
TRANSCRIBE_MAX_TOKENS: Final[int] = 2000
TRANSCRIBE_TIMEOUT_SECONDS: Final[float] = 300.0
EMBED_TIMEOUT_SECONDS: Final[float] = 60.0
EMBED_BATCH: Final[int] = 16

TRANSCRIBE_SYSTEM: Final[str] = (
    "Du schreibst Schulmaterial von Fotos und Scans ab. Du gibst nur den abgeschriebenen Text aus, "
    "ohne Einleitung und ohne Kommentar."
)
TRANSCRIBE_PROMPT: Final[str] = (
    "Schreibe den Inhalt dieses Bildes vollstaendig und wortgetreu ab. Es ist ein Aufschrieb, ein "
    "Arbeitsblatt oder eine Loesung aus der Schule, oft handschriftlich. Behalte die Form bei: "
    "Ueberschriften als '#', Aufzaehlungen als '-', Nummerierungen, Einrueckungen und Hervorhebungen "
    "(unterstrichen oder eingerahmt als **fett**). Formeln in einfacher Schreibweise (x^2, sqrt(x), a/b). "
    "Uebernimm Abkuerzungen genau so, wie sie dastehen. Ergaenze, erklaere und korrigiere nichts. "
    "Unleserliches markierst du als [unleserlich]."
)

_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})?")
#: Im Dateinamen frisst die Endung den letzten Punkt ("12.03.jpg" -> "12.03"). Im
#: Fliesstext waere das zu lose - dort ist "2.5" eine Zahl, kein Datum.
_NAME_DATE_RE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?![\d])")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$", re.MULTILINE)
_KIND_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (KIND_SOLUTION, re.compile(r"l(ö|oe|o)sung|\blsg\b|erwartungshorizont", re.IGNORECASE)),
    (
        KIND_WORKSHEET,
        re.compile(r"arbeitsblatt|\bab\s?\d|\bab\b|übungsblatt|uebungsblatt|aufgabe\s*1\b", re.IGNORECASE),
    ),
    (
        KIND_NOTES,
        re.compile(r"aufschrieb|hefteintrag|mitschrift|tafelbild|notizen|\bmerke\b|zusammenfassung", re.IGNORECASE),
    ),
)

#: Bild (Bytes, MIME-Typ) -> abgeschriebener Text.
Transcriber = Callable[[bytes, str], str]
#: Texte -> Vektoren, gleiche Reihenfolge.
Embedder = Callable[[Sequence[str]], list[list[float]]]


class MaterialReadError(ValueError):
    """Die Datei laesst sich nicht lesen. Deutsche Meldung."""


# --- Lesen -------------------------------------------------------------------


def file_kind(filename: str) -> str | None:
    """``text``, ``pdf``, ``image`` - oder ``None`` fuer ein Format, das es nicht gibt."""
    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return "text"
    if suffix == ".pdf":
        return "pdf"
    if suffix in IMAGE_TYPES:
        return "image"
    return None


@dataclass(frozen=True, slots=True)
class MaterialText:
    text: str
    source: str
    #: Es fehlen Seiten oder Bilder, die erst abgeschrieben werden muessen.
    needs_ocr: bool


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _pdf_pages(path: Path) -> list[tuple[str, tuple[bytes, str] | None]]:
    """Je Seite: Textebene und das groesste Bild (fuer Scanseiten)."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(str(path))
        pages: list[tuple[str, tuple[bytes, str] | None]] = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            text = (page.extract_text() or "").strip()
            image: tuple[bytes, str] | None = None
            if len(text) < MIN_PAGE_CHARS:
                candidates = [
                    (item.data, IMAGE_TYPES.get(Path(item.name).suffix.lower(), "image/png")) for item in page.images
                ]
                if candidates:
                    image = max(candidates, key=lambda candidate: len(candidate[0]))
            pages.append((text, image))
        return pages
    except (PyPdfError, ValueError, OSError, KeyError) as exc:
        raise MaterialReadError("Das PDF lässt sich nicht lesen.") from exc


def read_quick(path: Path) -> MaterialText:
    """Was sofort geht: Textdateien und die Textebene eines PDFs. Bilder warten auf den Indexer."""
    kind = file_kind(path.name)
    if kind == "text":
        return MaterialText(_decode(path.read_bytes()), SOURCE_TEXT, needs_ocr=False)
    if kind == "pdf":
        pages = _pdf_pages(path)
        text = "\n\n".join(page_text for page_text, _ in pages if page_text)
        missing = any(len(page_text) < MIN_PAGE_CHARS and image is not None for page_text, image in pages)
        return MaterialText(text, SOURCE_PDF, needs_ocr=missing)
    if kind == "image":
        return MaterialText("", SOURCE_OCR_LOCAL, needs_ocr=True)
    raise MaterialReadError(f"Nicht unterstütztes Format: {path.suffix or 'ohne Endung'}")


def read_with_ocr(path: Path, transcribe: Transcriber) -> str:
    """Der ganze Text: Textebene, wo es eine gibt, sonst abgeschrieben."""
    kind = file_kind(path.name)
    if kind == "image":
        return transcribe(path.read_bytes(), IMAGE_TYPES[path.suffix.lower()]).strip()
    if kind == "pdf":
        parts: list[str] = []
        for page_text, image in _pdf_pages(path):
            if len(page_text) >= MIN_PAGE_CHARS or image is None:
                if page_text:
                    parts.append(page_text)
                continue
            parts.append(transcribe(*image).strip())
        return "\n\n".join(part for part in parts if part)
    return read_quick(path).text


# --- Erkennen ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MaterialInfo:
    kind: str
    subject_id: int | None
    topic: str | None
    material_date: date | None


def _subject_match(text: str, subjects: Sequence[Subject], *, in_filename: bool = False) -> int | None:
    """Das eine Fach, das im Text vorkommt - bei zwei Treffern keins.

    Im Dateinamen gelten auch einbuchstabige Kuerzel ("M Aufschrieb") und
    Wortanfaenge ab vier Zeichen ("Mathe" fuer "Mathematik"); im Fliesstext
    waere ein einzelnes "M" oder "Bio" in "Biografie" zu oft Zufall.
    """
    found: set[int] = set()
    words = {word.casefold() for word in re.findall(r"\w+", text)} if in_filename else set()
    for subject in subjects:
        for name in (subject.name, subject.short_name):
            if not name or (len(name) < 2 and not in_filename):
                continue
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
                found.add(subject.id)
        if in_filename and any(len(word) >= 4 and subject.name.casefold().startswith(word) for word in words):
            found.add(subject.id)
    return found.pop() if len(found) == 1 else None


def _topic(text: str) -> str | None:
    heading = _HEADING_RE.search(text)
    if heading:
        return heading.group(1).strip("*_ ")[:240] or None
    for line in text.splitlines():
        line = line.strip(" -*#\t")
        if 3 <= len(line) <= 80 and not _DATE_RE.fullmatch(line):
            return line[:240]
    return None


def _date(text: str, today: date, *, pattern: re.Pattern[str] = _DATE_RE) -> date | None:
    match = pattern.search(text)
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    year_text = match.group(3)
    year = today.year if not year_text else int(year_text) + (2000 if len(year_text) == 2 else 0)
    try:
        found = date(year, month, day)
    except ValueError:
        return None
    # "12.11." ohne Jahr im Oktober ist der vergangene November, kein kuenftiger.
    if not year_text and found > today:
        try:
            found = date(year - 1, month, day)
        except ValueError:
            return None
    return found


def classify(
    filename: str, text: str, subjects: Sequence[Subject], *, handwritten: bool, today: date
) -> MaterialInfo:
    """Art, Fach, Thema und Datum - zuerst aus dem Dateinamen, dann vom Anfang des Textes."""
    head = text[:600]
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    kind = next((name for name, pattern in _KIND_PATTERNS if pattern.search(stem)), None)
    if kind is None:
        kind = next((name for name, pattern in _KIND_PATTERNS if pattern.search(head)), None)
    if kind is None:
        # Ein abfotografiertes, handschriftliches Blatt ist fast immer der eigene Aufschrieb.
        kind = KIND_NOTES if handwritten else KIND_OTHER
    subject_id = _subject_match(stem, subjects, in_filename=True) or _subject_match(head, subjects)
    return MaterialInfo(
        kind=kind,
        subject_id=subject_id,
        topic=_topic(text),
        material_date=_date(stem, today, pattern=_NAME_DATE_RE) or _date(head, today),
    )


def apply_classification(
    db: Session, document: Document, text: str, *, handwritten: bool, today: date
) -> MaterialInfo:
    """Traegt die erkannten Werte ein - ohne von Hand gesetzte zu ueberschreiben."""
    subjects = db.scalars(select(Subject).where(Subject.user_id == document.user_id)).all()
    info = classify(document.filename, text, subjects, handwritten=handwritten, today=today)
    manual = set((document.extra_data or {}).get("manual_fields", []))
    if "material_kind" not in manual:
        document.material_kind = info.kind
    if "material_topic" not in manual:
        document.material_topic = info.topic
    if "material_date" not in manual:
        document.material_date = info.material_date
    if "subject_id" not in manual and document.subject_id is None:
        document.subject_id = info.subject_id
    return info


# --- Bildmodell und Einbettungen ueber llama-server ----------------------------


def http_transcriber(base_url: str, *, transport: httpx.BaseTransport | None = None) -> Transcriber:
    """Abschreiben ueber einen OpenAI-kompatiblen Server mit Bildeingang (``llama-server --mmproj``)."""

    def transcribe(image: bytes, mime_type: str) -> str:
        data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
        body = {
            "messages": [
                {"role": "system", "content": TRANSCRIBE_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": TRANSCRIBE_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            "temperature": 0,
            "max_tokens": TRANSCRIBE_MAX_TOKENS,
        }
        with httpx.Client(timeout=TRANSCRIBE_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=body)
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"] or "")

    return transcribe


def http_embedder(base_url: str, *, transport: httpx.BaseTransport | None = None) -> Embedder:
    """Einbettungen ueber ``llama-server --embedding`` (``/v1/embeddings``)."""

    def embed(texts: Sequence[str]) -> list[list[float]]:
        with httpx.Client(timeout=EMBED_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(f"{base_url.rstrip('/')}/v1/embeddings", json={"input": list(texts)})
            response.raise_for_status()
            rows = sorted(response.json()["data"], key=lambda row: row["index"])
            return [[float(value) for value in row["embedding"]] for row in rows]

    return embed


def pack_vector(values: Sequence[float]) -> bytes:
    """Normiert auf Laenge 1 - dann ist der Kosinus ein einfaches Skalarprodukt."""
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return array("f", (value / norm for value in values)).tobytes()


def unpack_vector(raw: bytes) -> array[float]:
    vector = array("f")
    vector.frombytes(raw)
    return vector


def embed_missing_chunks(
    db: Session, user_id: int, model_id: str, embed: Embedder, *, limit: int = EMBED_BATCH * 4
) -> int:
    """Rechnet Einbettungen fuer eigene Absaetze ohne Vektor dieses Modells nach. Liefert die Anzahl."""
    done = select(ChunkEmbedding.chunk_id).where(ChunkEmbedding.model_id == model_id)
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.user_id == user_id, DocumentChunk.id.not_in(done))
        .order_by(DocumentChunk.id)
        .limit(limit)
    ).all()
    stored = 0
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start : start + EMBED_BATCH]
        vectors = embed([chunk.content for chunk in batch])
        for chunk, vector in zip(batch, vectors, strict=True):
            db.add(
                ChunkEmbedding(
                    chunk_id=chunk.id,
                    user_id=chunk.user_id,
                    model_id=model_id,
                    dims=len(vector),
                    vector=pack_vector(vector),
                )
            )
            stored += 1
    db.flush()
    return stored


def vector_ranking(
    db: Session, user_id: int, model_id: str, query: Sequence[float], limit: int
) -> list[tuple[int, float]]:
    """(chunk_id, Aehnlichkeit) der besten Absaetze, nur aus den eigenen Dokumenten."""
    target = unpack_vector(pack_vector(query))
    rows = db.execute(
        select(ChunkEmbedding.chunk_id, ChunkEmbedding.vector).where(
            ChunkEmbedding.user_id == user_id, ChunkEmbedding.model_id == model_id
        )
    ).all()
    scored: list[tuple[int, float]] = []
    for chunk_id, raw in rows:
        vector = unpack_vector(raw)
        if len(vector) != len(target):
            continue
        scored.append((chunk_id, sum(a * b for a, b in zip(vector, target, strict=True))))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


# --- Hintergrund: Abschreiben und Einbetten ------------------------------------


@dataclass
class MaterialTools:
    """Was ein Durchlauf gerade zur Verfuegung hat. Jedes Feld darf fehlen."""

    transcribe: Transcriber | None = None
    transcribe_source: str = SOURCE_OCR_LOCAL
    embed: Embedder | None = None
    embed_model_id: str | None = None


def process_queued_document(db: Session, document: Document, tools: MaterialTools, *, today: date) -> bool:
    """Schreibt ein wartendes Dokument ab, zerlegt es und erkennt es. ``False`` = kein Bildmodell."""
    from app.services.documents import write_chunks

    if tools.transcribe is None:
        document.status = STATUS_NEEDS_MODEL
        return False
    path = Path(document.storage_path)
    try:
        text = read_with_ocr(path, tools.transcribe)
    except (OSError, MaterialReadError, httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("Dokument %s konnte nicht abgeschrieben werden: %s", document.filename, exc)
        document.status = "error"
        return True
    if not text.strip():
        document.status = "error"
        return True
    write_chunks(db, document, text)
    document.text_source = tools.transcribe_source
    apply_classification(db, document, text, handwritten=True, today=today)
    return True


class MaterialIndexer:
    """Ein Hintergrundfaden: wartende Fotos abschreiben, fehlende Einbettungen rechnen.

    ``tools_for`` liefert je Nutzer, was gerade da ist (lokales Bildmodell,
    Online-Dienst, Einbettungsmodell); ``release`` gibt das Bildmodell frei,
    sobald nichts mehr wartet - es haelt Gigabytes im Speicher. Wartet ein
    Dokument auf ein Modell (``needs_model``), wird es bei jedem Durchlauf
    erneut versucht; ``tools_for`` ist billig, solange nichts geladen ist.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        tools_for: Callable[[Session, int], MaterialTools],
        *,
        release: Callable[[], None] = lambda: None,
        today: Callable[[], date] = date.today,
        interval_seconds: float = 20.0,
    ) -> None:
        self.session_factory = session_factory
        self.tools_for = tools_for
        self.release = release
        self.today = today
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="material-indexer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        """Nach einem Upload oder einem neu geladenen Modell sofort nachsehen."""
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                busy = self.run_once()
            except Exception:  # noqa: BLE001 - ein Fehler darf den Faden nicht beenden
                logger.exception("Materialindexer: Durchlauf fehlgeschlagen.")
                busy = False
            if not busy:
                self._wake.wait(self.interval_seconds)
                self._wake.clear()

    def run_once(self) -> bool:
        """Ein Schritt. ``True`` = es gab etwas zu tun, gleich weitermachen."""
        db = self.session_factory()
        try:
            waiting = db.scalars(
                select(Document)
                .where(Document.status.in_((STATUS_QUEUED, STATUS_NEEDS_MODEL)))
                .order_by(Document.id)
            ).all()
            for document in waiting:
                tools = self.tools_for(db, document.user_id)
                if process_queued_document(db, document, tools, today=self.today()):
                    db.commit()
                    return True
                db.commit()
            self.release()
            return self._embed_backlog(db)
        finally:
            db.close()

    def _embed_backlog(self, db: Session) -> bool:
        for user_id in db.scalars(select(DocumentChunk.user_id).distinct()).all():
            tools = self.tools_for(db, user_id)
            if tools.embed is None or tools.embed_model_id is None:
                continue
            try:
                stored = embed_missing_chunks(db, user_id, tools.embed_model_id, tools.embed)
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                logger.warning("Einbettungen fehlgeschlagen: %s", exc)
                db.rollback()
                return False
            db.commit()
            if stored:
                return True
        return False


def tools_factory(settings: object, runtimes: object | None) -> Callable[[Session, int], MaterialTools]:
    """Was ein Nutzer gerade hat: lokales Bildmodell vor Online-Dienst, dazu das Einbettungsmodell.

    Ein lokales Bildmodell schlaegt den Online-Dienst, weil Aufschriebe den
    Rechner dann nicht verlassen. Der Online-Weg ist der eingetragene
    KI-Dienst des Nutzers - derselbe, an den der Assistent ohnehin Auszuege
    schickt.
    """
    from app.core.config import Settings
    from app.services.ai_provider import generate_text_from_image
    from app.services.documents import _load_provider
    from app.services.local_runtimes import LocalRuntimeManager

    assert isinstance(settings, Settings)
    manager = runtimes if isinstance(runtimes, LocalRuntimeManager) else None

    def tools_for(db: Session, user_id: int) -> MaterialTools:
        tools = MaterialTools()
        if manager is not None:
            vision = manager.installed_models("image_to_text")
            if vision:
                spec = vision[0]
                tools.transcribe = lambda image, mime: http_transcriber(manager.ensure_vision_model(spec))(image, mime)
            embedders = manager.installed_models("embedding")
            if embedders:
                embed_spec = embedders[0]
                tools.embed = lambda texts: http_embedder(manager.ensure_embedding_model(embed_spec))(texts)
                tools.embed_model_id = embed_spec.id
        if tools.transcribe is None:
            provider = _load_provider(db, user_id, settings)
            if provider is not None:
                tools.transcribe = lambda image, mime: generate_text_from_image(
                    base_url=provider.base_url,
                    model=provider.model,
                    system_prompt=TRANSCRIBE_SYSTEM,
                    user_prompt=TRANSCRIBE_PROMPT,
                    image=image,
                    mime_type=mime,
                    api_key=provider.api_key,
                    timeout_seconds=TRANSCRIBE_TIMEOUT_SECONDS,
                    max_tokens=TRANSCRIBE_MAX_TOKENS,
                )
                tools.transcribe_source = SOURCE_OCR_ONLINE
        return tools

    return tools_for
