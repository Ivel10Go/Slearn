"""Die Sprache der Oberflaeche: ``Accept-Language`` steuert Assistent und Willkommenssatz."""

from __future__ import annotations

import pytest
from app.core.language import parse_accept_language, reply_instruction, voice_matches
from app.db.entities import GreetingCache, User
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

ENGLISCH = {"Accept-Language": "en"}


def login(client: TestClient) -> None:
    antwort = client.post("/api/v1/auth/login", json={"username": "tester", "password": "test-password"})
    assert antwort.status_code == 200


@pytest.mark.parametrize(
    ("kopf", "sprache"),
    [
        (None, "de"),
        ("", "de"),
        ("en", "en"),
        ("en-GB,en;q=0.9", "en"),
        ("fr-FR, en;q=0.5", "en"),
        ("de-AT", "de"),
        ("fr", "de"),
    ],
)
def test_accept_language_waehlt_die_erste_unterstuetzte_sprache(kopf: str | None, sprache: str) -> None:
    assert parse_accept_language(kopf) == sprache


def test_nur_englisch_bekommt_eine_sprachanweisung_und_eine_eigene_stimme() -> None:
    assert reply_instruction("de") == ""
    assert "Englisch" in reply_instruction("en")
    assert voice_matches("en_US", "en")
    assert voice_matches("de_DE", "de")
    assert not voice_matches("de_DE", "en")


def test_assistent_antwortet_ohne_modell_auf_englisch(app_client: TestClient) -> None:
    login(app_client)
    antwort = app_client.post(
        "/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "Hello"}]}, headers=ENGLISCH
    )
    assert antwort.status_code == 200
    body = antwort.json()
    assert body["source"] == "fallback"
    assert body["reply"].startswith("No AI model")


def test_englische_anfrage_haengt_die_sprachanweisung_an_den_prompt(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(app_client)
    verbindung = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "KI",
            "config": {"base_url": "https://api.example.org/v1", "model": "gpt-4o-mini"},
            "credentials": {"api_key": "sk-test"},
        },
    )
    assert verbindung.status_code == 201, verbindung.text
    prompts: list[str] = []

    def fake_generate(*, system_prompt: str, **_: object) -> str:
        prompts.append(system_prompt)
        return "Hi there."

    monkeypatch.setattr("app.services.ai_routing.generate_text", fake_generate)
    body = app_client.post(
        "/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "Hi"}]}, headers=ENGLISCH
    ).json()

    assert body["reply"] == "Hi there."
    assert prompts and prompts[-1].endswith(reply_instruction("en"))


def test_willkommenssatz_wechselt_mit_der_sprache_und_bleibt_eine_zeile(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)

    deutsch = client.get("/api/v1/greeting").json()
    assert deutsch["text"].startswith("Herzlich willkommen")

    englisch = client.get("/api/v1/greeting", headers=ENGLISCH).json()
    assert englisch["source"] == "fallback"
    assert englisch["text"].startswith("Welcome")

    # Gleiche Sprache: aus dem Cache, nicht neu geschrieben.
    assert client.get("/api/v1/greeting", headers=ENGLISCH).json()["generated_at"] == englisch["generated_at"]

    db.expire_all()
    zeilen = db.scalars(select(GreetingCache).where(GreetingCache.user_id == user.id)).all()
    assert len(zeilen) == 1
    assert zeilen[0].language == "en"
