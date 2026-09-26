from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Final, cast

from sqlalchemy.orm import Session

from app.core.crypto import decrypt_credentials, has_credentials
from app.db.entities import SyncSource
from app.schemas.integrations import IntegrationKind, IntegrationRead, IntegrationStatus
from app.services.ai_provider import AiProviderError, probe_ai_provider
from app.services.timetable_import import (
    DEFAULT_SYNC_WEEKS,
    SyncOutcome,
    import_timetable,
)
from app.services.webuntis import WebUntisClient, WebUntisError, probe_webuntis


class IntegrationValidationError(ValueError):
    pass


class IntegrationConnectionError(RuntimeError):
    """Die Integration ist vollständig konfiguriert, aber nicht erreichbar."""


@dataclass(frozen=True, slots=True)
class IntegrationSpec:
    kind: str
    label: str
    description: str
    required_config: tuple[str, ...] = ()
    required_credentials: tuple[str, ...] = ()
    optional_credentials: tuple[str, ...] = ()

    @property
    def credential_keys(self) -> tuple[str, ...]:
        return self.required_credentials + self.optional_credentials


INTEGRATION_SPECS: Final[dict[str, IntegrationSpec]] = {
    "webuntis": IntegrationSpec(
        kind="webuntis",
        label="WebUntis",
        description="Importiert Stundenplan, Kurse und Klausuren aus deinem Schulportal.",
        required_config=("base_url", "school"),
        required_credentials=("username", "password"),
    ),
    "ai_provider": IntegrationSpec(
        kind="ai_provider",
        label="KI-Provider",
        description="Lokales Ollama oder ein OpenAI-kompatibler Endpoint für den Assistenten.",
        required_config=("base_url", "model"),
        optional_credentials=("api_key",),
    ),
    "documents": IntegrationSpec(
        kind="documents",
        label="Dokumente und Sync-Ordner",
        description="Lokaler Ablageort für Dokumente, die später durchsuchbar werden.",
        required_config=("path",),
    ),
}

INTEGRATION_KINDS: Final[tuple[str, ...]] = tuple(INTEGRATION_SPECS)


def integration_spec(kind: str) -> IntegrationSpec:
    spec = INTEGRATION_SPECS.get(kind)
    if spec is None:
        raise IntegrationValidationError(f"Unsupported integration kind: {kind}")
    return spec


def _validate_base_url(value: str, field: str) -> str:
    cleaned = value.strip().rstrip("/")
    if not cleaned:
        raise IntegrationValidationError(f"{field} must not be empty")
    if not cleaned.startswith(("http://", "https://")):
        raise IntegrationValidationError(f"{field} must start with http:// or https://")
    if len(cleaned) > 500:
        raise IntegrationValidationError(f"{field} is too long")
    return cleaned


