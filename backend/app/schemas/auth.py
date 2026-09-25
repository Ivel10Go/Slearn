from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UTCResponseModel


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class UserRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None = None
    is_active: bool
    is_admin: bool
    timezone: str
    created_at: datetime
    last_login_at: datetime | None = None


class LoginResponse(BaseModel):
    user: UserRead
