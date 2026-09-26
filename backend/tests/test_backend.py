from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from app.core.config import Settings, normalize_database_url
from app.core.crypto import decrypt_credentials
from app.core.security import create_session_token, hash_password
from app.core.time import as_utc
from app.db.entities import (
    LearningProgress,
    Lesson,
    Room,
    StudyPlan,
    StudySession,
    Subject,
    SyncSource,
    Teacher,
    Topic,
    User,
)
from app.schemas.auth import UserRead
from app.services import integrations as integrations_service
from app.services.ai_provider import (
    AiProviderAuthError,
    AiProviderClient,
    AiProviderConfigError,
    AiProviderError,
    AiProviderModels,
    build_chat_url,
    build_models_url,
)
from app.services.dashboard import serialize_dashboard
from app.services.demo_seed import seed_demo_data
from app.services.integrations import IntegrationConnectionError, sync_integration, sync_window
from app.services.lessons import MAX_RANGE_DAYS, LessonRangeError, validate_range
from app.services.webuntis import (
    WebUntisAuthError,
    WebUntisClient,
    WebUntisConfigError,
    WebUntisError,
    WebUntisIdentity,
    WebUntisTimetable,
    build_rpc_url,
    parse_timetable,
)
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


def _create_webuntis(client: TestClient) -> int:
    created = client.post(
        "/api/v1/integrations",
        json={
            "kind": "webuntis",
            "name": "Schulportal",
            "config": {"base_url": "https://schule.webuntis.com", "school": "schule"},
            "credentials": {"username": "nutzer", "password": "geheim"},
        },
    )
    assert created.status_code == 201
    return int(created.json()["id"])


