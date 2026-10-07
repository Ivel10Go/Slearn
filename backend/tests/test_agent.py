"""Der Assistent als Agent: feste Befehle, Selbst-Eintragen und Rueckgaengig (``app/services/agent.py``)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.security import hash_password
from app.db.entities import Exam, Homework, StudyPlanTask, Subject, User
from app.services import agent
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def login(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "tester", "password": "test-password"})
    assert response.status_code == 200


def _ai(client: TestClient) -> None:
    response = client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "KI",
            "config": {"base_url": "https://api.example.com/v1", "model": "gpt-4o-mini"},
            "credentials": {"api_key": "geheim"},
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text


def _fach(db: Session, user: User, name: str, short: str | None = None) -> Subject:
    fach = Subject(user_id=user.id, name=name, short_name=short, color="#000000")
    db.add(fach)
    db.commit()
    db.refresh(fach)
    return fach


def _chat(client: TestClient, text: str, auto: bool = True) -> dict:
    response = client.post(
        "/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": text}], "auto_apply": auto}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _hausaufgabe(db: Session, user: User, title: str, subject: Subject | None = None) -> Homework:
    item = Homework(
        user_id=user.id,
        subject_id=subject.id if subject else None,
        title=title,
        due_date=datetime.now(UTC) + timedelta(days=2),
    )
    db.add(item)
    db.commit()
    return item


# --- Erkennen ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("satz", "erwartet"),
    [
        ("Plan meine Woche", True),
        ("Kannst du meine Lernzeit für die Woche verteilen", True),
        ("Mach mir einen Lernplan", True),
        ("Wochenplan bitte", True),
        ("Wie plane ich meine Woche am besten?", False),
        ("Erklär mir den Zellzyklus", False),
    ],
)
def test_wochenplan_wird_am_satz_erkannt(satz: str, erwartet: bool) -> None:
    assert agent.wants_week_plan(satz) is erwartet


@pytest.mark.parametrize(
    ("satz", "erwartet"),
    [
        ("Mathe Hausaufgabe erledigt", True),
        ("Hab Bio fertig", True),
        ("Hak Deutsch ab", True),
        ("Was ist noch nicht erledigt?", False),
        ("Welche Hausaufgaben sind fertig", False),
        ("Trag die Klausur ein", False),
    ],
)
def test_erledigt_wird_am_satz_erkannt(satz: str, erwartet: bool) -> None:
    assert agent.wants_homework_done(satz) is erwartet


# --- Feste Befehle -----------------------------------------------------------


def test_erledigt_hakt_die_passende_hausaufgabe_ab_ohne_modell(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Mathematik", "M")
    bio = _fach(db, user, "Biologie", "Bio")
    mathe_ha = _hausaufgabe(db, user, "S. 42 Nr. 3", mathe)
    _hausaufgabe(db, user, "Zellzyklus lesen", bio)

    body = _chat(client, "Mathe erledigt")

    assert body["source"] == "fallback"
    assert body["done"] == [
        {
            "kind": "homework_done",
            "id": mathe_ha.id,
            "title": "S. 42 Nr. 3",
            "message": "Hausaufgabe erledigt",
            "undoable": True,
        }
    ]
    db.expire_all()
    assert db.get(Homework, mathe_ha.id).completed is True

    zurueck = client.post("/api/v1/assistant/actions/undo", json={"kind": "homework_done", "id": mathe_ha.id})
    assert zurueck.status_code == 200
    db.expire_all()
    assert db.get(Homework, mathe_ha.id).completed is False


def test_mehrdeutig_erledigt_fragt_nach_statt_zu_raten(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    _hausaufgabe(db, user, "Vokabeln")
    _hausaufgabe(db, user, "Aufsatz")

    body = _chat(client, "Hab alles fertig gemacht")

    assert body["done"] == []
    assert "Welche meinst du?" in body["reply"]
    db.expire_all()
    assert all(not item.completed for item in db.scalars(select(Homework)).all())


def test_plan_meine_woche_traegt_bloecke_ein(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    _hausaufgabe(db, user, "Aufsatz schreiben")

    body = _chat(client, "Plan meine Woche")

    assert body["done"][0]["kind"] == "week_plan"
    assert body["done"][0]["undoable"] is False
    assert body["reply"].startswith("Erledigt: 1 Lernblöcke")
    db.expire_all()
    aufgabe = db.scalars(select(StudyPlanTask)).one()
    assert aufgabe.homework_id is not None
    assert aufgabe.scheduled_for is not None


def test_ohne_selbst_handeln_bleibt_es_beim_modell(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    item = _hausaufgabe(db, user, "Vokabeln")

    body = _chat(client, "Vokabeln erledigt", auto=False)

    assert body["done"] == []
    db.expire_all()
    assert db.get(Homework, item.id).completed is False


# --- Selbst eintragen --------------------------------------------------------


def test_vorschlaege_werden_selbst_eingetragen_und_lassen_sich_zuruecknehmen(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db, user = app_context
    login(client)
    _ai(client)
    _fach(db, user, "Mathematik", "M")

    def fake_generate(**_: object) -> str:
        return (
            "Trage ich ein.\n"
            'AKTION {"typ": "pruefung", "titel": "Mathe-Klausur", "fach": "Mathe", "datum": "2099-03-05"}\n'
            'AKTION {"typ": "pruefung", "titel": "Latein-Test", "fach": "Latein", "datum": "2099-03-06"}'
        )

    monkeypatch.setattr("app.services.ai_routing.generate_text", fake_generate)
    body = _chat(client, "Trag die Matheklausur und den Lateintest ein")

    # Mathe ist eingetragen; Latein gibt es nicht als Fach - das bleibt ein Vorschlag mit Knopf.
    assert [step["title"] for step in body["done"]] == ["Mathe-Klausur"]
    assert [proposal["title"] for proposal in body["proposals"]] == ["Latein-Test"]
    db.expire_all()
    exam = db.scalars(select(Exam)).one()
    assert exam.title == "Mathe-Klausur"

    zurueck = client.post("/api/v1/assistant/actions/undo", json={"kind": "exam", "id": exam.id})
    assert zurueck.status_code == 200
    db.expire_all()
    assert db.scalars(select(Exam)).all() == []


def test_fremder_eintrag_laesst_sich_nicht_zuruecknehmen(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, _ = app_context
    login(client)
    fremd = User(username="andere", password_hash=hash_password("pw-andere-123"), is_active=True, timezone="UTC")
    db.add(fremd)
    db.commit()
    item = _hausaufgabe(db, fremd, "Fremd")

    antwort = client.post("/api/v1/assistant/actions/undo", json={"kind": "homework", "id": item.id})

    assert antwort.status_code == 404
    db.expire_all()
    assert db.get(Homework, item.id) is not None


def test_wochenplan_laesst_sich_nicht_per_undo_loeschen(app_client: TestClient) -> None:
    login(app_client)
    antwort = app_client.post("/api/v1/assistant/actions/undo", json={"kind": "week_plan", "id": 1})
    assert antwort.status_code == 422
