"""Klassenarbeit als Arbeitsbereich: Material-Auswahl, Studio, Lernplan aus dem Material."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.db.entities import Document, DocumentChunk, Exam, Subject, Topic, User
from app.services.notebook import parse_pairs
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def login(client: TestClient) -> None:
    assert client.post("/api/v1/auth/login", json={"username": "tester", "password": "test-password"}).status_code == 200


def setup_exam(db: Session, user: User) -> tuple[Exam, Document, Document]:
    mathe = Subject(user_id=user.id, name="Mathe")
    deutsch = Subject(user_id=user.id, name="Deutsch")
    db.add_all([mathe, deutsch])
    db.flush()
    exam = Exam(
        user_id=user.id, subject_id=mathe.id, title="Ableitungen", date=datetime.now(UTC) + timedelta(days=10)
    )
    mathe_doc = Document(
        user_id=user.id, subject_id=mathe.id, filename="ableitung.txt", storage_path="x", material_topic="Kettenregel"
    )
    deutsch_doc = Document(user_id=user.id, subject_id=deutsch.id, filename="gedicht.txt", storage_path="y")
    db.add_all([exam, mathe_doc, deutsch_doc])
    db.flush()
    db.add_all(
        [
            DocumentChunk(user_id=user.id, document_id=mathe_doc.id, chunk_index=0, content="Kettenregel: f(g(x))'"),
            DocumentChunk(user_id=user.id, document_id=deutsch_doc.id, chunk_index=0, content="Metrum und Reim"),
        ]
    )
    db.commit()
    return exam, mathe_doc, deutsch_doc


def test_parse_pairs_folgezeilen_und_unvollstaendige_paare() -> None:
    raw = "Hier deine Karten:\n1. F: Was ist die Kettenregel?\nA: Äußere mal innere\nAbleitung.\nF: Ohne Antwort\n"
    assert parse_pairs(raw, ("F",), ("A",), ("front", "back")) == [
        {"front": "Was ist die Kettenregel?", "back": "Äußere mal innere\nAbleitung."}
    ]


def test_material_ist_fach_vorgewaehlt_und_waehlbar(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    exam, mathe_doc, deutsch_doc = setup_exam(db, user)
    login(client)

    docs = client.get(f"/api/v1/exams/{exam.id}/notebook").json()["documents"]
    assert {d["id"]: d["selected"] for d in docs} == {mathe_doc.id: True, deutsch_doc.id: False}

    assert client.put(f"/api/v1/exams/{exam.id}/notebook/documents", json={"document_ids": [deutsch_doc.id]}).status_code == 200
    docs = client.get(f"/api/v1/exams/{exam.id}/notebook").json()["documents"]
    assert {d["id"]: d["selected"] for d in docs} == {mathe_doc.id: False, deutsch_doc.id: True}

    assert client.put(f"/api/v1/exams/{exam.id}/notebook/documents", json={"document_ids": [9999]}).status_code == 404


def test_studio_erzeugt_und_speichert_karteikarten(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db, user = app_context
    exam, _, _ = setup_exam(db, user)
    login(client)
    prompts: list[str] = []

    def fake_routes(*_: object):
        def generate(system: str, prompt: str, max_tokens: int) -> tuple[str, str]:
            prompts.append(prompt)
            return "F: Kettenregel?\nA: Äußere mal innere Ableitung", "local"

        return generate

    monkeypatch.setattr("app.services.learner.routes_generator", fake_routes)
    response = client.post(f"/api/v1/exams/{exam.id}/notebook/creations", json={"kind": "flashcards"})
    assert response.status_code == 201
    assert response.json()["items"] == [{"front": "Kettenregel?", "back": "Äußere mal innere Ableitung"}]
    # Nur das Material der Auswahl (Fach Mathe) geht ins Modell.
    assert "Kettenregel: f(g(x))'" in prompts[0] and "Metrum" not in prompts[0]

    creations = client.get(f"/api/v1/exams/{exam.id}/notebook").json()["creations"]
    assert [c["kind"] for c in creations] == ["flashcards"]
    assert client.delete(f"/api/v1/exams/{exam.id}/notebook/creations/{creations[0]['id']}").status_code == 200
    assert client.get(f"/api/v1/exams/{exam.id}/notebook").json()["creations"] == []


def test_kurzfassung_ersetzt_den_text_und_wird_gemerkt(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db, user = app_context
    exam, mathe_doc, deutsch_doc = setup_exam(db, user)
    login(client)
    calls: list[tuple[str, str]] = []

    def fake_routes(*_: object):
        def generate(system: str, prompt: str, max_tokens: int) -> tuple[str, str]:
            calls.append((system, prompt))
            if max_tokens == 300:
                return "- Reim und Versmaß", "local"
            return "Zusammenfassung", "local"

        return generate

    monkeypatch.setattr("app.services.learner.routes_generator", fake_routes)
    body = {"document_ids": [mathe_doc.id, deutsch_doc.id], "summary_ids": [deutsch_doc.id]}
    assert client.put(f"/api/v1/exams/{exam.id}/notebook/documents", json=body).status_code == 200
    docs = client.get(f"/api/v1/exams/{exam.id}/notebook").json()["documents"]
    assert {d["id"]: d["summary"] for d in docs} == {mathe_doc.id: False, deutsch_doc.id: True}

    assert client.post(f"/api/v1/exams/{exam.id}/notebook/creations", json={"kind": "summary"}).status_code == 201
    final = calls[-1][1]
    assert "Kettenregel: f(g(x))'" in final  # ganzer Text
    assert "- Reim und Versmaß" in final and "Metrum und Reim" not in final  # nur Kurzfassung

    # Zweites Erstellen: Kurzfassung kommt aus dem Speicher, kein zweiter Kurzfassungsaufruf.
    assert client.post(f"/api/v1/exams/{exam.id}/notebook/creations", json={"kind": "summary"}).status_code == 201
    assert len(calls) == 3


def test_studio_ohne_modell_ist_422(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    exam, _, _ = setup_exam(db, user)
    login(client)
    response = client.post(f"/api/v1/exams/{exam.id}/notebook/creations", json={"kind": "summary"})
    assert response.status_code == 422
    assert "KI-Modell" in response.json()["detail"]


def test_lernplan_nimmt_themen_aus_dem_material(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    exam, _, _ = setup_exam(db, user)
    login(client)
    response = client.post(f"/api/v1/exams/{exam.id}/notebook/plan")
    assert response.status_code == 201, response.text
    assert response.json()["created_count"] == 1
    db.expire_all()
    assert db.scalars(select(Topic.name).where(Topic.subject_id == exam.subject_id)).all() == ["Kettenregel"]
    # Zweiter Klick: keine Duplikate.
    assert client.post(f"/api/v1/exams/{exam.id}/notebook/plan").json()["created_count"] == 0


def test_fremde_klassenarbeit_ist_404(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    other = User(username="other", password_hash="x", is_active=True)
    db.add(other)
    db.flush()
    exam = Exam(user_id=other.id, title="Fremd", date=datetime.now(UTC))
    db.add(exam)
    db.commit()
    login(client)
    assert client.get(f"/api/v1/exams/{exam.id}/notebook").status_code == 404
    assert client.post(f"/api/v1/exams/{exam.id}/notebook/plan").status_code == 404