def test_integration_test_endpoint_connects_on_success(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(app_client)
    integration_id = _create_webuntis(app_client)
    identity = WebUntisIdentity(person_id=4711, person_type=1, klasse_id=108)
    monkeypatch.setattr(integrations_service, "probe_webuntis", lambda **_: identity)

    result = app_client.post(f"/api/v1/integrations/{integration_id}/test")
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is True
    assert body["status"] == "connected"
    assert "Zugangsdaten stimmen" in body["message"]
    assert "Person-ID 4711" in body["message"]

    listed = {item["id"]: item for item in app_client.get("/api/v1/integrations").json()}
    assert listed[integration_id]["status"] == "connected"
    assert listed[integration_id]["last_error"] is None
    assert listed[integration_id]["last_tested_at"] is not None


def test_integration_test_endpoint_stores_failure_reason(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(app_client)
    integration_id = _create_webuntis(app_client)

    def boom(**_: object) -> WebUntisIdentity:
        raise WebUntisAuthError("WebUntis hat Benutzername oder Passwort abgelehnt.")

    monkeypatch.setattr(integrations_service, "probe_webuntis", boom)

    result = app_client.post(f"/api/v1/integrations/{integration_id}/test")
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is False
    assert body["status"] == "error"
    assert "abgelehnt" in body["message"]

    listed = {item["id"]: item for item in app_client.get("/api/v1/integrations").json()}
    assert listed[integration_id]["status"] == "error"
    assert listed[integration_id]["last_error"] == body["message"]


def test_check_connection_rejects_unknown_kind(tmp_path: Path) -> None:
    with pytest.raises(integrations_service.IntegrationConnectionError, match="kein Adapter implementiert"):
        integrations_service.check_integration_connection(
            kind="icalendar",
            config={},
            credentials={},
            document_storage_path=str(tmp_path),
            timeout_seconds=1.0,
        )


def test_integration_test_endpoint_skips_disabled(app_client: TestClient) -> None:
    login(app_client)
    integration_id = _create_webuntis(app_client)
    app_client.patch(f"/api/v1/integrations/{integration_id}", json={"enabled": False})

    body = app_client.post(f"/api/v1/integrations/{integration_id}/test").json()
    assert body["ok"] is False
    assert body["status"] == "disabled"
    assert "deaktiviert" in body["message"]


def test_integration_test_endpoint_checks_document_folder(app_client: TestClient) -> None:
    login(app_client)
    integration_id = app_client.post(
        "/api/v1/integrations",
        json={"kind": "documents", "name": "Dokumente", "config": {"path": "neu"}},
    ).json()["id"]

    body = app_client.post(f"/api/v1/integrations/{integration_id}/test").json()
    assert body["ok"] is True
    assert body["status"] == "connected"
    assert "angelegt" in body["message"]


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


class _FakeWebUntisTransport(httpx.BaseTransport):
    """Beantwortet JSON-RPC-Aufrufe, ohne das Netzwerk zu berühren."""

    def __init__(self, responses: dict[str, httpx.Response | Exception]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        method = request.read().decode("utf-8")
        for name, response in self.responses.items():
            if f'"method": "{name}"' in method or f'"method":"{name}"' in method:
                if isinstance(response, Exception):
                    raise response
                return httpx.Response(
                    response.status_code,
                    content=response.content,
                    request=request,
                )
        return httpx.Response(500, content=b'{"error": "unmocked"}', request=request)


def _rpc(result: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, content=json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode())


def _client(transport: httpx.BaseTransport) -> WebUntisClient:
    client = WebUntisClient("https://schule.webuntis.com", "schule", "nutzer", "geheim")
    client._client = httpx.Client(transport=transport, timeout=5.0)
    return client


def _models_response(*names: str, status_code: int = 200) -> httpx.Response:
    payload = {"object": "list", "data": [{"id": name, "object": "model"} for name in names]}
    return httpx.Response(status_code, content=json.dumps(payload).encode())


class _FakeAiTransport(httpx.BaseTransport):
    """Beantwortet jeden GET mit derselben Antwort, ohne das Netzwerk zu berühren."""

    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return httpx.Response(self.response.status_code, content=self.response.content, request=request)


def _ai_client(
    response: httpx.Response | Exception,
    model: str = "Auto",
    api_key: str = "geheim",
) -> AiProviderClient:
    client = AiProviderClient("https://api.example.com/v1", model, api_key)
    client._client = httpx.Client(transport=_FakeAiTransport(response), timeout=5.0)
    return client


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.openai.com/v1", "https://api.openai.com/v1/models"),
        ("https://api.openai.com/v1/", "https://api.openai.com/v1/models"),
        ("http://localhost:11434", "http://localhost:11434/v1/models"),
        ("http://localhost:11434/", "http://localhost:11434/v1/models"),
        ("http://localhost:8080/api/v1", "http://localhost:8080/api/v1/models"),
        ("https://x.test/v1/models", "https://x.test/v1/models"),
    ],
)
def test_build_models_url(base_url: str, expected: str) -> None:
    assert build_models_url(base_url) == expected


@pytest.mark.parametrize("base_url", ["ftp://x.test/v1", "nicht-url", "/v1", ""])
def test_build_models_url_rejects_invalid(base_url: str) -> None:
    with pytest.raises(AiProviderConfigError):
        build_models_url(base_url)


def test_ai_provider_lists_models_and_sends_key() -> None:
    client = _ai_client(_models_response("gpt-4o", "gpt-4o-mini"), model="gpt-4o-mini")
    result = client.check_connection()
    assert result.model_id == "gpt-4o-mini"
    assert result.auto_selected is False
    assert result.available == ("gpt-4o", "gpt-4o-mini")
    assert "gpt-4o-mini" in result.describe()

    request = client._client._transport.requests[0]  # type: ignore[union-attr]
    assert request.headers["Authorization"] == "Bearer geheim"
    assert str(request.url) == "https://api.example.com/v1/models"


def test_ai_provider_omits_authorization_without_key() -> None:
    client = _ai_client(_models_response("llama3.2"), api_key="")
    assert client.check_connection().model_id == "llama3.2"
    request = client._client._transport.requests[0]  # type: ignore[union-attr]
    assert "Authorization" not in request.headers


def test_ai_provider_auto_picks_first_model() -> None:
    client = _ai_client(_models_response("llama3.2:latest", "mistral"), model="Auto")
    result = client.check_connection()
    assert result.model_id == "llama3.2:latest"
    assert result.auto_selected is True
    assert "automatisch" in result.describe()


def test_ai_provider_matches_model_without_tag() -> None:
    client = _ai_client(_models_response("llama3.2:latest"), model="llama3.2")
    assert client.check_connection().model_id == "llama3.2:latest"


def test_ai_provider_reports_unknown_model_with_suggestions() -> None:
    client = _ai_client(_models_response("gpt-4o", "gpt-4o-mini"), model="llama9")
    with pytest.raises(AiProviderConfigError) as excinfo:
        client.check_connection()
    message = str(excinfo.value)
    assert "kennt das Modell 'llama9' nicht" in message
    assert "'gpt-4o'" in message
    assert "Auto" in message


def test_ai_provider_reports_rejected_key() -> None:
    denied = httpx.Response(401, content=json.dumps({"error": {"message": "Invalid API key"}}).encode())
    with pytest.raises(AiProviderAuthError, match="API-Schlüssel abgelehnt"):
        _ai_client(denied).check_connection()


def test_ai_provider_hints_at_missing_v1_suffix() -> None:
    with pytest.raises(AiProviderConfigError, match="muss mit /v1 enden"):
        _ai_client(httpx.Response(404, content=b"not found")).check_connection()


def test_ai_provider_reports_unreachable_server() -> None:
    with pytest.raises(AiProviderError, match="nicht erreichbar"):
        _ai_client(httpx.ConnectError("connection refused")).check_connection()


def test_ai_provider_reports_timeout() -> None:
    with pytest.raises(AiProviderError, match="Zeitüberschreitung"):
        _ai_client(httpx.ReadTimeout("too slow")).check_connection()


def test_ai_provider_rejects_empty_model_list() -> None:
    with pytest.raises(AiProviderConfigError, match="keine Modelle"):
        _ai_client(_models_response()).check_connection()


def test_ai_provider_rejects_malformed_body() -> None:
    broken = httpx.Response(200, content=b"kein json")
    with pytest.raises(AiProviderError, match="nicht lesbare"):
        _ai_client(broken).check_connection()

    without_data = httpx.Response(200, content=json.dumps({"object": "list"}).encode())
    with pytest.raises(AiProviderError, match="keine Modellliste"):
        _ai_client(without_data).check_connection()


def test_ai_provider_reads_ollama_style_names() -> None:
    payload = {"object": "list", "data": [{"name": "llama3.2:latest"}, {"name": "mistral"}]}
    client = AiProviderClient("http://localhost:11434", "mistral")
    client._client = httpx.Client(
        transport=_FakeAiTransport(httpx.Response(200, content=json.dumps(payload).encode())),
        timeout=5.0,
    )
    assert client.check_connection().model_id == "mistral"


def test_ai_provider_endpoint_connects_on_success(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(app_client)
    integration_id = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "Lokales Ollama",
            "config": {"base_url": "http://localhost:11434", "model": "llama3.2"},
            "credentials": {},
        },
    ).json()["id"]

    identity = AiProviderModels(model_id="llama3.2:latest", available=("llama3.2:latest",), auto_selected=False)
    monkeypatch.setattr(integrations_service, "probe_ai_provider", lambda **_: identity)

    body = app_client.post(f"/api/v1/integrations/{integration_id}/test").json()
    assert body["ok"] is True
    assert body["status"] == "connected"
    assert "llama3.2:latest" in body["message"]

    listed = {item["id"]: item for item in app_client.get("/api/v1/integrations").json()}
    assert listed[integration_id]["status"] == "connected"
    assert listed[integration_id]["last_error"] is None


def test_ai_provider_endpoint_stores_failure_reason(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(app_client)
    integration_id = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "Kaputter Anbieter",
            "config": {"base_url": "https://api.example.com/v1", "model": "gpt-4o"},
            "credentials": {"api_key": "falsch"},
        },
    ).json()["id"]

    def boom(**_: object) -> AiProviderModels:
        raise AiProviderAuthError("Der KI-Provider hat den API-Schlüssel abgelehnt oder erwartet keinen.")

    monkeypatch.setattr(integrations_service, "probe_ai_provider", boom)

    body = app_client.post(f"/api/v1/integrations/{integration_id}/test").json()
    assert body["ok"] is False
    assert body["status"] == "error"
    assert "API-Schlüssel" in body["message"]

    listed = {item["id"]: item for item in app_client.get("/api/v1/integrations").json()}
    assert listed[integration_id]["last_error"] == body["message"]


def test_ai_provider_create_requires_model(app_client: TestClient) -> None:
    login(app_client)
    incomplete = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "Ohne Modell",
            "config": {"base_url": "https://api.example.com/v1"},
        },
    )
    assert incomplete.status_code == 422
    assert "model" in incomplete.json()["detail"]


def test_ai_provider_create_accepts_missing_api_key(app_client: TestClient) -> None:
    login(app_client)
    created = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "Lokales Modell ohne Schlüssel",
            "config": {"base_url": "http://localhost:11434", "model": "llama3.2"},
        },
    )
    assert created.status_code == 201
    assert created.json()["missing_fields"] == []


