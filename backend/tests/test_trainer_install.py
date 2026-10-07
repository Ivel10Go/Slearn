"""Die Trainer-Umgebung einrichten (``app/services/trainer_install.py``).

Kein Test laedt etwas aus dem Netz oder startet uv: das uv-Paket ist ein
selbst gebautes ZIP hinter einem ``MockTransport``, und die drei Schritte
(``uv venv``, ``uv pip sync``, die GPU-Pruefung) sind gefaelschte Prozesse,
die nur aufschreiben, womit sie gerufen wurden.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.core.config import Settings
from app.db.entities import User
from app.services.local_runtimes import RuntimeAsset
from app.services.trainer_install import (
    MARKER_FILE,
    STATE_ERROR,
    STATE_INSTALLING,
    STATE_MISSING,
    STATE_OUTDATED,
    STATE_READY,
    STATE_UNSUPPORTED,
    TRAINER_PYTHON_VERSION,
    UV_VERSION,
    TrainerSetup,
    TrainerSetupError,
)
from app.services.training_hardware import BACKEND_CUDA, BACKEND_MLX, TrainingHardware
from app.services.training_jobs import TrainingRunner
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

UV_URL = "https://github.com/astral-sh/uv/releases/download/test/uv.zip"
LOCK = "requirements-cuda-windows.lock"


def nvidia() -> TrainingHardware:
    return TrainingHardware(True, BACKEND_CUDA, "NVIDIA RTX 4060 mit 8 GB", "Windows", "AMD64", 32.0, "RTX 4060", 8.0)


def uv_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("uv.exe", b"MZ fake uv")
    return buffer.getvalue()


class FakeProcess:
    def __init__(self, lines: list[str], code: int = 0) -> None:
        self.stdout = iter(line.encode() + b"\n" for line in lines)
        self.code = code

    def wait(self) -> int:
        return self.code

    def poll(self) -> int:
        return self.code

    def terminate(self) -> None:
        pass


class Steps:
    """Spielt uv und Python: legt das venv an, meldet die Pakete, sagt, ob CUDA da ist."""

    def __init__(self, cuda: bool = True, fail_sync: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.cuda = cuda
        self.fail_sync = fail_sync

    def __call__(self, argv: list[str], **kwargs: Any) -> FakeProcess:
        self.calls.append(argv)
        self.envs.append(kwargs["env"])
        if argv[1] == "venv":
            python = Path(argv[2]) / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"")
            return FakeProcess([f"Using CPython {TRAINER_PYTHON_VERSION}", "Creating virtual environment"])
        if argv[1:3] == ["pip", "sync"]:
            if self.fail_sync:
                return FakeProcess(["error: Hash mismatch for `torch==2.11.0+cu128`"], code=2)
            cache = Path(kwargs["env"]["UV_CACHE_DIR"])
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "torch.whl").write_bytes(b"x")
            return FakeProcess(["Resolved 43 packages", "Installed 43 packages"])
        return FakeProcess([json.dumps({"cuda": self.cuda, "torch": "2.11.0+cu128"})])


def make_setup(
    tmp_path: Path,
    *,
    steps: Steps | None = None,
    platform: str = "windows-x64",
    hardware: Callable[[], TrainingHardware] = nvidia,
    payload: bytes | None = None,
    free: int = 50 * 1024**3,
) -> TrainerSetup:
    trainer_dir = tmp_path / "trainer"
    trainer_dir.mkdir(exist_ok=True)
    lock = trainer_dir / LOCK
    if not lock.exists():
        lock.write_text("torch==2.11.0+cu128 --hash=sha256:abc\n", encoding="utf-8")
    body = uv_zip()
    asset = RuntimeAsset(UV_URL, hashlib.sha256(body).hexdigest(), len(body), "uv.exe")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=payload if payload is not None else body))
    return TrainerSetup(
        root=tmp_path / "trainer-env",
        trainer_dir=trainer_dir,
        platform=platform,
        transport=transport,
        popen=steps or Steps(),  # type: ignore[arg-type]
        hardware=hardware,
        uv_assets={"windows-x64": asset},
        lock_files={"windows-x64": LOCK},
        disk_free=lambda _path: free,
    )


def login(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "tester", "password": "test-password"})
    assert response.status_code == 200


def test_die_einrichtung_holt_uv_python_und_pakete_und_prueft_die_grafikkarte(tmp_path: Path) -> None:
    steps = Steps()
    setup = make_setup(tmp_path, steps=steps)
    assert setup.status().state == STATE_MISSING

    setup.install()

    status = setup.status()
    assert status.state == STATE_READY
    assert status.torch_version == "2.11.0+cu128"
    assert setup.python_path() == tmp_path / "trainer-env" / "venv" / "Scripts" / "python.exe"
    venv, sync, check = steps.calls
    # Genau die verifizierte Python-Version, nicht irgendein System-Python.
    assert venv[1:] == ["venv", str(tmp_path / "trainer-env" / "venv"), "--python", TRAINER_PYTHON_VERSION]
    assert steps.envs[0]["UV_PYTHON_PREFERENCE"] == "only-managed"
    # Jedes Paket mit fester Pruefsumme.
    assert sync[1:3] == ["pip", "sync"] and "--require-hashes" in sync
    assert str(tmp_path / "trainer" / LOCK) in sync
    assert check[0].endswith("python.exe") and "torch.cuda.is_available()" in check[2]
    marker = json.loads((tmp_path / "trainer-env" / MARKER_FILE).read_text(encoding="utf-8"))
    assert marker["lock_sha256"] == hashlib.sha256((tmp_path / "trainer" / LOCK).read_bytes()).hexdigest()
    # Nach Erfolg braucht niemand den Download-Cache mehr.
    assert not (tmp_path / "trainer-env" / "cache").exists()


def test_ein_zweiter_lauf_laedt_uv_nicht_noch_einmal(tmp_path: Path) -> None:
    setup = make_setup(tmp_path)
    setup.install()
    downloads: list[str] = []
    setup.transport = httpx.MockTransport(lambda request: downloads.append(str(request.url)) or httpx.Response(500))

    setup.install()

    assert downloads == []
    assert setup.status().state == STATE_READY


def test_ein_falsches_uv_paket_wird_verworfen(tmp_path: Path) -> None:
    steps = Steps()
    setup = make_setup(tmp_path, steps=steps, payload=b"manipuliert")

    with pytest.raises(TrainerSetupError, match="Prüfsumme"):
        setup.install()

    assert steps.calls == []
    assert setup.status().state == STATE_ERROR
    assert not (tmp_path / "trainer-env" / "uv" / UV_VERSION / "uv.exe").exists()


def test_ein_paket_mit_anderer_pruefsumme_bricht_ab(tmp_path: Path) -> None:
    setup = make_setup(tmp_path, steps=Steps(fail_sync=True))

    with pytest.raises(TrainerSetupError, match="Hash mismatch"):
        setup.install()

    assert setup.python_path() is None
    status = setup.status()
    assert status.state == STATE_ERROR
    assert status.error is not None and "PyTorch" in status.error


def test_ohne_cuda_gibt_es_keinen_fertigen_trainer(tmp_path: Path) -> None:
    setup = make_setup(tmp_path, steps=Steps(cuda=False))

    with pytest.raises(TrainerSetupError, match="Grafiktreiber"):
        setup.install()

    assert setup.python_path() is None
    assert not (tmp_path / "trainer-env" / MARKER_FILE).exists()


def test_eine_neue_paketliste_meldet_eine_veraltete_umgebung(tmp_path: Path) -> None:
    setup = make_setup(tmp_path)
    setup.install()

    (tmp_path / "trainer" / LOCK).write_text("torch==2.12.0+cu128 --hash=sha256:def\n", encoding="utf-8")

    assert setup.status().state == STATE_OUTDATED
    # Bis zur Aktualisierung trainiert die alte Umgebung weiter.
    assert setup.python_path() is not None


def test_mac_und_rechner_ohne_nvidia_bekommen_einen_grund(tmp_path: Path) -> None:
    mac = make_setup(tmp_path, platform="macos-arm64")
    assert mac.status().state == STATE_UNSUPPORTED
    assert "MLX" in (mac.status().reason or "")

    def apple() -> TrainingHardware:
        return TrainingHardware(True, BACKEND_MLX, "Apple M3", "Darwin", "arm64", 16.0)

    ohne_nvidia = make_setup(tmp_path, hardware=apple)
    assert ohne_nvidia.status().reason == "Training läuft vorerst nur auf NVIDIA-Grafikkarten."
    with pytest.raises(TrainerSetupError):
        ohne_nvidia.install_in_background()


def test_zu_wenig_platz_wird_vorher_gemeldet(tmp_path: Path) -> None:
    setup = make_setup(tmp_path, free=3 * 1024**3)

    with pytest.raises(TrainerSetupError, match="GB frei"):
        setup.install_in_background()


def test_entfernen_loescht_die_umgebung(tmp_path: Path) -> None:
    setup = make_setup(tmp_path)
    setup.install()

    setup.remove()

    assert not (tmp_path / "trainer-env").exists()
    assert setup.status().state == STATE_MISSING


def test_der_runner_nimmt_die_eingerichtete_umgebung_aber_von_hand_gewinnt(tmp_path: Path) -> None:
    setup = make_setup(tmp_path)
    eingerichtet = TrainingRunner(Settings(model_storage_path=str(tmp_path / "modelle")), setup=setup)
    assert eingerichtet.trainer_python() == ""

    setup.install()
    assert eingerichtet.trainer_python() == str(setup.python_path())

    von_hand = TrainingRunner(
        Settings(model_storage_path=str(tmp_path / "modelle"), trainer_python=sys.executable), setup=setup
    )
    assert von_hand.trainer_python() == sys.executable


def test_api_ohne_eingebaute_laufzeiten(app_context: tuple[TestClient, Session, User]) -> None:
    client, _, _ = app_context
    login(client)

    status = client.get("/api/v1/assistant/training/setup")
    assert status.status_code == 200, status.text
    assert status.json()["state"] == "unsupported"
    assert "TRAINER_PYTHON" in status.json()["reason"]
    assert client.post("/api/v1/assistant/training/setup").status_code == 503


def test_api_richtet_im_hintergrund_ein(app_context: tuple[TestClient, Session, User], tmp_path: Path) -> None:
    client, _, _ = app_context
    login(client)
    setup = make_setup(tmp_path)
    client.app.state.trainer_setup = setup  # type: ignore[attr-defined]

    started = client.post("/api/v1/assistant/training/setup")
    assert started.status_code == 200, started.text
    for _ in range(300):
        if setup.status().state != STATE_INSTALLING:
            break
        time.sleep(0.01)

    assert client.get("/api/v1/assistant/training/setup").json()["state"] == "ready"
    removed = client.delete("/api/v1/assistant/training/setup")
    assert removed.json()["state"] == "missing"
