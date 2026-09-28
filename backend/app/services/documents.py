"""Dokumente indexieren, durchsuchen und beantworten.

Zwei Wege fuellen die lokale Dokumentablage: Der Sync einer ``documents``-
Integration scannt den konfigurierten Ablageordner, und der Upload-Endpoint
schreibt einzelne Dateien. Beide enden in ``Document``-Zeilen mit Absaetzen
in ``DocumentChunk``. Ein konfigurierter KI-Provider liefert die Einbettungen
fuer die pgvector-Spalte; ohne Provider bleibt die Stichwortsuche der
tragende Weg und die Antworten entstehen aus den Daten (``source: fallback``).

Die Suche ist bewusst zweispurig: Vektoren helfen dort, wo Modell und
Schultext zusammenpassen, die Stichwortsuche funktioniert aber ohne Modell und
in jeder Datenbank. Auf SQLite (Tests) existieren keine Vektoren - die
Einbettungen werden erst abgelegt, wenn der Dialekt wirklich PostgreSQL ist.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from sqlalchemy import case, delete, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import Settings, require_integration_encryption_key
from app.core.crypto import CredentialDecryptionError, decrypt_credentials, has_credentials
from app.core.time import utc_now
from app.db.entities import Document, DocumentChunk, SyncSource
from app.services.ai_provider import AiProviderError, generate_embeddings, generate_text

logger = logging.getLogger("school_assistant")

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md", ".markdown"})
DOCUMENTS_SOURCE: str = "documents"
UPLOAD_SOURCE: str = "upload"

# Die pgvector-Spalte ist in der ersten Migration fest auf 1536 Dimensionen
# angelegt. Ein Modell mit anderer Dimension wird deshalb nicht gespeichert -
# die Stichwortsuche deckt diesen Fall weiterhin ab.
EMBEDDING_DIMENSION: int = 1536

CHUNK_CHARS: int = 1000
CHUNK_OVERLAP: int = 120
_MAX_ANSWER_TOKENS: int = 280

_AI_KIND: str = "ai_provider"
_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    """Die Verbindungsdaten des aktiven KI-Providers, fertig entschluesselt."""

    base_url: str
    model: str
    api_key: str


@dataclass(frozen=True, slots=True)
class DocumentSource:
    """Ein Absatz aus einem Dokument, wie ihn Suche und Antwort liefern."""

    document_id: int
    filename: str
    chunk_index: int
    content: str


@dataclass(frozen=True, slots=True)
class SearchHit(DocumentSource):
    distance: float | None = None

    def source(self) -> DocumentSource:
        return DocumentSource(
            document_id=self.document_id,
            filename=self.filename,
            chunk_index=self.chunk_index,
            content=self.content,
        )


@dataclass(frozen=True, slots=True)
class DocumentSyncOutcome:
    """Was ein Ordner-Sync getan hat, in Zahlen und in einem deutschen Satz."""

    imported_documents: int
    updated_documents: int
    indexed_chunks: int
    skipped_files: int
    removed_documents: int

    def describe(self) -> str:
        teile: list[str] = []
        if self.imported_documents:
            teile.append(f"{self.imported_documents} neue Dokumente")
        if self.updated_documents:
            teile.append(f"{self.updated_documents} aktualisiert")
        if self.indexed_chunks:
            teile.append(f"{self.indexed_chunks} Absätze indexiert")
        if self.removed_documents:
            teile.append(f"{self.removed_documents} entfernt")
        if self.skipped_files:
            teile.append(f"{self.skipped_files} Dateien übersprungen")
        if not teile:
            return "Alle Dokumente sind bereits aktuell indexiert."
        return " ".join(teile) + "."


@dataclass(frozen=True, slots=True)
class QuestionAnswer:
    """Die Antwort auf eine Frage ueber die eigenen Dokumente."""

    answer: str
    source: str
    sources: list[DocumentSource]
    model: str | None = None


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def read_document_text(path: Path) -> str:
    """Liest den Klartext einer unterstuetzten Datei.

    Wirft ``ValueError`` fuer nicht unterstuetzte Formate und ``UnicodeError``
    fuer Dateien, die sich keinem Lesen oeffnen. Bewusst nur Textformate: PDF
    und Office brauchen zusaetzliche Pakete, die der schlanke Stack bewusst
    nicht mitfuehrt.
    """
    if not is_supported(path):
        raise ValueError(f"Nicht unterstütztes Format: {path.suffix or 'ohne Endung'}")
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def chunk_text(text: str, *, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Zerlegt Text in ueberlappende Absaetze.

    Die Einheit sind Woerter, nicht Zeichen, damit kein Wort in der Mitte
    zerschnitten wird. Die Ueberlappung haelt einen Teil des letzten Absatzes
    zum naechsten, damit ein gedachter Abschnitt nicht durch die Naht faellt.
    """
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    woerter = _WHITESPACE_RE.split(text.strip())
    chunks: list[str] = []
    aktuell: list[str] = []
    laenge = 0
    for wort in woerter:
        wortlaenge = len(wort) + 1
        if aktuell and laenge + wortlaenge > size:
            chunks.append(" ".join(aktuell))
            keep: list[str] = []
            akku = 0
            for vorher in reversed(aktuell):
                if akku and akku + len(vorher) + 1 > overlap:
                    break
                keep.insert(0, vorher)
                akku += len(vorher) + 1
            aktuell = keep
            laenge = akku
        aktuell.append(wort)
        laenge += wortlaenge
    if aktuell:
        chunks.append(" ".join(aktuell))
    return [chunk for chunk in chunks if chunk]