@pytest.mark.parametrize(
    ("base_url", "school", "expected"),
    [
        ("https://schule.webuntis.com", "schule", "https://schule.webuntis.com/WebUntis/jsonrpc.do?school=schule"),
        ("https://schule.webuntis.com/", "schule", "https://schule.webuntis.com/WebUntis/jsonrpc.do?school=schule"),
        (
            "https://schule.webuntis.com/WebUntis",
            "schule",
            "https://schule.webuntis.com/WebUntis/jsonrpc.do?school=schule",
        ),
        (
            "https://schule.webuntis.com/WebUntis/",
            "schule",
            "https://schule.webuntis.com/WebUntis/jsonrpc.do?school=schule",
        ),
        ("http://localhost:8080", "schule", "http://localhost:8080/WebUntis/jsonrpc.do?school=schule"),
        (
            "https://schule.webuntis.com",
            "meine schule",
            "https://schule.webuntis.com/WebUntis/jsonrpc.do?school=meine%20schule",
        ),
        (
            "https://schule.webuntis.com",
            "schule/../admin",
            "https://schule.webuntis.com/WebUntis/jsonrpc.do?school=schule%2F..%2Fadmin",
        ),
    ],
)
def test_build_rpc_url(base_url: str, school: str, expected: str) -> None:
    assert build_rpc_url(base_url, school) == expected


@pytest.mark.parametrize("base_url", ["ftp://schule.webuntis.com", "schule.webuntis.com", "https://x/webuntis/app"])
def test_build_rpc_url_rejects_bad_input(base_url: str) -> None:
    with pytest.raises(WebUntisConfigError):
        build_rpc_url(base_url, "schule")


def test_webuntis_check_connection_logs_in_and_out() -> None:
    transport = _FakeWebUntisTransport(
        {
            "authenticate": _rpc({"sessionId": "ABC123", "personId": 4711, "personType": 1, "klasseId": 108}),
            "logout": _rpc({}),
        }
    )
    client = _client(transport)
    identity = client.check_connection()

    assert identity.person_id == 4711
    assert identity.klasse_id == 108
    assert "Person-ID 4711" in identity.describe()

    methods = [json.loads(request.read().decode("utf-8"))["method"] for request in transport.requests]
    assert methods == ["authenticate", "logout"]

    authenticate = json.loads(transport.requests[0].read().decode("utf-8"))
    assert authenticate["params"] == {"user": "nutzer", "password": "geheim", "client": "school-assistant/1.0"}
    assert authenticate["jsonrpc"] == "2.0"
    assert transport.requests[0].headers["X-Requested-With"] == "XMLHttpRequest"
    assert "school=schule" in str(transport.requests[0].url)


@pytest.mark.parametrize("code", [-8520, -8521, -8527])
def test_webuntis_rejects_bad_credentials(code: int) -> None:
    transport = _FakeWebUntisTransport({"authenticate": _rpc({"code": code, "message": "denied"})})
    with pytest.raises(WebUntisAuthError, match="abgelehnt"):
        _client(transport).check_connection()


def test_webuntis_reports_expired_session() -> None:
    transport = _FakeWebUntisTransport({"authenticate": _rpc({"code": -8509, "message": "timeout"})})
    with pytest.raises(WebUntisError, match="abgelaufen"):
        _client(transport).check_connection()


def test_webuntis_reports_unknown_school_with_suggestion() -> None:
    error = httpx.Response(
        404, content=json.dumps({"id": "error", "error": {"code": -8500, "message": "invalid schoolname"}}).encode()
    )
    with pytest.raises(WebUntisConfigError, match="kennt die Schule 'schule' nicht"):
        _client(_FakeWebUntisTransport({"authenticate": error})).check_connection()

    client = WebUntisClient("https://schule.webuntis.com", "Lessing-Gymnasium Karlsruhe", "u", "p")
    client._client = httpx.Client(transport=_FakeWebUntisTransport({"authenticate": error}), timeout=5.0)
    with pytest.raises(WebUntisConfigError, match="lessinggymnasiumkarlsruhe"):
        client.check_connection()


def test_webuntis_uses_jsonrpc_error_from_4xx_body() -> None:
    bad_credentials = httpx.Response(
        401, content=json.dumps({"error": {"code": -8520, "message": "denied"}}).encode()
    )
    with pytest.raises(WebUntisAuthError):
        _client(_FakeWebUntisTransport({"authenticate": bad_credentials})).check_connection()


def test_webuntis_reports_unreachable_and_timeouts() -> None:
    with pytest.raises(WebUntisError, match="Zeitüberschreitung"):
        _client(_FakeWebUntisTransport({"authenticate": httpx.ConnectTimeout("too slow")})).check_connection()
    with pytest.raises(WebUntisError, match="nicht erreichbar"):
        _client(_FakeWebUntisTransport({"authenticate": httpx.ConnectError("refused")})).check_connection()


def test_webuntis_rejects_bad_address_and_payload() -> None:
    with pytest.raises(WebUntisConfigError, match="Adresse wurde abgelehnt"):
        _client(_FakeWebUntisTransport({"authenticate": _rpc({}, status_code=404)})).check_connection()
    with pytest.raises(WebUntisError, match="nicht lesbar"):
        _client(
            _FakeWebUntisTransport({"authenticate": httpx.Response(200, content=b"<html>login</html>")})
        ).check_connection()
    with pytest.raises(WebUntisError, match="keine Sitzung"):
        _client(_FakeWebUntisTransport({"authenticate": _rpc({"personId": 1})})).check_connection()


def test_webuntis_surfaces_jsonrpc_error_object() -> None:
    error = httpx.Response(200, content=json.dumps({"error": {"code": -32600, "message": "Invalid Request"}}).encode())
    with pytest.raises(WebUntisError, match="-32600"):
        _client(_FakeWebUntisTransport({"authenticate": error})).check_connection()


def test_webuntis_logs_out_even_when_followup_fails() -> None:
    transport = _FakeWebUntisTransport(
        {
            "authenticate": _rpc({"sessionId": "ABC"}),
            "logout": httpx.Response(500, content=b"boom"),
        }
    )
    assert _client(transport).check_connection().describe() == "Session erfolgreich aufgebaut"
    assert len(transport.requests) == 2


# --- Stundenplan-Import ------------------------------------------------------

_TIMETABLE_DAY = date(2026, 9, 25)


