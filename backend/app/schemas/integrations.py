from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import UTCResponseModel

IntegrationKind = Literal["webuntis", "ai_provider", "documents"]
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
