"""``/api/v1/models``: der Katalog und die Steuerung des Download-Managers.

Vier Routen, mehr nicht:

* ``GET    /api/v1/models``                  Katalog mit Größe, RAM-Schätzung und Stand
* ``GET    /api/v1/models/{model_id}``       ein Modell mit demselben Stand
* ``POST   /api/v1/models/{model_id}/download``  starten oder fortsetzen
* ``POST   /api/v1/models/{model_id}/cancel``    abbrechen
* ``DELETE /api/v1/models/{model_id}``       die Ablage löschen

Der Pfad nimmt **keine URL** entgegen, nur eine Katalog-Id. Das ist die
Entscheidung gegen SSRF: Wer eine Adresse einspeisen dürfte, müsste Schema,
Ziel und jede Weiterleitung selbst prüfen - und eine Prüflogik im Anfragepfad
ist schlechter zu überblicken als eine Allowlist neben dem Katalog, aus dem die
Adresse ohnehin stammt.

Der Download läuft in einem Thread im Backend. Der Endpunkt startet ihn und
antwortet mit dem Stand von eben; wer danach fragt, liest ``status``. Ein
bereits geladenes Modell startet nichts, und der zweite Klick auf denselben
Knopf ist damit ein No-Op statt eines zweiten Threads.
"""

from typing import cast

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, ModelDownloads
from app.schemas.common import MessageResponse
from app.schemas.local_models import (
    ModelCatalogRead,
    ModelFileRead,
    ModelRead,
    ModelStatus,
    ModelStatusRead,
    ModelStorageRead,
    ModelTask,
)
from app.services.model_catalog import ModelCatalogError, ModelSpec, catalog_models, model_spec, ram_estimate
from app.services.model_downloads import (
    ModelDownloadBusy,
    ModelDownloadError,
    ModelDownloadManager,
    ModelInstallState,
    ModelSpaceError,
    ModelStorageError,
)

router = APIRouter(prefix="/api/v1/models", tags=["models"])


def _spec_or_404(model_id: str) -> ModelSpec:
    """Unbekannte Id ist 404 mit deutschem Klartext.

    Es gibt hier nichts zu verbergen: Der Katalog ist ohnehin öffentlich in der
    Anwendung sichtbar. Der Text ist deutsch, weil er so in der Oberfläche
    landet.
    """
    try:
        return model_spec(model_id)
    except ModelCatalogError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _status_read(state: ModelInstallState) -> ModelStatusRead:
    return ModelStatusRead(
        status=cast(ModelStatus, state.status),
        bytes_downloaded=state.bytes_downloaded,
        bytes_total=state.bytes_total,
        progress_percent=state.progress_percent,
        speed_bytes_per_second=state.speed_bytes_per_second,
        current_file=state.current_file,
        verified=state.verified,
        message=state.message,
        started_at=state.started_at,
        finished_at=state.finished_at,
    )


def _model_read(spec: ModelSpec, state: ModelInstallState) -> ModelRead:
    """Katalogangaben, abgeleitete Zahlen und der Stand in einem Objekt."""
    estimate = ram_estimate(spec)
    return ModelRead(
        id=spec.id,
        label=spec.label,
        task=cast(ModelTask, spec.task),
        family=spec.family,
        description=spec.description,
        license=spec.license,
        source_repository=spec.source_repository,
        source_page=spec.source_page,
        parameter_count=spec.parameter_count,
        quantization=spec.quantization,
        context_length=spec.context_length,
        size_bytes=spec.size_bytes,
        size_is_estimate=False,
        ram_estimate_bytes=estimate.ram_bytes,
        ram_estimate_gib=estimate.gib,
        ram_is_estimate=estimate.is_estimate,
        ram_basis=estimate.derived_from,
        files=[
            ModelFileRead(name=item.name, size_bytes=item.size_bytes, sha256=item.sha256)
            for item in spec.files
        ],
        status=_status_read(state),
    )


def _read(manager: ModelDownloadManager, spec: ModelSpec) -> ModelRead:
    return _model_read(spec, manager.status(spec))


def _storage_read(manager: ModelDownloadManager) -> ModelStorageRead:
    state = manager.storage_state()
    return ModelStorageRead(
        available=bool(state["available"]),
        writable=bool(state["writable"]),
        free_bytes=state["free_bytes"] if isinstance(state["free_bytes"], int) else None,
        message=str(state["message"]),
    )


