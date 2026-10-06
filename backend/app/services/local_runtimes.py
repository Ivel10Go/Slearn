"""Eingebaute Laufzeiten: die Programme, die die geladenen Modelle ausfuehren.

Der Modellkatalog (``app/services/model_catalog.py``) laedt nur **Dateien**. Ein
GGUF ohne ``llama-server``, ein Whisper-GGML ohne ``whisper-server`` und eine
Piper-Stimme ohne ``piper`` sind tote Daten - genau der Zustand, in dem ein
Modell "Geladen" zeigt und der Assistent trotzdem "kein KI-Modell" sagt.

Dieses Modul schliesst die Luecke fuer die Desktop-App:

1. **Programme holen.** Die offiziellen Release-Pakete von llama.cpp,
   whisper.cpp und Piper, mit **fest eingetragener Version und Pruefsumme**
   (``RUNTIME_CATALOG``). Ein Paket, dessen SHA-256 nicht passt, wird verworfen.
   Erlaubt sind nur Downloads von GitHub (``ALLOWED_RUNTIME_HOSTS``).
2. **Bei Bedarf starten.** ``llama-server`` und ``whisper-server`` laufen als
   Kindprozesse nur auf ``127.0.0.1`` und auf einem freien Port. Gestartet wird
   beim ersten Gebrauch, nicht beim Hochfahren: wer nie fragt, bezahlt keinen RAM.
   Es laeuft hoechstens **ein** Textmodell - ein Wechsel beendet das alte.
3. **Aufraeumen.** ``shutdown()`` beim Beenden des Backends; unter Windows
   haengen die Kindprozesse zusaetzlich an einem Job-Objekt, das sie mitnimmt,
   falls das Backend hart beendet wird. Ein verwaister ``llama-server`` hielte
   sonst Gigabytes an RAM.

**Warum nicht im Docker-Stack.** Dort ist das Ziel auch ein Raspberry Pi, und
der Betreiber bringt seine Laufzeiten selbst mit (siehe
``app/services/local_inference.py``). Deshalb ist das Modul nur mit
``LOCAL_RUNTIMES_ENABLED=true`` aktiv - die Desktop-App setzt es.

Fuer macOS veroeffentlicht whisper.cpp keine fertigen Programme; dort bleibt die
Spracherkennung beim eingetragenen ``WHISPER_SERVER_URL`` (z. B. per Homebrew).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import platform
import shutil
import socket
import stat
import subprocess
import tarfile
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Final
from urllib.parse import urlsplit

import httpx

from app.services.model_catalog import ModelSpec, catalog_models
from app.services.model_downloads import ModelDownloadManager

logger = logging.getLogger("school_assistant")

RUNTIME_LLAMA: Final = "llama_cpp"
RUNTIME_WHISPER: Final = "whisper_cpp"
RUNTIME_PIPER: Final = "piper"

#: Downloads nur von GitHub. Die Release-Adresse leitet auf den Objektspeicher um.
ALLOWED_RUNTIME_HOSTS: Final[frozenset[str]] = frozenset(
    {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
)

TEXT_CONTEXT_TOKENS: Final = 8192
#: Eigene llama-server neben dem Textmodell: Bilder lesen und Einbettungen.
#: Jeder Platz haelt hoechstens einen Server; ein Modellwechsel beendet den alten.
SLOT_VISION: Final = "llama_cpp_vision"
SLOT_EMBEDDING: Final = "llama_cpp_embedding"
#: Obergrenze je Bild. Ein A4-Scan in voller Aufloesung kostet sonst ~4200 Bild-Token
#: und auf dem Prozessor vier Minuten je Seite; 1280 reichen fuer Handschrift.
VISION_MAX_IMAGE_TOKENS: Final = 1280
#: Ein Einbettungsabsatz ist ~1000 Zeichen; der Stapel muss ihn ganz fassen.
EMBEDDING_BATCH_TOKENS: Final = 2048
SERVER_START_TIMEOUT_SECONDS: Final = 120.0
DOWNLOAD_TIMEOUT_SECONDS: Final = 60.0
_MARKER: Final = ".installed"
_SELECTION: Final = "selection.json"


class LocalRuntimeError(RuntimeError):
    """Eine Laufzeit liess sich nicht holen oder starten. Die Meldung ist deutsch."""


@dataclass(frozen=True, slots=True)
class RuntimeAsset:
    url: str
    sha256: str
    size_bytes: int
    #: Pfad des Programms im entpackten Paket, mit ``/`` getrennt.
    executable: str


@dataclass(frozen=True, slots=True)
class RuntimeSpec:
    id: str
    label: str
    version: str
    assets: dict[str, RuntimeAsset]


def _gh(repo: str, tag: str, name: str) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


_LLAMA_TAG: Final = "b11349"
_WHISPER_TAG: Final = "b5130"
_PIPER_TAG: Final = "2023.11.14-2"

#: Die Pruefsummen stammen aus dem ``digest`` der GitHub-Release-API (llama.cpp,
#: whisper.cpp) bzw. wurden fuer Piper, dessen Release keinen ``digest`` traegt,
#: aus dem heruntergeladenen Paket berechnet.
RUNTIME_CATALOG: Final[dict[str, RuntimeSpec]] = {
    RUNTIME_LLAMA: RuntimeSpec(
        id=RUNTIME_LLAMA,
        label="llama.cpp (llama-server)",
        version=_LLAMA_TAG,
        assets={
            "windows-x64": RuntimeAsset(
                _gh("ggml-org/llama.cpp", _LLAMA_TAG, f"llama-{_LLAMA_TAG}-bin-win-cpu-x64.zip"),
                "3f387c5877b66d078b89b52a2a59cf0f5aa10e69513f3956163edabacffa1c6e",
                19275576,
                "llama-server.exe",
            ),
            "windows-arm64": RuntimeAsset(
                _gh("ggml-org/llama.cpp", _LLAMA_TAG, f"llama-{_LLAMA_TAG}-bin-win-cpu-arm64.zip"),
                "2080e735e0b03666488132ef023fa9d812bb9c25bfa5b37eb158a9a9a2d6e705",
                12118840,
                "llama-server.exe",
            ),
            "macos-arm64": RuntimeAsset(
                _gh("ggml-org/llama.cpp", _LLAMA_TAG, f"llama-{_LLAMA_TAG}-bin-macos-arm64.tar.gz"),
                "bd0f0411541ad468959670ddd5ca8276135a9140cb978898286085aabe3f76f1",
                11828889,
                "llama-server",
            ),
            "macos-x64": RuntimeAsset(
                _gh("ggml-org/llama.cpp", _LLAMA_TAG, f"llama-{_LLAMA_TAG}-bin-macos-x64.tar.gz"),
                "eb3473a158ddf52af9c02d608bcc2eb98d8b7777c925afba83bff6da8df586a2",
                11383859,
                "llama-server",
            ),
            "linux-x64": RuntimeAsset(
                _gh("ggml-org/llama.cpp", _LLAMA_TAG, f"llama-{_LLAMA_TAG}-bin-ubuntu-x64.tar.gz"),
                "7efd2fb72db59f12b05614a709ba6d506b9859e867943955fc9bbd8ee13eccea",
                17551895,
                "llama-server",
            ),
            "linux-arm64": RuntimeAsset(
                _gh("ggml-org/llama.cpp", _LLAMA_TAG, f"llama-{_LLAMA_TAG}-bin-ubuntu-arm64.tar.gz"),
                "88e81cd6517c6dfe66afde7f2fdd07fc4b11bef505886f5b64b5e07b1ee7979a",
                13591937,
                "llama-server",
            ),
        },
    ),
    RUNTIME_WHISPER: RuntimeSpec(
        id=RUNTIME_WHISPER,
        label="whisper.cpp (whisper-server)",
        version=_WHISPER_TAG,
        assets={
            "windows-x64": RuntimeAsset(
                _gh("ggml-org/whisper.cpp", _WHISPER_TAG, "whisper-bin-x64.zip"),
                "f9ec6c52a2e949b62ab51fa21d0d497958f9e41c3010c157c4e42932d5316f3c",
                8573270,
                "Release/whisper-server.exe",
            ),
            "windows-arm64": RuntimeAsset(
                _gh("ggml-org/whisper.cpp", _WHISPER_TAG, "whisper-bin-win-cpu-arm64.zip"),
                "799543b926ab5b6c2d60cab269a2092e0ae8d27820e9e15429e59de3699546fc",
                4361895,
                "whisper-server.exe",
            ),
            "linux-x64": RuntimeAsset(
                _gh("ggml-org/whisper.cpp", _WHISPER_TAG, "whisper-bin-ubuntu-x64.tar.gz"),
                "53e7fd8b5764edad916b8848dd0af6abb1ff1d3b86c899e79c78652412536c32",
                9793438,
                "whisper-server",
            ),
            "linux-arm64": RuntimeAsset(
                _gh("ggml-org/whisper.cpp", _WHISPER_TAG, "whisper-bin-ubuntu-arm64.tar.gz"),
                "93532a0e3777f26f041ffa358ee77dd88b1a33a86847c1990745327ff335a5d6",
                4605905,
                "whisper-server",
            ),
        },
    ),
    RUNTIME_PIPER: RuntimeSpec(
        id=RUNTIME_PIPER,
        label="Piper",
        version=_PIPER_TAG,
        assets={
            "windows-x64": RuntimeAsset(
                _gh("rhasspy/piper", _PIPER_TAG, "piper_windows_amd64.zip"),
                "f3c58906402b24f3a96d92145f58acba6d86c9b5db896d207f78dc80811efcea",
                22477236,
                "piper/piper.exe",
            ),
            "macos-arm64": RuntimeAsset(
                _gh("rhasspy/piper", _PIPER_TAG, "piper_macos_aarch64.tar.gz"),
                "6b1eb03b3735946cb35216e063e7eebcc33a6bbf5dd96ec0217959bf1cdcb0cc",
                19146957,
                "piper/piper",
            ),
            "macos-x64": RuntimeAsset(
                _gh("rhasspy/piper", _PIPER_TAG, "piper_macos_x64.tar.gz"),
                "ced85c0a3df13945b1e623b878a48fdc2854d5c485b4b67f62857cf551deaf8b",
                19146927,
                "piper/piper",
            ),
            "linux-x64": RuntimeAsset(
                _gh("rhasspy/piper", _PIPER_TAG, "piper_linux_x86_64.tar.gz"),
                "a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992",
                26460462,
                "piper/piper",
            ),
            "linux-arm64": RuntimeAsset(
                _gh("rhasspy/piper", _PIPER_TAG, "piper_linux_aarch64.tar.gz"),
                "fea0fd2d87c54dbc7078d0f878289f404bd4d6eea6e7444a77835d1537ab88eb",
                26004717,
                "piper/piper",
            ),
        },
    ),
}


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    """``windows-x64``, ``macos-arm64``, ``linux-x64`` usw."""
    system_name = (system or platform.system()).lower()
    arch = (machine or platform.machine()).lower()
    os_key = {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(system_name, system_name)
    arch_key = "arm64" if arch in {"arm64", "aarch64"} else "x64" if arch in {"x86_64", "amd64", "x64"} else arch
    return f"{os_key}-{arch_key}"


def runtime_asset(runtime_id: str, key: str | None = None) -> RuntimeAsset | None:
    spec = RUNTIME_CATALOG.get(runtime_id)
    return spec.assets.get(key or platform_key()) if spec else None


def is_allowed_runtime_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "").lower() in ALLOWED_RUNTIME_HOSTS


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# --- Windows: Kindprozesse am Job-Objekt -------------------------------------


class _WindowsJob:
    """Ein Job-Objekt mit ``KILL_ON_JOB_CLOSE``: stirbt das Backend, sterben die Server mit."""

    def __init__(self) -> None:
        self._handle: int | None = None
        if os.name != "nt":
            return
        try:
            kernel32 = getattr(ctypes, "windll").kernel32
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return

            class _Basic(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", ctypes.c_uint32),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32),
                ]

            class _Io(ctypes.Structure):
                _fields_ = [
                    (name, ctypes.c_uint64)
                    for name in (
                        "ReadOperationCount",
                        "WriteOperationCount",
                        "OtherOperationCount",
                        "ReadTransferCount",
                        "WriteTransferCount",
                        "OtherTransferCount",
                    )
                ]

            class _Extended(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", _Basic),
                    ("IoInfo", _Io),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            info = _Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info))
            self._handle = handle
        except (AttributeError, OSError):
            self._handle = None

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if self._handle is None:
            return
        try:
            kernel32 = getattr(ctypes, "windll").kernel32
            proc_handle = kernel32.OpenProcess(0x1F0FFF, False, process.pid)
            if proc_handle:
                kernel32.AssignProcessToJobObject(self._handle, proc_handle)
                kernel32.CloseHandle(proc_handle)
        except (AttributeError, OSError):
            pass


# --- Manager ----------------------------------------------------------------


@dataclass(slots=True)
class RuntimeStatus:
    runtime_id: str
    label: str
    version: str
    supported: bool
    state: str  # missing | downloading | installed | error | unsupported
    downloaded_bytes: int = 0
    size_bytes: int = 0
    error: str | None = None


@dataclass(slots=True)
class _Server:
    model_id: str
    process: subprocess.Popen[bytes]
    port: int
    log: IO[bytes] | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self.process.poll() is None


@dataclass(slots=True)
class _Progress:
    downloaded: int = 0
    error: str | None = None
    active: bool = False


@dataclass
class LocalRuntimeManager:
    """Holt und startet die eingebauten Laufzeiten. Thread-sicher, ein Lock je Aufgabe."""

    root: Path
    platform: str = field(default_factory=platform_key)
    transport: httpx.BaseTransport | None = None
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen
    start_timeout_seconds: float = SERVER_START_TIMEOUT_SECONDS
    downloads: ModelDownloadManager | None = None

    def __post_init__(self) -> None:
        self._install_locks = {runtime_id: threading.Lock() for runtime_id in RUNTIME_CATALOG}
        self._server_lock = threading.Lock()
        self._progress = {runtime_id: _Progress() for runtime_id in RUNTIME_CATALOG}
        self._servers: dict[str, _Server] = {}
        #: Aktiver LoRA-Adapter je Textmodell (``training_jobs.py``), Desktop = ein Nutzer.
        self._text_adapters: dict[str, Path] = {}
        self._job = _WindowsJob()

    # ----------------------------------------------------------- Installation

    def supports(self, runtime_id: str) -> bool:
        return runtime_asset(runtime_id, self.platform) is not None

    def _directory(self, runtime_id: str) -> Path:
        spec = RUNTIME_CATALOG[runtime_id]
        return self.root / runtime_id / spec.version

    def executable(self, runtime_id: str) -> Path | None:
        """Das Programm, wenn es vollstaendig entpackt daliegt - sonst ``None``."""
        asset = runtime_asset(runtime_id, self.platform)
        if asset is None:
            return None
        directory = self._directory(runtime_id)
        program = directory / Path(*asset.executable.split("/"))
        if (directory / _MARKER).is_file() and program.is_file():
            return program
        return None

    def status(self, runtime_id: str) -> RuntimeStatus:
        spec = RUNTIME_CATALOG[runtime_id]
        asset = runtime_asset(runtime_id, self.platform)
        progress = self._progress[runtime_id]
        if asset is None:
            state = "unsupported"
        elif self.executable(runtime_id) is not None:
            state = "installed"
        elif progress.active:
            state = "downloading"
        elif progress.error:
            state = "error"
        else:
            state = "missing"
        return RuntimeStatus(
            runtime_id=runtime_id,
            label=spec.label,
            version=spec.version,
            supported=asset is not None,
            state=state,
            downloaded_bytes=progress.downloaded,
            size_bytes=asset.size_bytes if asset else 0,
            error=progress.error if state == "error" else None,
        )

    def install_in_background(self, runtime_id: str) -> None:
        if not self.supports(runtime_id) or self.executable(runtime_id) is not None or self._progress[runtime_id].active:
            return
        threading.Thread(
            target=self._install_quietly, args=(runtime_id,), daemon=True, name=f"runtime-{runtime_id}"
        ).start()

    def _install_quietly(self, runtime_id: str) -> None:
        try:
            self.ensure_installed(runtime_id)
        except LocalRuntimeError as exc:
            logger.warning("Laufzeit %s: %s", runtime_id, exc)

    def ensure_installed(self, runtime_id: str) -> Path:
        """Liefert das Programm und laedt es vorher, falls es fehlt. Blockiert."""
        asset = runtime_asset(runtime_id, self.platform)
        if asset is None:
            raise LocalRuntimeError(
                f"Für {RUNTIME_CATALOG[runtime_id].label} gibt es auf diesem System ({self.platform}) "
                "kein fertiges Programm."
            )
        with self._install_locks[runtime_id]:
            ready = self.executable(runtime_id)
            if ready is not None:
                return ready
            progress = self._progress[runtime_id]
            progress.active, progress.error, progress.downloaded = True, None, 0
            try:
                self._download_and_extract(runtime_id, asset, progress)
            except LocalRuntimeError as exc:
                progress.error = str(exc)
                raise
            finally:
                progress.active = False
            ready = self.executable(runtime_id)
            if ready is None:
                progress.error = "Das Programm fehlt im entpackten Paket."
                raise LocalRuntimeError(progress.error)
            return ready

    def _download_and_extract(self, runtime_id: str, asset: RuntimeAsset, progress: _Progress) -> None:
        if not is_allowed_runtime_url(asset.url):
            raise LocalRuntimeError("Die Download-Adresse ist nicht erlaubt.")
        target = self._directory(runtime_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        archive = target.parent / f"{target.name}.download"
        digest = hashlib.sha256()
        try:
            with (
                httpx.Client(follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SECONDS, transport=self.transport) as client,
                client.stream("GET", asset.url) as response,
            ):
                if not is_allowed_runtime_url(str(response.url)):
                    raise LocalRuntimeError("Der Download wurde auf eine nicht erlaubte Adresse umgeleitet.")
                if response.status_code != 200:
                    raise LocalRuntimeError(f"Der Download antwortete mit HTTP {response.status_code}.")
                with archive.open("wb") as handle:
                    for block in response.iter_bytes(1024 * 1024):
                        handle.write(block)
                        digest.update(block)
                        progress.downloaded += len(block)
        except httpx.HTTPError as exc:
            archive.unlink(missing_ok=True)
            raise LocalRuntimeError(f"Der Download ist fehlgeschlagen: {exc}") from exc
        except OSError as exc:
            archive.unlink(missing_ok=True)
            raise LocalRuntimeError(f"Das Paket ließ sich nicht speichern: {exc.strerror or exc}") from exc
        except LocalRuntimeError:
            archive.unlink(missing_ok=True)
            raise
        if digest.hexdigest() != asset.sha256:
            archive.unlink(missing_ok=True)
            raise LocalRuntimeError("Die Prüfsumme des Pakets stimmt nicht. Es wurde verworfen.")

        staging = target.parent / f"{target.name}.staging"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            _extract(archive, staging)
            program = staging / Path(*asset.executable.split("/"))
            if os.name != "nt":
                for path in staging.rglob("*"):
                    if path.is_file() and (path == program or not path.suffix or path.suffix in {".so", ".dylib"}):
                        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (staging / _MARKER).write_text(asset.sha256, encoding="utf-8")
            shutil.rmtree(target, ignore_errors=True)
            staging.rename(target)
        except (OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise LocalRuntimeError(f"Das Paket ließ sich nicht entpacken: {exc}") from exc
        finally:
            archive.unlink(missing_ok=True)

    # ------------------------------------------------------------- Server

    def ensure_text_server(self, model_id: str, model_path: Path) -> str:
        """Startet ``llama-server`` fuer dieses Modell (oder nutzt den laufenden) und liefert die Basis-URL."""
        program = self.ensure_installed(RUNTIME_LLAMA)
        return self._ensure_server(
            RUNTIME_LLAMA,
            model_id,
            lambda port: [
                str(program),
                "-m",
                str(model_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "-c",
                str(TEXT_CONTEXT_TOKENS),
                "--no-webui",
                *(["--lora", str(self._text_adapters[model_id])] if model_id in self._text_adapters else []),
            ],
            health_path="/health",
        )

    def set_text_adapter(self, model_id: str, adapter: Path | None) -> None:
        """Merkt den LoRA-Adapter fuer dieses Modell; ein laufender Server startet beim naechsten Gebrauch neu."""
        with self._server_lock:
            if self._text_adapters.get(model_id) == adapter:
                return
            if adapter is None:
                self._text_adapters.pop(model_id, None)
            else:
                self._text_adapters[model_id] = adapter
            server = self._servers.get(RUNTIME_LLAMA)
            if server is not None and server.model_id == model_id:
                self._stop(server)
                self._servers.pop(RUNTIME_LLAMA, None)

    def text_adapter(self, model_id: str) -> Path | None:
        return self._text_adapters.get(model_id)

    def attach_child(self, process: subprocess.Popen[bytes]) -> None:
        """Haengt einen fremden Kindprozess (Trainer) an das Job-Objekt: stirbt das Backend, stirbt er mit."""
        self._job.assign(process)

    def ensure_whisper_server(self, model_id: str, model_path: Path, language: str | None) -> str:
        """Startet ``whisper-server`` fuer dieses Modell und liefert die Basis-URL."""
        program = self.ensure_installed(RUNTIME_WHISPER)
        return self._ensure_server(
            RUNTIME_WHISPER,
            model_id,
            lambda port: [
                str(program),
                "-m",
                str(model_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "-l",
                language or "auto",
            ],
            health_path="/",
        )

    # ------------------------------------------------------- Textmodelle

    def installed_text_models(self) -> list[ModelSpec]:
        """Die geladenen und geprueften Textmodelle, groesstes zuerst."""
        if self.downloads is None:
            return []
        specs = [spec for spec in catalog_models() if spec.task == "text_generation" and spec.files]
        installed = [spec for spec in specs if self.downloads.is_installed(spec)]
        return sorted(installed, key=lambda spec: (-spec.size_bytes, spec.id))

    def preferred_text_model(self) -> ModelSpec | None:
        """Das zuletzt gewaehlte Textmodell - sonst das groesste geladene."""
        installed = self.installed_text_models()
        if not installed:
            return None
        try:
            chosen = json.loads((self.root / _SELECTION).read_text(encoding="utf-8")).get("text_model")
        except (OSError, ValueError, AttributeError):
            chosen = None
        for spec in installed:
            if spec.id == chosen:
                return spec
        return installed[0]

    def remember_text_model(self, model_id: str) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / _SELECTION).write_text(json.dumps({"text_model": model_id}), encoding="utf-8")
        except OSError as exc:
            logger.warning("Modellauswahl nicht gespeichert: %s", exc)

    def text_model(self, model_id: str) -> ModelSpec | None:
        return next((spec for spec in self.installed_text_models() if spec.id == model_id), None)

    def ensure_text_model(self, spec: ModelSpec) -> str:
        """Startet ``llama-server`` fuer dieses geladene Textmodell und liefert die Basis-URL."""
        if self.downloads is None:
            raise LocalRuntimeError("Es gibt keine Modellablage.")
        return self.ensure_text_server(spec.id, self.downloads.model_directory(spec) / spec.files[0].name)

    # ------------------------------------------------- Bilder und Einbettungen

    def installed_models(self, task: str) -> list[ModelSpec]:
        """Die geladenen Modelle einer Aufgabe, groesstes zuerst."""
        if self.downloads is None:
            return []
        specs = [spec for spec in catalog_models() if spec.task == task and spec.files]
        installed = [spec for spec in specs if self.downloads.is_installed(spec)]
        return sorted(installed, key=lambda spec: (-spec.size_bytes, spec.id))

    def ensure_vision_model(self, spec: ModelSpec) -> str:
        """Startet ``llama-server`` mit Bildteil (``--mmproj``) fuer Fotos und Scans."""
        if self.downloads is None:
            raise LocalRuntimeError("Es gibt keine Modellablage.")
        directory = self.downloads.model_directory(spec)
        projector = next((f for f in spec.files if f.name.startswith("mmproj")), None)
        weights = next((f for f in spec.files if not f.name.startswith("mmproj")), None)
        if projector is None or weights is None:
            raise LocalRuntimeError(f"{spec.label} hat keinen Bildteil.")
        program = self.ensure_installed(RUNTIME_LLAMA)
        return self._ensure_server(
            RUNTIME_LLAMA,
            spec.id,
            lambda port: [
                str(program),
                "-m",
                str(directory / weights.name),
                "--mmproj",
                str(directory / projector.name),
                "--image-max-tokens",
                str(VISION_MAX_IMAGE_TOKENS),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "-c",
                str(TEXT_CONTEXT_TOKENS),
                "--no-webui",
            ],
            health_path="/health",
            slot=SLOT_VISION,
        )

    def ensure_embedding_model(self, spec: ModelSpec) -> str:
        """Startet ``llama-server --embedding`` fuer die Bedeutungssuche."""
        if self.downloads is None:
            raise LocalRuntimeError("Es gibt keine Modellablage.")
        program = self.ensure_installed(RUNTIME_LLAMA)
        model_path = self.downloads.model_directory(spec) / spec.files[0].name
        return self._ensure_server(
            RUNTIME_LLAMA,
            spec.id,
            lambda port: [
                str(program),
                "-m",
                str(model_path),
                "--embedding",
                "--pooling",
                "cls",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "-c",
                str(TEXT_CONTEXT_TOKENS),
                "-b",
                str(EMBEDDING_BATCH_TOKENS),
                "-ub",
                str(EMBEDDING_BATCH_TOKENS),
                "--no-webui",
            ],
            health_path="/health",
            slot=SLOT_EMBEDDING,
        )

    def stop_slot(self, slot: str) -> None:
        """Beendet einen Nebenserver, etwa das Bildmodell nach dem Lesen - es haelt Gigabytes."""
        with self._server_lock:
            server = self._servers.pop(slot, None)
            if server is not None:
                self._stop(server)

    def running_model(self, runtime_id: str) -> str | None:
        server = self._servers.get(runtime_id)
        return server.model_id if server and server.alive() else None

    def _ensure_server(
        self,
        runtime_id: str,
        model_id: str,
        argv_for: Callable[[int], list[str]],
        *,
        health_path: str,
        slot: str | None = None,
    ) -> str:
        label = RUNTIME_CATALOG[runtime_id].label
        key = slot or runtime_id
        with self._server_lock:
            server = self._servers.get(key)
            if server and server.alive() and server.model_id == model_id:
                return server.base_url
            if server:
                self._stop(server)
                self._servers.pop(key, None)
            port = free_port()
            argv = argv_for(port)
            log_dir = self.root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{key}.log"
            log = log_path.open("wb")
            try:
                process = self.popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    cwd=str(Path(argv[0]).parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                log.close()
                raise LocalRuntimeError(f"{label} ließ sich nicht starten: {exc}") from exc
            self._job.assign(process)
            server = _Server(model_id=model_id, process=process, port=port, log=log)
            if not self._wait_healthy(server, health_path):
                self._stop(server)
                raise LocalRuntimeError(f"{label} ist nicht rechtzeitig bereit geworden. Details stehen in {log_path}.")
            self._servers[key] = server
            return server.base_url

    def _wait_healthy(self, server: _Server, health_path: str) -> bool:
        deadline = time.monotonic() + self.start_timeout_seconds
        with httpx.Client(timeout=2.0, transport=self.transport) as client:
            while time.monotonic() < deadline:
                if not server.alive():
                    return False
                try:
                    if client.get(server.base_url + health_path).status_code == 200:
                        return True
                except httpx.HTTPError:
                    pass
                time.sleep(0.3)
        return False

    @staticmethod
    def _stop(server: _Server) -> None:
        if server.alive():
            server.process.terminate()
            try:
                server.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.process.kill()
        if server.log is not None:
            server.log.close()

    def shutdown(self) -> None:
        with self._server_lock:
            for server in self._servers.values():
                self._stop(server)
            self._servers.clear()


def _extract(archive: Path, destination: Path) -> None:
    """Entpackt ZIP oder tar.gz - und weist Eintraege ab, die aus dem Ziel herausfuehren."""
    destination.mkdir(parents=True)
    root = destination.resolve()

    def _inside(name: str) -> None:
        target = (destination / name).resolve()
        if root != target and root not in target.parents:
            raise OSError(f"Unerlaubter Pfad im Paket: {name}")

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for member_name in bundle.namelist():
                _inside(member_name)
            bundle.extractall(destination)
        return
    with tarfile.open(archive, "r:gz") as tar_bundle:
        members = tar_bundle.getmembers()
        for member in members:
            _inside(member.name)
            if member.issym() or member.islnk():
                _inside(os.path.join(os.path.dirname(member.name), member.linkname))
        tar_bundle.extractall(destination, members=members)


# --- Prozessweiter Zugriff ---------------------------------------------------

_active: LocalRuntimeManager | None = None


def set_active_manager(manager: LocalRuntimeManager | None) -> None:
    """Die KI-Wege (``ai_routing``) laufen ohne Zugriff auf ``app.state``; sie finden den Manager hier."""
    global _active
    _active = manager


def active_manager() -> LocalRuntimeManager | None:
    return _active
