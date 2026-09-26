import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from app.core.config import Settings, normalize_database_url
from app.core.crypto import decrypt_credentials
from app.core.security import create_session_token, hash_password
from app.db.entities import LearningProgress, StudyPlan, StudySession, SyncSource, Topic, User
from app.schemas.auth import UserRead
from app.services.demo_seed import seed_demo_data
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "tester", "password": "test-password"},
    )
    assert response.status_code == 200


def create_task(client: TestClient, db: Session, user: User, duration_minutes: int = 30) -> dict[str, object]:
    plan = StudyPlan(user_id=user.id, title="Lernplan")
    db.add(plan)
    db.commit()
    db.refresh(plan)
    response = client.post(
        "/api/v1/study/tasks",
        json={"study_plan_id": plan.id, "title": "Lernaufgabe", "duration_minutes": duration_minutes},
    )
    assert response.status_code == 201
    return response.json()


def test_health_and_authentication(app_client: TestClient) -> None:
    health = app_client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "database": "ok"}
    assert app_client.get("/api/v1/subjects").status_code == 401

    login(app_client)
    me = app_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "tester"
    assert "school_session" in app_client.cookies

    logout = app_client.post("/api/v1/auth/logout")
    assert logout.status_code == 200
    assert app_client.get("/api/v1/auth/me").status_code == 401