def _raise_for_download_error(error: ModelDownloadError) -> None:
    """Übersetzt einen Manager-Fehler in eine HTTP-Antwort.

    Die Reihenfolge der Prüfungen folgt der Vererbung: ``ModelSpaceError`` ist
    eine ``ModelStorageError``, deshalb muss sie zuerst drankommen. Geteilt wird
    nach Ausnahmeklassen und nicht nach Formulierungen im Text - eine Meldung,
    nach der unterschieden wird, ist die schlechteste Stelle dafür.
    """
    if isinstance(error, ModelDownloadBusy):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if isinstance(error, ModelSpaceError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if isinstance(error, ModelStorageError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("", response_model=ModelCatalogRead)
def list_models(_user: CurrentUser, downloads: ModelDownloads) -> ModelCatalogRead:
    """Der ganze Katalog mit dem Stand jedes Eintrags und dem Zustand der Ablage."""
    return ModelCatalogRead(
        models=[_read(downloads, spec) for spec in catalog_models()],
        storage=_storage_read(downloads),
    )


@router.get("/{model_id}", response_model=ModelRead)
def get_model(model_id: str, _user: CurrentUser, downloads: ModelDownloads) -> ModelRead:
    """Ein einzelnes Modell. Pollt man während eines Downloads."""
    spec = _spec_or_404(model_id)
    return _read(downloads, spec)


@router.post("/{model_id}/download", response_model=ModelRead)
def start_download(model_id: str, _user: CurrentUser, downloads: ModelDownloads) -> ModelRead:
    """Startet den Download im Hintergrund oder setzt einen Teilstand fort.

    Die Antwort ist immer 200 und **der Stand unmittelbar nach dem Anstoß** -
    nicht das Ergebnis. Das liegt an der Natur der Sache: der Download läuft
    Sekunden bis Stunden, und ein 202 würde hier nur eine Antwort mit weniger
    Information sein. Wer das Ergebnis will, fragt ``GET /api/v1/models/{id}``
    erneut; ``status.status`` wechselt dann von ``downloading`` auf
    ``installed``.

    Ein zweiter Versuch während eines laufenden Downloads bekommt 409 - gesperrt
    ist **pro Modell**, nicht pro Nutzer, weil zwei Tabs auf denselben Knopf der
    Normalfall sind. Ein bereits vollständig geladenes Modell startet nichts.
    """
    spec = _spec_or_404(model_id)
    try:
        state = downloads.start(spec)
    except ModelDownloadError as exc:
        _raise_for_download_error(exc)
        raise  # pragma: no cover - _raise_for_download_error wirft immer
    return _model_read(spec, state)


@router.post("/{model_id}/cancel", response_model=ModelRead)
def cancel_download(model_id: str, _user: CurrentUser, downloads: ModelDownloads) -> ModelRead:
    """Bricht einen laufenden Download ab.

    Der Teilstand bleibt liegen und der nächste Start setzt dort fort; ein
    Abbruch meldet nie ``installed``. War gerade nichts in Arbeit, ist das kein
    Fehler, sondern 200 mit dem unveränderten Stand.
    """
    spec = _spec_or_404(model_id)
    downloads.cancel(model_id)
    return _read(downloads, spec)


@router.delete("/{model_id}", response_model=MessageResponse)
def delete_model(model_id: str, _user: CurrentUser, downloads: ModelDownloads) -> MessageResponse:
    """Entfernt die Ablage eines Modells samt Manifest und Teilstand.

    Idempotent: Gab es nichts zu löschen, ist das 200 mit einem deutschen Satz
    und kein 404 - der Katalogeintrag bleibt bestehen, nur die Dateien sind weg.
    """
    spec = _spec_or_404(model_id)
    try:
        removed = downloads.remove(spec)
    except ModelDownloadError as exc:
        _raise_for_download_error(exc)
        raise  # pragma: no cover - _raise_for_download_error wirft immer
    message = (
        f"{spec.label} wurde aus der Ablage entfernt."
        if removed
        else f"Für {spec.label} lag nichts in der Ablage."
    )
    return MessageResponse(message=message)