def token_count(content: str) -> int:
    return max(1, round(len(content) / 4))


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8192):
            digest.update(block)
    return digest.hexdigest()


def mime_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "text/markdown"
    if suffix == ".csv":
        return "text/csv"
    return "text/plain"


def _load_provider(db: Session, user_id: int, settings: Settings) -> ProviderProfile | None:
    """Der erste aktive KI-Provider des Nutzers, entschluesselt und gebrauchsfertig."""
    rows = db.scalars(
        select(SyncSource)
        .where(
            SyncSource.user_id == user_id,
            SyncSource.source_type == _AI_KIND,
            SyncSource.enabled.is_(True),
        )
        .order_by(SyncSource.id)
    ).all()
    selected: SyncSource | None = None
    for source in rows:
        configuration = source.configuration or {}
        if has_credentials(source.credentials) and configuration.get("base_url"):
            selected = source
            break
    if selected is None:
        return None
    try:
        key = require_integration_encryption_key(settings)
    except RuntimeError:
        return None
    try:
        credentials = decrypt_credentials(selected.credentials, key) if selected.credentials else {}
    except CredentialDecryptionError:
        return None
    configuration = selected.configuration or {}
    return ProviderProfile(
        base_url=str(configuration.get("base_url", "")),
        model=str(configuration.get("model", "Auto")),
        api_key=credentials.get("api_key", ""),
    )


def _write_chunks(db: Session, document: Document, text: str, provider: ProviderProfile | None) -> int:
    """Ersetzt die Absaetze eines Dokuments und pflegt Status und Einbettungen."""
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    absaetze = chunk_text(text)
    vectors: list[list[float]] | None = None
    if provider is not None and db.get_bind().dialect.name == "postgresql":
        try:
            vectors = generate_embeddings(
                base_url=provider.base_url,
                model=provider.model,
                input_texts=absaetze,
                api_key=provider.api_key,
            )
        except AiProviderError as exc:
            logger.warning("Einbettungen konnten nicht erzeugt werden: %s", exc)
    for index, inhalt in enumerate(absaetze):
        embedding = (
            vectors[index]
            if vectors is not None and len(vectors[index]) == EMBEDDING_DIMENSION
            else None
        )
        db.add(
            DocumentChunk(
                user_id=document.user_id,
                document_id=document.id,
                chunk_index=index,
                content=inhalt,
                token_count=token_count(inhalt),
                embedding=embedding,
            )
        )
    document.status = "indexed"
    document.processed_at = utc_now()
    db.flush()
    return len(absaetze)


def index_folder_document(
    db: Session,
    user_id: int,
    source: SyncSource,
    rel_path: str,
    file_path: Path,
    checksum: str,
    text: str,
    provider: ProviderProfile | None,
) -> tuple[Document, int]:
    """Legt die Zeile zu einer Ordnerdatei an oder aktualisiert sie und schreibt die Absaetze."""
    document = db.scalar(
        select(Document).where(
            Document.user_id == user_id,
            Document.source == DOCUMENTS_SOURCE,
            Document.external_id == rel_path,
        )
    )
    new_row = document is None
    if document is None:
        document = Document(
            user_id=user_id,
            sync_source_id=source.id,
            source=DOCUMENTS_SOURCE,
            external_id=rel_path,
            filename=file_path.name,
            storage_path=str(file_path),
            mime_type=mime_type_for(file_path),
            checksum=checksum,
            status="indexing",
        )
        db.add(document)
        db.flush()
    else:
        document.filename = file_path.name
        document.storage_path = str(file_path)
        document.mime_type = mime_type_for(file_path)
        document.checksum = checksum
    chunk_count = _write_chunks(db, document, text, provider)
    if not new_row:
        db.flush()
    return document, chunk_count


