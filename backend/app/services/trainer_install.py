"""Die Trainer-Umgebung einrichten: eigenes Python mit torch/CUDA, ohne Handarbeit.

Der LoRA-Trainer (``trainer/lernraum_trainer.py``) braucht torch mit CUDA,
mehrere GB, die die App nicht mitliefert. Bisher hiess das: von Hand ein venv
bauen und ``trainer.json`` schreiben. Dieses Modul macht das auf Knopfdruck:

1. **uv holen** - ein einzelnes Programm von GitHub, feste Version und SHA-256
   wie die anderen Laufzeiten (``local_runtimes.RUNTIME_CATALOG``).
2. **Python anlegen** - ``uv venv`` mit genau ``TRAINER_PYTHON_VERSION``. uv
   laedt dafuer ein eigenstaendiges Python und prueft dessen Pruefsumme selbst;
   ein installiertes System-Python wird nicht benutzt.
3. **Pakete installieren** - ``uv pip sync`` aus der Sperrdatei im
   Trainer-Ordner, mit ``--require-hashes``: jedes Paket, auch jede indirekte
   Abhaengigkeit, hat eine feste Version und SHA-256. Ein Paket mit anderer
   Pruefsumme bricht die Einrichtung ab.
4. **Pruefen** - die frische Umgebung importiert torch und meldet, ob sie die
   Grafikkarte sieht. Erst dann entsteht ``installed.json``.

* **Der Zustand steht im Ordner.** Fertig ist die Umgebung, wenn
  ``installed.json`` daliegt; die Pruefsumme der Sperrdatei darin sagt, ob eine
  neuere App andere Pakete will (``outdated``) - dann trainiert die alte
  Umgebung weiter, bis jemand aktualisiert.
* **Abgebrochen ist nicht verloren.** uv behaelt fertig geladene Pakete im
  Cache unter ``<root>/cache``; der naechste Versuch laedt nur, was fehlt.
  Nach Erfolg wird der Cache geleert, die Umgebung braucht ihn nicht mehr.
* **Vorerst nur Windows mit NVIDIA**, so weit reicht die Sperrdatei. Fuer
  Apple Silicon fehlt noch der MLX-Trainer; die Einrichtung meldet dort
  ``unsupported`` statt einen Trainer zu bauen, der nicht laeuft.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx

from app.services.local_runtimes import (
    RuntimeAsset,
    _extract,
    is_allowed_runtime_url,
    platform_key,
)
from app.services.training_hardware import BACKEND_CUDA, TrainingHardware, detect_training_hardware

logger = logging.getLogger("school_assistant")

UV_VERSION: Final = "0.12.19"
#: Dieselbe Version wie die auf einer RTX 4060 verifizierte Umgebung.
TRAINER_PYTHON_VERSION: Final = "3.12.7"

#: Pruefsummen aus dem ``digest`` der GitHub-Release-API von astral-sh/uv.
UV_ASSETS: Final[dict[str, RuntimeAsset]] = {
    "windows-x64": RuntimeAsset(
        f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip",
        "6dbb02d79e419522f1c500f0adb1cddcff0cda7d59b0d66ea7f5e3b4a1b2f5f0",
        17955780,
        "uv.exe",
    ),
}

#: Sperrdatei je System, erzeugt aus ``trainer/requirements-cuda.in``.
LOCK_FILES: Final[dict[str, str]] = {"windows-x64": "requirements-cuda-windows.lock"}

#: torch mit CUDA kommt aus dem PyTorch-Index, alles andere von PyPI. Die
#: Herkunft ist fuer die Sicherheit egal - die Pruefsummen stehen in der Sperrdatei.
PACKAGE_INDEX_ARGS: Final[tuple[str, ...]] = (
    "--index-url",
    "https://pypi.org/simple",
    "--extra-index-url",
    "https://download.pytorch.org/whl/cu128",
    "--index-strategy",
    "unsafe-best-match",
)

#: Rad von torch (~3 GB) + entpackte Umgebung (~5 GB) + Luft.
MIN_FREE_BYTES: Final = 12 * 1024**3
#: Ungefaehrer Platzbedarf der fertigen Umgebung, fuer die Oberflaeche.
APPROX_INSTALLED_BYTES: Final = 6 * 1024**3

MARKER_FILE: Final = "installed.json"
DOWNLOAD_TIMEOUT_SECONDS: Final = 120.0

STATE_UNSUPPORTED: Final = "unsupported"
STATE_MISSING: Final = "missing"
STATE_INSTALLING: Final = "installing"
STATE_READY: Final = "ready"
STATE_OUTDATED: Final = "outdated"
STATE_ERROR: Final = "error"

PHASE_LABELS: Final[dict[str, str]] = {
    "uv": "Lade das Installationsprogramm",
    "python": "Richte Python ein",
    "packages": "Lade und installiere PyTorch und Co. (ca. 3 GB)",
    "check": "Prüfe die Grafikkarte",
}

_CHECK_SCRIPT: Final = (
    "import json, torch, transformers, peft, bitsandbytes; "
    "print(json.dumps({'cuda': torch.cuda.is_available(), 'torch': torch.__version__}))"
)


class TrainerSetupError(RuntimeError):
    """Die Einrichtung ist gescheitert. Die Meldung ist deutsch."""


@dataclass(frozen=True, slots=True)
class TrainerSetupStatus:
    state: str
    phase: str | None
    phase_label: str | None
    supported: bool
    reason: str | None
    error: str | None
    log_tail: list[str]
    approx_bytes: int
    location: str
    torch_version: str | None
    installed_at: str | None
    #: Ein ``TRAINER_PYTHON`` von Hand schlaegt die eingerichtete Umgebung.
    manual_python: bool


@dataclass
class _Progress:
    active: bool = False
    phase: str | None = None
    error: str | None = None
    cancelled: bool = False
    log: deque[str] = field(default_factory=lambda: deque(maxlen=12))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


@dataclass
class TrainerSetup:
    """Richtet die Trainer-Umgebung unter ``root`` ein. Hoechstens eine Einrichtung gleichzeitig."""

    root: Path
    trainer_dir: Path
    platform: str = field(default_factory=platform_key)
    manual_python: str = ""
    transport: httpx.BaseTransport | None = None
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen
    hardware: Callable[[], TrainingHardware] = detect_training_hardware
    uv_assets: dict[str, RuntimeAsset] = field(default_factory=lambda: dict(UV_ASSETS))
    lock_files: dict[str, str] = field(default_factory=lambda: dict(LOCK_FILES))
    disk_free: Callable[[Path], int] = _free_bytes

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._progress = _Progress()
        self._process: subprocess.Popen[bytes] | None = None

    # ------------------------------------------------------------ Pfade

    @property
    def venv_dir(self) -> Path:
        return self.root / "venv"

    def _venv_python(self) -> Path:
        if self.platform.startswith("windows"):
            return self.venv_dir / "Scripts" / "python.exe"
        return self.venv_dir / "bin" / "python"

    def _uv_path(self) -> Path:
        asset = self.uv_assets[self.platform]
        return self.root / "uv" / UV_VERSION / Path(*asset.executable.split("/"))

    def _lock_path(self) -> Path | None:
        name = self.lock_files.get(self.platform)
        return self.trainer_dir / name if name else None

    def _marker(self) -> dict[str, object]:
        try:
            value = json.loads((self.root / MARKER_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def python_path(self) -> Path | None:
        """Das Trainer-Python, wenn die Umgebung fertig eingerichtet ist - sonst ``None``."""
        python = self._venv_python()
        if self._marker() and python.is_file():
            return python
        return None

    # ------------------------------------------------------------ Stand

    def unsupported_reason(self) -> str | None:
        if self.platform not in self.uv_assets or self.platform not in self.lock_files:
            if self.platform.startswith("macos"):
                return "Auf dem Mac fehlt der Trainer noch (MLX). Die Einrichtung kommt mit ihm."
            return f"Für dieses System ({self.platform}) gibt es noch keine Trainer-Umgebung."
        lock = self._lock_path()
        if lock is None or not lock.is_file():
            return "Die Paketliste des Trainers fehlt in der App."
        hardware = self.hardware()
        if not hardware.eligible:
            return hardware.reason
        if hardware.backend != BACKEND_CUDA:
            return "Training läuft vorerst nur auf NVIDIA-Grafikkarten."
        return None

    def status(self) -> TrainerSetupStatus:
        reason = self.unsupported_reason()
        marker = self._marker()
        progress = self._progress
        lock = self._lock_path()
        if progress.active:
            state = STATE_INSTALLING
        elif self.python_path() is not None:
            current = _sha256_file(lock) if lock is not None and lock.is_file() else None
            state = STATE_READY if current is None or marker.get("lock_sha256") == current else STATE_OUTDATED
        elif reason is not None:
            state = STATE_UNSUPPORTED
        elif progress.error:
            state = STATE_ERROR
        else:
            state = STATE_MISSING
        torch_version = marker.get("torch")
        installed_at = marker.get("installed_at")
        return TrainerSetupStatus(
            state=state,
            phase=progress.phase if progress.active else None,
            phase_label=PHASE_LABELS.get(progress.phase or "") if progress.active else None,
            supported=reason is None,
            reason=reason,
            error=progress.error if state == STATE_ERROR else None,
            log_tail=list(progress.log),
            approx_bytes=APPROX_INSTALLED_BYTES,
            location=str(self.root),
            torch_version=torch_version if isinstance(torch_version, str) else None,
            installed_at=installed_at if isinstance(installed_at, str) else None,
            manual_python=bool(self.manual_python.strip()),
        )

    # ------------------------------------------------------------ Einrichten

    def install_in_background(self) -> TrainerSetupStatus:
        """Startet die Einrichtung in einem Thread. Laeuft sie schon, passiert nichts."""
        reason = self.unsupported_reason()
        if reason is not None:
            raise TrainerSetupError(reason)
        with self._lock:
            if self._progress.active:
                return self.status()
            self._check_disk()
            self._progress = _Progress(active=True, phase="uv")
        threading.Thread(target=self._install_quietly, name="trainer-setup", daemon=True).start()
        return self.status()

    def _check_disk(self) -> None:
        anchor = self.root
        while not anchor.exists() and anchor != anchor.parent:
            anchor = anchor.parent
        free = self.disk_free(anchor)
        if free < MIN_FREE_BYTES:
            raise TrainerSetupError(
                f"Auf dem Laufwerk von {self.root} sind nur {free / 1024**3:.1f} GB frei; "
                f"die Einrichtung braucht etwa {MIN_FREE_BYTES / 1024**3:.0f} GB."
            )

    def _install_quietly(self) -> None:
        try:
            self.install()
        except TrainerSetupError as exc:
            logger.warning("Trainer-Einrichtung: %s", exc)
        except Exception:  # noqa: BLE001 - der Thread darf nicht still sterben
            logger.exception("Trainer-Einrichtung gescheitert.")

    def install(self) -> None:
        """Die vier Schritte nacheinander. Blockiert; ``install_in_background`` ruft das im Thread."""
        progress = self._progress
        progress.active, progress.error = True, None
        try:
            self._install_steps(progress)
        except TrainerSetupError as exc:
            progress.error = str(exc)
            raise
        except Exception as exc:
            progress.error = f"Unerwarteter Fehler: {exc}"
            raise
        finally:
            progress.active = False
            progress.phase = None
            self._process = None

    def _install_steps(self, progress: _Progress) -> None:
        lock = self._lock_path()
        if lock is None or not lock.is_file():
            raise TrainerSetupError("Die Paketliste des Trainers fehlt in der App.")
        self.root.mkdir(parents=True, exist_ok=True)

        progress.phase = "uv"
        uv = self._ensure_uv()

        progress.phase = "python"
        venv_args = [str(uv), "venv", str(self.venv_dir), "--python", TRAINER_PYTHON_VERSION]
        if self.venv_dir.exists():
            venv_args.append("--allow-existing")
        self._run(venv_args)

        progress.phase = "packages"
        python = self._venv_python()
        self._run([str(uv), "pip", "sync", str(lock), "--python", str(python), "--require-hashes", *PACKAGE_INDEX_ARGS])

        progress.phase = "check"
        result = self._check(python)
        if not result.get("cuda"):
            raise TrainerSetupError(
                "PyTorch ist installiert, sieht aber keine NVIDIA-Grafikkarte. "
                "Ein aktueller Grafiktreiber hilft meist; danach die Einrichtung noch einmal starten."
            )
        marker = {
            "lock_sha256": _sha256_file(lock),
            "uv": UV_VERSION,
            "python": TRAINER_PYTHON_VERSION,
            "torch": result.get("torch"),
            "installed_at": datetime.now(UTC).isoformat(),
        }
        (self.root / MARKER_FILE).write_text(json.dumps(marker, indent=2), encoding="utf-8")
        # Die Umgebung braucht den Cache nicht mehr; er ist so gross wie torch selbst.
        shutil.rmtree(self.root / "cache", ignore_errors=True)
        progress.log.append("Fertig eingerichtet.")

    def _env(self) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            # Fremde Python-Umgebungen duerfen die frische nicht verbiegen.
            if not key.startswith(("UV_", "PYTHON", "VIRTUAL_ENV", "CONDA"))
        }
        env.update(
            {
                "UV_CACHE_DIR": str(self.root / "cache"),
                "UV_PYTHON_INSTALL_DIR": str(self.root / "python"),
                "UV_PYTHON_PREFERENCE": "only-managed",
                "UV_NO_CONFIG": "1",
                "UV_NO_PROGRESS": "1",
                "UV_HTTP_TIMEOUT": "600",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        return env

    def _run(self, args: list[str]) -> list[str]:
        """Fuehrt einen Schritt aus und haengt seine Ausgabe an das Protokoll."""
        if self._progress.cancelled:
            raise TrainerSetupError("Abgebrochen.")
        try:
            process = self.popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(self.root),
                env=self._env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise TrainerSetupError(f"Der Schritt ließ sich nicht starten: {exc}") from exc
        self._process = process
        lines: list[str] = []
        if process.stdout is not None:
            for raw in process.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    lines.append(line)
                    self._progress.log.append(line)
        code = process.wait()
        self._process = None
        if self._progress.cancelled:
            raise TrainerSetupError("Abgebrochen.")
        if code != 0:
            last = lines[-1] if lines else f"Exit-Code {code}"
            label = PHASE_LABELS.get(self._progress.phase or "", "Ein Schritt")
            raise TrainerSetupError(f"{label} ist gescheitert: {last}")
        return lines

    def _check(self, python: Path) -> dict[str, object]:
        lines = self._run([str(python), "-c", _CHECK_SCRIPT])
        for line in reversed(lines):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
        raise TrainerSetupError("Die Prüfung der Umgebung lieferte kein Ergebnis.")

    def _ensure_uv(self) -> Path:
        program = self._uv_path()
        target = program.parent
        marker = target / ".sha256"
        asset = self.uv_assets[self.platform]
        if program.is_file() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == asset.sha256:
            return program
        if not is_allowed_runtime_url(asset.url):
            raise TrainerSetupError("Die Download-Adresse ist nicht erlaubt.")
        target.parent.mkdir(parents=True, exist_ok=True)
        archive = target.parent / f"{target.name}.download"
        digest = hashlib.sha256()
        try:
            with (
                httpx.Client(follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SECONDS, transport=self.transport) as client,
                client.stream("GET", asset.url) as response,
            ):
                if not is_allowed_runtime_url(str(response.url)):
                    raise TrainerSetupError("Der Download wurde auf eine nicht erlaubte Adresse umgeleitet.")
                if response.status_code != 200:
                    raise TrainerSetupError(f"Der Download antwortete mit HTTP {response.status_code}.")
                with archive.open("wb") as handle:
                    for block in response.iter_bytes(1024 * 1024):
                        handle.write(block)
                        digest.update(block)
        except httpx.HTTPError as exc:
            archive.unlink(missing_ok=True)
            raise TrainerSetupError(f"Der Download ist fehlgeschlagen: {exc}") from exc
        except TrainerSetupError:
            archive.unlink(missing_ok=True)
            raise
        if digest.hexdigest() != asset.sha256:
            archive.unlink(missing_ok=True)
            raise TrainerSetupError("Die Prüfsumme des Installationsprogramms stimmt nicht. Es wurde verworfen.")
        staging = target.parent / f"{target.name}.staging"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            _extract(archive, staging)
            extracted = staging / Path(*asset.executable.split("/"))
            if not extracted.is_file():
                raise TrainerSetupError("Das Installationsprogramm fehlt im Paket.")
            if os.name != "nt":
                extracted.chmod(extracted.stat().st_mode | 0o111)
            (staging / ".sha256").write_text(asset.sha256, encoding="utf-8")
            shutil.rmtree(target, ignore_errors=True)
            staging.rename(target)
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise TrainerSetupError(f"Das Installationsprogramm ließ sich nicht entpacken: {exc}") from exc
        except TrainerSetupError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            archive.unlink(missing_ok=True)
        self._progress.log.append(f"uv {UV_VERSION} geladen und geprüft.")
        return program

    # ------------------------------------------------------------ Abbrechen, Entfernen

    def cancel(self) -> None:
        """Bricht die laufende Einrichtung ab. Fertig geladene Pakete bleiben im Cache."""
        self._progress.cancelled = True
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

    def remove(self) -> None:
        """Loescht die ganze Umgebung (mehrere GB). Basisgewichte und Adapter bleiben."""
        with self._lock:
            if self._progress.active:
                raise TrainerSetupError("Die Einrichtung läuft gerade. Erst abbrechen, dann entfernen.")
            shutil.rmtree(self.root, ignore_errors=True)
            self._progress = _Progress()
