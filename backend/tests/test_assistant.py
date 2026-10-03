"""Der Lern-Assistent: Protokoll, Vorschlaege, Gedaechtnis und Nutzertrennung.

Die wichtigsten Zusagen:

* ``AKTION`` erzeugt nur einen **Vorschlag**; eingetragen wird erst ueber
  ``POST /actions``.
* ``MERKE`` landet im Gedaechtnis und steht bei der naechsten Anfrage im Prompt.
* Eine gewaehlte Verbindung (``integration_id``) wird allein versucht.
* Fremde Faecher und fremde Notizen sind 404.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from app.core.security import hash_password
from app.db.entities import AssistantMemory, CalendarEvent, Exam, Homework, Subject, User
from app.services import assistant as assistant_service
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

BERLIN = ZoneInfo("Europe/Berlin")


def login(client: TestClient, username: str = "tester", password: str = "test-password") -> None:
    antwort = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert antwort.status_code == 200


def _ai(client: TestClient, name: str = "KI", model: str = "gpt-4o-mini") -> int:
    created = client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": name,
            "config": {"base_url": "https://api.example.com/v1", "model": model},
            "credentials": {"api_key": "geheim"},
            "enabled": True,
        },
    )
    assert created.status_code == 201, created.text
    return int(created.json()["id"])


def _fach(db: Session, user: User, name: str, short: str | None = None) -> Subject:
    fach = Subject(user_id=user.id, name=name, short_name=short, color="#000000")
    db.add(fach)
    db.commit()
    db.refresh(fach)
    return fach


# --- Protokoll ---------------------------------------------------------------


def test_parse_reply_trennt_text_aktion_und_merke() -> None:
    roh = (
        "Klar, ich trage die Klausur ein.\n"
        'AKTION {"typ": "pruefung", "titel": "Mathe-Klausur", "datum": "2026-10-09"}\n'
        "MERKE: Lernt lieber morgens.\n"
        "AKTION {kaputt"
    )
    ergebnis = assistant_service.parse_reply(roh)
    assert ergebnis.text == "Klar, ich trage die Klausur ein."
    assert ergebnis.actions == [{"typ": "pruefung", "titel": "Mathe-Klausur", "datum": "2026-10-09"}]
    assert ergebnis.memories == ["Lernt lieber morgens."]


def test_vorschlag_rechnet_in_der_nutzerzone_und_setzt_standards() -> None:
    heute = date(2026, 10, 2)
    pruefung = assistant_service.build_proposal(
        {"typ": "Klausur", "titel": "Mathe", "datum": "2026-10-09"}, [], BERLIN, heute
    )
    assert pruefung is not None
    assert pruefung.kind == "exam"
    # 08:00 Berlin (Sommerzeit) = 06:00 UTC
    assert pruefung.starts_at == datetime(2026, 10, 9, 6, 0, tzinfo=UTC)

    termin = assistant_service.build_proposal(
        {"typ": "termin", "titel": "Zahnarzt", "datum": "9.10.", "uhrzeit": "15:30"}, [], BERLIN, heute
    )
    assert termin is not None and termin.ends_at is not None
    assert termin.starts_at == datetime(2026, 10, 9, 13, 30, tzinfo=UTC)
    assert (termin.ends_at - termin.starts_at).total_seconds() == 3600

    ganztags = assistant_service.build_proposal({"typ": "termin", "titel": "Ausflug", "datum": "morgen"}, [], BERLIN, heute)
    assert ganztags is not None and ganztags.all_day

    assert assistant_service.build_proposal({"typ": "unbekannt", "titel": "X", "datum": "2026-10-09"}, [], BERLIN, heute) is None
    assert assistant_service.build_proposal({"typ": "pruefung", "titel": "X"}, [], BERLIN, heute) is None


def test_fach_wird_ueber_name_kuerzel_oder_eindeutigen_anfang_gefunden() -> None:
    mathe = Subject(id=1, user_id=1, name="Mathematik", short_name="M", color="#000")
    deutsch = Subject(id=2, user_id=1, name="Deutsch", short_name="D", color="#000")
    daf = Subject(id=3, user_id=1, name="Deutsch als Fremdsprache", short_name=None, color="#000")
    faecher = [mathe, deutsch, daf]
    assert assistant_service.match_subject(faecher, "mathematik") is mathe
    assert assistant_service.match_subject(faecher, "M") is mathe
    assert assistant_service.match_subject(faecher, "Mathe") is mathe
    assert assistant_service.match_subject(faecher, "Deutsch") is deutsch
    assert assistant_service.match_subject(faecher, "Physik") is None


# --- Chat --------------------------------------------------------------------


def test_ohne_modell_antwortet_der_assistent_aus_den_daten(app_client: TestClient) -> None:
    login(app_client)
    antwort = app_client.post("/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "Hallo"}]})
    assert antwort.status_code == 200
    body = antwort.json()
    assert body["source"] == "fallback"
    assert body["proposals"] == []
    assert "kein KI-Modell" in body["reply"]


def test_aktion_wird_vorschlag_und_erst_nach_bestaetigung_eingetragen(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db, user = app_context
    login(client)
    _ai(client)
    fach = _fach(db, user, "Mathematik", "M")

    def fake_generate(**_: object) -> str:
        return (
            "Ich schlage die Klausur vor.\n"
            'AKTION {"typ": "pruefung", "titel": "Mathe-Klausur", "fach": "Mathe", "datum": "2099-03-05"}'
        )

    monkeypatch.setattr("app.services.ai_routing.generate_text", fake_generate)
    body = client.post(
        "/api/v1/assistant/chat",
        json={"messages": [{"role": "user", "content": "Trag die Matheklausur am 5.3. ein"}]},
    ).json()

    assert body["source"] == "provider"
    assert body["reply"] == "Ich schlage die Klausur vor."
    assert len(body["proposals"]) == 1
    vorschlag = body["proposals"][0]
    assert vorschlag["kind"] == "exam"
    assert vorschlag["subject_id"] == fach.id
    db.expire_all()
    assert db.scalars(select(Exam)).all() == []

    eingetragen = client.post("/api/v1/assistant/actions", json=vorschlag)
    assert eingetragen.status_code == 201, eingetragen.text
    db.expire_all()
    exam = db.scalars(select(Exam)).one()
    assert exam.title == "Mathe-Klausur"
    assert exam.subject_id == fach.id


def test_hausaufgabe_und_termin_lassen_sich_eintragen(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, _ = app_context
    login(client)
    hausaufgabe = client.post(
        "/api/v1/assistant/actions",
        json={"kind": "homework", "title": "S. 42 Nr. 3", "starts_at": "2099-01-10T07:00:00Z"},
    )
    termin = client.post(
        "/api/v1/assistant/actions",
        json={
            "kind": "event",
            "title": "Elternabend",
            "starts_at": "2099-01-11T17:00:00Z",
            "ends_at": "2099-01-11T18:30:00Z",
        },
    )
    assert hausaufgabe.status_code == 201 and termin.status_code == 201
    db.expire_all()
    assert db.scalars(select(Homework)).one().title == "S. 42 Nr. 3"
    assert db.scalars(select(CalendarEvent)).one().title == "Elternabend"


def test_merke_landet_im_gedaechtnis_und_im_naechsten_prompt(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = app_context
    login(client)
    _ai(client)
    prompts: list[str] = []

    def fake_generate(*, user_prompt: str, **_: object) -> str:
        prompts.append(user_prompt)
        return "Verstanden.\nMERKE: Lernt Vokabeln am liebsten morgens."

    monkeypatch.setattr("app.services.ai_routing.generate_text", fake_generate)
    erste = client.post(
        "/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "Ich lerne morgens am besten."}]}
    ).json()
    assert [m["content"] for m in erste["memories_added"]] == ["Lernt Vokabeln am liebsten morgens."]

    zweite = client.post(
        "/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "Wann soll ich lernen?"}]}
    ).json()
    # Dieselbe Notiz wird nicht doppelt abgelegt.
    assert zweite["memories_added"] == []
    assert "Lernt Vokabeln am liebsten morgens." in prompts[1]
    assert len(client.get("/api/v1/assistant/memories").json()) == 1


def test_gewaehlte_verbindung_wird_allein_versucht(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = app_context
    login(client)
    _ai(client, name="Erste", model="erstes")
    zweite = _ai(client, name="Zweite", model="zweites")
    modelle: list[str] = []

    def fake_generate(*, model: str, **_: object) -> str:
        modelle.append(model)
        return "Antwort."

    monkeypatch.setattr("app.services.ai_routing.generate_text", fake_generate)
    routen = client.get("/api/v1/assistant/routes").json()
    assert [r["name"] for r in routen] == ["Erste", "Zweite"]

    body = client.post(
        "/api/v1/assistant/chat",
        json={"messages": [{"role": "user", "content": "Hi"}], "integration_id": zweite},
    ).json()
    assert body["model"] == "zweites"
    assert modelle == ["zweites"]


def test_letzte_nachricht_muss_von_der_person_sein(app_client: TestClient) -> None:
    login(app_client)
    antwort = app_client.post(
        "/api/v1/assistant/chat", json={"messages": [{"role": "assistant", "content": "Hallo"}]}
    )
    assert antwort.status_code == 422


# --- Nutzertrennung ----------------------------------------------------------


def test_fremdes_fach_und_fremde_notiz_sind_404(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, _ = app_context
    andere = User(username="andere", password_hash=hash_password("pw-andere-123"), is_active=True, timezone="UTC")
    db.add(andere)
    db.commit()
    fremdes_fach = _fach(db, andere, "Chemie")
    notiz = AssistantMemory(user_id=andere.id, content="Geheim", source="manual")
    db.add(notiz)
    db.commit()

    login(client)
    eintrag = client.post(
        "/api/v1/assistant/actions",
        json={"kind": "exam", "title": "X", "starts_at": "2099-01-10T07:00:00Z", "subject_id": fremdes_fach.id},
    )
    assert eintrag.status_code == 404
    assert client.delete(f"/api/v1/assistant/memories/{notiz.id}").status_code == 404
    assert client.get("/api/v1/assistant/memories").json() == []


def test_notizen_von_hand_anlegen_und_vergessen(app_client: TestClient) -> None:
    login(app_client)
    angelegt = app_client.post("/api/v1/assistant/memories", json={"content": "Mag Beispiele mit Fußball."})
    assert angelegt.status_code == 201
    assert angelegt.json()["source"] == "manual"
    doppelt = app_client.post("/api/v1/assistant/memories", json={"content": "mag beispiele mit fußball."})
    assert doppelt.status_code == 409
    geloescht = app_client.delete(f"/api/v1/assistant/memories/{angelegt.json()['id']}")
    assert geloescht.status_code == 200
    assert app_client.get("/api/v1/assistant/memories").json() == []


def test_datumshilfe_meint_mit_naechstem_freitag_den_ersten_nach_heute() -> None:
    """Am Freitag, 2.10.2026, ist "naechster Freitag" der 9.10. - nicht der 16."""
    tabelle = assistant_service.relative_day_table(date(2026, 10, 2))
    assert "morgen = 2026-10-03" in tabelle
    assert "nächster Freitag = 2026-10-09" in tabelle
    assert "übernächster Freitag = 2026-10-16" in tabelle
    assert "nächster Montag = 2026-10-05" in tabelle


def test_parser_versteht_merke_mit_klammern_und_verwirft_weitergedichtete_zeilen() -> None:
    roh = (
        "Assistent: Gern, das merke ich mir.\n"
        'MERKE ("Lernt am liebsten mit einem Partner.")\n'
        "Person: Und was ist mit Mathe?\n"
        "Merke dir die Formel a² + b² = c²."
    )
    ergebnis = assistant_service.parse_reply(roh)
    assert ergebnis.memories == ["Lernt am liebsten mit einem Partner."]
    assert ergebnis.text == "Gern, das merke ich mir.\nMerke dir die Formel a² + b² = c²."


def test_gespraechsmodus_verlangt_kurze_vorlesbare_antworten(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = app_context
    login(client)
    _ai(client)
    aufrufe: list[dict[str, object]] = []

    def fake_generate(**kwargs: object) -> str:
        aufrufe.append(kwargs)
        return "Klar."

    monkeypatch.setattr("app.services.ai_routing.generate_text", fake_generate)
    nachricht = {"messages": [{"role": "user", "content": "Hallo"}]}
    client.post("/api/v1/assistant/chat", json=nachricht)
    client.post("/api/v1/assistant/chat", json={**nachricht, "voice": True})

    assert "vorgelesen" not in str(aufrufe[0]["system_prompt"])
    assert "vorgelesen" in str(aufrufe[1]["system_prompt"])
    assert int(str(aufrufe[1]["max_tokens"])) < int(str(aufrufe[0]["max_tokens"]))


def test_eintragswunsch_wird_erkannt() -> None:
    assert assistant_service.wants_entry("Trag die Mathe-Klausur am Freitag ein")
    assert assistant_service.wants_entry("Kannst du dir notieren, dass ich Montag Bio habe?")
    assert assistant_service.wants_entry("Erinnere mich an die Hausaufgabe")
    assert not assistant_service.wants_entry("Was soll ich heute noch lernen?")
    assert not assistant_service.wants_entry("Erklär mir den Satz des Pythagoras")


def test_ohne_eintragswunsch_gibt_es_keinen_vorschlag_und_keine_doppelte_notiz(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db, user = app_context
    login(client)
    _ai(client)
    db.add(AssistantMemory(user_id=user.id, content="Lernt am liebsten alleine.", source="manual"))
    db.commit()
    prompts: list[str] = []

    def fake_generate(*, system_prompt: str, **_: object) -> str:
        prompts.append(system_prompt)
        return (
            'AKTION {"typ": "hausaufgabe", "titel": "Vokabeln", "datum": "2099-01-05"}\n'
            "MERKE {Lernt am liebsten allein.}"
        )

    monkeypatch.setattr("app.services.ai_routing.generate_text", fake_generate)
    body = client.post(
        "/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "Was soll ich heute lernen?"}]}
    ).json()

    assert "AKTION" not in prompts[0]
    assert body["proposals"] == []
    assert body["memories_added"] == []
    assert body["reply"].startswith("Entschuldige")


def test_bekannte_notiz_mitten_im_satz_wird_entfernt(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db, user = app_context
    login(client)
    _ai(client)
    db.add(AssistantMemory(user_id=user.id, content="Lernt am liebsten alleine.", source="manual"))
    db.commit()
    monkeypatch.setattr(
        "app.services.ai_routing.generate_text",
        lambda **_: "Heute steht Mathe an. Merke: Lernst am liebsten alleine.",
    )
    body = client.post("/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "Was steht an?"}]}).json()
    assert body["reply"] == "Heute steht Mathe an."


def test_datum_aus_der_nachricht_schlaegt_das_modell() -> None:
    freitag = date(2026, 10, 2)
    assert assistant_service.date_from_text("Trag die Bio-Klausur übermorgen ein", freitag) == date(2026, 10, 4)
    assert assistant_service.date_from_text("morgen Mathe-Test", freitag) == date(2026, 10, 3)
    assert assistant_service.date_from_text("nächsten Freitag Klausur", freitag) == date(2026, 10, 9)
    assert assistant_service.date_from_text("übernächsten Montag", freitag) == date(2026, 10, 12)
    assert assistant_service.date_from_text("am Dienstag Elternabend", freitag) == date(2026, 10, 6)
    assert assistant_service.date_from_text("Klausur am 12.11.", freitag) == date(2026, 11, 12)
    assert assistant_service.date_from_text("Ich lerne morgens", freitag) is None
    assert assistant_service.date_from_text("Trag eine Klausur ein", freitag) is None


def test_vorschlag_wird_auf_den_genannten_tag_verschoben_und_ausgesprochen(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    client, _, _ = app_context
    login(client)
    _ai(client)
    # Das Modell verrechnet sich: "uebermorgen" wird bei ihm ein ganz anderer Tag.
    monkeypatch.setattr(
        "app.services.ai_routing.generate_text",
        lambda **_: 'AKTION {"typ": "pruefung", "titel": "Bio-Klausur", "datum": "2099-01-20", "uhrzeit": "10:00"}',
    )
    body = client.post(
        "/api/v1/assistant/chat",
        json={"messages": [{"role": "user", "content": "Trag bitte eine Bio-Klausur übermorgen ein"}]},
    ).json()
    vorschlag = body["proposals"][0]
    erwartet = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()
    assert vorschlag["starts_at"].startswith(f"{erwartet}T10:00")
    assert body["reply"].startswith("Soll ich die Prüfung „Bio-Klausur“ am")
