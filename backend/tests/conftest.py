from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.api.deps import get_db
from app.core.config import Settings
from app.core.security import hash_password
from app.db.entities import User
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def alembic_config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


@pytest.fixture
def app_context(tmp_path: Path) -> Iterator[tuple[TestClient, Session, User]]:
    database_path = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    command.upgrade(alembic_config(database_path), "head")
    session_factory = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)

    settings = Settings(
        database_url=f"sqlite:///{database_path.as_posix()}",
        secret_key="test-secret-key",
        integration_encryption_key="test-integration-encryption-key",
        admin_username="admin",
        admin_password="admin-password",
        bootstrap_on_startup=False,
        demo_mode=False,
        cookie_secure=False,
        document_storage_path=tmp_path.as_posix(),
    )
    application = create_app(settings)

    db = session_factory()
    user = User(
        username="tester",
        password_hash=hash_password("test-password"),
        is_admin=True,
        is_active=True,
        timezone="UTC",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    def override_get_db() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = override_get_db
    with TestClient(application) as client:
        yield client, db, user

    application.dependency_overrides.clear()
    db.close()
    engine.dispose()


@pytest.fixture
def app_client(app_context: tuple[TestClient, Session, User]) -> TestClient:
    return app_context[0]
