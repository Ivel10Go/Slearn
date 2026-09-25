from datetime import datetime, timedelta, timezone

import pytest
from app.core.config import Settings, normalize_database_url
from app.core.security import create_session_token, hash_password
from app.db.entities import LearningProgress, StudyPlan, StudySession, Topic, User
from app.schemas.auth import UserRead
from app.services.demo_seed import seed_demo_data
from fastapi.testclient import TestClient
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
        ADMIN_PASSWORD="a-real-admin-password",
    )
    assert settings.environment == "production"
    assert settings.secret_key is not None
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