def test_subject_crud_and_dashboard(app_client: TestClient) -> None:
    login(app_client)
    subject_response = app_client.post(
        "/api/v1/subjects",
        json={"name": "Mathematik", "color": "#2563EB", "description": "Analysis"},
    )
    assert subject_response.status_code == 201
    subject = subject_response.json()
    assert subject["name"] == "Mathematik"

    duplicate = app_client.post("/api/v1/subjects", json={"name": "Mathematik"})
    assert duplicate.status_code == 409
    assert app_client.post("/api/v1/subjects", json={"name": "Bad", "color": "blue"}).status_code == 422

    homework = app_client.post(
        "/api/v1/homework",
        json={
            "subject_id": subject["id"],
            "title": "Aufgaben",
            "due_date": "2030-01-02T12:00:00Z",
        },
    )
    assert homework.status_code == 201
    assert homework.json()["subject_name"] == "Mathematik"

    exam_date = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    exam = app_client.post(
        "/api/v1/exams",
        json={"subject_id": subject["id"], "title": "Klausur", "date": exam_date},
    )
    assert exam.status_code == 201

    event = app_client.post(
        "/api/v1/calendar/events",
        json={
            "title": "Lerngruppe",
            "start_time": "2030-01-03T15:00:00Z",
            "end_time": "2030-01-03T16:00:00Z",
        },
    )
    assert event.status_code == 201
    event_id = event.json()["id"]
    partial_update = app_client.patch(
        f"/api/v1/calendar/events/{event_id}",
        json={"end_time": "2030-01-03T17:00:00Z"},
    )
    assert partial_update.status_code == 200

    dashboard = app_client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert set(payload) == {
        "generated_at",
        "timezone",
        "summary",
        "lessons",
        "upcoming_exams",
        "homework",
        "study_tasks",
        "calendar_events",
    }
    assert payload["summary"]["open_homework"] == 1
    assert payload["summary"]["upcoming_exams"] == 1
    assert payload["homework"][0]["subject_name"] == "Mathematik"
    assert payload["upcoming_exams"][0]["subject_name"] == "Mathematik"
    assert payload["calendar_events"][0]["title"] == "Lerngruppe"

    updated = app_client.patch(f"/api/v1/subjects/{subject['id']}", json={"name": "Mathe"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Mathe"
    assert app_client.delete(f"/api/v1/subjects/{subject['id']}").status_code == 200
    assert app_client.get(f"/api/v1/subjects/{subject['id']}").status_code == 404


def test_dashboard_matches_frontend_contract(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    subject_response = client.post("/api/v1/subjects", json={"name": "Mathematik", "color": "#2563EB"})
    assert subject_response.status_code == 201
    subject_id = subject_response.json()["id"]
    exam_response = client.post(
        "/api/v1/exams",
        json={
            "subject_id": subject_id,
            "title": "Analysis",
            "date": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        },
    )
    assert exam_response.status_code == 201
    topic = Topic(user_id=user.id, subject_id=subject_id, name="Ableitungen")
    db.add(topic)
    db.flush()
    db.add(LearningProgress(user_id=user.id, topic_id=topic.id, confidence=0.5))
    db.commit()

    payload = client.get("/api/v1/dashboard").json()
    exam = payload["upcoming_exams"][0]
    assert set(payload) == {
        "generated_at",
        "timezone",
        "summary",
        "lessons",
        "upcoming_exams",
        "homework",
        "study_tasks",
        "calendar_events",
    }
    assert set(payload["summary"]) == {
        "completed_lessons",
        "total_lessons",
        "day_progress_percent",
        "open_homework",
        "upcoming_exams",
        "planned_minutes",
    }
    assert exam["topics"] == [{"topic_id": topic.id, "name": "Ableitungen", "confidence": 0.5}]
    assert isinstance(exam["topics"][0], dict)
    assert exam["subject_name"] == "Mathematik"
    assert "progress" not in exam
    user.timezone = "Europe/Berlin"
    db.commit()
    assert client.get("/api/v1/dashboard").json()["timezone"] == "Europe/Berlin"
    assert "homework_pending" not in payload["summary"]
    assert "day_progress" not in payload["summary"]


def test_resources_are_user_isolated(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    subject_response = client.post("/api/v1/subjects", json={"name": "Privat"})
    assert subject_response.status_code == 201
    subject_id = subject_response.json()["id"]

    other = User(
        username="other",
        password_hash=hash_password("other-password"),
        is_active=True,
        timezone="UTC",
    )
    db.add(other)
    db.commit()
    db.refresh(other)

    token = create_session_token(other.id, "test-secret-key", 3600)
    client.cookies.clear()
    response = client.get(
        f"/api/v1/subjects/{subject_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert user.id != other.id


def test_demo_seed_is_idempotent(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    first = seed_demo_data(db, user.id)
    db.commit()
    second = seed_demo_data(db, user.id)
    db.commit()
    assert first.created > 0
    assert first.updated == 0
    assert second.created == 0
    assert second.updated == first.total


def test_study_task_order_and_patch_validation(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    plan = StudyPlan(user_id=user.id, title="Plan")
    db.add(plan)
    db.commit()
    db.refresh(plan)

    first = client.post(
        "/api/v1/study/tasks",
        json={"study_plan_id": plan.id, "title": "Erste Aufgabe"},
    )
    second = client.post(
        "/api/v1/study/tasks",
        json={"study_plan_id": plan.id, "title": "Zweite Aufgabe"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert [first.json()["order_index"], second.json()["order_index"]] == [0, 1]
    assert client.patch(f"/api/v1/study/tasks/{first.json()['id']}", json={"title": None}).status_code == 422


def test_study_task_start_is_idempotent_and_stop_resets(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    task = create_task(client, db, user)
    task_id = task["id"]

    first_start = client.post(f"/api/v1/study/tasks/{task_id}/start")
    second_start = client.post(f"/api/v1/study/tasks/{task_id}/start")
    assert first_start.status_code == 200
    assert second_start.status_code == 200
    assert first_start.json()["id"] == second_start.json()["id"]
    assert first_start.json()["started_at"] == second_start.json()["started_at"]
    assert first_start.json()["completed_at"] is None
    assert client.get(f"/api/v1/study/tasks/{task_id}").json()["status"] == "in_progress"

    db.expire_all()
    sessions = db.query(StudySession).filter(StudySession.task_id == task_id).all()
    assert len(sessions) == 1

    stopped = client.post(f"/api/v1/study/tasks/{task_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "pending"
    assert stopped.json()["completed_at"] is None
    db.expire_all()
    session = db.query(StudySession).filter(StudySession.task_id == task_id).one()
    assert session.completed_at is not None
    assert session.duration_minutes is not None
    assert client.post(f"/api/v1/study/tasks/{task_id}/stop").json()["status"] == "pending"


def test_study_task_can_complete_without_start_and_is_idempotent(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    task = create_task(client, db, user, duration_minutes=45)
    task_id = task["id"]

    completed = client.post(f"/api/v1/study/tasks/{task_id}/complete", json={"notes": "ohne Provider"})
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_at"] is not None
    completed_at = completed.json()["completed_at"]

    db.expire_all()
    sessions = db.query(StudySession).filter(StudySession.task_id == task_id).all()
    assert len(sessions) == 1
    assert sessions[0].duration_minutes == 45
    assert sessions[0].notes == "ohne Provider"
    assert sessions[0].completed_at is not None

    repeated = client.post(f"/api/v1/study/tasks/{task_id}/complete")
    assert repeated.status_code == 200
    assert repeated.json()["completed_at"] == completed_at
    assert client.post(f"/api/v1/study/tasks/{task_id}/start").status_code == 409


def test_study_task_actions_are_user_isolated(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    task = create_task(client, db, user)
    task_id = task["id"]
    other = User(
        username="other-study",
        password_hash=hash_password("other-password"),
        is_active=True,
        timezone="UTC",
    )
    db.add(other)
    db.commit()
    db.refresh(other)

    client.cookies.clear()
    headers = {"Authorization": f"Bearer {create_session_token(other.id, 'test-secret-key', 3600)}"}
    assert client.post(f"/api/v1/study/tasks/{task_id}/start", headers=headers).status_code == 404
    assert client.post(f"/api/v1/study/tasks/{task_id}/complete", headers=headers).status_code == 404
    assert client.post(f"/api/v1/study/tasks/{task_id}/stop", headers=headers).status_code == 404
    db.expire_all()
    assert db.query(StudySession).filter(StudySession.task_id == task_id).count() == 0


def test_settings_aliases_and_url_normalization() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+psycopg2://user:pass@localhost/db",
        SECRET_KEY="secret",
        CORS_ORIGINS="https://one.example, https://two.example",
        SCHOOL_TIMEZONE="Europe/Berlin",
    )
    assert settings.database_url.startswith("postgresql+psycopg2://")
    assert settings.cors_origins == ["https://one.example", "https://two.example"]
    assert settings.timezone == "Europe/Berlin"
    assert normalize_database_url(settings.database_url) == "postgresql+psycopg://user:pass@localhost/db"
    assert normalize_database_url("postgres://user:pass@localhost/db") == "postgresql+psycopg://user:pass@localhost/db"


@pytest.mark.parametrize(
    ("secret_key", "admin_password"),
    [
        ("", "a-real-admin-password"),
        ("development-only-not-a-secret", "a-real-admin-password"),
        ("a-real-secret-key", ""),
        ("a-real-secret-key", "change-me-local-only"),
    ],
)
def test_production_settings_reject_empty_or_placeholder_secrets(
    secret_key: str,
    admin_password: str,
) -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY=secret_key,
            ADMIN_PASSWORD=admin_password,
        )


def test_production_settings_accept_non_placeholder_secrets() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="production",
        SECRET_KEY="a-real-secret-key",
        INTEGRATION_ENCRYPTION_KEY="a-real-integration-key",
        ADMIN_PASSWORD="a-real-admin-password",
    )
    assert settings.environment == "production"
    assert settings.secret_key is not None
    assert settings.integration_encryption_key is not None
    assert settings.admin_password is not None


def test_response_timestamps_are_utc_aware() -> None:
    user = User(
        id=1,
        username="timestamps",
        password_hash=hash_password("test-password"),
        is_active=True,
        is_admin=False,
        timezone="UTC",
        created_at=datetime(2026, 1, 1, 12, 0),
    )
    response = UserRead.model_validate(user)
    assert response.created_at.tzinfo is timezone.utc
    assert response.created_at.utcoffset() == timedelta(0)


def test_file_upload_requires_authentication_and_stores_checksum(app_client: TestClient) -> None:
    assert app_client.post("/api/v1/files", files={"file": ("notiz.txt", b"hallo")}).status_code == 401

    login(app_client)
    response = app_client.post("/api/v1/files", files={"file": ("notiz.txt", b"hallo welt")})
    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "notiz.txt"
    assert body["checksum"] == hashlib.sha256(b"hallo welt").hexdigest()
    assert body["mime_type"] == "text/plain"
    assert Path(body["path"]).is_file()


def test_non_integration_sync_sources_are_ignored(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    demo_source = SyncSource(
        user_id=user.id,
        source_type="demo",
        name="Demo",
        enabled=True,
        configuration={"kind": "demo"},
    )
    db.add(demo_source)
    db.commit()
    db.refresh(demo_source)

    login(client)
    assert client.get("/api/v1/integrations").json() == []

    real = client.post(
        "/api/v1/integrations",
        json={"kind": "documents", "name": "Dokumente", "config": {"path": "dokumente"}},
    )
    assert real.status_code == 201
    listed = client.get("/api/v1/integrations").json()
    assert [item["name"] for item in listed] == ["Dokumente"]

    assert client.get(f"/api/v1/integrations/{demo_source.id}").status_code == 404
    assert client.patch(f"/api/v1/integrations/{demo_source.id}", json={"name": "Umbenannt"}).status_code == 404
    assert client.post(f"/api/v1/integrations/{demo_source.id}/test").status_code == 404
    assert client.delete(f"/api/v1/integrations/{demo_source.id}").status_code == 404
    assert db.get(SyncSource, demo_source.id) is not None


def test_integration_routes_are_registered(app_client: TestClient) -> None:
    paths = {route.path for route in app_client.app.routes}
    assert "/api/v1/integrations" in paths
    assert "/api/v1/integrations/catalog" in paths
    assert "/api/v1/integrations/{integration_id}" in paths
    assert "/api/v1/integrations/{integration_id}/test" in paths


def test_integration_catalog_lists_all_kinds(app_client: TestClient) -> None:
    login(app_client)
    response = app_client.get("/api/v1/integrations/catalog")
    assert response.status_code == 200
    catalog = response.json()
    assert [entry["kind"] for entry in catalog] == ["webuntis", "ai_provider", "documents"]
    webuntis = next(entry for entry in catalog if entry["kind"] == "webuntis")
    assert webuntis["required_config"] == ["base_url", "school"]
    assert webuntis["required_credentials"] == ["username", "password"]


def test_integrations_require_authentication(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/integrations").status_code == 401
    assert app_client.post("/api/v1/integrations", json={"kind": "documents"}).status_code == 401


def test_integration_credentials_are_encrypted_at_rest(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, _user = app_context
    login(client)
    created = client.post(
        "/api/v1/integrations",
        json={
            "kind": "webuntis",
            "name": "Schulportal",
            "config": {"base_url": "https://portal.example.org/", "school": "Gymnasium"},
            "credentials": {"username": "schueler", "password": "geheim-passwort"},
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["has_credentials"] is True
    assert body["credential_keys"] == ["password", "username"]
    assert body["missing_fields"] == []
    assert body["config"]["base_url"] == "https://portal.example.org"
    assert "credentials" not in body

    source = db.scalar(select(SyncSource).where(SyncSource.id == body["id"]))
    assert source is not None
    assert source.credentials is not None
    assert "geheim-passwort" not in source.credentials
    assert source.credentials.startswith("sa1.")
    assert decrypt_credentials(source.credentials, "test-integration-encryption-key") == {
        "username": "schueler",
        "password": "geheim-passwort",
    }


def test_integration_create_rejects_incomplete_input(app_client: TestClient) -> None:
    login(app_client)
    missing_credentials = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "webuntis",
            "name": "Ohne Zugangsdaten",
            "config": {"base_url": "https://portal.example.org", "school": "Gymnasium"},
        },
    )
    assert missing_credentials.status_code == 422

    unknown_field = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "webuntis",
            "name": "Unbekanntes Feld",
            "config": {"base_url": "https://portal.example.org", "school": "Gymnasium", "token": "x"},
            "credentials": {"username": "schueler", "password": "geheim"},
        },
    )
    assert unknown_field.status_code == 422

    duplicate = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "documents",
            "name": "Dokumente",
            "config": {"path": "erste"},
        },
    )
    assert duplicate.status_code == 201
    second = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "documents",
            "name": "Dokumente",
            "config": {"path": "zweite"},
        },
    )
    assert second.status_code == 409


def test_integration_update_disable_and_delete(app_client: TestClient) -> None:
    login(app_client)
    integration_id = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "Ollama",
            "config": {"base_url": "http://localhost:11434", "model": "llama3"},
        },
    ).json()["id"]

    patched = app_client.patch(
        f"/api/v1/integrations/{integration_id}",
        json={"name": "Ollama lokal", "config": {"model": "llama3.2"}},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Ollama lokal"
    assert patched.json()["config"] == {"base_url": "http://localhost:11434", "model": "llama3.2"}

    disabled = app_client.patch(f"/api/v1/integrations/{integration_id}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["status"] == "disabled"

    assert app_client.delete(f"/api/v1/integrations/{integration_id}").status_code == 200
    assert app_client.get(f"/api/v1/integrations/{integration_id}").status_code == 404


def test_integration_test_endpoint_reports_pending_adapter(app_client: TestClient) -> None:
    login(app_client)
    integration_id = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "Ollama",
            "config": {"base_url": "http://localhost:11434", "model": "llama3"},
        },
    ).json()["id"]

    result = app_client.post(f"/api/v1/integrations/{integration_id}/test")
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is False
    assert body["status"] == "pending"
    assert "noch nicht implementiert" in body["message"]

    listed = {item["id"]: item for item in app_client.get("/api/v1/integrations").json()}
    assert listed[integration_id]["status"] == "pending"


def test_integration_test_endpoint_flags_missing_fields(app_client: TestClient) -> None:
    login(app_client)
    integration_id = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "webuntis",
            "name": "Unvollständig",
            "config": {"base_url": "https://portal.example.org", "school": "Gymnasium"},
            "credentials": {"username": "schueler", "password": "geheim"},
        },
    ).json()["id"]

    app_client.patch(f"/api/v1/integrations/{integration_id}", json={"credentials": {}})
    result = app_client.post(f"/api/v1/integrations/{integration_id}/test")
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is False
    assert body["status"] == "error"
    assert body["missing_fields"] == ["username", "password"]


def test_integrations_are_isolated_per_user(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, _user = app_context
    login(client)
    mine = client.post(
        "/api/v1/integrations",
        json={
            "kind": "documents",
            "name": "Meine Dokumente",
            "config": {"path": "meine"},
        },
    )
    assert mine.status_code == 201
    assert [item["name"] for item in client.get("/api/v1/integrations").json()] == ["Meine Dokumente"]

    other = User(
        username="other",
        password_hash=hash_password("other-password"),
        is_admin=False,
        is_active=True,
        timezone="UTC",
    )
    db.add(other)
    db.commit()
    db.refresh(other)

    client.post("/api/v1/auth/logout")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "other", "password": "other-password"},
    )
    assert response.status_code == 200

    assert client.get("/api/v1/integrations").json() == []
    assert client.get(f"/api/v1/integrations/{mine.json()['id']}").status_code == 404
    assert client.patch(f"/api/v1/integrations/{mine.json()['id']}", json={"name": "Gekapert"}).status_code == 404
    assert client.delete(f"/api/v1/integrations/{mine.json()['id']}").status_code == 404


def test_integration_endpoints_require_encryption_key(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, _db, _user = app_context
    login(client)
    integration_id = client.post(
        "/api/v1/integrations",
        json={
            "kind": "webuntis",
            "name": "Schulportal",
            "config": {"base_url": "https://portal.example.org", "school": "Gymnasium"},
            "credentials": {"username": "schueler", "password": "geheim-passwort"},
        },
    ).json()["id"]

    settings = client.app.state.settings
    original_key = settings.integration_encryption_key
    settings.integration_encryption_key = None
    try:
        assert client.get("/api/v1/integrations").status_code == 503
        assert client.get(f"/api/v1/integrations/{integration_id}").status_code == 503
        assert client.post(f"/api/v1/integrations/{integration_id}/test").status_code == 503
        assert (
            client.post(
                "/api/v1/integrations",
                json={"kind": "documents", "name": "Ohne Key", "config": {"path": "x"}},
            ).status_code
            == 503
        )
        assert client.get("/api/v1/integrations/catalog").status_code == 200
    finally:
        settings.integration_encryption_key = original_key

    assert client.get("/api/v1/integrations").status_code == 200
