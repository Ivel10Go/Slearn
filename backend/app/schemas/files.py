from datetime import datetime

from pydantic import BaseModel


class FileUploadResponse(BaseModel):
    id: int
    filename: str
    mime_type: str | None = None
    path: str
    checksum: str | None = None
    created_at: datetime | None = None
