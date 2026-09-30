from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.api.helpers import owned_or_404
from app.core.config import require_integration_encryption_key
from app.core.crypto import CredentialDecryptionError, decrypt_credentials, encrypt_credentials
from app.core.time import utc_now
from app.db.entities import SyncSource
from app.schemas.common import MessageResponse
from app.schemas.integrations import (
    IntegrationCreate,
    IntegrationRead,
    IntegrationSyncResult,
    IntegrationTestResult,
    IntegrationUpdate,
)
from app.services.integrations import (
    INTEGRATION_KINDS,
    INTEGRATION_SPECS,
    IntegrationConnectionError,
    IntegrationValidationError,
    check_integration_connection,
    integration_read,
    missing_fields,
    normalize_config,
    normalize_credentials,
    run_integration_sync,
)

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


def _encryption_key(settings: AppSettings) -> str:
    try:
        return require_integration_encryption_key(settings)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Integration encryption is not configured",
        ) from exc


def _read(source: SyncSource, settings: AppSettings) -> IntegrationRead:
    try:
        return integration_read(source, _encryption_key(settings))
    except CredentialDecryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stored integration credentials cannot be decrypted with the configured key",
        ) from exc


def _owned_integration(db: Session, integration_id: int, user_id: int) -> SyncSource:
    source = owned_or_404(db, SyncSource, integration_id, user_id)
    if source.source_type not in INTEGRATION_KINDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    return source


@router.get("", response_model=list[IntegrationRead])
def list_integrations(current_user: CurrentUser, db: DatabaseSession, settings: AppSettings) -> list[IntegrationRead]:
    sources = db.scalars(
        select(SyncSource)
        .where(SyncSource.user_id == current_user.id, SyncSource.source_type.in_(INTEGRATION_KINDS))
        .order_by(SyncSource.source_type, SyncSource.name, SyncSource.id)
    ).all()
    return [_read(source, settings) for source in sources]


@router.get("/catalog", response_model=list[dict[str, object]])
def integration_catalog(settings: AppSettings) -> list[dict[str, object]]:
    return [
        {
            "kind": spec.kind,
            "label": spec.label,
            "description": spec.description,
            "required_config": list(spec.required_config),
            "required_credentials": list(spec.required_credentials),
            "optional_credentials": list(spec.optional_credentials),
        }
        for spec in INTEGRATION_SPECS.values()
    ]


@router.post("", response_model=IntegrationRead, status_code=status.HTTP_201_CREATED)
def create_integration(
    payload: IntegrationCreate,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> IntegrationRead:
    key = _encryption_key(settings)
    try:
        config = normalize_config(payload.kind, payload.config, settings.document_storage_path)
        credentials = normalize_credentials(payload.kind, payload.credentials)
    except IntegrationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    gaps = missing_fields(payload.kind, config, sorted(credentials))
    if gaps:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required values: {', '.join(gaps)}",
        )

    source = SyncSource(
        user_id=current_user.id,
        source_type=payload.kind,
        name=payload.name,
        enabled=payload.enabled,
        configuration=config,
        credentials=encrypt_credentials(credentials, key) if credentials else None,
        status="configured",
    )
    db.add(source)
    try:
        db.commit()
        db.refresh(source)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An integration with this name already exists",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    return _read(source, settings)


@router.get("/{integration_id}", response_model=IntegrationRead)
def get_integration(
    integration_id: int, current_user: CurrentUser, db: DatabaseSession, settings: AppSettings
) -> IntegrationRead:
    return _read(_owned_integration(db, integration_id, current_user.id), settings)