def _lesson_payload(**overrides: object) -> dict[str, object]:
    """Ein Eintrag in der Gestalt, die WebUntis wirklich liefert."""
    entry: dict[str, object] = {
        "id": 1424881,
        "date": 20260925,
        "startTime": 935,
        "endTime": 1020,
        "kl": [{"id": 656, "name": "10b", "longname": "Domin/Zepfel"}],
        "te": [{"id": 299, "name": "Stü", "longname": "Stürmer, Frank"}],
        "su": [{"id": 34, "name": "Geo", "longname": "Geographie - Ek"}],
        "ro": [{"id": 120, "name": "322", "longname": "10b"}],
        "lsnumber": 13800,
        "lstext": "1.HJ",
        "activityType": "Unterricht",
    }
    entry.update(overrides)
    return entry


def test_webuntis_client_reads_timetable_and_sends_element() -> None:
    transport = _FakeWebUntisTransport(
        {
            "authenticate": _rpc({"sessionId": "ABC", "personId": 4723, "personType": 5, "klasseId": 656}),
            "getTimetable": _rpc([_lesson_payload()]),
        }
    )

    timetable = _client(transport).fetch_timetable(_TIMETABLE_DAY, date(2026, 9, 25))

    assert len(timetable.entries) == 1
    assert timetable.skipped == 0
    entry = timetable.entries[0]
    assert entry.external_id == "1424881"
    assert entry.day == _TIMETABLE_DAY
    assert (entry.start.hour, entry.start.minute) == (9, 35)
    assert (entry.end.hour, entry.end.minute) == (10, 20)
    assert entry.subjects[0].longname == "Geographie - Ek"
    assert entry.rooms[0].name == "322"
    assert entry.lesson_text == "1.HJ"

    # Die Person aus dem Login muss als Element-Referenz mitgeschickt werden.
    options = json.loads(transport.requests[1].read())["params"]["options"]
    assert options["element"] == {"id": 4723, "type": 5}
    assert options["startDate"] == "20260925"
    assert options["endDate"] == "20260925"
    assert options["subjectFields"] == ["id", "name", "longname", "externalkey"]
    assert transport.requests[1].headers.get("cookie") == "JSESSIONID=ABC"

    # Und die Sitzung wird wieder abgegeben.
    assert json.loads(transport.requests[2].read())["method"] == "logout"


def test_webuntis_timetable_skips_incomplete_entries() -> None:
    payload = [
        _lesson_payload(),
        _lesson_payload(id=2, date=20260926, endTime=800),  # Ende vor Beginn
        _lesson_payload(id=3, date=2026),  # kein Datum
        _lesson_payload(id=4, startTime=9999),  # keine Uhrzeit
        "kein Objekt",
    ]
    timetable = parse_timetable(payload, _TIMETABLE_DAY, _TIMETABLE_DAY)

    assert [entry.external_id for entry in timetable.entries] == ["1424881"]
    assert timetable.skipped == 4


def test_webuntis_timetable_accepts_missing_optional_fields() -> None:
    bare = {
        "id": 7,
        "date": "20260925",
        "startTime": 745,
        "endTime": "830",
        "kl": [],
        "te": [],
        "su": [{"id": 34, "name": "Geo"}],
        "ro": [],
    }
    timetable = parse_timetable([bare], _TIMETABLE_DAY, _TIMETABLE_DAY)

    assert timetable.skipped == 0
    entry = timetable.entries[0]
    assert (entry.start.hour, entry.start.minute) == (7, 45)
    assert entry.classes == ()
    # Ohne longname und ohne activityType bleiben sinnvolle Ersatzwerte.
    assert entry.subjects[0].display(prefer_long=True) == "Geo"
    assert entry.activity_type == "Unterricht"


def test_webuntis_timetable_reports_missing_element() -> None:
    error = httpx.Response(
        200, content=json.dumps({"error": {"code": -7002, "message": "no such element"}}).encode()
    )
    transport = _FakeWebUntisTransport(
        {
            "authenticate": _rpc({"sessionId": "ABC", "personId": 4723, "personType": 5}),
            "getTimetable": error,
        }
    )
    with pytest.raises(WebUntisConfigError, match="kein Element"):
        _client(transport).fetch_timetable(_TIMETABLE_DAY, _TIMETABLE_DAY)
    assert json.loads(transport.requests[2].read())["method"] == "logout"


def test_webuntis_timetable_without_person_is_rejected() -> None:
    transport = _FakeWebUntisTransport({"authenticate": _rpc({"sessionId": "ABC"})})
    with pytest.raises(WebUntisError, match="keine Person"):
        _client(transport).fetch_timetable(_TIMETABLE_DAY, _TIMETABLE_DAY)


@pytest.mark.parametrize(
    ("today", "expected_start", "expected_end"),
    [
        (date(2026, 9, 26), date(2026, 9, 21), date(2026, 10, 18)),
        (date(2026, 9, 21), date(2026, 9, 21), date(2026, 10, 18)),
        (date(2026, 10, 1), date(2026, 9, 28), date(2026, 10, 25)),
    ],
)
def test_sync_window_starts_on_monday_and_covers_weeks(
    today: date, expected_start: date, expected_end: date
) -> None:
    assert sync_window(today, 4) == (expected_start, expected_end)


