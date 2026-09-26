from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.api.helpers import owned_or_404
from app.core.config import require_integration_encryption_key
from app.core.crypto import CredentialDecryptionError, encrypt_credentials
from app.core.time import utc_now
from app.db.entities import SyncSource
from app.schemas.common import MessageResponse
from app.schemas.integrations import (
    IntegrationCreate,
    IntegrationRead,
    IntegrationTestResult,
    IntegrationUpdate,
)
from app.services.integrations import (
    INTEGRATION_SPECS,
    IntegrationValidationError,
    integration_read,
    missing_fields,
    normalize_config,
    normalize_credentials,
)

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])

_ADAPTER_PENDING = {
    "webuntis": "WebUntis",
    "ai_provider": "KI-Provider",
    "documents": "Dokumentordner",
}


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


@router.get("", response_model=list[IntegrationRead])
def list_integrations(current_user: CurrentUser, db: DatabaseSession, settings: AppSettings) -> list[IntegrationRead]:
    sources = db.scalars(
        select(SyncSource)
        .where(SyncSource.user_id == current_user.id)
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
    return _read(owned_or_404(db, SyncSource, integration_id, current_user.id), settings)


@router.patch("/{integration_id}", response_model=IntegrationRead)
def update_integration(
    integration_id: int,
    payload: IntegrationUpdate,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> IntegrationRead:
    key = _encryption_key(settings)
    source = owned_or_404(db, SyncSource, integration_id, current_user.id)
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
    source = owned_or_404(db, SyncSource, integration_id, current_user.id)
    db.delete(source)
    db.commit()
    return MessageResponse(message="Integration disconnected")


@router.post("/{integration_id}/test", response_model=IntegrationTestResult)
def test_integration(
    integration_id: int, current_user: CurrentUser, db: DatabaseSession, settings: AppSettings
) -> IntegrationTestResult:
    source = owned_or_404(db, SyncSource, integration_id, current_user.id)
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

    label = _ADAPTER_PENDING.get(source.source_type, source.source_type)
    source.status = "pending"
    source.last_error = None
    db.commit()
    return IntegrationTestResult(
        ok=False,
        status="pending",
        message=(
            f"Die Angaben für {label} sind vollständig gespeichert. "
            "Der Adapter für die echte Verbindung ist noch nicht implementiert, "
            "es wurde also keine Verbindung zu {label} getestet."
        ).replace("{label}", label),
        missing_fields=[],
    )