def _validate_documents_path(value: str, storage_root: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise IntegrationValidationError("path must not be empty")
    root = Path(storage_root)
    candidate = Path(cleaned)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise IntegrationValidationError("path must stay inside the configured document storage directory") from exc
    return str(resolved)


def normalize_config(kind: str, config: dict[str, str], storage_root: str) -> dict[str, str]:
    spec = integration_spec(kind)
    allowed = set(spec.required_config)
    normalized: dict[str, str] = {}
    for key, raw_value in config.items():
        field = key.strip().lower()
        value = str(raw_value).strip()
        if field not in allowed:
            raise IntegrationValidationError(f"Unsupported field for {spec.label}: {key}")
        if not value:
            raise IntegrationValidationError(f"{field} must not be empty")
        if field == "base_url":
            value = _validate_base_url(value, field)
        elif field == "path":
            value = _validate_documents_path(value, storage_root)
        normalized[field] = value
    return normalized


def normalize_credentials(kind: str, credentials: dict[str, str]) -> dict[str, str]:
    spec = integration_spec(kind)
    allowed = set(spec.credential_keys)
    normalized: dict[str, str] = {}
    for key, raw_value in credentials.items():
        field = key.strip().lower()
        if field not in allowed:
            raise IntegrationValidationError(f"Unsupported credential for {spec.label}: {key}")
        value = str(raw_value)
        if not value:
            raise IntegrationValidationError(f"{field} must not be empty")
        normalized[field] = value
    return normalized


def missing_fields(kind: str, config: dict[str, str] | None, credential_keys: list[str] | None) -> list[str]:
    spec = integration_spec(kind)
    stored = config or {}
    present_credentials = set(credential_keys or [])
    missing = [field for field in spec.required_config if not stored.get(field)]
    missing.extend(field for field in spec.required_credentials if field not in present_credentials)
    return missing


def integration_read(source: SyncSource, encryption_key: str) -> IntegrationRead:
    integration_spec(source.source_type)
    config = {str(key): str(value) for key, value in (source.configuration or {}).items()}
    stored = has_credentials(source.credentials)
    credential_keys: list[str] = []
    if stored and source.credentials is not None:
        credential_keys = sorted(decrypt_credentials(source.credentials, encryption_key))
    status = "disabled" if not source.enabled else (source.status or "configured")
    return IntegrationRead(
        id=source.id,
        kind=cast(IntegrationKind, source.source_type),
        name=source.name,
        enabled=source.enabled,
        status=cast(IntegrationStatus, status),
        config=config,
        has_credentials=stored,
        credential_keys=credential_keys,
        missing_fields=missing_fields(source.source_type, config, credential_keys),
        last_error=source.last_error,
        last_tested_at=source.last_tested_at,
        last_sync_at=source.last_sync_at,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def check_integration_connection(
    kind: str,
    config: dict[str, str],
    credentials: dict[str, str],
    document_storage_path: str,
    timeout_seconds: float,
) -> str:
    """Prüft eine Integration gegen das echte Gegenstück und gibt einen deutschen Text zurück.

    Wirft ``IntegrationConnectionError``, wenn die Verbindung nicht zustande kommt.
    """
    if kind == "webuntis":
        try:
            identity = probe_webuntis(
                base_url=config.get("base_url", ""),
                school=config.get("school", ""),
                username=credentials.get("username", ""),
                password=credentials.get("password", ""),
                timeout_seconds=timeout_seconds,
            )
        except WebUntisError as exc:
            raise IntegrationConnectionError(str(exc)) from exc
        return f"WebUntis ist erreichbar und die Zugangsdaten stimmen ({identity.describe()})."

    if kind == "ai_provider":
        try:
            models = probe_ai_provider(
                base_url=config.get("base_url", ""),
                model=config.get("model", ""),
                api_key=credentials.get("api_key", ""),
                timeout_seconds=timeout_seconds,
            )
        except AiProviderError as exc:
            raise IntegrationConnectionError(str(exc)) from exc
        return f"Der KI-Provider ist erreichbar und {models.describe()}"

    if kind == "documents":
        return _check_documents_path(config.get("path", ""), document_storage_path)

    raise IntegrationConnectionError(f"Für {kind} ist noch kein Adapter implementiert.")


def _check_documents_path(path: str, storage_root: str) -> str:
    root = Path(storage_root).resolve()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise IntegrationConnectionError("Der Dokumentpfad liegt außerhalb des konfigurierten Ablageordners.")
    if not resolved.exists():
        if not root.is_dir():
            raise IntegrationConnectionError(f"Der Ablageordner {root} existiert nicht.")
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise IntegrationConnectionError(f"Der Dokumentpfad ließ sich nicht anlegen: {exc.strerror}.") from exc
        return f"Der Dokumentordner {resolved.name or resolved} wurde angelegt und ist beschreibbar."
    if not resolved.is_dir():
        raise IntegrationConnectionError(f"{resolved} ist kein Ordner.")
    return f"Der Dokumentordner {resolved.name or resolved} ist vorhanden und beschreibbar."


def sync_window(today: date | None = None, weeks: int = DEFAULT_SYNC_WEEKS) -> tuple[date, date]:
    """Rechnet das Abruffenster aus: Montag der aktuellen Woche bis ``weeks`` Wochen.

    Der Start liegt bewusst am Montag, weil ein Schulwochenraster dort
    einheitlich beginnt; ein Sync am Mittwoch soll trotzdem dieselbe Woche
    liefern wie ein Sync am Montag.
    """
    reference = today or date.today()
    monday = reference - timedelta(days=reference.weekday())
    span = max(1, weeks)
    return monday, monday + timedelta(days=7 * span - 1)


def sync_integration(
    *,
    db: Session,
    source: SyncSource,
    config: dict[str, str],
    credentials: dict[str, str],
    timezone_name: str,
    timeout_seconds: float,
    weeks: int = DEFAULT_SYNC_WEEKS,
    today: date | None = None,
) -> SyncOutcome:
    """Holt Daten aus der Integration und schreibt sie in die Datenbank.

    Wirft ``IntegrationConnectionError``, wenn die Gegenseite nicht erreichbar
    ist oder ablehnt. Der Aufrufer entscheidet, was in ``last_error`` landet.
    """
    if source.source_type == "webuntis":
        return _sync_webuntis(
            db, source, config, credentials, timezone_name, timeout_seconds, weeks, today
        )
    raise IntegrationConnectionError(f"Für {source.source_type} ist noch kein Import implementiert.")


def _sync_webuntis(
    db: Session,
    source: SyncSource,
    config: dict[str, str],
    credentials: dict[str, str],
    timezone_name: str,
    timeout_seconds: float,
    weeks: int,
    today: date | None,
) -> SyncOutcome:
    start, end = sync_window(today, weeks)
    try:
        with WebUntisClient(
            base_url=config.get("base_url", ""),
            school=config.get("school", ""),
            username=credentials.get("username", ""),
            password=credentials.get("password", ""),
            timeout_seconds=timeout_seconds,
        ) as client:
            timetable = client.fetch_timetable(start, end)
    except WebUntisError as exc:
        raise IntegrationConnectionError(str(exc)) from exc
    return import_timetable(db, source, timetable, timezone_name)
