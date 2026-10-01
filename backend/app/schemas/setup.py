from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SetupStatus(BaseModel):
    needs_setup: bool


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    # Die Oberflaeche schickt die Zeitzone des Rechners mit
    # (`Intl.DateTimeFormat().resolvedOptions().timeZone`). Ohne sie stuende
    # jede Stunde im Stundenplan in UTC.
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = " ".join(value.split())
        if not stripped:
            raise ValueError("Bitte gib einen Namen ein.")
        return stripped

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unbekannte Zeitzone.") from exc
        return value
