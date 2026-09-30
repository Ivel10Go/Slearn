from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import UTCResponseModel

IntegrationKind = Literal["webuntis", "ai_provider", "documents", "ical", "google_calendar", "caldav"]
IntegrationStatus = Literal["configured", "pending", "connected", "error", "disabled"]


def _clean_mapping(value: dict[str, str] | None) -> dict[str, str]:
    if not value:
        return {}
    return {str(key).strip(): "" if item is None else str(item).strip() for key, item in value.items() if str(key).strip()}


def _clean_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("name must not be empty")
    return cleaned


class IntegrationCreate(UTCResponseModel):
    kind: IntegrationKind
    name: str = Field(min_length=1, max_length=160)
    config: dict[str, str] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _clean_name(value)

    @field_validator("config", "credentials")
    @classmethod
    def normalize_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        return _clean_mapping(value)


class IntegrationUpdate(UTCResponseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    config: dict[str, str] | None = None
    credentials: dict[str, str] | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_name(value)

    @field_validator("config", "credentials")
    @classmethod
    def normalize_mapping(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return None if value is None else _clean_mapping(value)


class IntegrationRead(UTCResponseModel):
    id: int
    kind: IntegrationKind
    name: str
    enabled: bool
    status: IntegrationStatus
    config: dict[str, str]
    has_credentials: bool
    credential_keys: list[str]
    missing_fields: list[str]
    last_error: str | None
    last_tested_at: datetime | None
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IntegrationTestResult(UTCResponseModel):
    ok: bool
    status: IntegrationStatus
    message: str
    missing_fields: list[str] = Field(default_factory=list)


class IntegrationSyncResult(UTCResponseModel):
    """Ergebnis eines Datenabrufs, in Zahlen und als deutschen Satz.

    Die Felder sind bewusst eine flache Liste statt einer Sektion pro Art. Alle
    Abrufe liefern eine Teilmenge, der Rest bleibt null - dieselbe Form wie bei
    ``imported_lessons``, das ein Dokumentabruf ebenfalls nicht füllt.
    """

    ok: bool
    status: IntegrationStatus
    message: str
    missing_fields: list[str] = Field(default_factory=list)
    imported_lessons: int = 0
    subjects: int = 0
    teachers: int = 0
    rooms: int = 0
    skipped_entries: int = 0
    imported_documents: int = 0
    updated_documents: int = 0
    indexed_chunks: int = 0
    removed_documents: int = 0
    skipped_files: int = 0
    imported_events: int = 0
    updated_events: int = 0
    pushed_events: int = 0
    deleted_events: int = 0
    removed_events: int = 0
    conflicts: int = 0
    floating_events: int = 0
    window_start: date | None = None
    window_end: date | None = None
    last_sync_at: datetime | None = None


class IntegrationAuthorizeRead(UTCResponseModel):
    """Die Adresse, auf die der Browser für die Einwilligung gehen soll.

    Kein ``RedirectResponse``: die Oberfläche entscheidet, ob sie den Browser
    wirklich verlässt. Ein Server, der hier umleitet, nimmt der Oberfläche die
    Möglichkeit, vorher noch zu prüfen, ob die Einwilligung erteilt ist.
    """

    authorization_url: str
    state: str
    scopes: list[str] = Field(default_factory=list)
    message: str = ""


class IntegrationAuthorizedRead(UTCResponseModel):
    """Was nach der Rückkehr von Google dasteht."""

    ok: bool
    integration_id: int
    status: IntegrationStatus
    message: str
    #: Wohin die Oberfläche nach der Autorisierung zurückkehrt. Kein Sitzungs-
    #: geheimnis, nur ein Zustandsschlüssel zum Ablesen der Meldung.
    return_to: str