def index_uploaded_document(db: Session, user_id: int, document: Document, settings: Settings) -> None:
    """Extrahiert die hochgeladene Datei und füllt die Absaetze nach.

    Nicht unterstuetzte oder unlesbare Dateien bleiben als ``error`` erhalten;
    die Suche darf an einer kaputten Datei nicht haengen bleiben.
    """
    path = Path(document.storage_path)
    provider = _load_provider(db, user_id, settings)
    try:
        text = read_document_text(path)
    except (OSError, UnicodeError, ValueError) as exc:
        logger.warning("Dokument %s konnte nicht gelesen werden: %s", path.name, exc)
        document.status = "error"
        db.commit()
        return
    if not text.strip():
        document.status = "error"
        db.commit()
        return
    _write_chunks(db, document, text, provider)
    document.extra_data = None
    db.commit()


def sync_documents(
    db: Session,
    source: SyncSource,
    folder: str,
    settings: Settings,
) -> DocumentSyncOutcome:
    """Scannt den Ablageordner einer ``documents``-Integration und ersetzt den Index."""
    from app.services.integrations import IntegrationConnectionError

    root = Path(folder)
    if not root.is_dir():
        raise IntegrationConnectionError(f"Der Dokumentordner '{root}' existiert nicht.")
    provider = _load_provider(db, source.user_id, settings)

    existing = {
        row.external_id: row
        for row in db.scalars(
            select(Document).where(
                Document.user_id == source.user_id,
                Document.source == DOCUMENTS_SOURCE,
                Document.sync_source_id == source.id,
            )
        ).all()
    }

    imported = 0
    updated = 0
    indexed_chunks = 0
    skipped = 0
    gefunden: set[str] = set()
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        rel_path = file_path.relative_to(root).as_posix()
        if not is_supported(file_path):
            skipped += 1
            continue
        gefunden.add(rel_path)
        checksum = file_checksum(file_path)
        row = existing.get(rel_path)
        if row is not None and row.checksum == checksum:
            continue
        try:
            text = read_document_text(file_path)
        except (OSError, UnicodeError, ValueError):
            skipped += 1
            continue
        if not text.strip():
            skipped += 1
            continue
        document, chunk_count = index_folder_document(
            db,
            source.user_id,
            source,
            rel_path,
            file_path,
            checksum,
            text,
            provider,
        )
        indexed_chunks += chunk_count
        if row is None:
            imported += 1
        else:
            updated += 1

    removed = 0
    for alter_rel, row in existing.items():
        if alter_rel not in gefunden:
            db.delete(row)
            removed += 1

    source.last_sync_at = utc_now()
    source.status = "connected"
    source.last_error = None
    db.commit()
    return DocumentSyncOutcome(
        imported_documents=imported,
        updated_documents=updated,
        indexed_chunks=indexed_chunks,
        skipped_files=skipped,
        removed_documents=removed,
    )


def _tokens(query: str) -> list[str]:
    return [token for token in _TOKEN_RE.findall(query.lower()) if len(token) >= 2]


def _keyword_search(db: Session, user_id: int, query: str, limit: int) -> list[SearchHit]:
    tokens = _tokens(query)
    if not tokens:
        return []
    conditions = [DocumentChunk.content.ilike(f"%{token}%") for token in tokens]
    treffer = cast(
        "ColumnElement[int]",
        sum(
            case((DocumentChunk.content.ilike(f"%{token}%"), 1), else_=0)
            for token in tokens
        ),
    )
    stmt = (
        select(DocumentChunk, Document.filename)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.user_id == user_id, or_(*conditions))
        .order_by(treffer.desc(), DocumentChunk.id)
        .limit(limit)
    )
    return [
        SearchHit(
            document_id=chunk.document_id,
            filename=filename,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
        )
        for chunk, filename in db.execute(stmt).all()
    ]


