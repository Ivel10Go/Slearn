"""Lernprofil und Abfragemodus.

Die wichtigsten Zusagen:

* Abgefragt wird aus dem eigenen Material; ohne Modell als Lueckenfrage.
* Die Bewertung aendert den Lernstand des passenden Themas - angelegt wird keins.
* Das Lernprofil nennt nur, was in den Daten steht, und steht im Prompt.
* Fremde Fragen sind 404.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from app.core.security import hash_password
from app.db.entities import Document, DocumentChunk, LearningProgress, Subject, Topic, User
from app.db.quiz import QuizQuestion
from app.services import assistant as assistant_service
from app.services import learner
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.test_assistant import login

BERLIN = ZoneInfo("Europe/Berlin")
AUFSCHRIEB = (
    "# Ableitungen\n- Die Ableitung beschreibt die Steigung der Tangente an einem Punkt des Graphen.\n"
    "- Potenzregel: Exponent nach vorne ziehen und den Exponenten um eins verringern."
)


def _aufschrieb(db: Session, user: User, *, thema: str = "Ableitungen") -> tuple[Document, Topic]:
    fach = Subject(user_id=user.id, name="Mathematik", short_name="M")
    db.add(fach)
    db.flush()
    topic = Topic(user_id=user.id, subject_id=fach.id, name=thema)
    db.add(topic)
    dokument = Document(
        user_id=user.id,
        filename="M Aufschrieb.jpg",
        storage_path="x",
        source="upload",
        status="indexed",
        material_kind="aufschrieb",
        material_topic=thema,
        subject_id=fach.id,
    )
    db.add(dokument)
    db.flush()
    db.add(DocumentChunk(user_id=user.id, document_id=dokument.id, chunk_index=0, content=AUFSCHRIEB))
    db.commit()
    return dokument, topic


def _kein_modell(_system: str, _prompt: str, _tokens: int) -> None:
    return None


def test_ohne_modell_lueckenfrage_und_bewertung_ueber_begriffe(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    _, topic = _aufschrieb(db, user)

    frage = learner.ask_question(db, user.id, _kein_modell, rng=random.Random(1))

    assert frage.source == "fallback" and frage.question.startswith("Ergänze die Lücke:")
    assert "_____" in frage.question
    gespeichert = db.get(QuizQuestion, frage.question_id)
    assert gespeichert is not None
    wort = gespeichert.correct_answer["antwort"]

    ergebnis = learner.answer_question(db, user.id, frage.question_id, wort, _kein_modell)

    assert (ergebnis.verdict, ergebnis.topic) == ("richtig", "Ableitungen")
    assert ergebnis.excerpt == AUFSCHRIEB
    fortschritt = db.get(LearningProgress, (user.id, topic.id))
    assert fortschritt is not None and fortschritt.confidence == pytest.approx(0.15)


def test_mit_modell_frage_und_bewertung_aus_der_antwort(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    _, topic = _aufschrieb(db, user)
    db.add(LearningProgress(user_id=user.id, topic_id=topic.id, confidence=0.5))
    db.commit()
    gesehen: list[str] = []

    def modell(_system: str, prompt: str, _tokens: int) -> tuple[str, str]:
        gesehen.append(prompt)
        if "BEWERTUNG" in prompt:
            return "BEWERTUNG: falsch\nERKLAERUNG: Die Ableitung ist die Steigung der Tangente, nicht die Fläche.", "local"
        return "FRAGE: Was beschreibt die Ableitung?\nANTWORT: Die Steigung der Tangente.", "local"

    frage = learner.ask_question(db, user.id, modell)
    assert (frage.question, frage.source) == ("Was beschreibt die Ableitung?", "local")
    assert AUFSCHRIEB in gesehen[0]

    ergebnis = learner.answer_question(db, user.id, frage.question_id, "Die Fläche unter dem Graphen", modell)

    assert ergebnis.verdict == "falsch" and "Tangente" in ergebnis.explanation
    assert ergebnis.reference == "Die Steigung der Tangente."
    db.expire_all()
    fortschritt = db.get(LearningProgress, (user.id, topic.id))
    assert fortschritt is not None and fortschritt.confidence == pytest.approx(0.35)


def test_lernprofil_nennt_nur_was_in_den_daten_steht(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    assert learner.profile_lines(db, user.id, BERLIN) == []
    _, topic = _aufschrieb(db, user)
    db.add(LearningProgress(user_id=user.id, topic_id=topic.id, confidence=0.1))
    db.commit()
    frage = learner.ask_question(db, user.id, _kein_modell, rng=random.Random(2))
    learner.answer_question(db, user.id, frage.question_id, "keine Ahnung", _kein_modell)

    zeilen = learner.profile_lines(db, user.id, BERLIN, datetime(2026, 10, 6, 10, tzinfo=UTC))

    assert "Unsicher bei: Ableitungen (M)." in zeilen
    assert "Abfragen zuletzt: M 0 von 1 richtig." in zeilen
    assert "Eigene Aufschriebe: M 1." in zeilen
    prompt = assistant_service.build_user_prompt([], ["Heute: Dienstag"], [], [], [], profile=zeilen)
    assert "--- Lernprofil ---\n- Unsicher bei: Ableitungen (M)." in prompt


def test_abfrage_ueber_die_api_und_fremde_fragen(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    assert client.post("/api/v1/assistant/quiz", json={}).status_code == 422
    _aufschrieb(db, user)

    frage = client.post("/api/v1/assistant/quiz", json={}).json()
    assert frage["filename"] == "M Aufschrieb.jpg"
    url = f"/api/v1/assistant/quiz/questions/{frage['question_id']}/answer"
    ergebnis = client.post(url, json={"answer": "Tangente"})
    assert ergebnis.status_code == 200 and ergebnis.json()["verdict"] in learner.VERDICTS
    assert client.get("/api/v1/assistant/profile").json()["lines"]

    andere = User(username="andere", password_hash=hash_password("pw-andere-123"), is_active=True, timezone="UTC")
    db.add(andere)
    db.commit()
    _aufschrieb(db, andere, thema="Geheim")
    fremd = learner.ask_question(db, andere.id, _kein_modell)
    antwort = client.post(f"/api/v1/assistant/quiz/questions/{fremd.question_id}/answer", json={"answer": "x"})
    assert antwort.status_code == 404
