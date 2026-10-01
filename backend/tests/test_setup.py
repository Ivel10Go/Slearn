"""Ersteinrichtung der Desktop-App (``/api/v1/setup``)."""

from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from alembic import command
from app.api.deps import get_db
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from tests.conftest import alembic_config


def _client(tmp_path: Path, *, auth_enabled: bool) -> Iterator[TestClient]:
    database_path = tmp_path / "setup.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", connect_args={"check_same_thread": False}, future=True)
    command.upgrade(alembic_config(database_path), "head")
    session_factory = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)
    settings = Settings(
        database_url=f"sqlite:///{database_path.as_posix()}",
        secret_key="test-secret-key",
        integration_encryption_key="test-integration-encryption-key",
        bootstrap_on_startup=False,
        demo_mode=False,
        auth_enabled=auth_enabled,
        timezone="Europe/Berlin",
        document_storage_path=tmp_path.as_posix(),
    )
    application = create_app(settings)

    def override_get_db() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = override_get_db
    with TestClient(application) as client:
        yield client
    engine.dispose()


@pytest.fixture
def local_client(tmp_path: Path) -> Iterator[TestClient]:
    yield from _client(tmp_path, auth_enabled=False)


@pytest.fixture
def server_client(tmp_path: Path) -> Iterator[TestClient]:
    yield from _client(tmp_path, auth_enabled=True)


def test_frische_installation_braucht_einrichtung(local_client: TestClient) -> None:
    assert local_client.get("/api/v1/setup").json() == {"needs_setup": True}
    # Ohne Konto antwortet der Rest der API mit 503 - genau das faengt die
    # Einrichtung im Frontend ab.
    assert local_client.get("/api/v1/auth/me").status_code == 503


def test_einrichtung_legt_das_konto_an_und_schaltet_die_api_frei(local_client: TestClient) -> None:
    response = local_client.post("/api/v1/setup", json={"name": "  Mia   Muster ", "timezone": "America/New_York"})
    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "Mia Muster"
    assert body["timezone"] == "America/New_York"
    assert body["is_admin"] is True

    assert local_client.get("/api/v1/setup").json() == {"needs_setup": False}
    assert local_client.get("/api/v1/auth/me").status_code == 200
    assert local_client.get("/api/v1/dashboard").status_code == 200


def test_ohne_zeitzone_gilt_die_servereinstellung(local_client: TestClient) -> None:
    response = local_client.post("/api/v1/setup", json={"name": "Mia"})
    assert response.status_code == 201
    assert response.json()["timezone"] == "Europe/Berlin"


def test_zweite_einrichtung_wird_abgelehnt(local_client: TestClient) -> None:
    assert local_client.post("/api/v1/setup", json={"name": "Mia"}).status_code == 201
    second = local_client.post("/api/v1/setup", json={"name": "Jemand anders"})
    assert second.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [{"name": "   "}, {"name": ""}, {"name": "Mia", "timezone": "Mond/Krater"}, {"name": "Mia", "password": "x"}],
)
def test_ungueltige_einrichtung_ist_422(local_client: TestClient, payload: dict[str, str]) -> None:
    assert local_client.post("/api/v1/setup", json=payload).status_code == 422
    assert local_client.get("/api/v1/setup").json() == {"needs_setup": True}


def test_mit_anmeldung_gibt_es_keine_einrichtung(server_client: TestClient) -> None:
    assert server_client.get("/api/v1/setup").status_code == 404
    assert server_client.post("/api/v1/setup", json={"name": "Eindringling"}).status_code == 404
