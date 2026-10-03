"""Eingebaute Laufzeiten: Download mit Pruefsumme, Entpacken, Serverstart, KI-Weg.

Kein Test laedt etwas aus dem Netz oder startet ein echtes Programm: der
Download laeuft gegen ``httpx.MockTransport``, der Prozessstart gegen eine
Attrappe. Gegen die echten Pakete ist der Ablauf von Hand geprueft (siehe
``AGENTS.md``).
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.services import ai_routing
from app.services import local_runtimes as lr
from app.services.model_catalog import model_spec
from app.services.model_downloads import ModelDownloadManager


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return buffer.getvalue()


def _tar(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _catalog(monkeypatch: pytest.MonkeyPatch, payload: bytes, executable: str, *, sha: str | None = None) -> None:
    asset = lr.RuntimeAsset(
        url="https://github.com/ggml-org/llama.cpp/releases/download/x/paket",
        sha256=sha or hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        executable=executable,
    )
    spec = lr.RuntimeSpec(id=lr.RUNTIME_LLAMA, label="llama.cpp", version="t1", assets={"test-x64": asset})
    monkeypatch.setitem(lr.RUNTIME_CATALOG, lr.RUNTIME_LLAMA, spec)


def _transport(payload: bytes, calls: list[str] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"Location": "https://objects.githubusercontent.com/paket"})
        return httpx.Response(200, content=payload)

    return httpx.MockTransport(handler)


def test_plattformschluessel() -> None:
    assert lr.platform_key("Windows", "AMD64") == "windows-x64"
    assert lr.platform_key("Darwin", "arm64") == "macos-arm64"
    assert lr.platform_key("Linux", "aarch64") == "linux-arm64"


def test_der_katalog_laedt_nur_von_github_und_hat_echte_pruefsummen() -> None:
    for spec in lr.RUNTIME_CATALOG.values():
        for asset in spec.assets.values():
            assert lr.is_allowed_runtime_url(asset.url), asset.url
            assert len(asset.sha256) == 64 and int(asset.sha256, 16) >= 0
            assert asset.size_bytes > 1_000_000
    # whisper.cpp veroeffentlicht fuer macOS keine Programme.
    assert lr.runtime_asset(lr.RUNTIME_WHISPER, "macos-arm64") is None
    assert lr.runtime_asset(lr.RUNTIME_LLAMA, "windows-x64") is not None


def test_download_folgt_der_umleitung_prueft_und_entpackt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _zip({"llama-server.exe": b"programm", "ggml.dll": b"lib"})
    _catalog(monkeypatch, payload, "llama-server.exe")
    calls: list[str] = []
    manager = lr.LocalRuntimeManager(tmp_path, platform="test-x64", transport=_transport(payload, calls))

    assert manager.status(lr.RUNTIME_LLAMA).state == "missing"
    program = manager.ensure_installed(lr.RUNTIME_LLAMA)

    assert program.read_bytes() == b"programm"
    assert manager.status(lr.RUNTIME_LLAMA).state == "installed"
    assert len(calls) == 2
    # Ein zweiter Aufruf laedt nichts mehr.
    manager.ensure_installed(lr.RUNTIME_LLAMA)
    assert len(calls) == 2


def test_falsche_pruefsumme_wird_verworfen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _zip({"llama-server.exe": b"programm"})
    _catalog(monkeypatch, payload, "llama-server.exe", sha="0" * 64)
    manager = lr.LocalRuntimeManager(tmp_path, platform="test-x64", transport=_transport(payload))

    with pytest.raises(lr.LocalRuntimeError, match="Prüfsumme"):
        manager.ensure_installed(lr.RUNTIME_LLAMA)
    assert manager.executable(lr.RUNTIME_LLAMA) is None
    assert manager.status(lr.RUNTIME_LLAMA).state == "error"
    assert not any(tmp_path.rglob("*.download"))


def test_umleitung_auf_fremden_host_wird_abgewiesen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _zip({"llama-server.exe": b"programm"})
    _catalog(monkeypatch, payload, "llama-server.exe")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"Location": "https://boese.example/paket"})
        return httpx.Response(200, content=payload)

    manager = lr.LocalRuntimeManager(tmp_path, platform="test-x64", transport=httpx.MockTransport(handler))
    with pytest.raises(lr.LocalRuntimeError, match="nicht erlaubte"):
        manager.ensure_installed(lr.RUNTIME_LLAMA)


def test_paket_mit_pfad_nach_draussen_wird_nicht_entpackt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _tar({"../draussen": b"x", "llama-server": b"programm"})
    _catalog(monkeypatch, payload, "llama-server")
    manager = lr.LocalRuntimeManager(tmp_path / "rt", platform="test-x64", transport=_transport(payload))

    with pytest.raises(lr.LocalRuntimeError, match="entpacken"):
        manager.ensure_installed(lr.RUNTIME_LLAMA)
    assert not (tmp_path / "rt" / "llama_cpp" / "draussen").exists()
    assert not (tmp_path / "draussen").exists()


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 4242
        self.terminated = False

    def poll(self) -> int | None:
        return 0 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.terminated = True


def _manager_mit_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[lr.LocalRuntimeManager, list[list[str]]]:
    payload = _zip({"llama-server.exe": b"programm"})
    _catalog(monkeypatch, payload, "llama-server.exe")
    gestartet: list[list[str]] = []

    def fake_popen(argv: list[str], **_: Any) -> _FakeProcess:
        gestartet.append(argv)
        return _FakeProcess()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "127.0.0.1":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"Location": "https://objects.githubusercontent.com/p"})
        return httpx.Response(200, content=payload)

    manager = lr.LocalRuntimeManager(
        tmp_path,
        platform="test-x64",
        transport=httpx.MockTransport(handler),
        popen=fake_popen,  # type: ignore[arg-type]
        start_timeout_seconds=2.0,
    )
    return manager, gestartet


def test_textserver_startet_einmal_und_wechselt_beim_modellwechsel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, gestartet = _manager_mit_server(tmp_path, monkeypatch)

    erste = manager.ensure_text_server("modell-a", tmp_path / "a.gguf")
    zweite = manager.ensure_text_server("modell-a", tmp_path / "a.gguf")
    assert erste == zweite and erste.startswith("http://127.0.0.1:")
    assert len(gestartet) == 1
    argv = gestartet[0]
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("-m") + 1].endswith("a.gguf")

    manager.ensure_text_server("modell-b", tmp_path / "b.gguf")
    assert len(gestartet) == 2
    assert manager.running_model(lr.RUNTIME_LLAMA) == "modell-b"

    manager.shutdown()
    assert manager.running_model(lr.RUNTIME_LLAMA) is None


def test_server_der_sofort_stirbt_meldet_einen_fehler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager, _ = _manager_mit_server(tmp_path, monkeypatch)

    def toter_popen(argv: list[str], **_: Any) -> _FakeProcess:
        prozess = _FakeProcess()
        prozess.terminated = True
        return prozess

    manager.popen = toter_popen  # type: ignore[assignment]
    with pytest.raises(lr.LocalRuntimeError, match="nicht rechtzeitig bereit"):
        manager.ensure_text_server("modell-a", tmp_path / "a.gguf")


def _installiere(downloads: ModelDownloadManager, *model_ids: str) -> None:
    """Laesst den Download-Manager diese Katalogmodelle als geladen melden."""
    installiert = {model_spec(model_id).id for model_id in model_ids}
    downloads.is_installed = lambda spec: spec.id in installiert  # type: ignore[method-assign]


def test_eingebauter_weg_startet_den_server_und_erzeugt_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, gestartet = _manager_mit_server(tmp_path, monkeypatch)
    downloads = ModelDownloadManager(tmp_path / "models")
    manager.downloads = downloads
    _installiere(downloads, "qwen2.5-1.5b-instruct-q4km")
    lr.set_active_manager(manager)
    aufrufe: list[dict[str, Any]] = []

    def fake_local(system: str, user: str, **kwargs: Any) -> str:
        aufrufe.append(kwargs)
        return "Hallo aus dem eingebauten Modell."

    monkeypatch.setattr("app.services.ai_routing.generate_locally", fake_local)
    try:
        route = ai_routing.builtin_route()
        assert route is not None and route.is_builtin and route.is_local
        ergebnis = ai_routing.generate_with_routes((route,), "S", "U")
    finally:
        lr.set_active_manager(None)
        manager.shutdown()

    assert ergebnis is not None
    assert ergebnis.source == "local"
    assert ergebnis.text == "Hallo aus dem eingebauten Modell."
    assert len(gestartet) == 1
    assert aufrufe[0]["base_url"].startswith("http://127.0.0.1:")


def test_ohne_manager_gibt_es_keinen_eingebauten_weg() -> None:
    lr.set_active_manager(None)
    assert ai_routing.builtin_route() is None


def test_modellauswahl_wird_gemerkt(tmp_path: Path) -> None:
    downloads = ModelDownloadManager(tmp_path / "models")
    _installiere(downloads, "qwen2.5-1.5b-instruct-q4km", "llama-3.2-1b-instruct-q4km")
    manager = lr.LocalRuntimeManager(tmp_path / "rt", platform="test-x64", downloads=downloads)

    # Ohne Wahl das groesste geladene Modell.
    gewaehlt = manager.preferred_text_model()
    assert gewaehlt is not None and gewaehlt.id == "qwen2.5-1.5b-instruct-q4km"
    manager.remember_text_model("llama-3.2-1b-instruct-q4km")
    gewaehlt = manager.preferred_text_model()
    assert gewaehlt is not None and gewaehlt.id == "llama-3.2-1b-instruct-q4km"


def test_sprachdienst_meldet_eingebaute_spracherkennung_als_bereit(tmp_path: Path) -> None:
    from app.services.speech import SpeechService

    downloads = ModelDownloadManager(tmp_path / "models")
    _installiere(downloads, "whisper-tiny-ggml")
    runtimes = lr.LocalRuntimeManager(tmp_path / "rt", platform="windows-x64", downloads=downloads)
    ohne = SpeechService(downloads=downloads, transcriber=None, synthesizer=None)
    mit = SpeechService(downloads=downloads, transcriber=None, synthesizer=None, runtimes=runtimes)

    assert ohne.capabilities().speech_to_text.available is False
    faehig = mit.capabilities().speech_to_text
    assert faehig.available is True
    assert "whisper.cpp" in faehig.reason

    # Auf macOS gibt es kein fertiges whisper-server: dann bleibt es beim Hinweis.
    mac = SpeechService(
        downloads=downloads,
        transcriber=None,
        synthesizer=None,
        runtimes=lr.LocalRuntimeManager(tmp_path / "rt2", platform="macos-arm64", downloads=downloads),
    )
    assert mac.capabilities().speech_to_text.available is False


def test_programme_werden_ohne_shell_gestartet() -> None:
    """Die Startargumente sind eine Liste - nie ein Shell-String mit Modellpfad."""
    assert lr.LocalRuntimeManager.__dataclass_fields__["popen"].default is subprocess.Popen


def test_wav_kopf_aus_der_ffmpeg_pipe_wird_repariert() -> None:
    """ffmpeg nach ``pipe:1`` laesst die Laengen auf 0xFFFFFFFF - die Dauer muss trotzdem stimmen."""
    import struct

    from app.services.speech import _wav_duration_seconds, repair_streamed_wav

    samples = b"\x00\x00" * 16000  # eine Sekunde, 16 kHz, mono, 16 Bit
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
    kaputt = (
        b"RIFF" + b"\xff\xff\xff\xff" + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
        + b"data" + b"\xff\xff\xff\xff" + samples
    )
    repariert = repair_streamed_wav(kaputt)
    assert abs(_wav_duration_seconds(repariert) - 1.0) < 0.01
    assert repair_streamed_wav(b"keine wav") == b"keine wav"
