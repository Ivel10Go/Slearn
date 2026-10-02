"""Stundenplan-Upload: Auslesen (Vorschlag) und Uebernehmen."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from app.api import integrations as _integrations_api
from app.db.entities import Lesson, Subject, User
from app.services import timetable_upload
from app.services.timetable_upload import TimetableUploadError, parse_entries
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_ECHTER_SOFORTABRUF = _integrations_api._sync_after_connect


def login(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "tester", "password": "test-password"})
    assert response.status_code == 200


MODEL_ANTWORT = """Hier ist der Plan:
```json
{"entries": [
  {"weekday": 0, "start": "07:45", "end": "08:30", "subject": "Mathematik", "short_name": "M", "room": "322"},
  {"weekday": 1, "start": "08:35", "end": "09:20", "subject": "Deutsch", "short_name": "D"},
  {"weekday": 9, "start": "08:35", "end": "09:20", "subject": "Samstag"},
  {"weekday": 2, "start": "10:00", "end": "09:00", "subject": "Rückwärts"}
]}
```"""


def test_parse_entries_ignoriert_zaun_und_kaputte_eintraege() -> None:
    entries = parse_entries(MODEL_ANTWORT)
    assert [e.subject for e in entries] == ["Mathematik", "Deutsch"]
    assert entries[0].room == "322"
    assert entries[1].room is None


def test_parse_entries_ohne_json_ist_klare_meldung() -> None:
    with pytest.raises(TimetableUploadError):
        parse_entries("Ich kann das Bild nicht lesen.")


def test_extract_ohne_ki_anbieter_gibt_422_mit_klartext(app_client: TestClient) -> None:
    login(app_client)
    response = app_client.post("/api/v1/timetable/extract", files={"file": ("plan.png", b"\x89PNG", "image/png")})
    assert response.status_code == 422
    assert "KI-Anbieter" in response.json()["detail"]


def test_extract_lehnt_unbekanntes_format_ab(app_client: TestClient) -> None:
    login(app_client)
    response = app_client.post("/api/v1/timetable/extract", files={"file": ("plan.docx", b"x", "text/plain")})
    assert response.status_code == 422
    assert "PDF" in response.json()["detail"]


def test_extract_liest_bild_ueber_anbieter(app_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    login(app_client)
    created = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "Vision",
            "config": {"base_url": "http://localhost:11434/v1", "model": "llava"},
            "credentials": {"api_key": "geheim"},
        },
    )
    assert created.status_code == 201
    seen: dict[str, object] = {}

    def fake(base_url: str, model: str, system: str, user: str, image: bytes, mime: str, **kwargs: object) -> str:
        seen.update(image=image, mime=mime, model=model)
        return MODEL_ANTWORT

    monkeypatch.setattr(timetable_upload, "generate_text_from_image", fake)
    response = app_client.post("/api/v1/timetable/extract", files={"file": ("plan.jpg", b"JPEGDATA", "image/jpeg")})
    assert response.status_code == 200
    body = response.json()
    assert body["via"] == "image"
    assert [s["name"] for s in body["subjects"]] == ["Mathematik", "Deutsch"]
    assert all(s["exists"] is False for s in body["subjects"])
    assert seen["mime"] == "image/jpeg"
    assert seen["image"] == b"JPEGDATA"


def test_apply_legt_faecher_an_und_ist_wiederholbar(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    payload = {
        "weeks": 2,
        "entries": [
            {"weekday": 0, "start": "07:45", "end": "08:30", "subject": "Mathematik", "short_name": "M", "room": "322"},
            {"weekday": 2, "start": "08:35", "end": "09:20", "subject": "Mathematik"},
        ],
    }
    first = client.post("/api/v1/timetable/apply", json=payload)
    assert first.status_code == 201
    assert first.json()["created_subjects"] == 1
    assert first.json()["imported_lessons"] == 4

    second = client.post("/api/v1/timetable/apply", json=payload)
    assert second.json()["created_subjects"] == 0

    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Subject).where(Subject.user_id == user.id)) == 1
    assert db.scalar(select(func.count()).select_from(Lesson).where(Lesson.user_id == user.id)) == 4
    assert db.scalar(select(Subject.short_name).where(Subject.user_id == user.id)) == "M"


def test_apply_fasst_webuntis_stunden_nicht_an(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    subject = Subject(user_id=user.id, name="Physik")
    db.add(subject)
    db.flush()
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    start = datetime.combine(monday, time(9, 0), tzinfo=UTC)
    db.add(
        Lesson(
            user_id=user.id,
            subject_id=subject.id,
            start_time=start,
            end_time=start + timedelta(hours=1),
            source="webuntis",
            external_id="w1",
        )
    )
    db.commit()

    response = client.post(
        "/api/v1/timetable/apply",
        json={"weeks": 1, "entries": [{"weekday": 0, "start": "07:45", "end": "08:30", "subject": "Mathematik"}]},
    )
    assert response.status_code == 201
    db.expire_all()
    sources = set(db.scalars(select(Lesson.source).where(Lesson.user_id == user.id)).all())
    assert sources == {"webuntis", "upload"}


def test_webuntis_wird_sofort_nach_dem_verbinden_abgerufen(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import integrations as integrations_api

    login(app_client)
    monkeypatch.setattr(integrations_api, "_sync_after_connect", _ECHTER_SOFORTABRUF)
    calls: list[str] = []

    def fake_sync(**kwargs: object) -> None:
        calls.append(str(kwargs["source"].source_type))  # type: ignore[attr-defined]

    monkeypatch.setattr(integrations_api, "run_integration_sync", fake_sync)

    webuntis = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "webuntis",
            "name": "Schulportal",
            "config": {"base_url": "https://schule.webuntis.com", "school": "schule"},
            "credentials": {"username": "nutzer", "password": "geheim"},
        },
    )
    assert webuntis.status_code == 201
    app_client.post(
        "/api/v1/integrations",
        json={"kind": "ai_provider", "name": "KI", "config": {"base_url": "http://localhost:11434/v1", "model": "x"}},
    )
    assert calls == ["webuntis"]

    # Nur Umbenennen holt nichts neu; neue Zugangsdaten schon.
    integration_id = webuntis.json()["id"]
    app_client.patch(f"/api/v1/integrations/{integration_id}", json={"name": "Anders"})
    assert calls == ["webuntis"]
    app_client.patch(
        f"/api/v1/integrations/{integration_id}", json={"credentials": {"username": "nutzer", "password": "neu"}}
    )
    assert calls == ["webuntis", "webuntis"]
