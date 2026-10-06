"""Trainingsdatensatz und Adapterablage.

Die wichtigsten Zusagen:

* Trainiert wird auf das, was eingetragen wurde, nicht auf die Rohantwort.
* Schlechte und unbewertete Beispiele gehen nicht ins Training.
* Die Pruefmenge haengt nur an der Id und ueberschneidet sich nie mit dem Training.
* Ein Adapter wird erst nach ``activate`` geladen, nur zum passenden Gewicht,
  und die aktive Version wird nie weggeraeumt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from app.core.security import hash_password
from app.db.entities import TrainingExample, User
from app.services import adapter_store, training_data
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.test_assistant import _ai, login
from tests.test_training import _chat, _freigeben, modell  # noqa: F401 - Fixture

BERLIN = ZoneInfo("Europe/Berlin")


def _beispiel(example_id: int, completion: str, **signale: Any) -> TrainingExample:
    werte: dict[str, Any] = {
        "applied_actions": [],
        "rejected_actions": 0,
        "memory_deleted": False,
        "server_adjusted": False,
        "user_rating": None,
    }
    werte.update(signale)
    return TrainingExample(
        id=example_id, user_id=1, system_prompt="sys", prompt="Person: Frage", completion=completion, source="local", **werte
    )


def _aktionen(text: str) -> list[dict[str, Any]]:
    return [json.loads(z.removeprefix("AKTION ")) for z in text.splitlines() if z.startswith("AKTION ")]


def test_korrigiertes_datum_wird_zum_ziel_und_zum_praeferenzpaar() -> None:
    roh = (
        "Ich trage die Klausur ein.\n"
        'AKTION {"typ": "pruefung", "titel": "Bio-Klausur", "datum": "2099-03-13"}\n'
        "MERKE: Mag Biologie."
    )
    beispiel = _beispiel(
        2,
        roh,
        server_adjusted=True,
        applied_actions=[{"kind": "exam", "title": "Bio-Klausur", "starts_at": "2099-03-06T07:00:00Z", "subject_id": 4}],
    )

    ziel = training_data.corrected_completion(beispiel, {4: "Biologie"}, BERLIN)

    # 08:00 Ortszeit ist nur der Standard - das Modell hatte keine Uhrzeit, also bleibt sie weg.
    assert _aktionen(ziel) == [{"typ": "pruefung", "titel": "Bio-Klausur", "fach": "Biologie", "datum": "2099-03-06"}]
    assert ziel.splitlines()[0] == "Ich trage die Klausur ein."
    assert ziel.splitlines()[-1] == "MERKE: Mag Biologie."

    assert not training_data.is_eval_example(2)  # Id 2 liegt im Training, nicht in der Pruefung.
    satz = training_data.build_dataset([beispiel], {4: "Biologie"}, BERLIN)
    zeile = satz.train[0]
    assert zeile["label"] == "corrected"
    assert zeile["messages"][-1]["content"] == ziel
    assert satz.preference == [{"example_id": 2, "prompt": zeile["messages"][:2], "chosen": ziel, "rejected": roh}]


def test_verworfener_vorschlag_faellt_weg_und_uhrzeiten_bleiben() -> None:
    roh = (
        'AKTION {"typ": "termin", "titel": "Nachhilfe", "datum": "2099-03-05", "uhrzeit": "16:00"}\n'
        'AKTION {"typ": "hausaufgabe", "titel": "S. 12", "datum": "2099-03-05"}\n'
        'AKTION {"typ": "termin", "titel": "Ausflug", "datum": "2099-03-09"}'
    )
    beispiel = _beispiel(
        3,
        roh,
        rejected_actions=1,
        applied_actions=[
            {
                "kind": "event",
                "title": "Nachhilfe",
                "starts_at": "2099-03-05T15:30:00Z",
                "ends_at": "2099-03-05T16:30:00Z",
            },
            {
                "kind": "event",
                "title": "Ausflug",
                "starts_at": "2099-03-08T23:00:00Z",
                "ends_at": "2099-03-09T23:00:00Z",
                "all_day": True,
            },
        ],
    )

    assert _aktionen(training_data.corrected_completion(beispiel, {}, BERLIN)) == [
        {"typ": "termin", "titel": "Nachhilfe", "datum": "2099-03-05", "uhrzeit": "16:30"},
        {"typ": "termin", "titel": "Ausflug", "datum": "2099-03-09"},
    ]


def test_nur_gute_und_korrigierte_beispiele_kommen_in_den_datensatz() -> None:
    gut = _beispiel(10, "Klar.", user_rating="good")
    schlecht = _beispiel(11, "Falsch.", user_rating="bad")
    ohne_signal = _beispiel(12, "Hm.")
    notiz_geloescht = _beispiel(13, "MERKE: Quatsch.", memory_deleted=True)

    satz = training_data.build_dataset([schlecht, gut, ohne_signal, notiz_geloescht], {}, BERLIN)

    assert satz.example_ids == [10]
    assert [z["messages"][-1]["content"] for z in satz.train + satz.eval] == ["Klar."]
    # Unveraenderte Antwort: kein Praeferenzpaar.
    assert satz.preference == []


def test_pruefmenge_ist_fest_und_getrennt(tmp_path: Path) -> None:
    beispiele = [_beispiel(i, f"Antwort {i}", user_rating="good") for i in range(1, 101)]

    erster = training_data.build_dataset(beispiele, {}, BERLIN)
    zweiter = training_data.build_dataset(list(reversed(beispiele)), {}, BERLIN)

    pruefung = {z["example_id"] for z in erster.eval}
    training = {z["example_id"] for z in erster.train}
    assert pruefung and training and not pruefung & training
    assert pruefung == {i for i in range(1, 101) if training_data.is_eval_example(i)}
    assert erster.fingerprint == zweiter.fingerprint

    ordner = training_data.write_dataset(erster, tmp_path / "runde")
    zusammenfassung = json.loads((ordner / training_data.DATASET_FILE).read_text(encoding="utf-8"))
    assert zusammenfassung["fingerprint"] == erster.fingerprint
    assert zusammenfassung["counts"]["eval"] == len(pruefung)
    zeilen = (ordner / training_data.TRAIN_FILE).read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == len(training)
    assert not list(ordner.glob("*.tmp"))


def test_eingetragener_wert_aus_dem_chat_landet_im_datensatz(
    app_context: tuple[TestClient, Session, User], modell: None  # noqa: F811
) -> None:
    client, db, nutzer = app_context
    login(client)
    _ai(client)
    _freigeben(client)
    body = _chat(client)
    # Die Lernende verschiebt die Klausur vor dem Eintragen um einen Tag.
    vorschlag = {**body["proposals"][0], "starts_at": "2099-03-06T07:00:00Z"}
    vorschlag["training_example_id"] = body["training_example_id"]
    assert client.post("/api/v1/assistant/actions", json=vorschlag).status_code == 201

    andere = User(username="andere", password_hash=hash_password("pw-andere-123"), is_active=True, timezone="UTC")
    db.add(andere)
    db.commit()
    db.add(
        TrainingExample(
            user_id=andere.id, system_prompt="s", prompt="p", completion="fremd", source="local",
            applied_actions=[], user_rating="good",
        )
    )
    db.commit()
    db.expire_all()

    satz = training_data.load_dataset(db, nutzer.id, BERLIN)

    assert satz.example_ids == [body["training_example_id"]]
    ziel = (satz.train + satz.eval)[0]["messages"][-1]["content"]
    assert _aktionen(ziel)[0]["datum"] == "2099-03-06"
    assert "fremd" not in json.dumps(satz.train + satz.eval)


# --- Adapterablage -----------------------------------------------------------


def _trainiert(tmp_path: Path, name: str) -> Path:
    ordner = tmp_path / name
    ordner.mkdir()
    (ordner / adapter_store.ADAPTER_FILE).write_bytes(b"gguf")
    return ordner


def _anlegen(root: Path, tmp_path: Path, name: str, sha: str = "a" * 64) -> adapter_store.AdapterManifest:
    return adapter_store.register_version(
        root,
        _trainiert(tmp_path, name),
        base_model_id="qwen2.5-3b-instruct-q4km",
        base_sha256=sha,
        dataset_fingerprint="f",
        example_count=12,
        eval_score=0.9,
        created_at="2026-10-05T12:00:00+00:00",
    )


def test_adapter_wird_erst_nach_aktivieren_und_nur_zum_gewicht_geladen(tmp_path: Path) -> None:
    root = adapter_store.model_root(tmp_path / "speicher", 7, "qwen2.5-3b-instruct-q4km")

    erste = _anlegen(root, tmp_path, "staging1")

    assert erste.version == 1
    assert adapter_store.active_adapter(root, "a" * 64) is None
    adapter_store.activate(root, 1)
    assert adapter_store.active_adapter(root, "a" * 64) == root / "v1" / adapter_store.ADAPTER_FILE
    # Anderes Gewicht unter derselben Id (neue Quantisierung): kein Adapter.
    assert adapter_store.active_adapter(root, "b" * 64) is None


def test_rollback_und_aufraeumen_behalten_die_aktive_version(tmp_path: Path) -> None:
    root = adapter_store.model_root(tmp_path, 7, "qwen2.5-3b-instruct-q4km")
    for nummer in range(1, 5):
        _anlegen(root, tmp_path, f"staging{nummer}")
    # Ein abgebrochener Trainingsordner ohne Manifest ist keine Version.
    (root / "v9").mkdir()

    adapter_store.activate(root, 2)
    assert adapter_store.prune(root, keep=1) == [1, 3]
    assert [m.version for m in adapter_store.list_versions(root)] == [2, 4]

    adapter_store.activate(root, 4)
    assert adapter_store.rollback(root) == 2
    assert adapter_store.rollback(root) is None
    assert adapter_store.active_version(root) is None
    assert adapter_store.rollback(root) is None
    assert _anlegen(root, tmp_path, "staging5").version == 10


def test_unsichere_modell_id_wird_abgelehnt(tmp_path: Path) -> None:
    with pytest.raises(adapter_store.AdapterStoreError):
        adapter_store.model_root(tmp_path, 1, "../fremd")
    with pytest.raises(adapter_store.AdapterStoreError):
        adapter_store.register_version(
            tmp_path / "x", tmp_path, base_model_id="m", base_sha256="", dataset_fingerprint="",
            example_count=0, eval_score=None, created_at="",
        )


# --- Material: Stil und Fragen ------------------------------------------------

AUFSCHRIEB = (
    "# Ableitungen\n"
    "- Die Ableitung f'(x) ist die Steigung der Tangente im Punkt x.\n"
    "- Potenzregel: (x^n)' = n * x^(n-1), gilt für alle reellen n.\n"
    "**Merke:** Konstanten fallen beim Ableiten weg, Faktoren bleiben stehen.\n"
    "- Bsp.: f(x) = 3x^2 -> f'(x) = 6x"
)
ARBEITSBLATT = (
    "Arbeitsblatt Zellbiologie. Aufgabe 1: Beschreibe den Aufbau einer tierischen Zelle mit Zellkern, "
    "Mitochondrien und Zellmembran. Aufgabe 2: Vergleiche sie mit einer Pflanzenzelle."
)


def _material(db: Session, user: User, name: str, art: str, inhalt: str) -> int:
    from app.db.entities import Document, DocumentChunk

    dokument = Document(
        user_id=user.id, filename=name, storage_path=name, source="upload", status="indexed", material_kind=art
    )
    db.add(dokument)
    db.flush()
    absatz = DocumentChunk(user_id=user.id, document_id=dokument.id, chunk_index=0, content=inhalt)
    db.add(absatz)
    db.commit()
    return absatz.id


def test_stilbeispiel_ohne_form_rein_original_raus() -> None:
    flach = training_data.flatten(AUFSCHRIEB)
    assert "#" not in flach and "**" not in flach and "\n" not in flach
    assert "Potenzregel" in flach and "Bsp.:" in flach
    assert training_data.has_structure(AUFSCHRIEB)
    assert not training_data.has_structure(ARBEITSBLATT)


def test_material_nur_mit_freigabe_und_im_echten_promptformat(
    app_context: tuple[TestClient, Session, User], tmp_path: Path
) -> None:
    client, db, user = app_context
    aufschrieb = _material(db, user, "Mathe Aufschrieb.jpg", "aufschrieb", AUFSCHRIEB)
    _material(db, user, "AB Zelle.pdf", "arbeitsblatt", ARBEITSBLATT)
    _material(db, user, "Lebenslauf.pdf", "sonstiges", "Lebenslauf " * 40)
    andere = User(username="andere", password_hash=hash_password("pw-andere-123"), is_active=True, timezone="UTC")
    db.add(andere)
    db.commit()
    _material(db, andere, "Fremd.jpg", "aufschrieb", AUFSCHRIEB.replace("Ableitungen", "Geheim"))

    ohne = training_data.load_dataset(db, user.id, BERLIN)
    assert ohne.qa_tasks == [] and ohne.material_chunk_ids == []

    satz = training_data.load_dataset(db, user.id, BERLIN, materials=True)

    # "sonstiges" (Lebenslauf) und fremdes Material bleiben draussen.
    assert len(satz.material_chunk_ids) == 2
    stil = [zeile for zeile in satz.train + satz.eval if zeile["label"] == "style"]
    assert len(stil) == 1 and stil[0]["material_chunk_id"] == aufschrieb
    nachricht = stil[0]["messages"]
    assert nachricht[-1]["content"] == AUFSCHRIEB
    assert "# Ableitungen" not in nachricht[1]["content"] and "Potenzregel" in nachricht[1]["content"]
    assert "--- Dokumentauszuege ---" in nachricht[1]["content"]
    assert "Geheim" not in json.dumps(satz.qa_tasks + stil)

    aufgaben = {aufgabe["chunk_id"]: aufgabe for aufgabe in satz.qa_tasks}
    assert set(aufgaben) == set(satz.material_chunk_ids)
    aufgabe = aufgaben[aufschrieb]
    assert training_data.QUESTION_PLACEHOLDER in aufgabe["user_template"]
    assert aufgabe["distractors"] == [ARBEITSBLATT]
    assert "Zellbiologie" in aufgabe["user_template"] and "Potenzregel" in aufgabe["user_template"]

    ordner = training_data.write_dataset(satz, tmp_path / "runde")
    zusammenfassung = json.loads((ordner / training_data.DATASET_FILE).read_text(encoding="utf-8"))
    assert zusammenfassung["counts"]["qa"] == 2
    assert sorted(zusammenfassung["material_chunk_ids"]) == sorted(satz.material_chunk_ids)
