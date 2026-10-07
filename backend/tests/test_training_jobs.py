"""Trainingsrunden: Starten, Anhalten, Fortsetzen, Uebernehmen - ohne echten Trainer.

Der Trainerprozess ist eine Attrappe; was er sonst schreiben wuerde
(``progress.json``, ``out/adapter.gguf``), schreibt der Test selbst. Die
wichtigsten Zusagen:

* Ohne passende Hardware oder Trainer gibt es 503 mit Klartext, nie einen Start.
* Zu wenige bewertete Beispiele sind 422.
* Eine offene Runde wird fortgesetzt, nicht neu angelegt; ``running`` ohne
  Prozess ist ``paused``.
* Ein fertiger Adapter wird abgelegt und nur aktiviert, wenn er auf der
  Pruefmenge nicht schlechter ist.
* Fremde Runden sieht niemand.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from app.core.config import Settings
from app.db.entities import TrainingExample, User
from app.services import adapter_store
from app.services.model_catalog import model_spec
from app.services.training_hardware import TrainingHardware
from app.services.training_jobs import MIN_TRAIN_EXAMPLES, TrainingRunner
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.test_assistant import login

MODELL = "qwen2.5-3b-instruct-q4km"


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        while self.returncode is None:
            time.sleep(0.01)
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class FakeRuntimes:
    def __init__(self) -> None:
        self.adapters: dict[str, Path | None] = {}
        self.children: list[Any] = []

    def preferred_text_model(self) -> Any:
        return model_spec(MODELL)

    def set_text_adapter(self, model_id: str, adapter: Path | None) -> None:
        self.adapters[model_id] = adapter

    def attach_child(self, process: Any) -> None:
        self.children.append(process)


def _hardware(eligible: bool = True, backend: str | None = "cuda") -> TrainingHardware:
    return TrainingHardware(
        eligible=eligible,
        backend=backend,
        reason="ok" if eligible else "Keine passende Grafikkarte.",
        system="Windows",
        machine="AMD64",
        ram_gb=32.0,
        gpu_name="RTX",
        vram_gb=8.0,
    )


@pytest.fixture
def trainer(app_context: tuple[TestClient, Session, User], tmp_path: Path) -> dict[str, Any]:
    client, _, _ = app_context
    calls: list[list[str]] = []
    processes: list[FakeProcess] = []

    def popen(argv: list[str], **_: Any) -> FakeProcess:
        calls.append(argv)
        process = FakeProcess()
        processes.append(process)
        return process

    runtimes = FakeRuntimes()
    state: dict[str, Any] = {"hardware": _hardware()}
    runner = TrainingRunner(
        Settings(model_storage_path=str(tmp_path / "modelle"), trainer_python=sys.executable),
        runtimes=runtimes,  # type: ignore[arg-type]
        popen=popen,
        hardware=lambda: state["hardware"],
    )
    client.app.state.training_runner = runner  # type: ignore[attr-defined]
    state.update(runner=runner, calls=calls, processes=processes, runtimes=runtimes)
    return state


def _beispiele(db: Session, user_id: int, anzahl: int) -> None:
    for nummer in range(anzahl):
        db.add(
            TrainingExample(
                user_id=user_id,
                system_prompt="sys",
                prompt=f"Person: Frage {nummer}\nAssistent:",
                completion=f"Antwort {nummer}",
                source="local",
                applied_actions=[],
                user_rating="good",
            )
        )
    db.commit()


def _fertig(run_dir: Path, *, eval_loss: float, eval_loss_base: float) -> None:
    """Was der Trainer am Ende hinterlaesst."""
    (run_dir / "out").mkdir(exist_ok=True)
    (run_dir / "out" / "adapter.gguf").write_bytes(b"gguf")
    progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
    progress.update(status="done", step=6, total_steps=6, eval_loss=eval_loss, eval_loss_base=eval_loss_base)
    (run_dir / "progress.json").write_text(json.dumps(progress), encoding="utf-8")


def test_ohne_grafikkarte_oder_trainer_gibt_es_klartext(
    app_context: tuple[TestClient, Session, User], trainer: dict[str, Any]
) -> None:
    client, db, user = app_context
    login(client)
    _beispiele(db, user.id, 20)

    trainer["hardware"] = _hardware(eligible=False, backend=None)
    antwort = client.post("/api/v1/assistant/training/run", json={})
    assert antwort.status_code == 503
    assert antwort.json()["detail"] == "Keine passende Grafikkarte."

    trainer["hardware"] = _hardware(backend="mlx")
    assert "NVIDIA" in client.post("/api/v1/assistant/training/run", json={}).json()["detail"]

    trainer["hardware"] = _hardware()
    trainer["runner"].python = ""
    assert "Trainer einrichten" in client.post("/api/v1/assistant/training/run", json={}).json()["detail"]
    assert trainer["calls"] == []


def test_zu_wenige_beispiele_und_unbekanntes_modell_sind_422(
    app_context: tuple[TestClient, Session, User], trainer: dict[str, Any]
) -> None:
    client, db, user = app_context
    login(client)
    _beispiele(db, user.id, 3)

    antwort = client.post("/api/v1/assistant/training/run", json={})
    assert antwort.status_code == 422
    assert f"von {MIN_TRAIN_EXAMPLES}" in antwort.json()["detail"]

    antwort = client.post("/api/v1/assistant/training/run", json={"model_id": "llama-3.2-1b-instruct-q4km"})
    assert antwort.status_code == 422
    assert client.get("/api/v1/assistant/training/run").json() is None


def test_runde_startet_haelt_an_und_setzt_fort(
    app_context: tuple[TestClient, Session, User], trainer: dict[str, Any]
) -> None:
    client, db, user = app_context
    login(client)
    _beispiele(db, user.id, 20)

    lauf = client.post("/api/v1/assistant/training/run", json={}).json()

    assert lauf["status"] == "pending" and lauf["active"] is True
    assert lauf["base_model_id"] == MODELL
    run_dir = trainer["runner"].runs_root(user.id) / lauf["run_id"]
    auftrag = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    assert auftrag["base_model"]["hf_repo"] == "Qwen/Qwen2.5-3B-Instruct"
    assert auftrag["base_model"]["sha256"] == model_spec(MODELL).files[0].sha256
    assert (run_dir / "data" / "dataset.json").is_file()
    assert trainer["calls"][0][-2:] == ["--job", str(run_dir)]
    assert trainer["runtimes"].children == trainer["processes"]
    # Ein zweiter Klick startet keinen zweiten Trainer.
    assert client.post("/api/v1/assistant/training/run", json={}).json()["run_id"] == lauf["run_id"]
    assert len(trainer["calls"]) == 1

    assert client.post("/api/v1/assistant/training/run/pause").status_code == 200
    assert (run_dir / "STOP").exists()

    # Der Trainer hat bei Schritt 3 gespeichert und sich beendet - oder ist abgestuerzt.
    (run_dir / "progress.json").write_text(
        json.dumps({"status": "running", "step": 3, "total_steps": 6}), encoding="utf-8"
    )
    trainer["processes"][0].returncode = 3
    stand = client.get("/api/v1/assistant/training/run").json()
    assert (stand["status"], stand["active"], stand["step"]) == ("paused", False, 3)

    # Neue Beispiele aendern die offene Runde nicht: derselbe Ordner wird fortgesetzt.
    _beispiele(db, user.id, 5)
    weiter = client.post("/api/v1/assistant/training/run", json={}).json()
    assert weiter["run_id"] == lauf["run_id"]
    assert len(trainer["calls"]) == 2
    assert not (run_dir / "STOP").exists()


def test_fertiger_adapter_wird_nur_aktiviert_wenn_er_nicht_schlechter_ist(
    app_context: tuple[TestClient, Session, User], trainer: dict[str, Any]
) -> None:
    client, db, user = app_context
    login(client)
    _beispiele(db, user.id, 20)
    runner: TrainingRunner = trainer["runner"]

    erste = client.post("/api/v1/assistant/training/run", json={}).json()
    run_dir = runner.runs_root(user.id) / erste["run_id"]
    _fertig(run_dir, eval_loss=0.8, eval_loss_base=1.2)
    trainer["processes"][-1].returncode = 0

    stand = client.get("/api/v1/assistant/training/run").json()
    assert (stand["status"], stand["adapter_version"], stand["activated"]) == ("done", 1, True)
    root = adapter_store.model_root(runner.storage, user.id, MODELL)
    assert trainer["runtimes"].adapters[MODELL] == root / "v1" / "adapter.gguf"

    # Die naechste Runde ist neu, und ihr Adapter ist schlechter: abgelegt, nicht aktiv.
    zweite = client.post("/api/v1/assistant/training/run", json={}).json()
    assert zweite["run_id"] != erste["run_id"]
    _fertig(runner.runs_root(user.id) / zweite["run_id"], eval_loss=1.5, eval_loss_base=1.2)
    trainer["processes"][-1].returncode = 0
    stand = client.get("/api/v1/assistant/training/run").json()
    assert (stand["adapter_version"], stand["activated"]) == (2, False)

    adapter = client.get("/api/v1/assistant/training/adapters").json()
    assert adapter["active_version"] == 1
    assert [(v["version"], v["active"], v["eval_loss"]) for v in adapter["versions"]] == [
        (2, False, 1.5),
        (1, True, 0.8),
    ]

    zurueck = client.post("/api/v1/assistant/training/adapters/rollback").json()
    assert zurueck["active_version"] is None
    assert trainer["runtimes"].adapters[MODELL] is None


def test_offene_runde_verwerfen_und_fremde_runden(
    app_context: tuple[TestClient, Session, User], trainer: dict[str, Any]
) -> None:
    client, db, user = app_context
    login(client)
    _beispiele(db, user.id, 20)
    lauf = client.post("/api/v1/assistant/training/run", json={}).json()

    assert client.delete("/api/v1/assistant/training/run").status_code == 409
    trainer["processes"][-1].returncode = 3
    assert client.delete("/api/v1/assistant/training/run").json()["message"] == "Runde verworfen."
    assert not (trainer["runner"].runs_root(user.id) / lauf["run_id"]).exists()

    # Eine Runde eines anderen Nutzers taucht nicht auf.
    fremd = trainer["runner"].runs_root(user.id + 1) / "run-20990101-000000"
    fremd.mkdir(parents=True)
    (fremd / "progress.json").write_text('{"status": "paused"}', encoding="utf-8")
    assert client.get("/api/v1/assistant/training/run").json() is None


def test_schlechter_bei_art_und_datum_wird_nicht_aktiviert(
    app_context: tuple[TestClient, Session, User], trainer: dict[str, Any]
) -> None:
    client, db, user = app_context
    login(client)
    _beispiele(db, user.id, 20)
    lauf = client.post("/api/v1/assistant/training/run", json={}).json()
    run_dir = trainer["runner"].runs_root(user.id) / lauf["run_id"]
    _fertig(run_dir, eval_loss=0.5, eval_loss_base=1.0)
    # Der Verlust ist besser, aber das Modell verrechnet sich oefter beim Datum.
    progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
    progress.update(eval_score=0.6, eval_score_base=0.8)
    (run_dir / "progress.json").write_text(json.dumps(progress), encoding="utf-8")
    trainer["processes"][-1].returncode = 0

    stand = client.get("/api/v1/assistant/training/run").json()

    assert (stand["adapter_version"], stand["activated"], stand["eval_score"]) == (1, False, 0.6)
    assert MODELL not in trainer["runtimes"].adapters
    assert client.get("/api/v1/assistant/training/adapters").json()["versions"][0]["eval_score"] == 0.6


def test_automatik_folgt_den_bedingungen_der_desktop_app(
    app_context: tuple[TestClient, Session, User], trainer: dict[str, Any]
) -> None:
    client, db, user = app_context
    login(client)
    _beispiele(db, user.id, 20)
    url = "/api/v1/assistant/training/conditions"

    # Ohne die eigene Freigabe passiert nichts, auch wenn die App es erlaubt.
    assert client.post(url, json={"allowed": True}).json() is None
    assert client.put("/api/v1/assistant/training/consent", json={"enabled": True}).status_code == 200
    assert client.post(url, json={"allowed": True}).json() is None
    status_body = client.put("/api/v1/assistant/training/auto", json={"enabled": True}).json()
    assert status_body["auto"] is True

    lauf = client.post(url, json={"allowed": True}).json()
    assert lauf["active"] is True
    run_dir = trainer["runner"].runs_root(user.id) / lauf["run_id"]

    # Akku oder Nutzung: anhalten.
    client.post(url, json={"allowed": False})
    assert (run_dir / "STOP").exists()
    trainer["processes"][-1].returncode = 3
    # Wieder erlaubt: dieselbe Runde geht weiter.
    assert client.post(url, json={"allowed": True}).json()["run_id"] == lauf["run_id"]
    assert len(trainer["calls"]) == 2

    _fertig(run_dir, eval_loss=0.5, eval_loss_base=1.0)
    trainer["processes"][-1].returncode = 0
    assert client.get("/api/v1/assistant/training/run").json()["status"] == "done"

    # Zu wenige neue Beispiele: keine neue Runde. Ab 20 neuen: ja.
    _beispiele(db, user.id, 5)
    assert client.post(url, json={"allowed": True}).json()["run_id"] == lauf["run_id"]
    assert len(trainer["calls"]) == 2
    _beispiele(db, user.id, 25)
    neu = client.post(url, json={"allowed": True}).json()
    assert neu["run_id"] != lauf["run_id"] and neu["active"] is True


def test_gescheiterte_runde_wird_nicht_automatisch_wiederholt(
    app_context: tuple[TestClient, Session, User], trainer: dict[str, Any]
) -> None:
    client, db, user = app_context
    login(client)
    _beispiele(db, user.id, 20)
    client.put("/api/v1/assistant/training/consent", json={"enabled": True})
    client.put("/api/v1/assistant/training/auto", json={"enabled": True})
    lauf = client.post("/api/v1/assistant/training/run", json={}).json()
    run_dir = trainer["runner"].runs_root(user.id) / lauf["run_id"]
    (run_dir / "progress.json").write_text(json.dumps({"status": "failed", "error": "CUDA out of memory"}), encoding="utf-8")
    trainer["processes"][-1].returncode = 1

    stand = client.post("/api/v1/assistant/training/conditions", json={"allowed": True}).json()

    assert (stand["status"], stand["error"]) == ("failed", "CUDA out of memory")
    assert len(trainer["calls"]) == 1


def _absaetze(db: Session, user_id: int, anzahl: int, start: int = 0) -> None:
    from app.db.entities import Document, DocumentChunk

    for nummer in range(start, start + anzahl):
        dokument = Document(
            user_id=user_id, filename=f"AB {nummer}.pdf", storage_path="x", source="upload", status="indexed",
            material_kind="arbeitsblatt",
        )
        db.add(dokument)
        db.flush()
        inhalt = f"Arbeitsblatt {nummer}: Aufgabe zur Bruchrechnung mit Kürzen, Erweitern und Hauptnenner " * 3
        db.add(DocumentChunk(user_id=user_id, document_id=dokument.id, chunk_index=0, content=inhalt))
    db.commit()


def test_material_allein_reicht_und_neues_material_startet_eine_neue_runde(
    app_context: tuple[TestClient, Session, User], trainer: dict[str, Any]
) -> None:
    client, db, user = app_context
    login(client)
    _absaetze(db, user.id, 10)
    url = "/api/v1/assistant/training/conditions"
    client.put("/api/v1/assistant/training/auto", json={"enabled": True})
    client.put("/api/v1/assistant/training/consent", json={"enabled": True})

    # Ohne Materialfreigabe zaehlt Material nicht.
    assert client.post(url, json={"allowed": True}).json() is None
    assert client.put("/api/v1/assistant/training/materials", json={"enabled": True}).json()["materials"] is True

    lauf = client.post(url, json={"allowed": True}).json()
    run_dir = trainer["runner"].runs_root(user.id) / lauf["run_id"]
    assert (run_dir / "data" / "qa_tasks.jsonl").read_text(encoding="utf-8").count("\n") == 10
    _fertig(run_dir, eval_loss=0.5, eval_loss_base=1.0)
    trainer["processes"][-1].returncode = 0
    assert client.get("/api/v1/assistant/training/run").json()["status"] == "done"

    _absaetze(db, user.id, 9, start=10)
    assert client.post(url, json={"allowed": True}).json()["run_id"] == lauf["run_id"]
    _absaetze(db, user.id, 1, start=19)
    assert client.post(url, json={"allowed": True}).json()["run_id"] != lauf["run_id"]
