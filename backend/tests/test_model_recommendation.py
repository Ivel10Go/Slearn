"""Empfehlung nach Hardware: welches Textmodell, und was dazu."""

from __future__ import annotations

import pytest
from app.db.entities import User
from app.services.model_catalog import catalog_models
from app.services.model_recommendation import describe_hardware, recommend
from app.services.training_hardware import GpuInfo
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

SPECS = catalog_models()


def _ids(ram: float | None, system: str = "Windows") -> dict[str, str]:
    return {pick.task: pick.id for pick in recommend(ram, system, SPECS, "de-de-thorsten-medium")}


def test_mehr_speicher_groesseres_textmodell_und_mehr_dazu() -> None:
    gross = _ids(16)
    klein = _ids(4)
    texts = sorted((s for s in SPECS if s.task == "text_generation"), key=lambda s: s.size_bytes)
    assert gross["text_generation"] == texts[-1].id
    assert klein["text_generation"] == texts[0].id
    assert {"embedding", "image_to_text", "speech_to_text", "voice"} <= gross.keys()
    assert "image_to_text" not in klein and "embedding" not in klein
    assert klein["voice"] == "de-de-thorsten-medium"


def test_ohne_messung_wie_ein_8_gb_laptop_und_auf_dem_mac_ohne_whisper() -> None:
    assert _ids(None) == _ids(8)
    assert "speech_to_text" not in _ids(16, "Darwin")


def test_hardware_beschreibung() -> None:
    assert describe_hardware(15.7, [GpuInfo("RTX 4060", 8.0)]) == "16 GB Arbeitsspeicher · RTX 4060 (8 GB)"
    assert "unbekannt" in describe_hardware(None, [])


def test_endpunkt_liefert_zweck_und_stand(app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = app_context
    monkeypatch.setattr("app.api.local_models.measure", lambda: (16.0, ()))
    assert client.post("/api/v1/auth/login", json={"username": "tester", "password": "test-password"}).status_code == 200
    body = client.get("/api/v1/models/recommendation").json()
    assert body["hardware"] == "16 GB Arbeitsspeicher"
    kinds = {item["kind"] for item in body["items"]}
    assert kinds == {"model", "voice"}
    assert all(item["status"]["status"] == "not_downloaded" and item["purpose"] for item in body["items"])