class _StubWebUntisClient:
    """Tritt an die Stelle des echten Clients und liefert einen festen Plan."""

    payload: list[object] = []
    failure: Exception | None = None
    requested: list[tuple[date, date]] = []

    def __init__(self, base_url: str, school: str, username: str, password: str, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url

    def __enter__(self) -> _StubWebUntisClient:
        return self
    def __exit__(self, *_: object) -> None:
        return None

    def fetch_timetable(self, start: date, end: date) -> WebUntisTimetable:
        type(self).requested.append((start, end))
        if type(self).failure is not None:
            raise type(self).failure
        return parse_timetable(type(self).payload, start, end)


@pytest.fixture
def stub_webuntis(monkeypatch: pytest.MonkeyPatch) -> type[_StubWebUntisClient]:
    _StubWebUntisClient.payload = [_lesson_payload()]
    _StubWebUntisClient.failure = None
    _StubWebUntisClient.requested = []
    monkeypatch.setattr(integrations_service, "WebUntisClient", _StubWebUntisClient)
    return _StubWebUntisClient


def test_sync_endpoint_imports_timetable_into_dashboard(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    login(client)
    integration_id = _create_webuntis(client)

    result = client.post(f"/api/v1/integrations/{integration_id}/sync")
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is True
    assert body["imported_lessons"] == 1
    assert body["subjects"] == 1
    assert body["teachers"] == 1
    assert body["rooms"] == 1
    assert body["window_start"] == "2026-09-21"
    assert body["window_end"] == "2026-10-18"
    assert "1 Unterrichtseinheiten" in body["message"]

    # Fächer, Lehrer und Räume werden mit sprechenden Namen angelegt.
    assert db.scalars(select(Subject)).all()[0].name == "Geographie - Ek"
    # ... und bekommen zusätzlich das Kuerzel, damit der Plan einzeilig bleibt.
    assert db.scalars(select(Subject)).all()[0].short_name == "Geo"
    assert db.scalars(select(Teacher)).all()[0].name == "Stürmer, Frank"
    # Räume behalten ihre Nummer, auch wenn WebUntis ein seltsames longname liefert.
    assert db.scalars(select(Room)).all()[0].name == "322"

    lesson = db.scalars(select(Lesson)).one()
    assert lesson.source == "webuntis"
    assert lesson.external_id == "1424881"
    assert lesson.title == "1.HJ"
    # Neu angelegte Stammdaten brauchen einen Flush, sonst bleibt der Verweis leer.
    assert lesson.subject_id is not None
    assert lesson.teacher_id is not None
    assert lesson.room_id is not None
    # 09:35 Schulzeit in Europe/Berlin ist 07:35 UTC.
    assert as_utc(lesson.start_time) == datetime(2026, 9, 25, 7, 35, tzinfo=timezone.utc)
    assert as_utc(lesson.end_time) == datetime(2026, 9, 25, 8, 20, tzinfo=timezone.utc)

    assert stub_webuntis.requested == [(date(2026, 9, 21), date(2026, 10, 18))]

    # Das Dashboard zeigt die Einheit, sobald der Tag erreicht ist.
    assert client.get("/api/v1/dashboard").json()["lessons"] == []
    source = db.get(SyncSource, integration_id)
    assert source is not None and source.last_sync_at is not None


def test_dashboard_shows_imported_lessons_on_a_school_day(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    """Der Import landet so im Dashboard, wie die Oberfläche es erwartet."""
    client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    login(client)
    integration_id = _create_webuntis(client)
    assert client.post(f"/api/v1/integrations/{integration_id}/sync").json()["ok"] is True

    # Der Sync holt die Wochen um den heutigen Tag; der Plan liegt auf dem 25.09.
    # Das Dashboard wird deshalb mit einem festen "jetzt" auf diesen Freitag
    # gesetzt, statt auf das echte Datum des Testlaufs zu vertrauen.
    settings = Settings(secret_key="test-secret-key", timezone="Europe/Berlin")
    friday = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    payload = serialize_dashboard(db, user.id, settings, now=friday, user_timezone="Europe/Berlin")

    assert payload.timezone == "Europe/Berlin"
    assert payload.summary.total_lessons == 1
    assert payload.summary.completed_lessons == 1
    assert payload.summary.day_progress_percent == 100.0

    lesson = payload.lessons[0]
    assert lesson.subject_name == "Geographie - Ek"
    assert lesson.subject_short_name == "Geo"
    assert lesson.teacher_name == "Stürmer, Frank"
    assert lesson.room_name == "322"
    assert lesson.title == "1.HJ"
    # 09:35 Schulzeit erscheint als UTC-Zeitpunkt, wie es der Client erwartet.
    assert lesson.start_time == datetime(2026, 9, 25, 7, 35, tzinfo=timezone.utc)
    assert lesson.end_time == datetime(2026, 9, 25, 8, 20, tzinfo=timezone.utc)
    assert lesson.is_current is False
    # Ohne `code` im Portal ist nichts ausgefallen.
    assert lesson.is_cancelled is False


def test_import_marks_cancelled_lessons_from_the_code_field(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    """WebUntis kennzeichnet Ausfall nur über ``code``; live steht dort "cancelled"."""
    client, db, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)
    stub_webuntis.payload = [_lesson_payload(code="cancelled")]

    assert client.post(f"/api/v1/integrations/{integration_id}/sync").json()["ok"] is True

    lesson = db.scalars(select(Lesson)).one()
    assert lesson.is_cancelled is True


def test_import_keeps_a_foreign_code_as_a_normal_lesson(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    """Ein unbekannter ``code`` ist kein Entfall - nur genau "cancelled" zählt."""
    client, db, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)
    stub_webuntis.payload = [_lesson_payload(code="roomChanged")]

    assert client.post(f"/api/v1/integrations/{integration_id}/sync").json()["ok"] is True

    assert db.scalars(select(Lesson)).one().is_cancelled is False


def test_lessons_endpoint_exposes_cancellation_and_short_name(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    login(client)
    integration_id = _create_webuntis(client)
    stub_webuntis.payload = [_lesson_payload(code="cancelled")]
    assert client.post(f"/api/v1/integrations/{integration_id}/sync").json()["ok"] is True

    body = client.get("/api/v1/lessons?start=2026-09-25&end=2026-09-25").json()
    lesson = body["lessons"][0]
    assert lesson["subject_name"] == "Geographie - Ek"
    assert lesson["subject_short_name"] == "Geo"
    assert lesson["is_cancelled"] is True


def test_import_backfills_short_name_for_subjects_from_earlier_syncs(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    """Aeltere Faecher wurden ueber den Namen wiederverwendet und nie befuellt."""
    client, db, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)
    assert client.post(f"/api/v1/integrations/{integration_id}/sync").json()["ok"] is True

    # So sieht ein Fach aus, das vor dem Kuerzel-Feld importiert wurde.
    subject = db.scalars(select(Subject)).one()
    subject.short_name = None
    db.commit()
    assert subject.short_name is None

    # Der naechste Sync traegt das Kuerzel nach, statt es zu ueberschreiben.
    assert client.post(f"/api/v1/integrations/{integration_id}/sync").json()["ok"] is True
    # Die laufende Test-Session hat die Zeile noch im Identity-Map-Cache aus
    # dem ersten Sync; ohne expire_all() wuerde der alte Wert gelesen.
    db.expire_all()
    assert db.scalars(select(Subject)).one().short_name == "Geo"


def test_import_keeps_an_existing_short_name(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    """Von Hand gepflegte Kuerzel werden nicht ueberschrieben."""
    client, db, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)
    assert client.post(f"/api/v1/integrations/{integration_id}/sync").json()["ok"] is True

    subject = db.scalars(select(Subject)).one()
    subject.short_name = "EK"
    db.commit()

    assert client.post(f"/api/v1/integrations/{integration_id}/sync").json()["ok"] is True
    db.expire_all()
    assert db.scalars(select(Subject)).one().short_name == "EK"


def test_sync_is_idempotent_and_replaces_the_window(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    client, db, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)

    first = client.post(f"/api/v1/integrations/{integration_id}/sync").json()
    assert first["imported_lessons"] == 1

    # Ein zweiter Lauf ersetzt statt zu verdoppeln.
    second = client.post(f"/api/v1/integrations/{integration_id}/sync").json()
    assert second["imported_lessons"] == 1
    assert db.query(Lesson).count() == 1

    # Eine in WebUntis gelöschte Stunde verschwindet beim nächsten Sync.
    stub_webuntis.payload = [_lesson_payload(id=1424882, startTime=1040, endTime=1125)]
    third = client.post(f"/api/v1/integrations/{integration_id}/sync").json()
    assert third["imported_lessons"] == 1
    assert db.query(Lesson).count() == 1
    assert db.scalars(select(Lesson)).one().external_id == "1424882"


def test_sync_keeps_lessons_outside_the_window(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    client, db, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)

    client.post(f"/api/v1/integrations/{integration_id}/sync")

    # Eine bewusst weit in der Zukunft liegende Einheit darf nicht mitgelöscht werden.
    outside = Lesson(
        user_id=1,
        start_time=datetime(2027, 3, 1, 8, 0, tzinfo=timezone.utc),
        end_time=datetime(2027, 3, 1, 8, 45, tzinfo=timezone.utc),
        source="webuntis",
        external_id="ausserhalb",
    )
    db.add(outside)
    db.commit()

    client.post(f"/api/v1/integrations/{integration_id}/sync")
    assert db.get(Lesson, outside.id) is not None


def test_sync_keeps_manual_lessons(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    client, db, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)

    manual = Lesson(
        user_id=1,
        title="Eigene Vorlesung",
        start_time=datetime(2026, 9, 25, 9, 35, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 25, 10, 20, tzinfo=timezone.utc),
    )
    db.add(manual)
    db.commit()

    client.post(f"/api/v1/integrations/{integration_id}/sync")
    assert db.get(Lesson, manual.id) is not None
    assert db.query(Lesson).count() == 2


def test_sync_reuses_existing_subject_with_same_name(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    client, db, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)

    # Von Hand angelegt, weil die Datenbank je Nutzer und Name eindeutig ist.
    existing = Subject(user_id=1, name="Geographie - Ek")
    db.add(existing)
    db.commit()

    result = client.post(f"/api/v1/integrations/{integration_id}/sync")
    assert result.status_code == 200
    assert result.json()["ok"] is True
    assert db.query(Subject).count() == 1
    assert db.scalars(select(Lesson)).one().subject_id == existing.id


def test_sync_links_repeated_entities_only_once(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    """Dieselbe Fach-ID in mehreren Stunden darf kein Duplikat erzeugen."""
    client, db, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)
    stub_webuntis.payload = [
        _lesson_payload(id=1, startTime=745, endTime=830),
        _lesson_payload(id=2, startTime=835, endTime=920),
        _lesson_payload(id=3, startTime=935, endTime=1020),
    ]

    result = client.post(f"/api/v1/integrations/{integration_id}/sync").json()
    assert result["imported_lessons"] == 3
    assert result["subjects"] == 1
    assert result["teachers"] == 1
    assert result["rooms"] == 1
    assert db.query(Subject).count() == 1
    assert db.query(Teacher).count() == 1
    assert db.query(Room).count() == 1
    assert len({lesson.subject_id for lesson in db.scalars(select(Lesson)).all()}) == 1


def test_sync_keeps_lessons_without_subject(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    """Eine Freistunde ohne Fach, Lehrer und Raum muss importiert werden."""
    client, db, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)
    stub_webuntis.payload = [
        {
            "id": 99,
            "date": 20260925,
            "startTime": 1310,
            "endTime": 1355,
            "kl": [],
            "te": [],
            "su": [],
            "ro": [],
            "activityType": "Freistunde",
        }
    ]

    result = client.post(f"/api/v1/integrations/{integration_id}/sync").json()
    assert result["imported_lessons"] == 1
    assert result["subjects"] == 0
    lesson = db.scalars(select(Lesson)).one()
    assert lesson.subject_id is None
    assert lesson.teacher_id is None
    assert lesson.room_id is None


def test_sync_endpoint_reports_connection_failure(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    client, db, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)
    stub_webuntis.failure = WebUntisAuthError("WebUntis hat Benutzername oder Passwort abgelehnt.")

    result = client.post(f"/api/v1/integrations/{integration_id}/sync")
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is False
    assert body["status"] == "error"
    assert "abgelehnt" in body["message"]
    assert db.query(Lesson).count() == 0

    listed = {item["id"]: item for item in client.get("/api/v1/integrations").json()}
    assert listed[integration_id]["status"] == "error"
    assert "abgelehnt" in listed[integration_id]["last_error"]


def test_sync_endpoint_skips_disabled_and_incomplete_integrations(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    client, db, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)
    client.patch(f"/api/v1/integrations/{integration_id}", json={"enabled": False})

    disabled = client.post(f"/api/v1/integrations/{integration_id}/sync").json()
    assert disabled["ok"] is False
    assert disabled["status"] == "disabled"

    incomplete = client.post(
        "/api/v1/integrations",
        json={"kind": "webuntis", "name": "Ohne Portal", "config": {"base_url": "https://schule.webuntis.com"}},
    )
    assert incomplete.status_code == 422
    assert db.query(Lesson).count() == 0


def test_sync_endpoint_requires_authentication(app_client: TestClient) -> None:
    assert app_client.post("/api/v1/integrations/1/sync").status_code == 401


def test_sync_endpoint_requires_encryption_key(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = app_context
    login(client)
    integration_id = _create_webuntis(client)
    monkeypatch.setattr(
        "app.api.integrations.require_integration_encryption_key",
        lambda _settings: (_ for _ in ()).throw(RuntimeError("missing")),
    )
    result = client.post(f"/api/v1/integrations/{integration_id}/sync")
    assert result.status_code == 503


def test_sync_rejects_unknown_kind(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    source = SyncSource(
        user_id=user.id, source_type="documents", name="Ablage", configuration={"path": tmp_path_value()}
    )
    db.add(source)
    db.commit()
    with pytest.raises(IntegrationConnectionError, match="noch kein Import"):
        sync_integration(
            db=db,
            source=source,
            config={"path": tmp_path_value()},
            credentials={},
            timezone_name="UTC",
            timeout_seconds=5.0,
        )


def tmp_path_value() -> str:
    return str(Path.cwd())


# --- Stundenplan über einen Zeitraum -----------------------------------------


def test_lesson_routes_are_registered(app_client: TestClient) -> None:
    assert "/api/v1/lessons" in {route.path for route in app_client.app.routes}


def test_lessons_require_authentication(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/lessons").status_code == 401


def _add_lesson(db: Session, user: User, day: date, hour: int, minute: int, title: str) -> Lesson:
    """Legt eine Stunde so ab, wie sie in der Schul-Ortszeit stattfindet."""
    zone = ZoneInfo("Europe/Berlin")
    start = datetime.combine(day, time(hour, minute), tzinfo=zone)
    lesson = Lesson(
        user_id=user.id,
        title=title,
        start_time=as_utc(start),
        end_time=as_utc(start + timedelta(minutes=45)),
        source="webuntis",
        external_id=f"{day.isoformat()}-{hour}-{minute}",
    )
    db.add(lesson)
    db.commit()
    db.refresh(lesson)
    return lesson


def test_lessons_return_only_the_requested_range(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    _add_lesson(db, user, date(2026, 9, 21), 8, 0, "Montag")
    _add_lesson(db, user, date(2026, 9, 25), 9, 35, "Freitag")
    _add_lesson(db, user, date(2026, 10, 2), 11, 0, "Nächste Woche")

    login(client)
    response = client.get("/api/v1/lessons", params={"start": "2026-09-21", "end": "2026-09-27"})
    assert response.status_code == 200
    body = response.json()

    assert set(body) == {"generated_at", "timezone", "range_start", "range_end", "lessons"}
    assert body["timezone"] == "Europe/Berlin"
    assert body["range_start"] == "2026-09-21"
    assert body["range_end"] == "2026-09-27"
    # Die Woche draußen bleibt draußen, und zwar an beiden Grenzen.
    assert [lesson["title"] for lesson in body["lessons"]] == ["Montag", "Freitag"]
    assert [lesson["start_time"] for lesson in body["lessons"]] == [
        "2026-09-21T06:00:00Z",
        "2026-09-25T07:35:00Z",
    ]

    # Ein einzelner Tag lässt die Nachbartage aus.
    single = client.get("/api/v1/lessons", params={"start": "2026-09-25"}).json()
    assert [lesson["title"] for lesson in single["lessons"]] == ["Freitag"]
    assert single["range_start"] == single["range_end"] == "2026-09-25"

    # 09:35 Schulzeit ist 07:35 UTC - der Tag darf dadurch nicht kippen.
    assert body["lessons"][1]["is_current"] is False


def test_lessons_are_isolated_per_user(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    _add_lesson(db, user, date(2026, 9, 21), 8, 0, "Meine Stunde")

    other = User(
        username="other-lessons",
        password_hash=hash_password("other-password"),
        is_active=True,
        is_admin=False,
        timezone="UTC",
    )
    db.add(other)
    db.commit()
    db.refresh(other)

    client.cookies.clear()
    headers = {"Authorization": f"Bearer {create_session_token(other.id, 'test-secret-key', 3600)}"}
    response = client.get(
        "/api/v1/lessons",
        params={"start": "2026-09-21", "end": "2026-09-27"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["lessons"] == []
    assert response.json()["timezone"] == "UTC"


def test_lessons_reject_inverted_and_oversized_ranges(app_client: TestClient) -> None:
    login(app_client)

    inverted = app_client.get("/api/v1/lessons", params={"start": "2026-09-27", "end": "2026-09-21"})
    assert inverted.status_code == 422
    assert "Ende" in inverted.json()["detail"]

    oversized = app_client.get("/api/v1/lessons", params={"start": "2026-01-01", "end": "2026-12-31"})
    assert oversized.status_code == 422
    assert str(MAX_RANGE_DAYS) in oversized.json()["detail"]


def test_lesson_range_helper_guards_the_window() -> None:
    validate_range(date(2026, 9, 21), date(2026, 9, 27))
    with pytest.raises(LessonRangeError, match="vor dem Anfang"):
        validate_range(date(2026, 9, 27), date(2026, 9, 21))
    with pytest.raises(LessonRangeError, match="höchstens"):
        validate_range(date(2026, 1, 1), date(2026, 6, 1))

# --- Willkommenssatz -------------------------------------------------------


def _create_ai_provider(client: TestClient, *, model: str = "gpt-4o-mini") -> int:
    created = client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "KI",
            "config": {"base_url": "https://api.example.com/v1", "model": model},
            "credentials": {"api_key": "geheim"},
            "enabled": True,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _chat_response(text: str) -> httpx.Response:
    body = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    return httpx.Response(200, content=json.dumps(body).encode())


def test_chat_url_is_built_from_the_base_url() -> None:
    assert build_chat_url("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"
    assert build_chat_url("http://localhost:11434") == "http://localhost:11434/v1/chat/completions"
    # Eine URL, die auf /models endet, darf nicht zu /models/chat/completions werden.
    assert build_chat_url("https://x.test/v1/models") == "https://x.test/v1/chat/completions"


def test_generate_reads_the_chat_completion() -> None:
    # Ein festes Modell, damit kein /models-Abruf davor laufen muss.
    transport = _FakeAiTransport(_chat_response("Herzlich willkommen, um 8:00 beginnt Mathe."))
    client = AiProviderClient("https://api.example.com/v1", "gpt-4o-mini", "geheim")
    client._client = httpx.Client(transport=transport, timeout=5.0)

    text = client.generate("System", "Daten", max_tokens=120)

    assert text == "Herzlich willkommen, um 8:00 beginnt Mathe."
    request = transport.requests[0]
    assert str(request.url) == "https://api.example.com/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer geheim"
    koerper = json.loads(request.read().decode("utf-8"))
    assert koerper["model"] == "gpt-4o-mini"
    assert koerper["messages"][0] == {"role": "system", "content": "System"}
    assert koerper["max_tokens"] == 120


def test_generate_resolves_auto_by_listing_models_first() -> None:
    """``Auto`` muss die Modellliste holen, sonst wüsste der Client nichts zu wählen."""

    class _BothEndpoints(httpx.BaseTransport):
        def __init__(self) -> None:
            self.requests: list[httpx.Request] = []

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.url.path.endswith("/models"):
                return httpx.Response(200, content=_models_response("llama3.2", "gpt-4o-mini").content, request=request)
            return httpx.Response(200, content=_chat_response("Hallo.").content, request=request)

    transport = _BothEndpoints()
    client = AiProviderClient("https://api.example.com/v1", "Auto", "geheim")
    client._client = httpx.Client(transport=transport, timeout=5.0)

    assert client.generate("S", "U") == "Hallo."
    assert [r.url.path for r in transport.requests] == ["/v1/models", "/v1/chat/completions"]
    assert json.loads(transport.requests[1].read().decode("utf-8"))["model"] == "llama3.2"


def test_generate_accepts_content_split_into_parts() -> None:
    body = {
        "choices": [
            {"message": {"content": [{"type": "text", "text": "Hallo "}, {"type": "text", "text": "welt!"}]}}
        ]
    }
    client = AiProviderClient("https://api.example.com/v1", "gpt-4o-mini", "geheim")
    client._client = httpx.Client(
        transport=_FakeAiTransport(httpx.Response(200, content=json.dumps(body).encode())), timeout=5.0
    )
    assert client.generate("S", "U") == "Hallo welt!"


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": {}}]}])
def test_generate_rejects_empty_answers(body: dict[str, object]) -> None:
    client = AiProviderClient("https://api.example.com/v1", "gpt-4o-mini", "geheim")
    client._client = httpx.Client(
        transport=_FakeAiTransport(httpx.Response(200, content=json.dumps(body).encode())), timeout=5.0
    )
    with pytest.raises(AiProviderError):
        client.generate("S", "U")


def test_greeting_falls_back_without_a_configured_provider(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Ohne Provider muss trotzdem ein Satz kommen - das Dashboard darf nie leer bleiben."""
    client, _, _ = app_context
    login(client)

    antwort = client.get("/api/v1/greeting")

    assert antwort.status_code == 200
    body = antwort.json()
    assert body["source"] == "fallback"
    assert body["text"].startswith("Herzlich willkommen")
    assert body["model"] is None


def test_greeting_uses_the_provider_when_configured(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db, _ = app_context
    login(client)
    _create_ai_provider(client)

    aufrufe: list[tuple[str, str]] = []

    def fake_generate(*, base_url: str, model: str, system_prompt: str, user_prompt: str, **kwargs: object) -> str:
        aufrufe.append((system_prompt, user_prompt))
        return "Herzlich willkommen, heute steht ein gro�er Tag an."

    monkeypatch.setattr("app.services.greeting.generate_text", fake_generate)

    body = client.get("/api/v1/greeting").json()

    assert body["source"] == "ai"
    assert body["text"] == "Herzlich willkommen, heute steht ein gro�er Tag an."
    assert body["model"] == "gpt-4o-mini"
    assert len(aufrufe) == 1
    assert "Herzlich willkommen" in aufrufe[0][0]
    # Der Prompt traegt echte Zahlen aus dem Dashboard, nicht Platzhalter.
    assert "Stunden heute:" in aufrufe[0][1]


def test_greeting_is_cached_per_day(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein zweiter Abruf am selben Tag darf keinen weiteren Modelllauf kosten."""
    client, _, _ = app_context
    login(client)
    _create_ai_provider(client)

    anzahl: list[int] = [0]

    def fake_generate(**kwargs: object) -> str:
        anzahl[0] += 1
        return f"Satz Nummer {anzahl[0]}."

    monkeypatch.setattr("app.services.greeting.generate_text", fake_generate)

    erster = client.get("/api/v1/greeting").json()
    zweiter = client.get("/api/v1/greeting").json()

    assert erster["text"] == zweiter["text"] == "Satz Nummer 1."
    assert anzahl[0] == 1


def test_greeting_refresh_forces_a_new_sentence(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = app_context
    login(client)
    _create_ai_provider(client)

    anzahl: list[int] = [0]

    def fake_generate(**kwargs: object) -> str:
        anzahl[0] += 1
        return f"Neu {anzahl[0]}."

    monkeypatch.setattr("app.services.greeting.generate_text", fake_generate)

    client.get("/api/v1/greeting")
    aktualisiert = client.post("/api/v1/greeting/refresh").json()
    # Ein GET danach liefert weiter den erneuerten Satz, nicht einen dritten.
    danach = client.get("/api/v1/greeting").json()

    assert aktualisiert["text"] == "Neu 2."
    assert danach["text"] == "Neu 2."
    assert anzahl[0] == 2


def test_greeting_survives_a_failing_provider(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = app_context
    login(client)
    _create_ai_provider(client)

    def kaputt(**kwargs: object) -> str:
        raise AiProviderError("Der KI-Provider ist nicht erreichbar: ConnectError")

    monkeypatch.setattr("app.services.greeting.generate_text", kaputt)

    antwort = client.get("/api/v1/greeting")

    assert antwort.status_code == 200
    body = antwort.json()
    assert body["source"] == "fallback"
    assert body["text"].startswith("Herzlich willkommen")


def test_greeting_strips_markdown_from_the_model(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = app_context
    login(client)
    _create_ai_provider(client)

    monkeypatch.setattr(
        "app.services.greeting.generate_text",
        lambda **kwargs: "**Herzlich willkommen.**\n- Mathe um 8:00",
    )

    body = client.get("/api/v1/greeting").json()

    assert body["text"] == "Herzlich willkommen. Mathe um 8:00"


def test_greeting_requires_authentication(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/greeting").status_code == 401
    assert app_client.post("/api/v1/greeting/refresh").status_code == 401

def test_greeting_fallback_shows_local_school_time(
    app_context: tuple[TestClient, Session, User], stub_webuntis: type[_StubWebUntisClient]
) -> None:
    """Die Uhrzeit im Satz ist Schulzeit, nicht UTC.

    Die Teststunde beginnt um 09:35 Europe/Berlin, was 07:35 UTC ist. Erscheint
    im Satz eine andere Uhrzeit als 09:35, wird in der falschen Zone gerechnet.
    """
    client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    login(client)
    integration_id = _create_webuntis(client)
    assert client.post(f"/api/v1/integrations/{integration_id}/sync").json()["ok"] is True

    from app.core.time import zone_or_utc
    from app.services.greeting import build_fallback_greeting

    settings = Settings(secret_key="test-secret-key", timezone="Europe/Berlin")
    # 07:00 UTC ist 09:00 Schulzeit, also steht die 09:35-Stunde noch aus.
    jetzt = datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc)
    daten = serialize_dashboard(db, user.id, settings, now=jetzt, user_timezone="Europe/Berlin")
    satz = build_fallback_greeting(daten, jetzt, zone_or_utc("Europe/Berlin"))

    assert "09:35" in satz, satz
    assert "07:35" not in satz, satz