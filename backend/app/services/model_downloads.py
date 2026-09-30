"""Der Download-Manager: holt Katalog-Modelle herunter, prüft sie und legt sie ab.

Der Download läuft **im Backend**, nicht im Browser. Drei Gründe, alle aus dem
Auftrag heraus:

* Ein Browser kann einen Download nicht fortsetzen, wenn der Tab zu ist.
* Der Browser kennt die SHA256 des Modells nicht und könnte sie auch nicht
  nachrechnen, ohne das halbe Gigabyte selbst zu laden.
* Nur der Server kennt die Ablage, die Rechte darauf und den Zustand, der nach
  einem Neustart noch da ist.

Die Ablage ist ein Verzeichnis pro Modell und sonst nichts::

    MODEL_STORAGE_PATH/
      qwen2.5-1.5b-instruct-q4km/
        Qwen2.5-1.5B-Instruct-Q4_K_M.gguf          <- erst nach der Prüfung so benannt
        Qwen2.5-1.5B-Instruct-Q4_K_M.gguf.part     <- der Teilstand
        installed.json                              <- Prüfsummen und Zeitpunkt

Das Muster ist dem Backup-Runner im Worker abgeschaut (``worker/backup.py``):
Tmp-Datei schreiben, prüfen, **dann** atomar umbenennen. Eine halb geladene
Datei unter dem endgültigen Namen ist ein Zustand, aus dem ein späterer Start
ein kaputtes Modell lädt.

Die sechs Fallen, die dieser Manager ausdrücklich behandelt:

1. **Range.** Es wird ``Range: bytes=<offset>-`` geschickt, dazu
   ``Accept-Encoding: identity``, weil eine transparente Kompression den
   Byte-Versatz zerstören würde. Antwortet der Server mit ``200`` statt ``206``,
   hat er die Range ignoriert: dann wird **nicht** angehängt, sondern die
   Teildatei verworfen und ohne Range neu angefordert. Ein Angebot von hinten
   an eine vorhandene Datei wäre eine stille Korruption.
2. **Prüfsumme über alle Teilstücke.** Beim Fortsetzen wird der vorhandene
   Präfix neu gelesen und in den laufenden Hash eingerechnet. Ein Resume, das
   nur den neuen Teil prüft, prüft gar nichts.
3. **Prüfen vor Umbenennen.** ``part.replace(target)`` passiert erst, wenn die
   SHA256 stimmt; bei Abweichung wird die Teildatei gelöscht, weil ein
   vergifteter Präfix sonst endlos weitergeführt würde.
4. **Kein gespeicherter Fortsetzungspunkt.** Der Offset kommt immer aus
   ``stat().st_size`` der Teildatei. Er kann also weder von einem Abbruch noch
   von einem Absturz hinterlassen werden, und er ist nach einem Neustart genauso
   richtig wie vorher.
5. **Abbruch.** Zwischen den Blöcken wird ein ``Event`` geprüft. Ein Abbruch
   lässt den gültigen Präfix liegen und meldet ``interrupted`` - der nächste
   Start setzt dort fort. Er meldet nie ``installed``.
6. **Doppelter Download.** Gesperrt wird **pro Modell**, nicht pro Nutzer: zwei
   Tabs, die dasselbe Modell anstoßen, sind der Normalfall, und beide schrieben
   sonst in dieselbe Teildatei. Das Schloss ist ein ``threading.Lock`` je
   ``model_id``; ein zweiter Versuch bekommt 409 statt mitzulaufen.

Nicht gebaut (bewusst, weil es eine andere Welle ist): das Laden des Modells in
eine Laufzeit. Diese Datei kennt keine Inference und muss sie auch nicht kennen -
sie hinterlässt ein geprüftes Verzeichnis, und wer es liest, entscheidet selbst.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol

import httpx

from app.core.time import utc_now
from app.services.model_catalog import (
    MAX_REDIRECTS,
    MODEL_ID_PATTERN,
    ModelCatalogError,
    ModelFile,
    ModelSpec,
    validate_download_url,
)

logger = logging.getLogger("school_assistant.models")

#: Blockgröße beim Schreiben. Groß genug, dass der Syscall-Anteil verschwindet,
#: klein genug, dass der Abbruch zwischen zwei Blöcken schnell greift.
CHUNK_SIZE: Final[int] = 1024 * 1024

#: Endung der unfertigen Datei. Bewusst kein Punkt-Präfix wie in
#: ``worker/backup.py``: dort ist die Benennung egal, hier wäre eine versteckte
#: Datei neben einer Modellgewichtsdatei nur schwerer zu erkennen.
PART_SUFFIX: Final[str] = ".part"

#: Nachweis, dass **alle** Dateien geprüft sind. Ohne diese Datei gilt ein
#: Modell als nicht installiert, auch wenn die Gewichte zufällig vollständig da
#: liegen - sonst könnte eine abgebrochene Prüfung wie ein Erfolg aussehen.
MANIFEST_NAME: Final[str] = "installed.json"

#: Freier Speicher, der unter allen Umständen übrig bleiben soll. Ohne Reserve
#: füllt ein 2-GB-Modell auf einer 3-GB-Partition den Datenträger und reißt
#: dabei die Datenbank mit.
DEFAULT_MIN_FREE_BYTES: Final[int] = 512 * 1024 * 1024

USER_AGENT: Final[str] = "school-assistant/1.0 (lokale Modelle)"

#: Die Zustände, die die Oberfläche sieht. ``interrupted`` heißt: es liegt ein
#: Teilstand vor, aber es läuft gerade kein Download - nach einem Abbruch wie
#: nach einem Absturz.
MODEL_STATUSES: Final[tuple[str, ...]] = (
    "not_downloaded",
    "downloading",
    "installed",
    "interrupted",
    "failed",
)


class ModelDownloadError(RuntimeError):
    """Ein Download ist fehlgeschlagen. Die Meldung ist deutsch und geht in die API."""


class ModelDownloadBusy(ModelDownloadError):
    """Für dieses Modell läuft bereits ein Download."""


class ModelDownloadCancelled(ModelDownloadError):
    """Der Download wurde abgebrochen; der Teilstand bleibt gültig."""


class ModelStorageError(ModelDownloadError):
    """Die Ablage ist nicht beschreibbar, oder eine Datei liess sich nicht schreiben."""


class ModelSpaceError(ModelStorageError):
    """Zu wenig Platz auf dem Datentraeger.

    Eigene Klasse und nicht nur eine Formulierung im Text: Der Endpunkt
    antwortet hier mit 409 (eine Frage der Partition und ihrer Reserve) und bei
    einer nicht beschreibbaren Ablage mit 503. Eine Meldung, nach der
    unterschieden wird, ist die schlechteste Stelle für diese Entscheidung.
    """


@dataclass(frozen=True, slots=True)
class ModelInstallState:
    """Was über ein Modell bekannt ist: der Katalog und die Ablage.

    ``status`` ist eine der Zeichenketten aus :data:`MODEL_STATUSES`. Der
    Fortschritt kommt aus dem Dateisystem statt aus einer Zählerstelle im
    Speicher - deshalb übersteht er einen Neustart und kann nicht von einem
    stehengebliebenen Thread abweichen.
    """

    model_id: str
    status: str
    bytes_downloaded: int
    bytes_total: int
    verified: bool
    current_file: str | None
    message: str
    started_at: datetime | None
    finished_at: datetime | None
    speed_bytes_per_second: float | None

    @property
    def progress_percent(self) -> float:
        if self.bytes_total <= 0:
            return 0.0
        return round(min(100.0, self.bytes_downloaded / self.bytes_total * 100), 1)


@dataclass
class _Progress:
    """Der Laufzeitzustand eines Download-Threads. Nur im Speicher."""

    status: str = "not_downloaded"
    bytes_done: int = 0
    bytes_at_start: int = 0
    current_file: str | None = None
    message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


class _Digest(Protocol):
    """Nur die zwei Methoden, die der Manager von einem Hash-Objekt braucht.

    Bewusst ein Protokoll statt ``hashlib._Hash``: Der private Typ ist kein
    öffentlicher Vertrag, und diese Datei soll nicht davon abhängen, wie Python
    einen Hash erzeugt.
    """

    def update(self, data: bytes) -> None: ...

    def hexdigest(self) -> str: ...


def _hash_prefix(path: Path, length: int, digest: _Digest) -> None:
    """Rechnet die ersten ``length`` Byte einer Datei in ``digest`` ein.

    Pflicht beim Fortsetzen: ein Hash über nur den neuen Teil würde die
    Prüfsumme der Gesamtdatei nicht bestätigen, sondern die des Anhangs.
    """
    remaining = length
    with path.open("rb") as handle:
        while remaining > 0:
            block = handle.read(min(CHUNK_SIZE, remaining))
            if not block:
                raise ModelDownloadError(f"Die Teildatei {path.name} ist kürzer geworden als erwartet.")
            digest.update(block)
            remaining -= len(block)


def _content_range_start(response: httpx.Response) -> int | None:
    """Der Startbyte aus dem ``Content-Range``-Header, sonst ``None``."""
    header = response.headers.get("content-range", "")
    if not header.lower().startswith("bytes "):
        return None
    span = header[len("bytes ") :].split("/", 1)[0].strip()
    start, separator, _end = span.partition("-")
    if not separator or not start.strip().isdigit():
        return None
    return int(start.strip())


def _free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


class ModelDownloadManager:
    """Lädt Modelle aus dem Katalog, sperrt je Modell und meldet den Stand.

    Der Manager kennt **keine** HTTP-Routen und **keine** Benutzer. Er ist ein
    Dienst mit vier Aufgaben: ``status`` lesen, ``start``, ``cancel`` und
    ``remove``. Die Sperre ist bewusst je ``model_id`` gebaut und nicht je
    Nutzer - zwei Tabs auf dasselbe Modell sind der Normalfall, nicht der
    Ausnahmefall.

    ``client_factory`` ist der einzige Griff nach draußen. Im Betrieb ist das ein
    ``httpx.Client`` mit deaktiviertem automatischen Redirect-Follow: die
    Weiterleitung wird von Hand verfolgt, damit **jeder** Sprung gegen die
    Host-Allowlist aus :mod:`app.services.model_catalog` geprüft werden kann.
    """

    def __init__(
        self,
        root: Path,
        *,
        timeout_seconds: float = 60.0,
        min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._root = Path(root)
        self._timeout_seconds = timeout_seconds
        self._min_free_bytes = max(0, min_free_bytes)
        self._client_factory: Callable[[], httpx.Client] = client_factory or (
            lambda: httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        )
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._progress: dict[str, _Progress] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._shutdown = threading.Event()

    # ---------------------------------------------------------------- Ablage

    @property
    def root(self) -> Path:
        return self._root

    def model_directory(self, spec: ModelSpec) -> Path:
        """Das Verzeichnis eines Modells - ohne jeden Dateisystemzugriff.

        Die ``model_id`` ist heute aus dem Katalog und über
        :data:`MODEL_ID_PATTERN` gegen ``..`` und Trennzeichen abgesichert. Die
        Prüfung bleibt trotzdem hier, weil derselbe Aufruf in einer späteren
        Datenbankvariante eine Id aus der Datenbank bekommt.
        """
        if not MODEL_ID_PATTERN.match(spec.id):
            raise ModelStorageError(f"Die Modellkennung {spec.id!r} ist kein zulässiger Verzeichnisname.")
        return self._root / spec.id

    def storage_state(self) -> dict[str, object]:
        """Ob die Ablage benutzbar ist, und wie viel Platz übrig ist.

        Der Pfad selbst kommt **nicht** in die Antwort: Ein Container-Pfad sagt
        dem Browser nichts und legt die Verzeichnisstruktur offen. Dieselbe
        Entscheidung hat ``app.services.backups`` für den Programmnamen getroffen.
        """
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return {
                "available": False,
                "writable": False,
                "free_bytes": None,
                "message": (
                    f"Die Modellablage liess sich nicht anlegen: {error.strerror or type(error).__name__}. "
                    "Im Container muss der Modellpfad read/write eingebunden sein."
                ),
            }
        if not os.access(self._root, os.W_OK):
            return {
                "available": True,
                "writable": False,
                "free_bytes": _free_bytes(self._root),
                "message": (
                    "Die Modellablage ist nicht beschreibbar. Im Container muss der Modellpfad "
                    "read/write eingebunden sein, sonst schlägt jeder Download fehl."
                ),
            }
        return {
            "available": True,
            "writable": True,
            "free_bytes": _free_bytes(self._root),
            "message": (
                "Die Modelle liegen in einem eigenen Verzeichnis und werden nicht durchsucht. "
                "Im Container muss der Modellpfad read/write eingebunden sein, sonst sind sie nach "
                "einem Neustart weg."
            ),
        }

    # ---------------------------------------------------------------- Status

    def status(self, spec: ModelSpec) -> ModelInstallState:
        """Was über ein Modell bekannt ist.

        Die Reihenfolge der Prüfungen ist die Kernaussage: Erst ein vorhandenes
        Manifest, dann ein laufender Thread, dann der Dateisystemzustand. Ein
        vollständig geladenes Modell ist nie ``downloading``, auch wenn der
        Thread eben erst fertig geworden ist.
        """
        progress = self._progress_for(spec.id)
        installed = self._is_installed(spec)
        downloaded = self._downloaded_bytes(spec)
        running = self._is_running(spec.id)
        bytes_total = spec.size_bytes

        if installed:
            status_value = "installed"
            message = "Das Modell liegt vollständig und geprüft auf dem Datenträger."
        elif running:
            status_value = "downloading"
            current = progress.current_file or "der ersten Datei"
            message = f"Wird geladen, gerade {current}. Ein Abbruch setzt später dort fort."
        elif progress.status == "failed":
            status_value = "failed"
            message = progress.message or "Der Download ist fehlgeschlagen."
        elif downloaded > 0:
            status_value = "interrupted"
            message = "Der Download wurde unterbrochen. Der nächste Start setzt beim Teilstand fort."
        else:
            status_value = "not_downloaded"
            message = "Das Modell ist noch nicht geladen."

        return ModelInstallState(
            model_id=spec.id,
            status=status_value,
            bytes_downloaded=downloaded,
            bytes_total=bytes_total,
            verified=installed,
            current_file=progress.current_file if status_value == "downloading" else None,
            message=message,
            started_at=progress.started_at,
            finished_at=progress.finished_at if not running else None,
            speed_bytes_per_second=self._speed(progress, downloaded) if running else None,
        )

    def is_installed(self, spec: ModelSpec) -> bool:
        return self._is_installed(spec)

    # -------------------------------------------------------------- Steuerung

    def start(self, spec: ModelSpec) -> ModelInstallState:
        """Startet den Download im Hintergrund oder setzt ihn fort.

        Ein bereits vollständig geladenes Modell startet **nichts**: der Aufruf
        ist dann ein No-Op mit demselben Zustand, damit ein wiederholter Klick
        keinen zweiten Thread und keine zweite Teildatei erzeugt.
        """
        lock = self._lock_for(spec.id)
        if not lock.acquire(blocking=False):
            raise ModelDownloadBusy(
                f"Für {spec.label} läuft bereits ein Download. Der zweite Versuch wird abgelehnt, "
                "damit nicht zwei Threads in dieselbe Datei schreiben."
            )
        try:
            if self._is_installed(spec):
                # Das Schloss wandert an den Thread, nicht an den Aufrufer - auf
                # diesem Pfad wird es deshalb hier wieder freigegeben, sonst
                # waere das Modell nach dem ersten No--Klick dauerhaft gesperrt.
                lock.release()
                return self.status(spec)
            if self._shutdown.is_set():
                raise ModelDownloadError("Der Dienst wird heruntergefahren; der Download wurde nicht gestartet.")
            directory = self._prepare_directory(spec)
            self._assert_enough_space(spec, directory)
            cancel = threading.Event()
            already = self._downloaded_bytes(spec)
            progress = _Progress(
                status="downloading",
                bytes_done=already,
                bytes_at_start=already,
                started_at=utc_now(),
                message="Der Download läuft.",
            )
            thread = threading.Thread(
                target=self._run,
                args=(spec, directory, cancel, progress, lock),
                name=f"model-download-{spec.id}",
                daemon=True,
            )
            with self._guard:
                self._cancels[spec.id] = cancel
                self._progress[spec.id] = progress
                self._threads[spec.id] = thread
            thread.start()
        except BaseException:
            # Auch hier wird aufgeräumt: ein stehen gebliebener Eintrag in
            # `_cancels` wuerde ein spaeteres `cancel` als Erfolg melden, obwohl
            # gar nichts laeuft, und `status` bliebe dauerhaft auf "laeuft".
            with self._guard:
                self._cancels.pop(spec.id, None)
                self._progress.pop(spec.id, None)
                self._threads.pop(spec.id, None)
            lock.release()
            raise
        return self.status(spec)

    def cancel(self, model_id: str) -> bool:
        """Bittet einen laufenden Download, nach dem aktuellen Block zu enden.

        Gibt ``False`` zurück, wenn gerade nichts läuft - das ist kein Fehler,
        sondern die Antwort auf einen Klick, der zu spät kam.
        """
        with self._guard:
            cancel = self._cancels.get(model_id)
        if cancel is None:
            return False
        cancel.set()
        return True

    def remove(self, spec: ModelSpec) -> bool:
        """Löscht die Ablage eines Modells. ``False``, wenn nichts da war.

        Während ein Download läuft, wird nicht gelöscht: der Thread schriebe in
        genau das Verzeichnis, das gerade verschwindet. Der Aufrufer bekommt
        :class:`ModelDownloadBusy` und antwortet 409.
        """
        lock = self._lock_for(spec.id)
        if not lock.acquire(blocking=False):
            raise ModelDownloadBusy(
                f"Für {spec.label} läuft gerade ein Download. Abbruch zuerst, dann löschen."
            )
        try:
            directory = self.model_directory(spec)
            if not directory.exists():
                return False
            try:
                shutil.rmtree(directory)
            except OSError as error:
                raise ModelStorageError(
                    f"Die Ablage von {spec.label} liess sich nicht löschen: "
                    f"{error.strerror or type(error).__name__}."
                ) from error
            with self._guard:
                self._progress.pop(spec.id, None)
                self._cancels.pop(spec.id, None)
            logger.info("Modell %s aus der Ablage entfernt.", spec.id)
            return True
        finally:
            lock.release()

    def wait_until_idle(self, model_id: str, timeout_seconds: float) -> bool:
        """Wartet auf das Ende eines Download-Threads. ``False`` bei Timeout.

        Genutzt vom Herunterfahren; der Test wartet damit, statt zu schlafen.
        """
        with self._guard:
            thread = self._threads.get(model_id)
        if thread is None:
            return True
        thread.join(timeout=timeout_seconds)
        return not thread.is_alive()

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        """Beendet laufende Downloads und lässt die Teilstände liegen.

        Der Thread prüft dasselbe ``Event`` wie ein Abbruch über die API, deshalb
        endet er sauber an einer Blockgrenze und nicht mitten in einer Datei.
        """
        self._shutdown.set()
        with self._guard:
            cancels = list(self._cancels.values())
            model_ids = list(self._threads)
        for cancel in cancels:
            cancel.set()
        for model_id in model_ids:
            self.wait_until_idle(model_id, timeout_seconds)

    # --------------------------------------------------------------- Thread

    def _run(
        self,
        spec: ModelSpec,
        directory: Path,
        cancel: threading.Event,
        progress: _Progress,
        lock: threading.Lock,
    ) -> None:
        """Der Thread. Fängt alles, denn ein Thread, der stirbt, blockiert das Modell."""
        try:
            with self._client_factory() as client:
                for item in spec.files:
                    if cancel.is_set() or self._shutdown.is_set():
                        raise ModelDownloadCancelled()
                    progress.current_file = item.name
                    self._download_file(client, spec, item, directory, cancel, progress)
            self._write_manifest(spec, directory)
            progress.status = "installed"
            progress.current_file = None
            progress.message = "Das Modell ist geladen und geprüft."
            logger.info("Modell %s vollständig geladen (%d Byte).", spec.id, spec.size_bytes)
        except ModelDownloadCancelled:
            progress.status = "interrupted"
            progress.message = (
                "Der Download wurde abgebrochen. Der Teilstand bleibt erhalten und wird beim nächsten "
                "Start fortgesetzt."
            )
            logger.info("Download von %s abgebrochen.", spec.id)
        except (ModelDownloadError, OSError) as error:
            progress.status = "failed"
            progress.message = str(error)
            logger.warning("Download von %s fehlgeschlagen: %s", spec.id, error)
        finally:
            progress.finished_at = utc_now()
            with self._guard:
                self._cancels.pop(spec.id, None)
                self._threads.pop(spec.id, None)
            lock.release()

    def _download_file(
        self,
        client: httpx.Client,
        spec: ModelSpec,
        item: ModelFile,
        directory: Path,
        cancel: threading.Event,
        progress: _Progress,
    ) -> None:
        """Lädt **eine** Datei: fortsetzen, prüfen, atomar umbenennen."""
        target = directory / item.name
        part = directory / f"{item.name}{PART_SUFFIX}"
        offset = self._resume_offset(item, part)
        written, checksum = self._stream_file(
            client=client,
            item=item,
            part=part,
            cancel=cancel,
            progress=progress,
            base=self._completed_bytes(spec, directory),
            offset=offset,
        )
        if item.size_bytes and written != item.size_bytes:
            raise ModelDownloadError(
                f"{item.name} ist unvollständig: {written} statt {item.size_bytes} Byte. "
                "Der nächste Versuch setzt beim Teilstand fort."
            )
        if checksum != item.sha256:
            # Ein vergifteter Präfix darf nicht weitergeführt werden: Sonst
            # scheitert der Download bei jedem Versuch an derselben Stelle.
            part.unlink(missing_ok=True)
            raise ModelDownloadError(
                f"Die Prüfsumme von {item.name} stimmt nicht (erwartet {item.sha256[:12]}..., "
                f"berechnet {checksum[:12]}...). Die Datei wurde verworfen."
            )
        # Erst hier, nach der Prüfung: vorher wäre ein halbes Modell unter dem
        # endgültigen Namen ein Zustand, aus dem ein Start ein kaputtes lädt.
        try:
            part.replace(target)
        except OSError as error:
            raise ModelStorageError(
                f"{item.name} liess sich nicht umbenennen: {error.strerror or type(error).__name__}."
            ) from error

    def _stream_file(
        self,
        *,
        client: httpx.Client,
        item: ModelFile,
        part: Path,
        cancel: threading.Event,
        progress: _Progress,
        base: int,
        offset: int,
    ) -> tuple[int, str]:
        """Holt den Inhalt und liefert ``(Bytes auf der Platte, SHA256)``.

        Die Schleife ist das Kernstück: Sie erkennt die drei Antworten, mit denen
        ein Server ein Resume kaputtmachen kann, und fängt jeden Fall durch einen
        Neustart von null ab, statt die Teildatei zu beschädigen.
        """
        digest: _Digest = hashlib.sha256()
        if offset:
            _hash_prefix(part, offset, digest)

        for _attempt in range(3):
            headers = {"Accept-Encoding": "identity", "User-Agent": USER_AGENT}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            response = self._send(client, item.url, headers)
            status_code = response.status_code

            if status_code == 416:
                # Der Bereich liegt hinter dem Ende: der lokale Stand ist zu
                # groß oder die Quelle ist eine andere geworden.
                response.close()
                offset = 0
                digest = hashlib.sha256()
                part.unlink(missing_ok=True)
                continue
            if status_code == 206:
                start = _content_range_start(response)
                if offset and start is not None and start != offset:
                    # Der Server hat ab einer anderen Stelle geliefert. Anhängen
                    # würde die Datei zerreißen, also wird verworfen.
                    response.close()
                    offset = 0
                    digest = hashlib.sha256()
                    part.unlink(missing_ok=True)
                    continue
            elif status_code == 200 and offset:
                # Range ignoriert: die Antwort ist die **ganze** Datei. Sie darf
                # nicht hinten an einen vorhandenen Präfig gehängt werden.
                response.close()
                offset = 0
                digest = hashlib.sha256()
                part.unlink(missing_ok=True)
                continue
            elif status_code >= 400:
                response.close()
                raise ModelDownloadError(f"Die Modellquelle lehnte {item.name} mit HTTP {status_code} ab.")

            written = offset
            try:
                # Kein `with response`: `httpx.Response` ist erst dann ein
                # Kontextmanager, wenn er ueber `client.stream` geoeffnet wurde.
                # Hier wird er von Hand mit `stream=True` angefordert, also wird
                # er auch von Hand geschlossen - in `finally`, damit ein
                # Abbruch keine Verbindung offen laesst.
                try:
                    handle = part.open("ab" if offset else "wb")
                    with handle:
                        for block in response.iter_bytes(CHUNK_SIZE):
                            if cancel.is_set() or self._shutdown.is_set():
                                raise ModelDownloadCancelled()
                            handle.write(block)
                            digest.update(block)
                            written += len(block)
                            progress.bytes_done = base + written
                finally:
                    response.close()
            except ModelDownloadCancelled:
                raise
            except httpx.HTTPError as error:
                raise ModelDownloadError(
                    f"Die Verbindung zu {item.name} wurde unterbrochen ({type(error).__name__}). "
                    "Der Teilstand bleibt erhalten; der nächste Versuch setzt fort."
                ) from error
            except OSError as error:
                raise ModelStorageError(
                    f"{item.name} liess sich nicht schreiben: {error.strerror or type(error).__name__}."
                ) from error
            return written, digest.hexdigest()

        raise ModelDownloadError(
            f"Der Download von {item.name} musste mehrfach neu beginnen. Die Quelle liefert "
            "keinen brauchbaren Bereichsabruf."
        )

    def _send(self, client: httpx.Client, url: str, headers: dict[str, str]) -> httpx.Response:
        """GET mit von Hand verfolgten Weiterleitungen.

        Jedes Ziel - auch der erste und jedes ``Location`` - läuft durch
        :func:`validate_download_url`. Das ist die eigentliche SSRF-Sperre: Nicht
        die Anfrage entscheidet, wohin der Server darf umleiten, sondern die
        Allowlist im Katalog. ``follow_redirects`` bleibt aus, damit ein Sprung
        nicht unbemerkt an der Prüfung vorbeikommt.
        """
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            try:
                validate_download_url(current)
            except ModelCatalogError as error:
                raise ModelDownloadError(str(error)) from error
            request = client.build_request("GET", current, headers=headers)
            try:
                response = client.send(request, stream=True)
            except httpx.HTTPError as error:
                raise ModelDownloadError(
                    f"Die Modellquelle ist nicht erreichbar ({type(error).__name__})."
                ) from error
            if response.is_redirect:
                location = response.headers.get("location", "")
                response.close()
                if not location:
                    raise ModelDownloadError("Die Modellquelle hat ohne Ziel umgeleitet.")
                current = str(response.url.join(location))
                continue
            return response
        raise ModelDownloadError(
            f"Die Modellquelle hat mehr als {MAX_REDIRECTS} Mal umgeleitet; der Download wird abgebrochen."
        )

    # ------------------------------------------------------------ Hilfsmittel

    def _prepare_directory(self, spec: ModelSpec) -> Path:
        directory = self.model_directory(spec)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ModelStorageError(
                f"Die Modellablage liess sich nicht anlegen: {error.strerror or type(error).__name__}. "
                "Im Container muss der Modellpfad read/write eingebunden sein."
            ) from error
        return directory

    def _assert_enough_space(self, spec: ModelSpec, directory: Path) -> None:
        """Bricht vor dem ersten Byte ab, wenn der Platz nicht reicht."""
        missing = self._missing_bytes(spec)
        if missing <= 0:
            return
        free = _free_bytes(directory)
        if free is None or free - missing >= self._min_free_bytes:
            return
        raise ModelSpaceError(
            f"Zu wenig Speicherplatz: Für {spec.label} fehlen {missing} Byte, es sind aber nur "
            f"{free or 0} Byte frei. Es sollen {self._min_free_bytes} Byte Reserve bleiben."
        )

    def _missing_bytes(self, spec: ModelSpec) -> int:
        directory = self.model_directory(spec)
        return max(0, spec.size_bytes - self._downloaded_bytes(spec, directory))

    def _resume_offset(self, item: ModelFile, part: Path) -> int:
        """Der Fortsetzungspunkt - aus der Teildatei, nirgends gespeichert."""
        try:
            size = part.stat().st_size
        except OSError:
            return 0
        if size <= 0:
            return 0
        if item.size_bytes and size > item.size_bytes:
            # Zu groß: entweder eine andere Quelle oder ein verrutschter
            # Präfix. Beides ist ein Grund, die Datei zu verwerfen.
            part.unlink(missing_ok=True)
            return 0
        return size

    def _file_size(self, path: Path) -> int | None:
        try:
            return path.stat().st_size if path.is_file() else None
        except OSError:
            return None

    def _completed_bytes(self, spec: ModelSpec, directory: Path) -> int:
        """Bytes der Dateien, die den Endnamen schon tragen."""
        total = 0
        for item in spec.files:
            size = self._file_size(directory / item.name)
            if size is not None and size == item.size_bytes:
                total += size
        return total

    def _partial_bytes(self, spec: ModelSpec, directory: Path) -> int:
        total = 0
        for item in spec.files:
            size = self._file_size(directory / f"{item.name}{PART_SUFFIX}")
            if size:
                total += size
        return total

    def _downloaded_bytes(self, spec: ModelSpec, directory: Path | None = None) -> int:
        target = directory if directory is not None else self.model_directory(spec)
        return self._completed_bytes(spec, target) + self._partial_bytes(spec, target)

    def _read_manifest(self, directory: Path) -> dict[str, object] | None:
        try:
            raw = (directory / MANIFEST_NAME).read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _is_installed(self, spec: ModelSpec) -> bool:
        """Installiert heißt: Manifest vorhanden **und** jede Datei vollständig."""
        directory = self.model_directory(spec)
        manifest = self._read_manifest(directory)
        if manifest is None or manifest.get("model_id") != spec.id:
            return False
        recorded = manifest.get("files")
        if not isinstance(recorded, list):
            return False
        names = {str(entry.get("name")) for entry in recorded if isinstance(entry, dict)}
        if names != {item.name for item in spec.files}:
            return False
        return all(self._file_size(directory / item.name) == item.size_bytes for item in spec.files)

    def _write_manifest(self, spec: ModelSpec, directory: Path) -> None:
        """Schreibt den Nachweis der Prüfung - wieder tmp-Datei, dann umbenannt."""
        payload = {
            "model_id": spec.id,
            "verified_at": utc_now().isoformat(),
            "files": [
                {"name": item.name, "sha256": item.sha256, "size_bytes": item.size_bytes}
                for item in spec.files
            ],
        }
        target = directory / MANIFEST_NAME
        staging = directory / f"{MANIFEST_NAME}{PART_SUFFIX}"
        try:
            staging.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            staging.replace(target)
        except OSError as error:
            staging.unlink(missing_ok=True)
            raise ModelStorageError(
                f"Der Nachweis für {spec.label} liess sich nicht schreiben: "
                f"{error.strerror or type(error).__name__}."
            ) from error

    def _lock_for(self, model_id: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(model_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[model_id] = lock
            return lock

    def _progress_for(self, model_id: str) -> _Progress:
        with self._guard:
            progress = self._progress.get(model_id)
            if progress is None:
                progress = _Progress()
                self._progress[model_id] = progress
            return progress

    def _is_running(self, model_id: str) -> bool:
        with self._guard:
            cancel = self._cancels.get(model_id)
        return cancel is not None and not cancel.is_set()

    def _speed(self, progress: _Progress, downloaded: int) -> float | None:
        if progress.started_at is None:
            return None
        elapsed = (utc_now() - progress.started_at).total_seconds()
        if elapsed <= 0:
            return None
        return round(max(0, downloaded - progress.bytes_at_start) / elapsed, 1)
