"""Trainingsbeispiele: Freigabe, Signale, Urteil, Nutzertrennung, Hardware.

Die wichtigsten Zusagen:

* Ohne Freigabe wird **nichts** abgelegt - Standard ist aus.
* Ein Beispiel ist genau das, was das Modell gesehen und geantwortet hat
  (Rohtext mit ``AKTION``/``MERKE``).
* Eintragen, Verwerfen, eine geloeschte Notiz und der Daumen werden als
  Tatsachen vermerkt; das Urteil wird daraus gerechnet.
* Fremde Beispiele sind 404 und tauchen nirgends auf.
* Training wird nur auf Apple Silicon (ab 16 GB) oder NVIDIA (ab 6 GB) angeboten.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.core.security import hash_password
from app.db.entities import AssistantMemory, TrainingExample, User
from app.services import training as training_service
from app.services.training_hardware import GpuInfo, evaluate_hardware, parse_nvidia_smi
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.test_assistant import _ai, login

ANTWORT_MIT_AKTION = (
    "Ich schlage die Klausur vor.\n"
    'AKTION {"typ": "pruefung", "titel": "Mathe-Klausur", "datum": "2099-03-05"}\n'
    "MERKE: Schreibt Klausuren gern früh im Halbjahr."
)


def _chat(client: TestClient, text: str = "Trag die Matheklausur ein") -> dict[str, Any]:
    antwort = client.post("/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": text}]})
    assert antwort.status_code == 200, antwort.text
    body: dict[str, Any] = antwort.json()
    return body


def _freigeben(client: TestClient, enabled: bool = True) -> None:
    antwort = client.put("/api/v1/assistant/training/consent", json={"enabled": enabled})
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["enabled"] is enabled


@pytest.fixture
def modell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.ai_routing.generate_text", lambda **_: ANTWORT_MIT_AKTION)


def test_ohne_freigabe_wird_nichts_abgelegt(app_context: tuple[TestClient, Session, User], modell: None) -> None:
    client, db, _ = app_context
    login(client)
    _ai(client)
    assert client.get("/api/v1/assistant/training").json()["enabled"] is False

    body = _chat(client)

    assert body["training_example_id"] is None
    db.expire_all()
    assert db.scalars(select(TrainingExample)).all() == []


def test_mit_freigabe_wird_der_rohtext_samt_prompt_abgelegt(
    app_context: tuple[TestClient, Session, User], modell: None
) -> None:
    client, db, _ = app_context
    login(client)
    _ai(client)
    _freigeben(client)

    body = _chat(client)

    db.expire_all()
    beispiel = db.scalars(select(TrainingExample)).one()
    assert body["training_example_id"] == beispiel.id
    assert beispiel.completion == ANTWORT_MIT_AKTION
    assert "AKTION" in beispiel.system_prompt
    assert beispiel.prompt.rstrip().endswith("Assistent:")
    assert beispiel.source == "provider"
    # Die gemerkte Notiz zeigt auf die Antwort, aus der sie stammt.
    notiz = db.scalars(select(AssistantMemory)).one()
    assert notiz.training_example_id == beispiel.id
    assert training_service.example_label(beispiel) is None


def test_ohne_modell_gibt_es_kein_beispiel(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, _ = app_context
    login(client)
    _freigeben(client)

    body = _chat(client, "Was steht an?")

    assert body["source"] == "fallback"
    assert body["training_example_id"] is None
    db.expire_all()
    assert db.scalars(select(TrainingExample)).all() == []


def test_eintragen_macht_das_beispiel_gut_und_merkt_den_zielwert(
    app_context: tuple[TestClient, Session, User], modell: None
) -> None:
    client, db, _ = app_context
    login(client)
    _ai(client)
    _freigeben(client)
    body = _chat(client)
    vorschlag = {**body["proposals"][0], "training_example_id": body["training_example_id"]}

    assert client.post("/api/v1/assistant/actions", json=vorschlag).status_code == 201

    db.expire_all()
    beispiel = db.scalars(select(TrainingExample)).one()
    assert beispiel.applied_actions[0]["title"] == "Mathe-Klausur"
    assert "training_example_id" not in beispiel.applied_actions[0]
    assert training_service.example_label(beispiel) == "good"
    assert client.get("/api/v1/assistant/training").json()["stats"]["good"] == 1


def test_verwerfen_und_geloeschte_notiz_machen_das_beispiel_schlecht(
    app_context: tuple[TestClient, Session, User], modell: None
) -> None:
    client, db, _ = app_context
    login(client)
    _ai(client)
    _freigeben(client)
    erste = _chat(client)
    zweite = _chat(client, "Und noch einmal eintragen bitte")

    verworfen = client.post(
        f"/api/v1/assistant/training/examples/{erste['training_example_id']}/feedback", json={"event": "rejected"}
    )
    assert verworfen.status_code == 200
    notiz_id = client.get("/api/v1/assistant/memories").json()[0]["id"]
    assert client.delete(f"/api/v1/assistant/memories/{notiz_id}").status_code == 200

    db.expire_all()
    labels = {e.id: training_service.example_label(e) for e in db.scalars(select(TrainingExample))}
    assert labels[erste["training_example_id"]] == "bad"
    # Die Notiz stammt aus der ersten Antwort; die zweite hat sie nicht noch einmal gemerkt.
    assert labels[zweite["training_example_id"]] is None


def test_daumen_schlaegt_die_anderen_signale_und_laesst_sich_zuruecknehmen(
    app_context: tuple[TestClient, Session, User], modell: None
) -> None:
    client, _, _ = app_context
    login(client)
    _ai(client)
    _freigeben(client)
    beispiel_id = _chat(client)["training_example_id"]
    url = f"/api/v1/assistant/training/examples/{beispiel_id}/feedback"

    client.post(url, json={"event": "rejected"})
    client.post(url, json={"event": "good"})
    assert client.get("/api/v1/assistant/training/examples").json()[0]["label"] == "good"

    client.post(url, json={"event": "cleared"})
    eintrag = client.get("/api/v1/assistant/training/examples").json()[0]
    assert eintrag["label"] == "bad"
    assert eintrag["question"] == "Trag die Matheklausur ein"


def test_korrigiertes_datum_gilt_als_korrigiert_nicht_als_gut() -> None:
    beispiel = TrainingExample(
        user_id=1,
        system_prompt="s",
        prompt="p",
        completion="c",
        source="local",
        server_adjusted=True,
        applied_actions=[{"title": "Bio-Klausur"}],
        rejected_actions=0,
        memory_deleted=False,
    )
    assert training_service.example_label(beispiel) == "corrected"
    assert training_service.export_rows([beispiel])[0]["label"] == "corrected"


def test_widerruf_behaelt_die_beispiele_und_loeschen_raeumt_auf(
    app_context: tuple[TestClient, Session, User], modell: None
) -> None:
    client, _, _ = app_context
    login(client)
    _ai(client)
    _freigeben(client)
    _chat(client)
    _chat(client, "Nochmal bitte eintragen")

    _freigeben(client, enabled=False)
    assert client.get("/api/v1/assistant/training").json()["stats"]["total"] == 2
    assert _chat(client)["training_example_id"] is None

    erstes = client.get("/api/v1/assistant/training/examples").json()[0]["id"]
    assert client.delete(f"/api/v1/assistant/training/examples/{erstes}").status_code == 200
    assert client.delete("/api/v1/assistant/training/examples").json()["message"] == "1 Beispiele gelöscht."
    assert client.get("/api/v1/assistant/training").json()["stats"]["total"] == 0


def test_fremde_beispiele_sind_404(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, _ = app_context
    andere = User(username="andere", password_hash=hash_password("pw-andere-123"), is_active=True, timezone="UTC")
    db.add(andere)
    db.commit()
    fremd = TrainingExample(
        user_id=andere.id, system_prompt="s", prompt="Person: geheim", completion="c", source="local", applied_actions=[]
    )
    db.add(fremd)
    db.commit()
    fremd_id = fremd.id

    login(client)
    feedback = client.post(f"/api/v1/assistant/training/examples/{fremd_id}/feedback", json={"event": "good"})
    assert feedback.status_code == 404
    assert client.delete(f"/api/v1/assistant/training/examples/{fremd_id}").status_code == 404
    assert client.get("/api/v1/assistant/training/examples").json() == []
    # Ein fremdes Beispiel am Eintrag wird still uebergangen, der Eintrag selbst klappt.
    eintrag = client.post(
        "/api/v1/assistant/actions",
        json={"kind": "homework", "title": "S. 1", "starts_at": "2099-01-10T07:00:00Z", "training_example_id": fremd_id},
    )
    assert eintrag.status_code == 201
    db.expire_all()
    gespeichert = db.get(TrainingExample, fremd_id)
    assert gespeichert is not None and gespeichert.applied_actions == []
    assert client.delete("/api/v1/assistant/training/examples").json()["message"] == "0 Beispiele gelöscht."


def test_obergrenze_wirft_die_aeltesten_heraus(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, db, user = app_context
    monkeypatch.setattr(training_service, "MAX_EXAMPLES_PER_USER", 2)
    ids = [
        training_service.record_example(
            db, user.id, system_prompt="s", prompt=f"Person: {n}", completion="c", source="local", model=None
        ).id
        for n in range(3)
    ]
    db.commit()
    assert sorted(db.scalars(select(TrainingExample.id)).all()) == ids[1:]


# --- Hardware ----------------------------------------------------------------


def test_nvidia_smi_wird_gelesen() -> None:
    gpus = parse_nvidia_smi("NVIDIA GeForce RTX 4060 Laptop GPU, 8188\nkaputt\n")
    assert gpus == [GpuInfo(name="NVIDIA GeForce RTX 4060 Laptop GPU", memory_gb=8.0)]


@pytest.mark.parametrize(
    ("system", "machine", "ram", "gpus", "backend"),
    [
        ("Windows", "AMD64", 16.0, [GpuInfo("RTX 4060", 8.0)], "cuda"),
        ("Linux", "x86_64", 32.0, [GpuInfo("GTX 1650", 4.0)], None),
        ("Darwin", "arm64", 16.0, [], "mlx"),
        ("Darwin", "arm64", 8.0, [], None),
        ("Darwin", "x86_64", 32.0, [], None),
        ("Windows", "AMD64", 64.0, [], None),
    ],
)
def test_training_nur_auf_starker_hardware(
    system: str, machine: str, ram: float, gpus: list[GpuInfo], backend: str | None
) -> None:
    ergebnis = evaluate_hardware(system, machine, ram, gpus)
    assert ergebnis.backend == backend
    assert ergebnis.eligible is (backend is not None)
    assert ergebnis.reason
