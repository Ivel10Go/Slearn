from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

from app.core.time import as_utc


class UTCResponseModel(BaseModel):
    @field_validator("*", mode="after")
    @classmethod
    def normalize_datetime_fields(cls, value: Any) -> Any:
        return as_utc(value) if isinstance(value, datetime) else value


class MessageResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    database: str
