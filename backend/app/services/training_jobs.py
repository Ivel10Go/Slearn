"""Trainingsrunden: Auftrag anlegen, Trainer starten, anhalten, Ergebnis uebernehmen.

Der Trainer (``trainer/lernraum_trainer.py``) ist ein eigener Prozess mit
eigenem Python (torch/CUDA). Dieses Modul spricht mit ihm nur ueber Dateien:

    <MODEL_STORAGE_PATH>/training/<user_id>/run-<zeit>/
        job.json         Auftrag, hier geschrieben
        data/            Datensatz (training_data.write_dataset)
        progress.json    Stand, vom Trainer geschrieben
        STOP             hier angelegt = bitte anhalten
        registered.json  hier geschrieben, sobald der Adapter uebernommen ist

* **Der Zustand steht im Ordner, nicht in der Datenbank.** Der Trainer
  schreibt ``progress.json`` ohnehin; eine Tabelle daneben waere ein zweiter
  Stand, der auseinanderlaufen kann.
* **Eine offene Runde wird fortgesetzt, nicht neu angelegt.** Ihr Datensatz
  ist eingefroren; neue Beispiele kommen in die naechste Runde.
* **Ein ``running`` ohne Prozess ist ``paused``.** Nach einem Absturz oder
  Neustart setzt der naechste Start beim letzten Checkpoint fort.
* **Uebernommen wird nur, was nicht schlechter ist** - in zwei Massen: der
  Verlust auf der Pruefmenge und, strenger, der Anteil der Pruefantworten, in
  denen Art und Datum der ``AKTION`` stimmen (der Trainer laesst das Modell
  dafuer wirklich antworten, mit und ohne Adapter). Faellt eines schlechter
  aus, wird die Version abgelegt, aber nicht aktiviert.
* **Automatisch nur mit eigener Freigabe und nur, wenn die Desktop-App es
  erlaubt** (:meth:`TrainingRunner.on_conditions`: am Netz, Rechner ruht).
  Faellt die Erlaubnis weg, haelt der Trainer am naechsten Schrittende an.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services import adapter_store, training_data
from app.services import training as training_service
from app.services.local_runtimes import LocalRuntimeManager
from app.services.model_catalog import ModelCatalogError, model_spec
from app.services.trainer_install import TrainerSetup
from app.services.training_hardware import BACKEND_CUDA, TrainingHardware, detect_training_hardware

logger = logging.getLogger("school_assistant")


@dataclass(frozen=True, slots=True)
class TrainableBase:
    """Die unquantisierten Gewichte zu einem GGUF aus dem Katalog - darauf wird trainiert."""

    hf_repo: str
    #: Fester Commit, wie die festen Versionen der Laufzeiten.
    revision: str
    architecture: str


#: Llama 3.2 fehlt mit Absicht: die Gewichte verlangen eine Anmeldung bei Hugging Face.
TRAINABLE_BASES: Final[dict[str, TrainableBase]] = {
    "qwen2.5-3b-instruct-q4km": TrainableBase(
        "Qwen/Qwen2.5-3B-Instruct", "aa8e72537993ba99e69dfaafa59ed015b17504d1", "qwen2"
    ),
    "qwen2.5-1.5b-instruct-q4km": TrainableBase(
        "Qwen/Qwen2.5-1.5B-Instruct", "989aa7980e4cf806f80c7fef2b1adb7bc71aa306", "qwen2"
    ),
}

#: Weniger bewertete Beispiele lohnen keine Runde.
MIN_TRAIN_EXAMPLES: Final[int] = 8
#: Automatisch beginnt eine neue Runde erst mit so vielen neuen bewerteten Beispielen.
MIN_NEW_EXAMPLES: Final[int] = 20
#: ... oder mit so vielen neuen Absaetzen aus Aufschrieben und Arbeitsblaettern.
MIN_NEW_MATERIAL_CHUNKS: Final[int] = 10
#: So lange darf der Trainer beim Beenden noch einen Checkpoint schreiben.
SHUTDOWN_WAIT_SECONDS: Final[float] = 60.0

DEFAULT_TRAINER_DIR: Final[Path] = Path(__file__).resolve().parents[3] / "trainer"
TRAINER_SCRIPT: Final[str] = "lernraum_trainer.py"
RUN_PREFIX: Final[str] = "run-"
JOB_FILE: Final[str] = "job.json"
PROGRESS_FILE: Final[str] = "progress.json"
STOP_FILE: Final[str] = "STOP"
REGISTERED_FILE: Final[str] = "registered.json"

STATUS_PENDING: Final[str] = "pending"
STATUS_PAUSED: Final[str] = "paused"
STATUS_DONE: Final[str] = "done"
STATUS_FAILED: Final[str] = "failed"
#: Der Trainer schreibt diese drei nur, solange er lebt.
_LIVE_STATUSES: Final[frozenset[str]] = frozenset({"loading", "generating", "running", "converting"})


class TrainingUnavailableError(RuntimeError):
    """Training geht hier grundsaetzlich nicht (Hardware, Trainer fehlt). Deutsche Meldung."""


class TrainingJobError(ValueError):
    """Diese Runde geht gerade nicht (zu wenige Beispiele, kein Modell). Deutsche Meldung."""


@dataclass(frozen=True, slots=True)
class RunStatus:
    run_id: str
    base_model_id: str
    status: str
    active: bool
    step: int
    total_steps: int
    example_count: int
    loss: float | None
    eval_loss_base: float | None
    eval_loss: float | None
    eval_score_base: float | None
    eval_score: float | None
    #: Fragen zum Material: erzeugt / gesamt / behalten (belegt aus dem Auszug).
    qa_done: int
    qa_total: int
    qa_kept: int
    seconds_per_step: float | None
    skipped_too_long: int
    error: str | None
    adapter_version: int | None
    activated: bool
    created_at: str | None
    updated_at: str | None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _not_worse(value: float | None, base: float | None, *, higher_is_better: bool) -> bool:
    if value is None or base is None:
        return True
    return value >= base if higher_is_better else value <= base


def adapter_passes(
    eval_loss: float | None,
    eval_loss_base: float | None,
    eval_score: float | None = None,
    eval_score_base: float | None = None,
) -> bool:
    """Nicht schlechter als ohne Adapter - weder im Verlust noch bei Art und Datum.

    Fehlt ein Wert (keine Pruefbeispiele, keine mit Aktion), gibt es dort nichts
    zu vergleichen, und das Mass entscheidet nicht mit.
    """
    return _not_worse(eval_loss, eval_loss_base, higher_is_better=False) and _not_worse(
        eval_score, eval_score_base, higher_is_better=True
    )


class TrainingRunner:
    """Hoechstens ein Trainer gleichzeitig - die Grafikkarte gibt es einmal."""

    def __init__(
        self,
        settings: Settings,
        *,
        runtimes: LocalRuntimeManager | None = None,
        setup: TrainerSetup | None = None,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        hardware: Callable[[], TrainingHardware] = detect_training_hardware,
    ) -> None:
        self.storage = Path(settings.model_storage_path)
        #: Von Hand gesetztes Trainer-Python (``TRAINER_PYTHON``/``trainer.json``).
        self.python = settings.trainer_python.strip()
        #: Die von der App eingerichtete Umgebung (``trainer_install.py``).
        self.setup = setup
        self.trainer_dir = Path(settings.trainer_dir) if settings.trainer_dir.strip() else DEFAULT_TRAINER_DIR
        self.cache_dir = (
            Path(settings.trainer_cache_path) if settings.trainer_cache_path.strip() else self.storage / "trainer-cache"
        )
        self.runtimes = runtimes
        self.popen = popen
        self.hardware = hardware
        self._lock = threading.Lock()
        self._finalize_lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._run_dir: Path | None = None
        self._watcher: threading.Thread | None = None

    # ------------------------------------------------------------ Ordner

    def runs_root(self, user_id: int) -> Path:
        return self.storage / "training" / str(int(user_id))

    def _runs(self, user_id: int) -> list[Path]:
        root = self.runs_root(user_id)
        if not root.is_dir():
            return []
        return sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith(RUN_PREFIX))

    def latest_run(self, user_id: int) -> Path | None:
        runs = self._runs(user_id)
        return runs[-1] if runs else None

    def _alive(self, run_dir: Path | None = None) -> bool:
        process = self._process
        if process is None or process.poll() is not None:
            return False
        return run_dir is None or self._run_dir == run_dir

    def _open_run(self, user_id: int) -> Path | None:
        run = self.latest_run(user_id)
        if run is None or (run / REGISTERED_FILE).exists():
            return None
        return None if _read_json(run / PROGRESS_FILE).get("status") == STATUS_DONE else run

    # ------------------------------------------------------------ Stand

    def run_status(self, run_dir: Path) -> RunStatus:
        job = _read_json(run_dir / JOB_FILE)
        progress = _read_json(run_dir / PROGRESS_FILE)
        registered = _read_json(run_dir / REGISTERED_FILE)
        active = self._alive(run_dir)
        status = str(progress.get("status") or STATUS_PENDING)
        if not active and status in _LIVE_STATUSES:
            status = STATUS_PAUSED
        version = registered.get("version")
        error = progress.get("error")
        return RunStatus(
            run_id=run_dir.name,
            base_model_id=str(job.get("base_model", {}).get("id", "")),
            status=status,
            active=active,
            step=int(progress.get("step") or 0),
            total_steps=int(progress.get("total_steps") or 0),
            example_count=int(job.get("dataset", {}).get("example_count") or 0),
            loss=_float(progress.get("loss")),
            eval_loss_base=_float(progress.get("eval_loss_base")),
            eval_loss=_float(progress.get("eval_loss")),
            eval_score_base=_float(progress.get("eval_score_base")),
            eval_score=_float(progress.get("eval_score")),
            qa_done=int(progress.get("qa_done") or 0),
            qa_total=int(progress.get("qa_total") or 0),
            qa_kept=int(progress.get("qa_kept") or 0),
            seconds_per_step=_float(progress.get("seconds_per_step")),
            skipped_too_long=int(progress.get("skipped_too_long") or 0),
            error=error if isinstance(error, str) else None,
            adapter_version=version if isinstance(version, int) else None,
            activated=bool(registered.get("activated")),
            created_at=job.get("created_at"),
            updated_at=progress.get("updated_at"),
        )

    def status(self, user_id: int) -> RunStatus | None:
        run = self.latest_run(user_id)
        if run is None:
            return None
        if not self._alive(run):
            self.finalize(run)
        return self.run_status(run)

    # ------------------------------------------------------------ Starten

    def trainer_python(self) -> str:
        """Von Hand gesetzt schlaegt eingerichtet: wer selbst ein venv pflegt, behaelt es."""
        if self.python:
            return self.python
        installed = self.setup.python_path() if self.setup is not None else None
        return str(installed) if installed is not None else ""

    def is_busy(self) -> bool:
        """Laeuft gerade ein Trainer? Dann darf seine Umgebung nicht verschwinden."""
        return self._alive()

    def check_available(self) -> None:
        hardware = self.hardware()
        if not hardware.eligible:
            raise TrainingUnavailableError(hardware.reason)
        if hardware.backend != BACKEND_CUDA:
            raise TrainingUnavailableError("Training läuft vorerst nur auf NVIDIA-Grafikkarten.")
        python = self.trainer_python()
        if not python or not Path(python).is_file():
            raise TrainingUnavailableError(
                "Der Trainer ist noch nicht eingerichtet. Auf „KI & Modelle“ gibt es dafür „Trainer einrichten“."
            )
        if not (self.trainer_dir / TRAINER_SCRIPT).is_file():
            raise TrainingUnavailableError(f"{TRAINER_SCRIPT} fehlt in {self.trainer_dir}.")

    def start(self, db: Session, user_id: int, zone: ZoneInfo, base_model_id: str | None = None) -> RunStatus:
        """Setzt die offene Runde fort oder legt eine neue an und startet den Trainer."""
        with self._lock:
            if self._alive():
                if self._run_dir is not None and self._run_dir.parent == self.runs_root(user_id):
                    return self.run_status(self._run_dir)
                raise TrainingJobError("Gerade trainiert schon jemand anderes auf diesem Rechner.")
            self.check_available()
            run = self._open_run(user_id) or self._prepare(db, user_id, zone, base_model_id)
            self._spawn(run)
            return self.run_status(run)

    def _base_model(self, base_model_id: str | None) -> str:
        if base_model_id is None and self.runtimes is not None:
            preferred = self.runtimes.preferred_text_model()
            base_model_id = preferred.id if preferred else None
        if base_model_id is None:
            raise TrainingJobError("Es ist kein lokales Textmodell geladen.")
        if base_model_id not in TRAINABLE_BASES:
            raise TrainingJobError(f"Für {base_model_id} gibt es noch kein Training.")
        return base_model_id

    def _prepare(
        self,
        db: Session,
        user_id: int,
        zone: ZoneInfo,
        base_model_id: str | None,
        dataset: training_data.TrainingDataset | None = None,
    ) -> Path:
        model_id = self._base_model(base_model_id)
        base = TRAINABLE_BASES[model_id]
        if dataset is None:
            dataset = self.load(db, user_id, zone)
        usable = len(dataset.train) + len(dataset.qa_tasks)
        if usable < MIN_TRAIN_EXAMPLES:
            raise TrainingJobError(
                f"Noch zu wenig zum Trainieren: {usable} von {MIN_TRAIN_EXAMPLES} "
                "(bewertete Antworten und Absätze aus freigegebenem Material)."
            )
        now = datetime.now(UTC)
        run = self.runs_root(user_id) / f"{RUN_PREFIX}{now:%Y%m%d-%H%M%S}"
        suffix = 1
        while run.exists():
            suffix += 1
            run = self.runs_root(user_id) / f"{RUN_PREFIX}{now:%Y%m%d-%H%M%S}-{suffix}"
        training_data.write_dataset(dataset, run / "data")
        _write_json(
            run / JOB_FILE,
            {
                "format": 1,
                "user_id": user_id,
                "created_at": now.isoformat(timespec="seconds"),
                "base_model": {
                    "id": model_id,
                    "hf_repo": base.hf_repo,
                    "revision": base.revision,
                    "architecture": base.architecture,
                    "sha256": model_spec(model_id).files[0].sha256,
                },
                "cache_dir": str(self.cache_dir),
                "dataset": {
                    "fingerprint": dataset.fingerprint,
                    "example_count": len(dataset.example_ids) + len(dataset.material_chunk_ids),
                },
                "hyper": {},
            },
        )
        _write_json(run / PROGRESS_FILE, {"status": STATUS_PENDING, "step": 0, "total_steps": 0})
        return run

    def _spawn(self, run: Path) -> None:
        (run / STOP_FILE).unlink(missing_ok=True)
        log = (run / "trainer.log").open("ab")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_TELEMETRY": "1"}
        try:
            process = self.popen(
                [self.trainer_python(), "-u", str(self.trainer_dir / TRAINER_SCRIPT), "--job", str(run)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(self.trainer_dir),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            log.close()
            raise TrainingUnavailableError(f"Der Trainer ließ sich nicht starten: {exc}") from exc
        if self.runtimes is not None:
            self.runtimes.attach_child(process)
        self._process, self._run_dir = process, run
        self._watcher = threading.Thread(
            target=self._watch, args=(process, run, log), name="trainer-watch", daemon=True
        )
        self._watcher.start()

    def _watch(self, process: subprocess.Popen[bytes], run: Path, log: Any) -> None:
        process.wait()
        log.close()
        try:
            self.finalize(run)
        except Exception:  # noqa: BLE001 - der Waechter darf nicht still sterben
            logger.exception("Trainingsergebnis ließ sich nicht übernehmen.")

    # ------------------------------------------------------------ Anhalten

    def pause(self, user_id: int) -> RunStatus | None:
        """Bittet den Trainer anzuhalten; er speichert und beendet sich nach dem laufenden Schritt."""
        run = self.latest_run(user_id)
        if run is not None and self._alive(run):
            (run / STOP_FILE).touch()
        return self.status(user_id)

    def discard(self, user_id: int) -> bool:
        """Verwirft die offene Runde samt Checkpoint; die naechste nimmt die neuen Beispiele."""
        with self._lock:
            run = self._open_run(user_id)
            if run is None:
                return False
            if self._alive(run):
                raise TrainingJobError("Das Training läuft noch - erst anhalten.")
            # Unter Windows haelt der Waechter trainer.log offen, bis er fertig ist.
            if self._watcher is not None:
                self._watcher.join(timeout=10)
            shutil.rmtree(run)
            return True

    def forget(self, user_id: int) -> None:
        """Beim Loeschen des Kontos: Trainer beenden, Runden und Adapter entfernen.

        Hart beendet statt ``STOP``: ein Checkpoint, der gleich geloescht wird,
        muss nicht erst gespeichert werden.
        """
        with self._lock:
            process, run = self._process, self._run_dir
            if process is not None and run is not None and run.parent == self.runs_root(user_id):
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=SHUTDOWN_WAIT_SECONDS)
                if self._watcher is not None:
                    self._watcher.join(timeout=10)
            adapters = self.storage / "adapters" / str(int(user_id))
            if self.runtimes is not None and adapters.is_dir():
                for root in adapters.iterdir():
                    self.runtimes.set_text_adapter(root.name, None)
            shutil.rmtree(self.runs_root(user_id), ignore_errors=True)
            shutil.rmtree(adapters, ignore_errors=True)

    # ------------------------------------------------------------ Automatik

    def load(self, db: Session, user_id: int, zone: ZoneInfo) -> training_data.TrainingDataset:
        """Der Datensatz - Material nur mit der eigenen Freigabe ``train_on_materials``."""
        materials = training_service.materials_training_enabled(db, user_id)
        return training_data.load_dataset(db, user_id, zone, materials=materials)

    def trained_ids(self, user_id: int, key: str = "example_ids") -> set[int]:
        """Alle Beispiele (oder Materialabsaetze), die schon in einer abgeschlossenen Runde steckten."""
        ids: set[int] = set()
        for run in self._runs(user_id):
            if (run / REGISTERED_FILE).exists():
                ids.update(_read_json(run / "data" / training_data.DATASET_FILE).get(key, []))
        return ids

    def round_due(self, user_id: int, dataset: training_data.TrainingDataset) -> bool:
        """Lohnt eine neue Runde? Beim ersten Mal ab ``MIN_TRAIN_EXAMPLES``, danach mit genug Neuem.

        Neu ist, was in keiner abgeschlossenen Runde steckte: 20 bewertete
        Antworten oder 10 Absaetze aus freigegebenem Material.
        """
        if len(dataset.train) + len(dataset.qa_tasks) < MIN_TRAIN_EXAMPLES:
            return False
        trained = self.trained_ids(user_id)
        trained_material = self.trained_ids(user_id, "material_chunk_ids")
        if not trained and not trained_material:
            return True
        new_examples = len(set(dataset.example_ids) - trained)
        new_material = len(set(dataset.material_chunk_ids) - trained_material)
        return new_examples >= MIN_NEW_EXAMPLES or new_material >= MIN_NEW_MATERIAL_CHUNKS

    def on_conditions(
        self, db: Session, user_id: int, zone: ZoneInfo, *, allowed: bool, auto: bool
    ) -> RunStatus | None:
        """Die Desktop-App meldet, ob gerade trainiert werden darf. Haelt an oder setzt fort.

        Nicht erlaubt: ein laufender Trainer haelt am naechsten Schrittende an -
        egal ob von Hand oder automatisch gestartet, der Akku leert sich fuer
        beide gleich. Erlaubt und ``auto``: eine offene Runde wird fortgesetzt,
        sonst beginnt eine neue, sobald genug neue Beispiele da sind. Eine
        gescheiterte Runde wird nicht automatisch wiederholt - sonst liefe
        derselbe Fehler bei jeder Meldung erneut.
        """
        with self._lock:
            if not allowed:
                if self._alive() and self._run_dir is not None:
                    (self._run_dir / STOP_FILE).touch()
            elif auto and not self._alive():
                try:
                    self.check_available()
                    run = self._open_run(user_id)
                    if run is None:
                        dataset = self.load(db, user_id, zone)
                        if self.round_due(user_id, dataset):
                            run = self._prepare(db, user_id, zone, None, dataset)
                    elif _read_json(run / PROGRESS_FILE).get("status") == STATUS_FAILED:
                        run = None
                    if run is not None:
                        self._spawn(run)
                except (TrainingUnavailableError, TrainingJobError):
                    pass
        return self.status(user_id)

    def shutdown(self) -> None:
        process, run = self._process, self._run_dir
        if process is None or run is None or process.poll() is not None:
            return
        (run / STOP_FILE).touch()
        try:
            process.wait(timeout=SHUTDOWN_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            # Noch beim Laden des Modells: dort gibt es nichts zu speichern.
            process.kill()

    # ------------------------------------------------------------ Uebernehmen

    def finalize(self, run: Path) -> None:
        """Legt den fertigen Adapter ab und aktiviert ihn, wenn er nicht schlechter ist. Idempotent."""
        with self._finalize_lock:
            if (run / REGISTERED_FILE).exists():
                return
            progress = _read_json(run / PROGRESS_FILE)
            out = run / "out"
            if progress.get("status") != STATUS_DONE or not (out / adapter_store.ADAPTER_FILE).is_file():
                return
            job = _read_json(run / JOB_FILE)
            base = job["base_model"]
            root = adapter_store.model_root(self.storage, int(job["user_id"]), base["id"])
            eval_loss, eval_loss_base = _float(progress.get("eval_loss")), _float(progress.get("eval_loss_base"))
            eval_score, eval_score_base = _float(progress.get("eval_score")), _float(progress.get("eval_score_base"))
            manifest = adapter_store.register_version(
                root,
                out,
                base_model_id=base["id"],
                base_sha256=base["sha256"],
                dataset_fingerprint=job["dataset"]["fingerprint"],
                example_count=int(job["dataset"]["example_count"]),
                eval_score=eval_score,
                created_at=datetime.now(UTC).isoformat(timespec="seconds"),
                eval_loss=eval_loss,
                eval_loss_base=eval_loss_base,
            )
            activated = adapter_passes(eval_loss, eval_loss_base, eval_score, eval_score_base)
            if activated:
                adapter_store.activate(root, manifest.version)
                self._apply(base["id"], root, base["sha256"])
            adapter_store.prune(root)
            _write_json(
                run / REGISTERED_FILE,
                {
                    "version": manifest.version,
                    "activated": activated,
                    "reason": "Auf den Prüfbeispielen nicht schlechter als ohne Adapter."
                    if activated
                    else "Auf den Prüfbeispielen schlechter als ohne Adapter - nicht aktiviert.",
                },
            )

    def _apply(self, model_id: str, root: Path, sha256: str) -> None:
        if self.runtimes is not None:
            self.runtimes.set_text_adapter(model_id, adapter_store.active_adapter(root, sha256))

    def adapter_root(self, user_id: int, base_model_id: str | None) -> tuple[str, Path]:
        model_id = self._base_model(base_model_id)
        return model_id, adapter_store.model_root(self.storage, user_id, model_id)

    def rollback(self, user_id: int, base_model_id: str | None) -> int | None:
        model_id, root = self.adapter_root(user_id, base_model_id)
        version = adapter_store.rollback(root)
        self._apply(model_id, root, model_spec(model_id).files[0].sha256)
        return version

    def restore_adapters(self) -> None:
        """Beim Start: aktive Adapter in die Laufzeit laden. Desktop = ein Nutzer je Modell."""
        if self.runtimes is None:
            return
        for user_dir in sorted((self.storage / "adapters").glob("*")):
            for root in sorted(user_dir.glob("*")):
                try:
                    sha256 = model_spec(root.name).files[0].sha256
                except ModelCatalogError:
                    continue
                path = adapter_store.active_adapter(root, sha256)
                if path is not None:
                    self.runtimes.set_text_adapter(root.name, path)