def _vector_search(
    db: Session, user_id: int, embedding: list[float], limit: int
) -> list[SearchHit]:
    col = DocumentChunk.embedding
    distance = col.cosine_distance(embedding)
    stmt = (
        select(DocumentChunk, Document.filename, distance.label("_distance"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.user_id == user_id, col.is_not(None))
        .order_by(distance.asc())
        .limit(limit)
    )
    return [
        SearchHit(
            document_id=chunk.document_id,
            filename=filename,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            distance=float(distance_value),
        )
        for chunk, filename, distance_value in db.execute(stmt).all()
    ]


def _merge_hits(vector_hits: list[SearchHit], keyword_hits: list[SearchHit], limit: int) -> list[SearchHit]:
    gesehen: set[tuple[int, int]] = set()
    merged: list[SearchHit] = []
    for hit in [*vector_hits, *keyword_hits]:
        schluessel = (hit.document_id, hit.chunk_index)
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        merged.append(hit)
        if len(merged) >= limit:
            break
    return merged


def search_chunks(
    db: Session,
    user_id: int,
    settings: Settings,
    query: str,
    limit: int = 8,
) -> list[SearchHit]:
    """Durchsucht die eigenen Dokumentabsaetze nach Treffern.

    Die Stichwortsuche liefert immer Ergebnisse. Steht ein Provider bereit und
    ist die Datenbank PostgreSQL, kommen die Vektortreffer davor - dann wird
    auch die eigentliche Suche des Prompts eingebettet.
    """
    keyword_hits = _keyword_search(db, user_id, query, limit=limit * 2)
    vector_hits: list[SearchHit] = []
    if db.get_bind().dialect.name == "postgresql":
        provider = _load_provider(db, user_id, settings)
        if provider is not None:
            try:
                embedding = generate_embeddings(
                    base_url=provider.base_url,
                    model=provider.model,
                    input_texts=[query],
                    api_key=provider.api_key,
                )[0]
                if len(embedding) == EMBEDDING_DIMENSION:
                    vector_hits = _vector_search(db, user_id, embedding, limit)
            except AiProviderError as exc:
                logger.warning("Vektorsuche nicht möglich: %s", exc)
    return _merge_hits(vector_hits, keyword_hits, limit)


_SYSTEM_PROMPT: str = (
    "Du bist ein Lern-Assistent, der ausschließlich aus den mitgelieferten Dokumentauszügen "
    "antwortet. Antworte auf Deutsch in ganzen Sätzen. Beziehe dich nur auf die Auszüge und "
    "erfinde nichts. Wenn die Auszüge die Frage nicht beantworten, sage das ausdrücklich. "
    "Nenne keine Quellen im Fließtext."
)


def build_rag_prompt(question: str, hits: list[SearchHit]) -> str:
    zeilen = [f"Frage: {question}", "", "Dokumentauszüge:"]
    for index, hit in enumerate(hits, start=1):
        zeilen.append(f"--- Auszug {index}: {hit.filename} (Abschnitt {hit.chunk_index + 1}) ---")
        zeilen.append(hit.content)
    return "\n".join(zeilen)


def answer_question(
    db: Session,
    user_id: int,
    settings: Settings,
    question: str,
    limit: int = 5,
) -> QuestionAnswer:
    """Beantwortet eine Frage anhand der eigenen Dokumente.

    ``source`` unterscheidet dreiwertig: ``ai`` hinter einem echten Modell, ``fallback``
    aus den Daten (kein Provider oder Modellfehler) und ``none`` ohne jeden Treffer.
    """
    hits = search_chunks(db, user_id, settings, question, limit=limit)
    quellen = [hit.source() for hit in hits]
    if not hits:
        return QuestionAnswer(
            answer="In deinen Dokumenten wurde nichts Passendes gefunden.",
            source="none",
            sources=[],
        )

    def _fallback() -> QuestionAnswer:
        dateien = ", ".join(sorted({hit.filename for hit in hits}))
        return QuestionAnswer(
            answer=(
                "Kein KI-Provider ist verbunden, deshalb wurde die Antwort aus den Daten gebaut. "
                f"Relevante Stellen liegen in: {dateien}."
            ),
            source="fallback",
            sources=quellen,
        )

    provider = _load_provider(db, user_id, settings)
    if provider is None:
        return _fallback()
    try:
        text = generate_text(
            base_url=provider.base_url,
            model=provider.model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=build_rag_prompt(question, hits),
            api_key=provider.api_key,
            max_tokens=_MAX_ANSWER_TOKENS,
        )
    except AiProviderError as exc:
        logger.warning("Dokumenten-Antwort konnte nicht erzeugt werden: %s", exc)
        return _fallback()
    return QuestionAnswer(answer=text, source="ai", sources=quellen, model=provider.model)