@router.patch("/{integration_id}", response_model=IntegrationRead)
def update_integration(
    integration_id: int,
    payload: IntegrationUpdate,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> IntegrationRead:
    key = _encryption_key(settings)
    source = _owned_integration(db, integration_id, current_user.id)
    values = payload.model_dump(exclude_unset=True)

    if "name" in values and values["name"] is not None:
        source.name = values["name"]

    if "config" in values and values["config"] is not None:
        merged = {**{str(k): str(v) for k, v in (source.configuration or {}).items()}, **values["config"]}
        try:
            source.configuration = normalize_config(source.source_type, merged, settings.document_storage_path)
        except IntegrationValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if "credentials" in values and values["credentials"] is not None:
        try:
            credentials = normalize_credentials(source.source_type, values["credentials"])
        except IntegrationValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        if credentials:
            source.credentials = encrypt_credentials(credentials, key)
        else:
            source.credentials = None
        source.status = "configured"
        source.last_error = None

    if "enabled" in values and values["enabled"] is not None:
        source.enabled = values["enabled"]

    try:
        db.commit()
        db.refresh(source)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An integration with this name already exists",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    return _read(source, settings)


@router.delete("/{integration_id}", response_model=MessageResponse)
def delete_integration(integration_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    source = _owned_integration(db, integration_id, current_user.id)
    db.delete(source)
    db.commit()
    return MessageResponse(message="Integration disconnected")


@router.post("/{integration_id}/test", response_model=IntegrationTestResult)
def test_integration(
    integration_id: int, current_user: CurrentUser, db: DatabaseSession, settings: AppSettings
) -> IntegrationTestResult:
    source = _owned_integration(db, integration_id, current_user.id)
    integration = _read(source, settings)
    source.last_tested_at = utc_now()

    if integration.missing_fields:
        source.status = "error"
        source.last_error = f"Fehlende Angaben: {', '.join(integration.missing_fields)}"
        db.commit()
        return IntegrationTestResult(
            ok=False,
            status="error",
            message=source.last_error,
            missing_fields=integration.missing_fields,
        )

    label = INTEGRATION_SPECS[source.source_type].label
    if not source.enabled:
        source.status = "disabled"
        source.last_error = None
        db.commit()
        return IntegrationTestResult(
            ok=False,
            status="disabled",
            message=f"{label} ist deaktiviert, es wurde keine Verbindung getestet.",
            missing_fields=[],
        )

    key = _encryption_key(settings)
    credentials = decrypt_credentials(source.credentials, key) if source.credentials else {}
    try:
        message = check_integration_connection(
            kind=source.source_type,
            config=integration.config,
            credentials=credentials,
            document_storage_path=settings.document_storage_path,
            timeout_seconds=settings.integration_timeout_seconds,
        )
    except IntegrationConnectionError as exc:
        source.status = "error"
        source.last_error = str(exc)
        db.commit()
        return IntegrationTestResult(ok=False, status="error", message=str(exc), missing_fields=[])

    source.status = "connected"
    source.last_error = None
    db.commit()
    return IntegrationTestResult(ok=True, status="connected", message=message, missing_fields=[])


@router.post("/{integration_id}/sync", response_model=IntegrationSyncResult)
def sync_integration_endpoint(
    integration_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> IntegrationSyncResult:
    """Holt Daten aus der Integration und speichert sie lokal.

    Der Abruf dauert mehrere Sekunden und braucht eine bestehende Sitzung beim
    Schulportal, deshalb holt ihn die Oberfläche über diesen Knopf. Ein
    planmäßiger Lauf im Hintergrund teilt sich denselben Ablauf in
    ``run_integration_sync``, beachtet aber zusätzlich ``last_sync_at``.
    Ein Klick hier bleibt davon unberührt: der Knopf holt immer, auch wenn der
    planmäßige Lauf heute schon gelaufen ist.
    """
    source = _owned_integration(db, integration_id, current_user.id)
    integration = _read(source, settings)

    try:
        run = run_integration_sync(
            db=db,
            source=source,
            config=integration.config,
            credential_keys=integration.credential_keys,
            settings=settings,
            timezone_name=current_user.timezone or settings.timezone,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Integration encryption is not configured",
        ) from exc
    except SQLAlchemyError:
        db.rollback()
        raise

    if not run.ok:
        return IntegrationSyncResult(
            ok=False,
            status=run.status,
            message=run.message,
            missing_fields=list(run.missing_fields),
        )

    outcome = run.outcome
    message = run.message
    if getattr(outcome, "imported_lessons", 0):
        skipped = getattr(outcome, "skipped_entries", 0)
        if skipped:
            message += f" {skipped} unvollständige Einträge wurden übersprungen."
    return IntegrationSyncResult(
        ok=True,
        status=run.status,
        message=message,
        imported_lessons=getattr(outcome, "imported_lessons", 0),
        subjects=getattr(outcome, "subjects", 0),
        teachers=getattr(outcome, "teachers", 0),
        rooms=getattr(outcome, "rooms", 0),
        skipped_entries=getattr(outcome, "skipped_entries", 0),
        imported_documents=getattr(outcome, "imported_documents", 0),
        updated_documents=getattr(outcome, "updated_documents", 0),
        indexed_chunks=getattr(outcome, "indexed_chunks", 0),
        removed_documents=getattr(outcome, "removed_documents", 0),
        skipped_files=getattr(outcome, "skipped_files", 0),
        window_start=getattr(outcome, "window_start", None),
        window_end=getattr(outcome, "window_end", None),
        last_sync_at=source.last_sync_at,
    )
