from pydantic import BaseModel, Field


class AccountDeleteRequest(BaseModel):
    #: Muss genau ``LÖSCHEN`` sein - ein versehentlicher Klick tippt das nicht.
    confirm: str = Field(max_length=32)
    #: Nur mit Anmeldung (Server): das eigene Passwort. Die Desktop-App kennt keins.
    password: str | None = Field(default=None, max_length=256)


class AccountDeleteResponse(BaseModel):
    deleted_rows: int
    deleted_files: int
