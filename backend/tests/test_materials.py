"""Unterrichtsmaterial: Lesen, Erkennen, Abschreiben im Hintergrund, Bedeutungssuche.

Bild- und Einbettungsmodell sind Attrappen. Die wichtigsten Zusagen:

* Ein Foto blockiert den Upload nicht: es wartet (``queued``) und wird vom
  Indexer abgeschrieben; ohne Bildmodell wartet es (``needs_model``), bis eins da ist.
* Art, Fach, Thema und Datum kommen zuerst aus dem Dateinamen.
* Von Hand Korrigiertes ueberschreibt die Erkennung nie wieder.
* Die Bedeutungssuche findet nur eigene Absaetze und den passenden zuerst.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from app.core.security import hash_password
from app.db.entities import ChunkEmbedding, Document, DocumentChunk, Subject, User
from app.services import materials
from app.services.documents import search_chunks
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from tests.test_assistant import login

HEUTE = date(2026, 10, 6)
ABSCHRIFT = "# Ableitungen\n- Steigung der Tangente = f'(x)\n- **Merke:** Potenzregel: (x^n)' = n*x^(n-1)"


def _fach(db: Session, user: User, name: str, kurz: str | None = None) -> Subject:
    subject = Subject(user_id=user.id, name=name, short_name=kurz)
    db.add(subject)
    db.commit()
    return subject


def _indexer(db: Session, tools: materials.MaterialTools) -> materials.MaterialIndexer:
    factory = sessionmaker(bind=db.get_bind(), class_=Session, autoflush=False, expire_on_commit=False)
    return materials.MaterialIndexer(factory, lambda _db, _user: tools, today=lambda: HEUTE)


def _hochladen(client: TestClient, name: str, inhalt: bytes, typ: str) -> dict[str, Any]:
    antwort = client.post("/api/v1/files", files={"file": (name, inhalt, typ)})
    assert antwort.status_code == 201, antwort.text
    body: dict[str, Any] = antwort.json()
    return body


# --- Erkennen ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("dateiname", "text", "art"),
    [
        ("Mathe Aufschrieb 12.03.2026.jpg", "", "aufschrieb"),
        ("AB 3 Brüche.pdf", "", "arbeitsblatt"),
        ("Bio Lösung Zellatmung.pdf", "", "loesung"),
        ("IMG_2041.jpg", "Aufgabe 1: Berechne", "arbeitsblatt"),
        ("scan.pdf", "Merke: Der Satz des Pythagoras", "aufschrieb"),
    ],
)
def test_art_kommt_aus_dateiname_und_textanfang(dateiname: str, text: str, art: str) -> None:
    assert materials.classify(dateiname, text, [], handwritten=False, today=HEUTE).kind == art


def test_fach_thema_und_datum(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    mathe = _fach(db, user, "Mathematik", "M")
    _fach(db, user, "Deutsch", "D")
    subjects = db.scalars(select(Subject)).all()

    info = materials.classify("M Aufschrieb 12.03.jpg", ABSCHRIFT, subjects, handwritten=True, today=HEUTE)

    assert (info.kind, info.subject_id, info.topic) == ("aufschrieb", mathe.id, "Ableitungen")
    # Ohne Jahr: der vergangene 12. Maerz, nicht der kommende.
    assert info.material_date == date(2026, 3, 12)
    assert materials.classify("IMG_1.jpg", "kritzel", subjects, handwritten=True, today=HEUTE).kind == "aufschrieb"
    assert materials.classify("IMG_1.jpg", "kritzel", subjects, handwritten=False, today=HEUTE).kind == "sonstiges"


# --- Hochladen und Abschreiben -------------------------------------------------


def test_foto_wartet_und_wird_im_hintergrund_abgeschrieben(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    _fach(db, user, "Mathe")

    hochgeladen = _hochladen(client, "Mathe Aufschrieb 12.03.jpg", b"\xff\xd8bild", "image/jpeg")
    assert hochgeladen["status"] == "queued"

    # Ohne Bildmodell: warten statt raten.
    assert _indexer(db, materials.MaterialTools()).run_once() is False
    db.expire_all()
    wartend = db.get(Document, hochgeladen["id"])
    assert wartend is not None and wartend.status == "needs_model"

    gesehen: list[tuple[bytes, str]] = []

    def abschreiben(bild: bytes, typ: str) -> str:
        gesehen.append((bild, typ))
        return ABSCHRIFT

    assert _indexer(db, materials.MaterialTools(transcribe=abschreiben)).run_once() is True

    db.expire_all()
    dokument = db.get(Document, hochgeladen["id"])
    assert dokument is not None
    assert gesehen == [(b"\xff\xd8bild", "image/jpeg")]
    assert (dokument.status, dokument.material_kind, dokument.material_topic) == ("indexed", "aufschrieb", "Ableitungen")
    assert dokument.text_source == "ocr_local"
    assert dokument.material_date == date(2026, 3, 12)
    absatz = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == dokument.id)).one()
    assert "Potenzregel" in absatz.content

    liste = client.get("/api/v1/documents").json()
    assert liste[0]["material_kind"] == "aufschrieb"


def test_textdatei_wird_sofort_gelesen_und_erkannt(app_context: tuple[TestClient, Session, User]) -> None:
    client, _, _ = app_context
    login(client)

    hochgeladen = _hochladen(client, "Arbeitsblatt Brüche.md", "# Brüche kürzen\nAufgabe 1".encode(), "text/markdown")

    assert hochgeladen["status"] == "indexed"
    eintrag = client.get("/api/v1/documents").json()[0]
    assert (eintrag["material_kind"], eintrag["material_topic"], eintrag["text_source"]) == (
        "arbeitsblatt",
        "Brüche kürzen",
        "text",
    )


def test_korrektur_von_hand_bleibt_bestehen(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, _ = app_context
    login(client)
    hochgeladen = _hochladen(client, "Notizen.md", b"# Gedichtanalyse\nText", "text/markdown")
    url = f"/api/v1/documents/{hochgeladen['id']}/material"

    antwort = client.patch(url, json={"material_kind": "loesung", "material_topic": "Lyrik"})
    assert antwort.status_code == 200
    assert (antwort.json()["material_kind"], antwort.json()["material_topic"]) == ("loesung", "Lyrik")

    # Erneute Erkennung (etwa nach dem Abschreiben) laesst die Korrektur stehen.
    db.expire_all()
    dokument = db.get(Document, hochgeladen["id"])
    assert dokument is not None
    materials.apply_classification(db, dokument, "# Anderes Thema\nAufschrieb", handwritten=True, today=HEUTE)
    assert (dokument.material_kind, dokument.material_topic) == ("loesung", "Lyrik")
    db.rollback()

    andere = User(username="andere", password_hash=hash_password("pw-andere-123"), is_active=True, timezone="UTC")
    db.add(andere)
    db.commit()
    fremdes_fach = _fach(db, andere, "Physik")
    assert client.patch(url, json={"subject_id": fremdes_fach.id}).status_code == 404


# --- Bedeutungssuche -----------------------------------------------------------

WOERTER = ("steigung", "gedicht", "zelle")


def _einbettung(texte: Any) -> list[list[float]]:
    """Ein Vektor je Text: wie oft kommt jedes Merkwort vor (Ableitung zaehlt als Steigung)."""
    vektoren = []
    for text in texte:
        klein = text.lower().replace("ableitung", "steigung").replace("tangente", "steigung")
        vektoren.append([float(klein.count(wort)) + 0.01 for wort in WOERTER])
    return vektoren


class FakeRuntimes:
    def installed_models(self, task: str) -> list[Any]:
        return [type("Spec", (), {"id": "bge-m3-q8"})()] if task == "embedding" else []

    def ensure_embedding_model(self, spec: Any) -> str:
        return "http://einbettung"


def test_bedeutungssuche_findet_den_passenden_absatz_nur_bei_einem_selbst(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db, user = app_context
    andere = User(username="andere", password_hash=hash_password("pw-andere-123"), is_active=True, timezone="UTC")
    db.add(andere)
    db.commit()
    for besitzer, name, inhalt in (
        (user, "Bio.md", "Die Zelle hat einen Kern."),
        (user, "Mathe.md", "Die Ableitung ist die Tangente an den Graphen."),
        (andere, "Fremd.md", "Steigung Steigung Steigung"),
    ):
        dokument = Document(user_id=besitzer.id, filename=name, storage_path=name, source="upload", status="indexed")
        db.add(dokument)
        db.flush()
        db.add(DocumentChunk(user_id=besitzer.id, document_id=dokument.id, chunk_index=0, content=inhalt))
    db.commit()

    for besitzer in (user, andere):
        materials.embed_missing_chunks(db, besitzer.id, "bge-m3-q8", _einbettung)
    db.commit()
    assert len(db.scalars(select(ChunkEmbedding)).all()) == 3
    # Ein zweiter Lauf rechnet nichts doppelt.
    assert materials.embed_missing_chunks(db, user.id, "bge-m3-q8", _einbettung) == 0

    monkeypatch.setattr("app.services.local_runtimes.active_manager", lambda: FakeRuntimes())
    monkeypatch.setattr(materials, "http_embedder", lambda _url: _einbettung)
    settings = client.app.state.settings  # type: ignore[attr-defined]

    treffer = search_chunks(db, user.id, settings, "Wie berechne ich die Steigung?", limit=3)

    assert treffer[0].filename == "Mathe.md"
    assert "Fremd.md" not in {hit.filename for hit in treffer}
