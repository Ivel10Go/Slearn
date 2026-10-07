"""Datenauskunft und Kontoloeschung (``/api/v1/account``).

Die wichtigste Zusage ist die Vollstaendigkeit: jede Tabelle mit ``user_id``
landet in der Auskunft und wird beim Loeschen geleert. Der erste Test prueft
das ueber die Metadaten, damit eine neue Migration nicht still herausfaellt.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from app.core.security import hash_password
from app.db.base import Base
from app.db.entities import Document, Subject, SyncSource, User, UserSettings
from app.services import account
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def login(client: TestClient, username: str = "tester", password: str = "test-password") -> None:
    antwort = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert antwort.status_code == 200


def zweiter_nutzer(db: Session) -> User:
    other = User(username="andere", password_hash=hash_password("anderes-passwort"), is_active=True, timezone="UTC")
    db.add(other)
    db.commit()
    db.refresh(other)
    db.add(Subject(user_id=other.id, name="Physik"))
    db.commit()
    return other


def test_jede_tabelle_ausser_users_traegt_user_id() -> None:
    namen = {table.name for table in account.user_tables()}
    alle = {table.name for table in Base.metadata.sorted_tables}
    assert alle - namen == {"users"}


def test_export_verlangt_anmeldung(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/account/export").status_code == 401


def test_export_enthaelt_eigene_daten_ohne_geheimnisse(
    app_context: tuple[TestClient, Session, User], tmp_path: Path
) -> None:
    client, db, user = app_context
    zweiter_nutzer(db)
    db.add(Subject(user_id=user.id, name="Mathematik"))
    db.add(UserSettings(user_id=user.id, preferences={"calendar_feed_token": "geheim", "weekly_goal_minutes": 120}))
    db.add(SyncSource(user_id=user.id, source_type="webuntis", name="Schule", credentials="sa1.verschluesselt"))
    db.commit()
    login(client)
    hochgeladen = client.post("/api/v1/files", files={"file": ("notiz.txt", b"Ableitungen und Integrale")})
    assert hochgeladen.status_code == 201

    antwort = client.get("/api/v1/account/export")

    assert antwort.status_code == 200
    assert antwort.headers["content-type"] == "application/zip"
    archiv = zipfile.ZipFile(io.BytesIO(antwort.content))
    daten = json.loads(archiv.read("daten.json"))
    tabellen = daten["tables"]
    assert [fach["name"] for fach in tabellen["subjects"]] == ["Mathematik"]
    assert tabellen["users"][0]["username"] == "tester"
    assert tabellen["users"][0]["password_hash"] == account.REDACTED_MARK
    assert tabellen["sync_sources"][0]["credentials"] == account.REDACTED_MARK
    praeferenzen = tabellen["settings"][0]["preferences"]
    assert praeferenzen["calendar_feed_token"] == account.REDACTED_MARK
    assert praeferenzen["weekly_goal_minutes"] == 120
    assert set(tabellen) >= {table.name for table in account.user_tables()}
    dateien = [name for name in archiv.namelist() if name.startswith("dokumente/")]
    assert len(dateien) == 1 and dateien[0].endswith("notiz.txt")
    assert archiv.read(dateien[0]) == b"Ableitungen und Integrale"
    assert "geheim" not in archiv.read("daten.json").decode()


def test_loeschen_verlangt_bestaetigung_und_passwort(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)

    ohne_wort = client.post("/api/v1/account/delete", json={"confirm": "ja", "password": "test-password"})
    falsches_passwort = client.post("/api/v1/account/delete", json={"confirm": "LÖSCHEN", "password": "falsch"})

    assert ohne_wort.status_code == 422
    assert falsches_passwort.status_code == 403
    db.expire_all()
    assert db.get(User, user.id) is not None


def test_loeschen_entfernt_alles_und_nur_die_eigenen_daten(
    app_context: tuple[TestClient, Session, User], tmp_path: Path
) -> None:
    client, db, user = app_context
    user_id = user.id
    other = zweiter_nutzer(db)
    db.add(Subject(user_id=user.id, name="Mathematik"))
    db.commit()
    login(client)
    hochgeladen = client.post("/api/v1/files", files={"file": ("notiz.txt", b"Text")})
    assert hochgeladen.status_code == 201
    pfad = Path(db.scalar(select(Document.storage_path).where(Document.user_id == user.id)) or "")
    assert pfad.is_file()
    # Eine Datei aus einem verbundenen Ordner gehoert dem Nutzer, nicht der App.
    ordner = tmp_path / "eigener-ordner"
    ordner.mkdir()
    fremd = ordner / "heft.txt"
    fremd.write_text("bleibt", encoding="utf-8")
    db.add(Document(user_id=user.id, filename="heft.txt", storage_path=str(fremd), source="documents"))
    db.commit()

    antwort = client.post("/api/v1/account/delete", json={"confirm": "LÖSCHEN", "password": "test-password"})

    assert antwort.status_code == 200
    assert antwort.json()["deleted_files"] == 1
    db.expire_all()
    assert db.get(User, user_id) is None
    for table in account.user_tables():
        anzahl = db.scalar(select(func.count()).select_from(table).where(table.c.user_id == user_id))
        assert anzahl == 0, table.name
    assert not pfad.exists()
    assert fremd.read_text(encoding="utf-8") == "bleibt"
    assert db.scalar(select(func.count()).select_from(Subject).where(Subject.user_id == other.id)) == 1
    assert client.get("/api/v1/auth/me").status_code == 401


def test_ohne_anmeldung_genuegt_die_bestaetigung(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    user_id = user.id
    client.app.state.settings = client.app.state.settings.model_copy(update={"auth_enabled": False})  # type: ignore[attr-defined]

    antwort = client.post("/api/v1/account/delete", json={"confirm": "LÖSCHEN"})

    assert antwort.status_code == 200
    db.expire_all()
    assert db.get(User, user_id) is None
    assert client.get("/api/v1/setup").json() == {"needs_setup": True}
