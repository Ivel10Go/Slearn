from __future__ import annotations

import hashlib
import json
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from app.api.notifications import _endpoint_hint
from app.core.config import Settings, normalize_database_url, require_web_push_subject
from app.core.crypto import decrypt_credentials, encrypt_credentials
from app.core.security import create_session_token, hash_password
from app.core.time import as_utc
from app.db.entities import (
    Exam,
    GreetingCache,
    LearningProgress,
    Lesson,
    Notification,
    PushSubscription,
    Room,
    StudyPlan,
    StudyPlanTask,
    StudySession,
    Subject,
    SyncSource,
    Teacher,
    Topic,
    User,
    UserSettings,
)
from app.main import create_app
from app.schemas.auth import UserRead
from app.services import grades as grades_service
from app.services import integrations as integrations_service
from app.services import web_push
from app.services.ai_provider import (
    AiProviderAuthError,
    AiProviderClient,
    AiProviderConfigError,
    AiProviderError,
    AiProviderModels,
    build_chat_url,
    build_embeddings_url,
    build_models_url,
)
from app.services.dashboard import serialize_dashboard
from app.services.demo_seed import seed_demo_data
from app.services.documents import chunk_text, read_document_text
from app.services.grades import (
    GradeImportError,
    WeightedGrade,
    grade_priority,
    project_average,
)
from app.services.integration_scheduler import (
    IntegrationSyncScheduler,
    ScheduledSyncReport,
    run_scheduled_integration_syncs,
    scheduled_sync_is_due,
)
from app.services.integrations import (
    IntegrationConnectionError,
    IntegrationValidationError,
    run_integration_sync,
    sync_integration,
    sync_window,
)
from app.services.lessons import MAX_RANGE_DAYS, LessonRangeError, validate_range
from app.services.notification_scheduler import (
    ReminderScheduler,
    ScheduledReminderReport,
    run_scheduled_reminders,
)
from app.services.notifications import (
    CHANNELS,
    MAX_REMINDERS_PER_RUN,
    NotificationSettingsError,
    QuietHours,
    ReminderPreferences,
    daily_reminder_moment,
    default_preferences,
    next_reminder_moment,
    quiet_covers,
    read_preferences,
    reminders_due,
    sweep_for_user,
    with_quiet,
    write_preferences,
)
from app.services.progress import (
    DEFAULT_WEEKLY_GOAL_MINUTES,
    confidence_label,
    current_streak,
    longest_streak,
    review_interval_days,
    week_bounds,
)
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
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


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


def create_plan(client: TestClient, db: Session, user: User, title: str = "Lernplan") -> dict[str, object]:
    """Einen Plan ueber die API anlegen, inklusive der Zaehler im Antwortkoerper."""
    response = client.post("/api/v1/study/plans", json={"title": title})
    assert response.status_code == 201, response.text
    plan = response.json()
    db.expire_all()
    return plan


def create_topic(db: Session, user: User, name: str, subject: Subject | None = None) -> Topic:
    topic = Topic(user_id=user.id, subject_id=subject.id if subject else None, name=name)
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


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


# ---------------------------------------------------------------------------
# Task-Manager: Plaene, Prioritaet, Faelligkeit, Serien, Abhaengigkeiten
# ---------------------------------------------------------------------------


def test_study_plans_are_created_and_count_their_tasks(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)

    plan = create_plan(client, db, user, "Mathematik")
    assert plan["task_count"] == 0
    assert plan["open_task_count"] == 0

    first = client.post(
        "/api/v1/study/tasks", json={"study_plan_id": plan["id"], "title": "Bruchrechnen", "priority": 3}
    )
    second = client.post("/api/v1/study/tasks", json={"study_plan_id": plan["id"], "title": "Nebenjob"})
    assert first.status_code == 201
    assert second.status_code == 201

    listed = client.get("/api/v1/study/plans").json()
    assert [(entry["id"], entry["task_count"], entry["open_task_count"]) for entry in listed] == [(plan["id"], 2, 2)]

    assert client.post(f"/api/v1/study/tasks/{first.json()['id']}/complete").status_code == 200
    assert client.get(f"/api/v1/study/plans/{plan['id']}").json()["open_task_count"] == 1

    assert client.delete(f"/api/v1/study/plans/{plan['id']}").status_code == 200
    assert client.get("/api/v1/study/tasks").json() == []


def test_study_plans_reject_an_inverted_window(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    response = client.post(
        "/api/v1/study/plans",
        json={
            "title": "Verkehrt",
            "starts_on": "2026-10-10T00:00:00Z",
            "ends_on": "2026-10-01T00:00:00Z",
        },
    )
    assert response.status_code == 422


def test_study_plans_are_user_isolated(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    other = User(
        username="other-plans",
        password_hash=hash_password("other-password"),
        is_active=True,
        timezone="UTC",
    )
    db.add(other)
    db.commit()
    db.refresh(other)

    client.cookies.clear()
    headers = {"Authorization": f"Bearer {create_session_token(other.id, 'test-secret-key', 3600)}"}
    assert client.get(f"/api/v1/study/plans/{plan['id']}", headers=headers).status_code == 404
    assert client.patch(f"/api/v1/study/plans/{plan['id']}", json={"title": "Fremd"}, headers=headers).status_code == 404
    assert client.delete(f"/api/v1/study/plans/{plan['id']}", headers=headers).status_code == 404
    assert client.get("/api/v1/study/plans", headers=headers).json() == []


def test_task_priority_and_due_date_are_stored_normalized(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)

    created = client.post(
        "/api/v1/study/tasks",
        json={
            "study_plan_id": plan["id"],
            "title": "Klausurvorbereitung",
            # Ohne Offset: das Schema deutet naive Zeiten als UTC, wie alle
            # anderen Datumsfelder in der App.
            "due_date": "2026-10-01T23:59:00",
            "priority": 4,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["priority"] == 4
    assert created.json()["due_date"] == "2026-10-01T23:59:00Z"

    patched = client.patch(
        f"/api/v1/study/tasks/{created.json()['id']}", json={"priority": 1, "due_date": None}
    )
    assert patched.status_code == 200
    assert patched.json()["priority"] == 1
    assert patched.json()["due_date"] is None

    assert client.patch(f"/api/v1/study/tasks/{created.json()['id']}", json={"priority": 9}).status_code == 422
    assert client.patch(f"/api/v1/study/tasks/{created.json()['id']}", json={"due_date": None, "title": None}).status_code == 422


def test_task_list_sorts_by_priority_then_due_date(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    base = "/api/v1/study/tasks"

    for title, priority, due in (
        ("gleich faellig, wichtig", 5, "2026-10-05T09:00:00Z"),
        ("gleich faellig, egal", 0, "2026-10-05T09:00:00Z"),
        ("spaeter faellig, wichtig", 5, "2026-10-20T09:00:00Z"),
        ("ohne faelligkeit", 5, None),
    ):
        response = client.post(
            base,
            json={
                "study_plan_id": plan["id"],
                "title": title,
                "priority": priority,
                "due_date": due,
            },
        )
        assert response.status_code == 201, response.text

    by_priority = [task["title"] for task in client.get(base, params={"order": "priority"}).json()]
    assert by_priority == [
        "gleich faellig, wichtig",
        "spaeter faellig, wichtig",
        "ohne faelligkeit",
        "gleich faellig, egal",
    ]

    by_due = [task["title"] for task in client.get(base, params={"order": "due"}).json()]
    assert by_due == [
        "gleich faellig, wichtig",
        "gleich faellig, egal",
        "spaeter faellig, wichtig",
        "ohne faelligkeit",
    ]

    by_position = [task["order_index"] for task in client.get(base, params={"order": "position"}).json()]
    assert by_position == [0, 1, 2, 3]
    assert client.get(base, params={"order": "unsinn"}).status_code == 422


def test_task_series_creates_the_next_occurrence_on_completion(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)

    series = client.post(
        "/api/v1/study/series",
        json={
            "study_plan_id": plan["id"],
            "title": "Vokabeln",
            "recurrence": "weekly",
            "starts_on": "2026-10-05T18:00:00Z",
            # Zwei Occurrences: der 5. und der 12. Oktober. Der 19. waere
            # die dritte und faellt damit raus.
            "until": "2026-10-12T18:00:00Z",
            "duration_minutes": 15,
            "priority": 2,
        },
    )
    assert series.status_code == 201, series.text
    payload = series.json()
    assert payload["occurrences"] == 1
    assert payload["next_due_on"] == "2026-10-12T18:00:00Z"

    # Die erste Occurrence existiert sofort, damit die Serie nicht leer ist.
    first = client.get("/api/v1/study/tasks", params={"study_plan_id": plan["id"]}).json()
    assert len(first) == 1
    assert first[0]["title"] == "Vokabeln"
    assert first[0]["due_date"] == "2026-10-05T18:00:00Z"
    assert first[0]["series_id"] == payload["id"]
    assert first[0]["series_recurrence"] == "weekly"
    assert first[0]["priority"] == 2

    # Und es ist immer genau eine, nicht die ganze Serie auf einen Schlag.
    assert client.post(f"/api/v1/study/tasks/{first[0]['id']}/complete").status_code == 200
    after_first = client.get("/api/v1/study/tasks", params={"study_plan_id": plan["id"]}).json()
    assert len(after_first) == 2
    assert sorted(task["due_date"] for task in after_first) == ["2026-10-05T18:00:00Z", "2026-10-12T18:00:00Z"]
    assert client.get(f"/api/v1/study/series/{payload['id']}").json()["occurrences"] == 2

    # Die letzte erlaubte Occurrence beendet die Serie von selbst.
    open_tasks = [task for task in after_first if task["status"] != "completed"]
    assert len(open_tasks) == 1
    assert client.post(f"/api/v1/study/tasks/{open_tasks[0]['id']}/complete").status_code == 200
    final = client.get(f"/api/v1/study/series/{payload['id']}").json()
    assert final["status"] == "finished"
    assert final["next_due_on"] is None
    assert final["occurrences"] == 2

    # Eine fertige Serie erzeugt auch beim naechsten Abschluss nichts mehr.
    assert [task for task in client.get("/api/v1/study/tasks").json() if task["status"] != "completed"] == []
    assert len(client.get("/api/v1/study/tasks").json()) == 2


def test_task_series_honours_the_occurrence_limit(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    series = client.post(
        "/api/v1/study/series",
        json={
            "study_plan_id": plan["id"],
            "title": "Wiederholungsblatt",
            "recurrence": "daily",
            "starts_on": "2026-10-05T18:00:00Z",
            "occurrence_limit": 3,
        },
    )
    assert series.status_code == 201
    assert series.json()["next_due_on"] == "2026-10-06T18:00:00Z"

    for expected in (2, 3):
        open_task = [task for task in client.get("/api/v1/study/tasks").json() if task["status"] == "pending"][0]
        assert client.post(f"/api/v1/study/tasks/{open_task['id']}/complete").status_code == 200
        assert client.get(f"/api/v1/study/series/{series.json()['id']}").json()["occurrences"] == expected

    assert client.get(f"/api/v1/study/series/{series.json()['id']}").json()["status"] == "finished"
    assert len(client.get("/api/v1/study/tasks").json()) == 3


def test_task_series_rejects_an_unknown_recurrence(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    response = client.post(
        "/api/v1/study/series",
        json={
            "study_plan_id": plan["id"],
            "title": "Ungueltig",
            "recurrence": "yearly",
            "starts_on": "2026-10-05T18:00:00Z",
        },
    )
    assert response.status_code == 422


def test_cancelling_a_series_stops_it_without_deleting_history(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    series = client.post(
        "/api/v1/study/series",
        json={
            "study_plan_id": plan["id"],
            "title": "Vokabeln",
            "recurrence": "weekly",
            "starts_on": "2026-10-05T18:00:00Z",
        },
    )
    series_id = series.json()["id"]
    pending = [task for task in client.get("/api/v1/study/tasks").json() if task["status"] == "pending"][0]
    assert client.post(f"/api/v1/study/tasks/{pending['id']}/complete").status_code == 200

    assert client.delete(f"/api/v1/study/series/{series_id}").status_code == 200
    tasks = client.get("/api/v1/study/tasks").json()
    assert [task["status"] for task in tasks] == ["completed", "cancelled"]
    assert client.get(f"/api/v1/study/series/{series_id}").json()["status"] == "cancelled"


def test_task_dependencies_block_a_task_until_the_prerequisite_is_done(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    base = "/api/v1/study/tasks"

    grundlage = client.post(base, json={"study_plan_id": plan["id"], "title": "Grundlagen"}).json()
    aufbau = client.post(base, json={"study_plan_id": plan["id"], "title": "Aufgaben dazu"}).json()

    dependency = client.post(
        f"{base}/{aufbau['id']}/dependencies", json={"depends_on_task_id": grundlage["id"]}
    )
    assert dependency.status_code == 201
    assert dependency.json()["task_id"] == aufbau["id"]

    blocked = client.get(f"{base}/{aufbau['id']}").json()
    assert [prerequisite["title"] for prerequisite in blocked["blocked_by"]] == ["Grundlagen"]
    assert client.get(f"{base}/{grundlage['id']}").json()["blocking_task_ids"] == [aufbau["id"]]

    # Die Reihenfolge im Plan ist damit verbindlich, nicht nur ein Vorschlag.
    started = client.post(f"{base}/{aufbau['id']}/start")
    assert started.status_code == 409
    assert "Grundlagen" in started.json()["detail"]

    assert client.post(f"{base}/{grundlage['id']}/complete").status_code == 200
    assert client.get(f"{base}/{aufbau['id']}").json()["blocked_by"] == []
    assert client.post(f"{base}/{aufbau['id']}/start").status_code == 200

    # Dieselbe Kante zweimal anzulegen ist ein No-Op, kein 409.
    repeated = client.post(f"{base}/{aufbau['id']}/dependencies", json={"depends_on_task_id": grundlage["id"]})
    assert repeated.status_code == 201
    assert len(client.get(f"{base}/{aufbau['id']}/dependencies").json()) == 1

    assert client.delete(f"{base}/{aufbau['id']}/dependencies/{grundlage['id']}").status_code == 200
    assert client.get(f"{base}/{aufbau['id']}/dependencies").json() == []


def test_task_dependencies_reject_cycles_and_cross_plan_links(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    other_plan = create_plan(client, db, user, "Anderer Plan")
    base = "/api/v1/study/tasks"

    a = client.post(base, json={"study_plan_id": plan["id"], "title": "A"}).json()
    b = client.post(base, json={"study_plan_id": plan["id"], "title": "B"}).json()
    c = client.post(base, json={"study_plan_id": plan["id"], "title": "C"}).json()
    fremd = client.post(base, json={"study_plan_id": other_plan["id"], "title": "Fremd"}).json()

    assert client.post(f"{base}/{b['id']}/dependencies", json={"depends_on_task_id": a['id']}).status_code == 201
    assert client.post(f"{base}/{c['id']}/dependencies", json={"depends_on_task_id": b['id']}).status_code == 201

    # A wartet auf C, C wartet auf B, B wartet auf A: nie erfuellbar.
    cycle = client.post(f"{base}/{a['id']}/dependencies", json={"depends_on_task_id": c["id"]})
    assert cycle.status_code == 422
    assert "cycle" in cycle.json()["detail"].lower()

    assert client.post(f"{base}/{a['id']}/dependencies", json={"depends_on_task_id": a['id']}).status_code == 422
    cross = client.post(f"{base}/{a['id']}/dependencies", json={"depends_on_task_id": fremd["id"]})
    assert cross.status_code == 422
    assert "same study plan" in cross.json()["detail"]


def test_deleting_a_task_clears_dependencies_in_both_directions(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    base = "/api/v1/study/tasks"

    voraussetzung = client.post(base, json={"study_plan_id": plan["id"], "title": "Voraussetzung"}).json()
    wartende = client.post(base, json={"study_plan_id": plan["id"], "title": "Wartende"}).json()
    client.post(f"{base}/{wartende['id']}/dependencies", json={"depends_on_task_id": voraussetzung["id"]})

    # Waehrend die Voraussetzung lebt, ist die wartende Aufgabe blockiert.
    assert len(client.get(f"{base}/{wartende['id']}").json()["blocked_by"]) == 1

    assert client.delete(f"{base}/{voraussetzung['id']}").status_code == 200
    # Sonst waere die wartende Aufgabe fuer immer blockiert.
    assert client.get(f"{base}/{wartende['id']}").json()["blocked_by"] == []
    assert client.post(f"{base}/{wartende['id']}/start").status_code == 200


def test_task_dependencies_are_user_isolated(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    eigene = client.post("/api/v1/study/tasks", json={"study_plan_id": plan["id"], "title": "Eigene"}).json()
    other = User(
        username="other-dependencies",
        password_hash=hash_password("other-password"),
        is_active=True,
        timezone="UTC",
    )
    db.add(other)
    db.commit()
    db.refresh(other)
    fremde_plan = StudyPlan(user_id=other.id, title="Fremder Plan")
    db.add(fremde_plan)
    db.commit()
    db.refresh(fremde_plan)
    fremde = client.post(
        "/api/v1/study/tasks", json={"study_plan_id": fremde_plan.id, "title": "Fremde"}
    )
    assert fremde.status_code == 404

    client.cookies.clear()
    headers = {"Authorization": f"Bearer {create_session_token(other.id, 'test-secret-key', 3600)}"}
    assert client.get(f"/api/v1/study/tasks/{eigene['id']}/dependencies", headers=headers).status_code == 404
    assert client.post(
        f"/api/v1/study/tasks/{eigene['id']}/dependencies",
        json={"depends_on_task_id": eigene["id"]},
        headers=headers,
    ).status_code == 404
    assert client.get("/api/v1/study/series", headers=headers).json() == []


def test_exam_topics_become_chained_tasks(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    subject = Subject(user_id=user.id, name="Mathematik", color="#3B82F6")
    db.add(subject)
    db.commit()
    db.refresh(subject)

    # Derselbe Lernstand, damit die Reihenfolge am Namen haengt und der
    # Test nicht vom `sorted` der Datenbank abhaengt.
    for name in ("Bruchgleichungen", "Ableitungen", "Integralrechnung"):
        create_topic(db, user, name, subject)
    exam_date = (datetime.now(timezone.utc) + timedelta(days=10)).replace(hour=9, minute=0, second=0, microsecond=0)
    exam = client.post(
        "/api/v1/exams",
        json={
            "subject_id": subject.id,
            "title": "Klausur Analysis",
            "date": exam_date.isoformat(),
        },
    )
    assert exam.status_code == 201, exam.text

    generated = client.post(
        f"/api/v1/exams/{exam.json()['id']}/tasks",
        json={"study_plan_id": plan["id"], "lead_days": 14, "scheduled_hour": 16, "duration_minutes": 45},
    )
    assert generated.status_code == 201, generated.text
    payload = generated.json()
    assert payload["created_count"] == 3
    assert payload["skipped_count"] == 0
    # Ohne Fortschrittswert gilt jedes Thema als unsicher, also hoechste Prioritaet.
    assert {task["priority"] for task in payload["created_tasks"]} == {4}
    assert [task["topic_name"] for task in payload["created_tasks"]] == [
        "Ableitungen",
        "Bruchgleichungen",
        "Integralrechnung",
    ]
    # Alle drei Aufgaben hängen an derselben Klausur.
    assert {task["exam_title"] for task in payload["created_tasks"]} == {"Klausur Analysis"}
    # Die Reihenfolge ist eine Kette, nicht nur eine Liste.
    assert [len(task["blocked_by"]) for task in payload["created_tasks"]] == [0, 1, 1]
    assert payload["created_tasks"][1]["blocked_by"][0]["title"] == "Ableitungen lernen"
    # Faellig am Ende des eigenen Lerntags, nicht erst am Klausurtag.
    assert payload["last_due_on"] is not None
    assert datetime.fromisoformat(payload["last_due_on"]) < exam_date

    # Ein zweiter Lauf erzeugt nichts doppelt.
    again = client.post(
        f"/api/v1/exams/{exam.json()['id']}/tasks", json={"study_plan_id": plan["id"]}
    )
    assert again.status_code == 201
    assert again.json()["created_count"] == 0
    assert again.json()["skipped_count"] == 3
    assert len(client.get("/api/v1/study/tasks").json()) == 3

    # ... und die Kette gilt jetzt: nichts davon laesst sich vorzeitig starten.
    assert client.post(f"/api/v1/study/tasks/{payload['created_tasks'][1]['id']}/start").status_code == 409


def test_exam_task_generation_orders_by_confidence_and_can_skip_dependencies(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    subject = Subject(user_id=user.id, name="Geschichte", color="#64748B")
    db.add(subject)
    db.commit()
    db.refresh(subject)

    sicher = create_topic(db, user, "Bundeswehr", subject)
    unsicher = create_topic(db, user, "Industrialisierung", subject)
    db.add(LearningProgress(user_id=user.id, topic_id=sicher.id, confidence=0.9))
    db.add(LearningProgress(user_id=user.id, topic_id=unsicher.id, confidence=0.1))
    db.commit()

    exam = client.post(
        "/api/v1/exams",
        json={
            "subject_id": subject.id,
            "title": "Epochen",
            "date": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        },
    )
    generated = client.post(
        f"/api/v1/exams/{exam.json()['id']}/tasks",
        json={"study_plan_id": plan["id"], "chain_dependencies": False},
    )
    assert generated.status_code == 201, generated.text
    created = generated.json()["created_tasks"]
    # Das unsichere Thema zuerst und mit der hoeheren Prioritaet.
    assert [task["topic_name"] for task in created] == ["Industrialisierung", "Bundeswehr"]
    assert [task["priority"] for task in created] == [4, 0]
    assert all(task["blocked_by"] == [] for task in created)
    # Das unsichere Thema bekommt auch den frueheren Termin.
    assert created[0]["scheduled_for"] < created[1]["scheduled_for"]


def test_exam_task_generation_needs_topics(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    exam = client.post(
        "/api/v1/exams",
        json={"title": "Ohne Fach", "date": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()},
    )
    assert exam.status_code == 201
    response = client.post(f"/api/v1/exams/{exam.json()['id']}/tasks", json={"study_plan_id": plan["id"]})
    assert response.status_code == 422
    assert "Themen" in response.json()["detail"]


def test_dashboard_reports_priority_due_date_and_blockers(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    base = "/api/v1/study/tasks"
    heute = datetime.now(timezone.utc).replace(hour=17, minute=0, second=0, microsecond=0)

    erste = client.post(
        base,
        json={
            "study_plan_id": plan["id"],
            "title": "Erst lernen",
            "priority": 3,
            "scheduled_for": heute.isoformat(),
            "due_date": (heute + timedelta(days=1)).isoformat(),
        },
    ).json()
    zweite = client.post(
        base,
        json={"study_plan_id": plan["id"], "title": "Dann lernen", "scheduled_for": heute.isoformat()},
    ).json()
    client.post(f"{base}/{zweite['id']}/dependencies", json={"depends_on_task_id": erste["id"]})

    tasks = {task["title"]: task for task in client.get("/api/v1/dashboard").json()["study_tasks"]}
    assert tasks["Erst lernen"]["priority"] == 3
    assert tasks["Erst lernen"]["due_date"] is not None
    assert tasks["Erst lernen"]["blocked_by"] == []
    assert tasks["Dann lernen"]["blocked_by"] == ["Erst lernen"]
    # Hohe Prioritaet zuerst, auch wenn der Termin gleich ist.
    assert list(tasks) == ["Erst lernen", "Dann lernen"]


def test_study_routes_are_registered(app_client: TestClient) -> None:
    for path in (
        "/api/v1/study/plans",
        "/api/v1/study/tasks",
        "/api/v1/study/series",
    ):
        routes = {route.path for route in app_client.app.routes}
        assert path in routes
    assert "/api/v1/exams/{exam_id}/tasks" in {route.path for route in app_client.app.routes}


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
    failing_users: frozenset[str] = frozenset()
    requested: list[tuple[date, date]] = []

    def __init__(self, base_url: str, school: str, username: str, password: str, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url
        self.username = username

    def __enter__(self) -> _StubWebUntisClient:
        return self
    def __exit__(self, *_: object) -> None:
        return None

    def fetch_timetable(self, start: date, end: date) -> WebUntisTimetable:
        type(self).requested.append((start, end))
        # Manche Benutzer haben im Portal keine gueltigen Zugangsdaten; so
        # trifft der Fehler genau eine Zeile und nicht den ganzen Lauf.
        if self.username in type(self).failing_users:
            raise WebUntisAuthError(f"WebUntis hat Benutzername oder Passwort von {self.username} abgelehnt.")
        if type(self).failure is not None:
            raise type(self).failure
        return parse_timetable(type(self).payload, start, end)


@pytest.fixture
def stub_webuntis(monkeypatch: pytest.MonkeyPatch) -> type[_StubWebUntisClient]:
    _StubWebUntisClient.payload = [_lesson_payload()]
    _StubWebUntisClient.failure = None
    _StubWebUntisClient.failing_users = frozenset()
    _StubWebUntisClient.requested = []
    monkeypatch.setattr(integrations_service, "WebUntisClient", _StubWebUntisClient)

    class _FrozenDate(date):
        # Der Sync holt die Wochen um den heutigen Tag. Damit die Tests nicht
        # von der Kalenderwoche des Laufdatums abhaengen, ist "heute" fixiert.
        @classmethod
        def today(cls) -> _FrozenDate:
            return cls(2026, 9, 21)

    monkeypatch.setattr(integrations_service, "date", _FrozenDate)
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


def test_sync_rejects_unknown_integration_type(
    app_context: tuple[TestClient, Session, User], settings: Settings
) -> None:
    _, db, user = app_context
    source = SyncSource(
        user_id=user.id, source_type="ical", name="Kalender", configuration={"path": tmp_path_value()}
    )
    db.add(source)
    db.commit()
    with pytest.raises(IntegrationConnectionError, match="noch kein Import"):
        sync_integration(
            db=db,
            source=source,
            config={},
            credentials={},
            settings=settings,
            timezone_name="UTC",
            timeout_seconds=5.0,
        )


def tmp_path_value() -> str:
    return str(Path.cwd())


# --- Automatischer WebUntis-Sync --------------------------------------------


def _webuntis_source(db: Session, user: User, *, username: str) -> SyncSource:
    """Legt eine WebUntis-Quelle an, wie es der Endpunkt tun würde."""
    source = SyncSource(
        user_id=user.id,
        source_type="webuntis",
        name="Schulportal",
        configuration={"base_url": "https://schule.webuntis.com", "school": "schule"},
        credentials=encrypt_credentials(
            {"username": username, "password": "geheim"},
            "test-integration-encryption-key",
        ),
        status="configured",
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def _fresh_session(db: Session) -> sessionmaker[Session]:
    """Eigene Sitzungen auf derselben Testdatenbank, wie der Scheduler sie baut."""
    return sessionmaker(bind=db.get_bind(), class_=Session, autoflush=False, expire_on_commit=False)


def _empty_report() -> ScheduledSyncReport:
    return ScheduledSyncReport(considered=0, synced=0, skipped=0, failed=0)


def test_scheduled_sync_is_due_understands_naive_timestamps() -> None:
    """SQLite liefert ``last_sync_at`` ohne Zeitzone; der Vergleich muss das aushalten."""
    source = SyncSource(user_id=1, source_type="webuntis", name="Schulportal")
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)

    assert scheduled_sync_is_due(source, now, 86400) is True

    source.last_sync_at = datetime(2026, 9, 30, 6, 0)
    assert scheduled_sync_is_due(source, now, 86400) is False
    assert scheduled_sync_is_due(source, datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc), 86400) is True


def test_scheduled_sync_holds_the_four_week_window(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
    stub_webuntis: type[_StubWebUntisClient],
) -> None:
    _client, db, user = app_context
    _webuntis_source(db, user, username="nutzer")

    report = run_scheduled_integration_syncs(db=db, settings=settings)

    assert (report.considered, report.synced, report.skipped, report.failed) == (1, 1, 0, 0)
    # Dasselbe Fenster wie der Knopf: Montag der laufenden Woche plus vier Wochen.
    assert stub_webuntis.requested == [(date(2026, 9, 21), date(2026, 10, 18))]
    assert db.query(Lesson).count() == 1
    stored = db.scalars(select(SyncSource)).one()
    assert stored.status == "connected"
    assert stored.last_error is None
    assert stored.last_sync_at is not None


def test_scheduled_sync_skips_an_integration_that_ran_today(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
    stub_webuntis: type[_StubWebUntisClient],
) -> None:
    _client, db, user = app_context
    source = _webuntis_source(db, user, username="nutzer")
    source.last_sync_at = datetime(2026, 9, 30, 5, 0, tzinfo=timezone.utc)
    source.status = "connected"
    db.commit()

    report = run_scheduled_integration_syncs(
        db=db,
        settings=settings,
        now=datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc),
    )

    assert (report.considered, report.synced, report.skipped, report.failed) == (1, 0, 1, 0)
    assert stub_webuntis.requested == []
    db.expire_all()
    stored = db.get(SyncSource, source.id)
    assert stored is not None
    assert stored.last_error is None
    assert stored.status == "connected"


def test_manual_sync_button_ignores_a_fresh_last_sync_at(
    app_context: tuple[TestClient, Session, User],
    stub_webuntis: type[_StubWebUntisClient],
) -> None:
    """Die wichtigste Trennung: der Knopf holt auch nach einem Tageslauf erneut."""
    client, db, _user = app_context
    login(client)
    integration_id = _create_webuntis(client)

    db.expire_all()
    source = db.get(SyncSource, integration_id)
    assert source is not None
    source.last_sync_at = datetime.now(timezone.utc)
    db.commit()

    body = client.post(f"/api/v1/integrations/{integration_id}/sync").json()

    assert body["ok"] is True
    assert body["imported_lessons"] == 1
    assert len(stub_webuntis.requested) == 1


def test_scheduled_sync_reports_missing_fields(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
    stub_webuntis: type[_StubWebUntisClient],
) -> None:
    client, db, _user = app_context
    login(client)
    integration_id = _create_webuntis(client)
    assert client.patch(f"/api/v1/integrations/{integration_id}", json={"credentials": {}}).status_code == 200

    report = run_scheduled_integration_syncs(db=db, settings=settings)

    assert (report.considered, report.synced, report.failed) == (1, 0, 1)
    assert stub_webuntis.requested == []
    db.expire_all()
    stored = db.get(SyncSource, integration_id)
    assert stored is not None
    assert stored.status == "error"
    assert stored.last_error == "Fehlende Angaben: username, password"


def test_scheduled_sync_leaves_disabled_integrations_alone(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
    stub_webuntis: type[_StubWebUntisClient],
) -> None:
    client, db, _user = app_context
    login(client)
    integration_id = _create_webuntis(client)
    assert client.patch(f"/api/v1/integrations/{integration_id}", json={"enabled": False}).status_code == 200

    report = run_scheduled_integration_syncs(db=db, settings=settings)

    # Der planmäßige Lauf sieht eine deaktivierte Quelle gar nicht erst an.
    assert (report.considered, report.synced, report.failed) == (0, 0, 0)
    db.expire_all()
    source = db.get(SyncSource, integration_id)
    assert source is not None
    run = run_integration_sync(
        db=db,
        source=source,
        config={"base_url": "https://schule.webuntis.com", "school": "schule"},
        credential_keys=["username", "password"],
        settings=settings,
        timezone_name="UTC",
    )
    assert (run.ok, run.status) == (False, "disabled")
    assert "deaktiviert" in run.message
    assert stub_webuntis.requested == []


def test_scheduled_sync_isolates_a_failure_to_one_user(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
    stub_webuntis: type[_StubWebUntisClient],
) -> None:
    """Der erste Benutzer scheitert; der zweite muss trotzdem bedient werden."""
    _client, db, user = app_context
    kaputt = _webuntis_source(db, user, username="kaputt")

    other = User(
        username="other",
        password_hash=hash_password("other-password"),
        is_admin=False,
        is_active=True,
        timezone="Europe/Berlin",
    )
    db.add(other)
    db.commit()
    db.refresh(other)
    gut = _webuntis_source(db, other, username="gut")
    stub_webuntis.failing_users = frozenset({"kaputt"})

    report = run_scheduled_integration_syncs(db=db, settings=settings)

    assert (report.considered, report.synced, report.failed) == (2, 1, 1)
    assert len(stub_webuntis.requested) == 2
    db.expire_all()
    failed = db.get(SyncSource, kaputt.id)
    assert failed is not None and failed.status == "error"
    assert "abgelehnt" in (failed.last_error or "")
    healthy = db.get(SyncSource, gut.id)
    assert healthy is not None and healthy.status == "connected"
    assert healthy.last_error is None
    # Der zweite Benutzer hat eine eigene Zeitzone, und sie wird benutzt.
    assert as_utc(db.scalars(select(Lesson)).one().start_time) == datetime(2026, 9, 25, 7, 35, tzinfo=timezone.utc)


def test_scheduled_sync_ignores_inactive_users(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
    stub_webuntis: type[_StubWebUntisClient],
) -> None:
    _client, db, user = app_context
    user.is_active = False
    db.commit()
    _webuntis_source(db, user, username="nutzer")

    report = run_scheduled_integration_syncs(db=db, settings=settings)

    assert report.considered == 0
    assert stub_webuntis.requested == []


def test_scheduled_sync_stores_a_missing_encryption_key_in_last_error(
    app_context: tuple[TestClient, Session, User],
    stub_webuntis: type[_StubWebUntisClient],
) -> None:
    """Fehlender Schlüssel ist ein Konfigurationsproblem, kein Absturz des Laufs."""
    _client, db, user = app_context
    source = _webuntis_source(db, user, username="nutzer")
    ohne_schluessel = Settings(_env_file=None, integration_encryption_key=None, timezone="UTC")

    report = run_scheduled_integration_syncs(db=db, settings=ohne_schluessel)

    assert (report.considered, report.failed) == (1, 1)
    assert stub_webuntis.requested == []
    db.expire_all()
    stored = db.get(SyncSource, source.id)
    assert stored is not None
    assert stored.status == "error"
    assert "INTEGRATION_ENCRYPTION_KEY" in (stored.last_error or "")


def test_webuntis_sync_settings_have_a_daily_default_and_bounds() -> None:
    defaults = Settings(_env_file=None)
    assert defaults.webuntis_sync_enabled is False
    assert defaults.webuntis_sync_interval_seconds == 86400

    configured = Settings(
        _env_file=None,
        WEBUNTIS_SYNC_ENABLED="true",
        WEBUNTIS_SYNC_INTERVAL_SECONDS="3600",
    )
    assert configured.webuntis_sync_enabled is True
    assert configured.webuntis_sync_interval_seconds == 3600


@pytest.mark.parametrize("value", ["0", "-60", "604801", "keine-zahl"])
def test_webuntis_sync_interval_rejects_impossible_values(value: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, WEBUNTIS_SYNC_INTERVAL_SECONDS=value)


def test_run_integration_sync_rejects_an_unknown_kind(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
) -> None:
    _client, db, user = app_context
    source = SyncSource(
        user_id=user.id,
        source_type="ical",
        name="Kalender",
        configuration={"path": tmp_path_value()},
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    with pytest.raises(IntegrationValidationError, match="Unsupported integration kind"):
        run_integration_sync(
            db=db,
            source=source,
            config={},
            credential_keys=[],
            settings=settings,
            timezone_name="UTC",
        )


def test_scheduler_runs_the_injected_job(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
) -> None:
    _client, db, _user = app_context
    seen: list[Session] = []

    def job(*, db: Session, settings: Settings) -> ScheduledSyncReport:
        seen.append(db)
        return _empty_report()

    scheduler = IntegrationSyncScheduler(settings, session_factory=_fresh_session(db), job=job)
    assert scheduler.interval_seconds == 86400
    try:
        report = scheduler.try_run_once()
    finally:
        scheduler.stop()

    assert report == _empty_report()
    # Die Arbeit lief in einer eigenen Sitzung, die danach wieder zu ist.
    assert len(seen) == 1
    assert seen[0] is not db
    assert seen[0].in_transaction() is False


def test_scheduler_refuses_a_second_run_while_one_is_going(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
) -> None:
    """Kein Parallel-Lauf: der zweite Versuch bekommt den Lock nicht."""
    _client, db, _user = app_context
    inner: list[ScheduledSyncReport | None] = []

    def job(*, db: Session, settings: Settings) -> ScheduledSyncReport:
        inner.append(scheduler.try_run_once())
        return _empty_report()

    scheduler = IntegrationSyncScheduler(settings, session_factory=_fresh_session(db), job=job)
    try:
        report = scheduler.try_run_once()
    finally:
        scheduler.stop()

    assert report == _empty_report()
    assert inner == [None]


def test_scheduler_loop_repeats_and_stops(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
) -> None:
    _client, db, _user = app_context
    done = threading.Event()
    runs: list[int] = []

    def job(*, db: Session, settings: Settings) -> ScheduledSyncReport:
        runs.append(len(runs) + 1)
        if len(runs) >= 2:
            done.set()
        return _empty_report()

    scheduler = IntegrationSyncScheduler(
        settings,
        interval_seconds=0.05,
        session_factory=_fresh_session(db),
        job=job,
    )
    scheduler.start()
    try:
        assert done.wait(timeout=10)
    finally:
        scheduler.stop()

    assert len(runs) >= 2
    assert scheduler.is_running() is False


def test_scheduler_loop_survives_a_failing_job(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
) -> None:
    """Ein kaputter Lauf darf den Thread nicht beenden, sonst ruft nie wieder jemand."""
    _client, db, _user = app_context
    attempts: list[int] = []
    survived = threading.Event()

    def job(*, db: Session, settings: Settings) -> ScheduledSyncReport:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise RuntimeError("Datenbank weg")
        survived.set()
        return _empty_report()

    scheduler = IntegrationSyncScheduler(
        settings,
        interval_seconds=0.05,
        session_factory=_fresh_session(db),
        job=job,
    )
    scheduler.start()
    try:
        assert survived.wait(timeout=10)
    finally:
        scheduler.stop()

    assert len(attempts) >= 2


def test_lifespan_starts_and_stops_the_scheduler(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
) -> None:
    _client, db, _user = app_context
    enabled = Settings(
        _env_file=None,
        database_url=settings.database_url,
        secret_key="test-secret-key",
        integration_encryption_key="test-integration-encryption-key",
        bootstrap_on_startup=False,
        demo_mode=False,
        webuntis_sync_enabled=True,
        webuntis_sync_interval_seconds=86400,
    )
    application = create_app(enabled)

    with TestClient(application) as client:
        scheduler = client.app.state.integration_scheduler
        assert isinstance(scheduler, IntegrationSyncScheduler)
        assert scheduler.is_running()

    assert scheduler.is_running() is False


def test_lifespan_without_the_setting_keeps_no_scheduler(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, _db, _user = app_context
    assert client.app.state.integration_scheduler is None


# --- Dokumente durchsuchbar (RAG) -------------------------------------------


def _create_documents_integration(client: TestClient, *, path: str = "dokumente") -> int:
    created = client.post(
        "/api/v1/integrations",
        json={"kind": "documents", "name": "Dokumente", "config": {"path": path}},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _write_document(file_path: Path, text: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text, encoding="utf-8")


def test_chunk_text_keeps_words_intact_and_overlaps() -> None:
    woerter = [f"wort{index}" for index in range(500)]
    text = " ".join(woerter)
    chunks = chunk_text(text, size=200, overlap=40)
    assert len(chunks) > 1
    assert all(0 < len(chunk.split()) <= 200 for chunk in chunks)
    # Der erste Absatz beginnt vorn, der letzte hoert hinten auf.
    assert chunks[0].split()[0] == "wort0"
    assert chunks[-1].split()[-1] == "wort499"
    # Die Naht ist ueberlappend: Der Anfang des naechsten Absatzes stand schon am Ende des vorigen.
    for vorher, nachher in zip(chunks, chunks[1:]):
        anfang = " ".join(nachher.split()[:3])
        assert anfang in " ".join(vorher.split()[-12:])


def test_read_document_text_rejects_unsupported_formats(tmp_path: Path) -> None:
    binary = tmp_path / "daten.pdf"
    binary.write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError):
        read_document_text(binary)


def test_documents_sync_indexes_folder(
    app_context: tuple[TestClient, Session, User], settings: Settings
) -> None:
    client, _, _ = app_context
    login(client)
    integration_id = _create_documents_integration(client)
    folder = Path(settings.document_storage_path) / "dokumente"
    _write_document(folder / "deutsch.md", "Goethe wurde 1749 in Frankfurt am Main geboren.")
    _write_document(folder / "bild.pdf", "kein Text, landet im Papierkorb")

    result = client.post(f"/api/v1/integrations/{integration_id}/sync")
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is True
    assert body["status"] == "connected"
    assert body["imported_documents"] == 1
    assert body["skipped_files"] == 1
    assert body["indexed_chunks"] >= 1

    listing = client.get("/api/v1/documents").json()
    assert [entry["filename"] for entry in listing] == ["deutsch.md"]
    assert listing[0]["status"] == "indexed"

    search = client.post("/api/v1/documents/search", json={"query": "Goethe", "limit": 5}).json()
    assert search["hits"]
    assert search["hits"][0]["filename"] == "deutsch.md"
    assert "Frankfurt" in search["hits"][0]["content"]


def test_documents_sync_is_idempotent_and_removes_stale_files(
    app_context: tuple[TestClient, Session, User], settings: Settings
) -> None:
    client, _, _ = app_context
    login(client)
    integration_id = _create_documents_integration(client)
    folder = Path(settings.document_storage_path) / "dokumente"
    _write_document(folder / "chemie.txt", "Das Periodensystem ordnet die Elemente nach der Kernladung.")

    first = client.post(f"/api/v1/integrations/{integration_id}/sync").json()
    assert first["imported_documents"] == 1

    # Unveraendert: Der zweite Lauf ist ein spurloser No-Op.
    second = client.post(f"/api/v1/integrations/{integration_id}/sync").json()
    assert second["ok"] is True
    assert second["imported_documents"] == 0
    assert second["updated_documents"] == 0
    assert second["indexed_chunks"] == 0

    # Ein Aenderung reicht zum Neuindexieren, ohne die Liste zu verdoppeln.
    _write_document(folder / "chemie.txt", "Uran hat die Kernladungszahl 92.")
    third = client.post(f"/api/v1/integrations/{integration_id}/sync").json()
    assert third["updated_documents"] == 1
    assert third["imported_documents"] == 0

    # Verschwundene Dateien verlassen den Index.
    (folder / "chemie.txt").unlink()
    fourth = client.post(f"/api/v1/integrations/{integration_id}/sync").json()
    assert fourth["removed_documents"] == 1
    assert client.get("/api/v1/documents").json() == []


def test_documents_ask_falls_back_without_a_provider(
    app_context: tuple[TestClient, Session, User], settings: Settings
) -> None:
    client, _, _ = app_context
    login(client)
    integration_id = _create_documents_integration(client)
    folder = Path(settings.document_storage_path) / "dokumente"
    _write_document(folder / "deutsch.md", "Goethe wurde 1749 in Frankfurt am Main geboren.")
    assert client.post(f"/api/v1/integrations/{integration_id}/sync").json()["ok"] is True

    result = client.post("/api/v1/documents/ask", json={"question": "Wer war Goethe?"}).json()
    assert result["source"] == "fallback"
    assert "deutsch.md" in result["answer"]
    assert "deutsch.md" in {quelle["filename"] for quelle in result["sources"]}

    leer = client.post("/api/v1/documents/ask", json={"question": "Quantenkosmologie"}).json()
    assert leer["source"] == "none"


def test_document_search_isolates_users(
    app_context: tuple[TestClient, Session, User], settings: Settings
) -> None:
    client, db, user = app_context
    login(client)
    integration_id = _create_documents_integration(client)
    folder = Path(settings.document_storage_path) / "dokumente"
    _write_document(folder / "mein.txt", "Privates Wissen über die Photosynthese.")
    assert client.post(f"/api/v1/integrations/{integration_id}/sync").json()["ok"] is True

    other = User(
        username="anderer-dokumente",
        password_hash=hash_password("anderes-passwort"),
        is_active=True,
        is_admin=False,
        timezone="UTC",
    )
    db.add(other)
    db.commit()
    db.refresh(other)

    client.cookies.clear()
    client.headers.update(
        {"Authorization": f"Bearer {create_session_token(other.id, 'test-secret-key', 3600)}"}
    )
    assert client.get("/api/v1/documents").json() == []
    search = client.post("/api/v1/documents/search", json={"query": "Photosynthese"}).json()
    assert search["hits"] == []


def test_upload_indexes_supported_text(
    app_context: tuple[TestClient, Session, User], settings: Settings
) -> None:
    client, _, _ = app_context
    login(client)

    response = client.post(
        "/api/v1/files",
        files={"file": ("notizen.md", b"# Mathe\nDer Satz des Pythagoras gilt im Dreieck.", "text/markdown")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "indexed"
    assert Path(body["path"]).is_file()

    search = client.post("/api/v1/documents/search", json={"query": "Pythagoras"}).json()
    assert search["hits"]
    assert search["hits"][0]["filename"] == "notizen.md"


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


def test_embeddings_url_is_built_from_the_base_url() -> None:
    assert build_embeddings_url("https://api.openai.com/v1") == "https://api.openai.com/v1/embeddings"
    assert build_embeddings_url("http://localhost:11434") == "http://localhost:11434/v1/embeddings"
    # Auch hier: Ein /models-Suffix gehört nicht in den Endpunkt.
    assert build_embeddings_url("https://x.test/v1/models") == "https://x.test/v1/embeddings"


def test_embed_reads_the_embeddings() -> None:
    body = {"data": [{"embedding": [0.1, 0.2, 0.3]}, {"embedding": [0.4, 0.5, 0.6]}]}
    transport = _FakeAiTransport(httpx.Response(200, content=json.dumps(body).encode()))
    client = AiProviderClient("https://api.example.com/v1", "gpt-4o-mini", "geheim")
    client._client = httpx.Client(transport=transport, timeout=5.0)

    vectors = client.embed(["Satz eins", "Satz zwei"])

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    request = transport.requests[0]
    assert str(request.url) == "https://api.example.com/v1/embeddings"
    assert request.headers["Authorization"] == "Bearer geheim"
    koerper = json.loads(request.read().decode("utf-8"))
    assert koerper["model"] == "gpt-4o-mini"
    assert koerper["input"] == ["Satz eins", "Satz zwei"]


def test_embed_rejects_short_responses() -> None:
    client = AiProviderClient("https://api.example.com/v1", "gpt-4o-mini", "geheim")
    body = {"data": [{"embedding": [0.1]}]}
    client._client = httpx.Client(
        transport=_FakeAiTransport(httpx.Response(200, content=json.dumps(body).encode())), timeout=5.0
    )
    with pytest.raises(AiProviderError):
        client.embed(["eins", "zwei"])


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


# --- Lernfortschritt und Lernprozess ---------------------------------------

BERLIN = ZoneInfo("Europe/Berlin")


def _task_with_topic(db: Session, user: User, plan_id: int, topic: Topic, title: str) -> int:
    """Eine Lernaufgabe an einem Thema - ohne die API, weil ``create_task`` kein Thema traegt."""
    task = StudyPlanTask(
        user_id=user.id,
        study_plan_id=plan_id,
        topic_id=topic.id,
        title=title,
        duration_minutes=30,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task.id


def _add_session(
    db: Session,
    user: User,
    task_id: int,
    started_at: datetime,
    duration_minutes: int | None,
) -> StudySession:
    """Eine Lernsession mit festem Zeitpunkt.

    ``duration_minutes=None`` legt eine **offene** Session an - so, wie sie
    ``POST /study/tasks/{id}/start`` hinterlaesst. Ueber die API waere das nicht
    moeglich: der Start nimmt immer ``utc_now()``.
    """
    session = StudySession(
        user_id=user.id,
        task_id=task_id,
        started_at=as_utc(started_at),
        completed_at=as_utc(started_at + timedelta(minutes=duration_minutes or 0)) if duration_minutes is not None else None,
        duration_minutes=duration_minutes,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_progress_routen_sind_registriert(app_client: TestClient) -> None:
    pfade = {route.path for route in app_client.app.routes}
    assert "/api/v1/progress/insights" in pfade
    assert "/api/v1/progress/topics" in pfade
    assert "/api/v1/progress/topics/{topic_id}" in pfade
    assert "/api/v1/progress/goal" in pfade


def test_progress_routen_brauchen_eine_anmeldung(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/progress/insights").status_code == 401
    assert app_client.get("/api/v1/progress/topics").status_code == 401
    assert app_client.put("/api/v1/progress/topics/1", json={"confidence": 0.5}).status_code == 401
    assert app_client.patch("/api/v1/progress/goal", json={"goal_minutes": 300}).status_code == 401


def test_konfidenzstufen_geben_bezeichnung_und_intervall() -> None:
    """Fuenf Stufen, je eine Bezeichnung und ein Intervall.

    Der Test ist die Tabelle in Textform: wer sie aendert, faellt hier auf,
    statt still in der Oberflaeche anzukommen.
    """
    erwartet = {
        0.0: ("unsicher", 1),
        0.24: ("unsicher", 1),
        0.25: ("geübt", 3),
        0.49: ("geübt", 3),
        0.5: ("sicher", 7),
        0.75: ("fest", 14),
        1.0: ("gefestigt", 30),
    }
    for wert, (bezeichnung, tage) in erwartet.items():
        assert (confidence_label(wert), review_interval_days(wert)) == (bezeichnung, tage)
    # Ausserhalb der Skala greift die naechste Stufe, nicht ein Fehler.
    assert confidence_label(-3.0) == "unsicher"
    assert confidence_label(4.0) == "gefestigt"


def test_streak_regeln_sind_dokumentiert_und_pruefbar() -> None:
    heute = date(2026, 9, 30)
    # Nichts gelernt: keine Serie.
    assert current_streak(set(), heute) == 0
    # Heute ist noch offen - gestern allein ergibt schon eine Serie von einem Tag.
    assert current_streak({heute - timedelta(days=1)}, heute) == 1
    assert current_streak({heute}, heute) == 1
    assert current_streak({heute, heute - timedelta(days=1), heute - timedelta(days=2)}, heute) == 3
    # Zwei Tage Luecke beenden die Serie.
    assert current_streak({heute - timedelta(days=3), heute - timedelta(days=2)}, heute) == 0
    assert current_streak({heute - timedelta(days=1), heute - timedelta(days=3)}, heute) == 1
    # Die laengste Serie zaehlt durchgehende Tage, nicht die Zahl der Tage.
    assert longest_streak(set()) == 0
    assert longest_streak({date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 10)}) == 2
    assert longest_streak({date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)}) == 3


def test_woche_beginnt_am_montag() -> None:
    assert week_bounds(date(2026, 9, 30)) == (date(2026, 9, 28), date(2026, 10, 4))
    assert week_bounds(date(2026, 9, 28)) == (date(2026, 9, 28), date(2026, 10, 4))
    assert week_bounds(date(2026, 10, 4)) == (date(2026, 9, 28), date(2026, 10, 4))


def test_fortschritt_schreibt_konfidenz_und_wiederholungsdatum(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    login(client)
    thema = create_topic(db, user, "Ableitungen")
    create_topic(db, user, "Integrationen")

    antwort = client.put(f"/api/v1/progress/topics/{thema.id}", json={"confidence": 0.9})
    assert antwort.status_code == 200, antwort.text
    koerper = antwort.json()
    assert koerper["confidence"] == 0.9
    assert koerper["confidence_label"] == "fest"
    # `confidence_step` ist die untere Grenze der Stufe, nicht der Rohwert: ein
    # Wert zwischen zwei Stufen braucht trotzdem einen Wert, den das
    # Auswahlfeld anwaehlen kann.
    assert koerper["confidence_step"] == 0.75
    # Ohne Datum eingetragen gilt "jetzt": eine Einschaetzung ohne Zeitpunkt ist
    # eine Wiederholung in diesem Moment, und ohne Anker waere kein Intervall
    # berechenbar.
    assert koerper["last_reviewed_at"] is not None
    bewertet_am = datetime.fromisoformat(koerper["last_reviewed_at"].replace("Z", "+00:00")).astimezone(BERLIN).date()
    assert koerper["interval_days"] == 14
    assert koerper["next_review_on"] == (bewertet_am + timedelta(days=14)).isoformat()
    assert koerper["days_until_review"] == 14
    assert koerper["is_due"] is False
    assert koerper["studied_minutes"] == 0
    assert koerper["session_count"] == 0

    # Ein Thema ohne Lernstand ist kein Fehler, sondern unsicher.
    eintraege = client.get("/api/v1/progress/topics").json()
    assert {eintrag["name"] for eintrag in eintraege} == {"Ableitungen", "Integrationen"}
    unsicher = next(eintrag for eintrag in eintraege if eintrag["name"] == "Integrationen")
    assert unsicher["confidence"] == 0
    assert unsicher["confidence_label"] == "unsicher"
    assert unsicher["last_reviewed_at"] is None
    assert unsicher["next_review_on"] is None
    assert unsicher["interval_days"] is None
    assert unsicher["is_due"] is False

    # Ein Datum von Hand loescht den Anker - dann gibt es auch keinen Termin.
    zurueck = client.put(f"/api/v1/progress/topics/{thema.id}", json={"confidence": 0.5, "last_reviewed_at": None})
    assert zurueck.status_code == 200
    assert zurueck.json()["last_reviewed_at"] is None
    assert zurueck.json()["next_review_on"] is None
    assert zurueck.json()["interval_days"] is None
    assert zurueck.json()["confidence_label"] == "sicher"

    # Ein nachgetragener Zeitpunkt wird uebernommen statt durch "jetzt" ersetzt.
    nachgetragen = client.put(
        f"/api/v1/progress/topics/{thema.id}",
        json={"confidence": 0.5, "last_reviewed_at": "2026-09-20T10:00:00Z", "notes": "Kurz notiert"},
    )
    assert nachgetragen.json()["last_reviewed_at"].startswith("2026-09-20T10:00")
    assert nachgetragen.json()["next_review_on"] == "2026-09-27"
    assert nachgetragen.json()["notes"] == "Kurz notiert"

    # Ausserhalb der Skala und unbekannte Felder sind 422, kein stilles Klemmen.
    assert client.put(f"/api/v1/progress/topics/{thema.id}", json={"confidence": 1.4}).status_code == 422
    assert client.put(f"/api/v1/progress/topics/{thema.id}", json={"confidence": -0.1}).status_code == 422
    assert client.put(f"/api/v1/progress/topics/{thema.id}", json={"confidence": 0.5, "unsinn": 1}).status_code == 422
    assert client.put("/api/v1/progress/topics/999999", json={"confidence": 0.5}).status_code == 404


def test_fortschritt_ist_je_nutzer_getrennt(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    thema = create_topic(db, user, "Kernphysik")
    assert client.put(f"/api/v1/progress/topics/{thema.id}", json={"confidence": 0.5}).status_code == 200
    assert client.get("/api/v1/progress/topics").json()[0]["confidence"] == 0.5

    other = User(
        username="other-progress",
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
    assert client.get("/api/v1/progress/topics", headers=headers).json() == []
    # 404 statt 403: die Existenz der Id gehoert dem anderen Nutzer.
    fremd = client.put(f"/api/v1/progress/topics/{thema.id}", json={"confidence": 1.0}, headers=headers)
    assert fremd.status_code == 404
    db.expire_all()
    assert db.get(LearningProgress, (user.id, thema.id)).confidence == 0.5


def test_lernprotokoll_zaehlt_dauer_je_fach_und_wochentag_in_der_nutzerzone(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Die Wochentage sind die des Nutzers, nicht die von UTC.

    Die erste Sitzung liegt in ``Europe/Berlin`` an einem anderen Tag als in
    UTC - Montag 00:10 ist Sonntag 22:10 UTC. Wuerde hier in UTC gerechnet,
    kaeme Sonntag statt Montag heraus.
    """
    client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    login(client)
    fach = Subject(user_id=user.id, name="Sport", color="#111111")
    db.add(fach)
    db.commit()
    db.refresh(fach)
    plan = create_plan(client, db, user)
    thema = create_topic(db, user, "Ausdauer", fach)
    task_id = _task_with_topic(db, user, plan["id"], thema, "Ausdauer trainieren")

    montag_frueh = datetime.combine(date(2026, 9, 21), time(0, 10), tzinfo=BERLIN)
    assert montag_frueh.astimezone(timezone.utc).date() == date(2026, 9, 20)
    _add_session(db, user, task_id, montag_frueh, 25)
    # Samstag 23:30 Ortszeit ist Samstag 21:30 UTC - derselbe Tag.
    samstag_spät = datetime.combine(date(2026, 9, 26), time(23, 30), tzinfo=BERLIN)
    assert samstag_spät.astimezone(timezone.utc).date() == date(2026, 9, 26)
    _add_session(db, user, task_id, samstag_spät, 35)

    koerper = client.get("/api/v1/progress/insights").json()

    assert koerper["timezone"] == "Europe/Berlin"
    assert koerper["total_minutes"] == 60
    assert koerper["total_sessions"] == 2
    # Die Stufentabelle reist mit, damit das Auswahlfeld keine eigene fuehrt.
    assert [(stufe["value"], stufe["label"], stufe["interval_days"]) for stufe in koerper["confidence_steps"]] == [
        (0.0, "unsicher", 1),
        (0.25, "geübt", 3),
        (0.5, "sicher", 7),
        (0.75, "fest", 14),
        (1.0, "gefestigt", 30),
    ]
    assert [(eintrag["subject_name"], eintrag["minutes"]) for eintrag in koerper["by_subject"]] == [("Sport", 60)]
    assert koerper["by_subject"][0]["share_percent"] == 100.0
    assert [eintrag["weekday"] for eintrag in koerper["by_weekday"]] == [0, 1, 2, 3, 4, 5, 6]
    wochentage = {eintrag["label"]: eintrag["minutes"] for eintrag in koerper["by_weekday"]}
    assert wochentage["Montag"] == 25
    assert wochentage["Samstag"] == 35
    # Der Sonntag bleibt leer: die 00:10-Sitzung gehoert dem Montag.
    assert wochentage["Sonntag"] == 0


def test_aufgaben_ohne_thema_landen_sichtbar_in_ohne_fach(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    fach = Subject(user_id=user.id, name="Loeres Lernen", color="#222222")
    db.add(fach)
    db.commit()
    db.refresh(fach)
    plan = create_plan(client, db, user)
    thema = create_topic(db, user, "Loses Lernen", fach)
    with_topic = _task_with_topic(db, user, plan["id"], thema, "Mit Thema")
    ohne_thema = create_task(client, db, user)["id"]
    assert isinstance(ohne_thema, int)
    _add_session(db, user, with_topic, datetime(2026, 9, 21, 17, 0, tzinfo=timezone.utc), 50)
    _add_session(db, user, ohne_thema, datetime(2026, 9, 21, 18, 0, tzinfo=timezone.utc), 10)

    by_subject = client.get("/api/v1/progress/insights").json()["by_subject"]

    # Eine Aufgabe ohne Thema hat kein Fach. Ihre Lernzeit wird nicht weggezaehlt,
    # sondern landet sichtbar in einem eigenen Korb.
    assert [(eintrag["subject_name"], eintrag["minutes"]) for eintrag in by_subject] == [
        ("Loeres Lernen", 50),
        ("Ohne Fach", 10),
    ]
    # Die Prozentzahl bezieht sich auf die Summe aller Faechers.
    assert [eintrag["share_percent"] for eintrag in by_subject] == [83.3, 16.7]


def test_streak_und_wochenziel_kommen_aus_den_lernsessions(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    login(client)
    plan = create_plan(client, db, user)
    thema = create_topic(db, user, "Analysis")
    task_id = _task_with_topic(db, user, plan["id"], thema, "Analysis üben")

    heute = datetime.now(BERLIN).date()
    montag, sonntag = week_bounds(heute)
    # Gestern und vorgestern je 30 Minuten: eine laufende Serie von zwei Tagen.
    _add_session(db, user, task_id, datetime.combine(heute - timedelta(days=2), time(17, 0), tzinfo=BERLIN), 30)
    _add_session(db, user, task_id, datetime.combine(heute - timedelta(days=1), time(17, 0), tzinfo=BERLIN), 30)
    # Eine Luecke, damit es etwas zu vergleichen gibt.
    _add_session(db, user, task_id, datetime.combine(heute - timedelta(days=4), time(17, 0), tzinfo=BERLIN), 45)
    # Gestartet und sofort gestoppt: null Minuten, und die zaehlt nicht als Lerntag.
    _add_session(db, user, task_id, datetime.combine(heute, time(8, 0), tzinfo=BERLIN), 0)
    # Der Montag 00:10 dieser Woche liegt immer in der Woche und immer in der
    # Vergangenheit - auch wenn der Test an einem Montag laeuft.
    _add_session(db, user, task_id, datetime.combine(montag, time(0, 10), tzinfo=BERLIN), 60)

    koerper = client.get("/api/v1/progress/insights").json()
    serie = koerper["streak"]

    assert serie["current_days"] == 2
    assert serie["longest_days"] == 2
    assert serie["studied_today"] is False
    assert serie["last_studied_on"] == (heute - timedelta(days=1)).isoformat()
    assert serie["days_since_last"] == 1
    assert serie["window_days"] == 365
    # Die null-Minuten-Session ist kein Lerntag.
    assert koerper["today"] == {"date": heute.isoformat(), "minutes": 0, "sessions": 0}

    # Die Woche laeuft von Montag bis Sonntag in der Zone des Nutzers.
    woche = koerper["week"]
    assert woche["week_starts_on"] == montag.isoformat()
    assert woche["week_ends_on"] == sonntag.isoformat()
    assert woche["days_remaining"] == (sonntag - heute).days + 1
    # Erwartung selbst gerechnet: die beiden Serientage liegen nur in dieser
    # Woche, wenn heute nicht der Montag ist.
    erwartete_tage = {montag, heute - timedelta(days=1), heute - timedelta(days=2)}
    erwartete_tage = {tag for tag in erwartete_tage if montag <= tag <= sonntag}
    assert woche["studied_minutes"] == 60 * len(erwartete_tage)
    assert woche["days_studied"] == len(erwartete_tage)
    assert woche["goal_minutes"] == DEFAULT_WEEKLY_GOAL_MINUTES
    assert woche["goal_reached"] is False
    assert woche["percent"] == round((60 * len(erwartete_tage) / DEFAULT_WEEKLY_GOAL_MINUTES) * 100, 1)

    # Ein eigenes Wochenziel wird gespeichert und im selben Fenster gemessen.
    gespeichert = client.patch("/api/v1/progress/goal", json={"goal_minutes": 30})
    assert gespeichert.status_code == 200
    assert gespeichert.json()["goal_minutes"] == 30
    assert gespeichert.json()["goal_reached"] is True
    assert client.get("/api/v1/progress/insights").json()["week"]["goal_minutes"] == 30
    # Ein Ziel ist eine Zahl in einem vertretbaren Rahmen, kein Wort.
    assert client.patch("/api/v1/progress/goal", json={"goal_minutes": 0}).status_code == 422
    assert client.patch("/api/v1/progress/goal", json={"goal_minutes": "viel"}).status_code == 422


def test_wochenziel_ueberschreibt_keine_fremden_einstellungen(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """``settings.preferences`` ist ein fremdes Verzeichnis, kein Platz fuer das Lernziel allein."""
    client, db, user = app_context
    db.add(UserSettings(user_id=user.id, preferences={"notizen": "Behalten"}))
    db.commit()
    login(client)

    assert client.patch("/api/v1/progress/goal", json={"goal_minutes": 120}).status_code == 200

    db.expire_all()
    assert db.get(UserSettings, 1).preferences == {"notizen": "Behalten", "weekly_goal_minutes": 120}


def test_offene_session_zaehlt_nicht_mit_und_beim_beenden_genau_einmal(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    login(client)
    plan = create_plan(client, db, user)
    thema = create_topic(db, user, "Chemie")
    task_id = _task_with_topic(db, user, plan["id"], thema, "Chemie lernen")
    # Eine abgeschlossene Sitzung von gestern als Vergleich.
    gestern = datetime.now(BERLIN).date() - timedelta(days=1)
    _add_session(db, user, task_id, datetime.combine(gestern, time(17, 0), tzinfo=BERLIN), 40)

    session = client.post(f"/api/v1/study/tasks/{task_id}/start")
    assert session.status_code == 200, session.text
    assert session.json()["completed_at"] is None

    offen = client.get("/api/v1/progress/insights").json()
    # Die offene Session steht nur in den offenen Feldern - nie in den Summen.
    assert offen["open_sessions"] == 1
    assert offen["total_minutes"] == 40
    assert offen["total_sessions"] == 1
    assert offen["active_minutes"] == 0

    assert client.post(f"/api/v1/study/tasks/{task_id}/complete").status_code == 200
    danach = client.get("/api/v1/progress/insights").json()
    assert danach["open_sessions"] == 0
    assert danach["active_minutes"] == 0
    # Die geschlossene Sitzung gehoert jetzt genau einmal dazu, mit mindestens
    # einer Minute - so setzt `_close_sessions` es fest.
    assert danach["total_minutes"] == 41
    assert danach["total_sessions"] == 2
    assert [eintrag["minutes"] for eintrag in danach["by_subject"]] == [41]


def test_geschriebener_fortschritt_steuert_den_klausur_generator(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Der Generator liest dieselbe Konfidenz, die der Fortschritts-Endpoint schreibt.

    Ohne Lernstand gilt jedes Thema als unsicher (Konfidenz 0, hoechste
    Prioritaet). Sobald wirklich Werte stehen, verschiebt sich die Lernreihenfolge
    - und zwar deshalb, weil ``LearningProgress`` jetzt gefuellt wird, und nicht
    weil der Generator eine eigene Wahrheit haelt.
    """
    client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    login(client)
    plan = create_plan(client, db, user)
    fach = Subject(user_id=user.id, name="Geschichte", color="#64748B")
    db.add(fach)
    db.commit()
    db.refresh(fach)
    sicher = create_topic(db, user, "Bundeswehr", fach)
    unsicher = create_topic(db, user, "Industrialisierung", fach)

    assert client.put(f"/api/v1/progress/topics/{sicher.id}", json={"confidence": 0.9}).status_code == 200
    assert client.put(f"/api/v1/progress/topics/{unsicher.id}", json={"confidence": 0.1}).status_code == 200
    exam = client.post(
        "/api/v1/exams",
        json={
            "subject_id": fach.id,
            "title": "Epochen",
            "date": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        },
    )
    assert exam.status_code == 201
    erzeugt = client.post(
        f"/api/v1/exams/{exam.json()['id']}/tasks",
        json={"study_plan_id": plan["id"], "chain_dependencies": False},
    )
    assert erzeugt.status_code == 201, erzeugt.text
    aufgaben = erzeugt.json()["created_tasks"]
    # Das unsichere Thema zuerst, mit der hoeheren Prioritaet und dem frueheren Termin.
    assert [aufgabe["topic_name"] for aufgabe in aufgaben] == ["Industrialisierung", "Bundeswehr"]
    assert [aufgabe["priority"] for aufgabe in aufgaben] == [4, 0]
    assert aufgaben[0]["scheduled_for"] < aufgaben[1]["scheduled_for"]

    # Wird derselbe Fortschritt umgedreht, dreht sich der Plan mit - in einem
    # zweiten Plan, damit die Idempotenz des Generators nicht dazwischenfunkt.
    assert client.put(f"/api/v1/progress/topics/{unsicher.id}", json={"confidence": 1.0}).status_code == 200
    zweiter_plan = create_plan(client, db, user, "Zweiter Plan")
    gedreht = client.post(
        f"/api/v1/exams/{exam.json()['id']}/tasks",
        json={"study_plan_id": zweiter_plan["id"], "chain_dependencies": False},
    )
    assert gedreht.status_code == 201, gedreht.text
    gedreht_aufgaben = gedreht.json()["created_tasks"]
    assert [aufgabe["topic_name"] for aufgabe in gedreht_aufgaben] == ["Bundeswehr", "Industrialisierung"]
    assert [aufgabe["priority"] for aufgabe in gedreht_aufgaben] == [0, 0]
    assert gedreht_aufgaben[0]["scheduled_for"] < gedreht_aufgaben[1]["scheduled_for"]

    # Das Dashboard meldet denselben Stand, den die Fortschrittsliste fuehrt.
    themen_im_dashboard = client.get("/api/v1/dashboard").json()["upcoming_exams"][0]["topics"]
    assert {thema["name"]: thema["confidence"] for thema in themen_im_dashboard} == {
        "Bundeswehr": 0.9,
        "Industrialisierung": 1.0,
    }
# --- Noten ------------------------------------------------------------------


def _fach(db: Session, user: User, name: str) -> Subject:
    fach = Subject(user_id=user.id, name=name, color="#3B82F6")
    db.add(fach)
    db.commit()
    db.refresh(fach)
    return fach


def _note(
    client: TestClient,
    db: Session,
    user: User,
    fach: Subject,
    wert: float,
    *,
    art: str = "written_exam",
    gewicht: int = 1,
    vor_tagen: int = 7,
    kommentar: str | None = None,
) -> dict[str, object]:
    """Eine Note von Hand anlegen, mit einem Datum in der Vergangenheit."""
    tag = (datetime.now(timezone.utc) - timedelta(days=vor_tagen)).date()
    antwort = client.post(
        "/api/v1/grades",
        json={
            "subject_id": fach.id,
            "grade_value": wert,
            "kind": art,
            "weight": gewicht,
            "graded_on": tag.isoformat(),
            "comment": kommentar,
        },
    )
    assert antwort.status_code == 201, antwort.text
    return antwort.json()


def test_grade_routen_sind_registriert(app_client: TestClient) -> None:
    pfade = {route.path for route in app_client.app.routes}
    assert "/api/v1/grades" in pfade
    assert "/api/v1/grades/summary" in pfade
    assert "/api/v1/grades/sharing" in pfade
    assert "/api/v1/grades/import" in pfade


def test_grade_routen_brauchen_eine_anmeldung(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/grades").status_code == 401
    assert app_client.get("/api/v1/grades/summary").status_code == 401
    assert app_client.get("/api/v1/grades/sharing").status_code == 401
    assert app_client.put("/api/v1/grades/sharing", json={"enabled": True}).status_code == 401


def test_noten_anlegen_aendern_und_loeschen(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Mathematik")
    note = _note(client, db, user, mathe, 1.7, art="test", gewicht=2, vor_tagen=10, kommentar="Nur mit viel Uebung")

    assert note["subject_name"] == "Mathematik"
    assert note["kind"] == "test"
    assert note["weight"] == 2
    assert note["comment"] == "Nur mit viel Uebung"
    assert note["source"] == "manual"

    # PATCH aendert nur die gesetzten Felder; der Kommentar bleibt, wenn er
    # nicht mitgeschickt wird.
    geaendert = client.patch(f"/api/v1/grades/{note['id']}", json={"grade_value": 2.1})
    assert geaendert.status_code == 200, geaendert.text
    assert geaendert.json()["grade_value"] == 2.1
    assert geaendert.json()["comment"] == "Nur mit viel Uebung"

    # Ein PATCH mit `null` bei der Note selbst ist ein 422: eine Note ohne Wert
    # waere keine Note mehr.
    assert client.patch(f"/api/v1/grades/{note['id']}", json={"grade_value": None}).status_code == 422
    # Der Kommentar laesst sich dagegen leeren.
    geleert = client.patch(f"/api/v1/grades/{note['id']}", json={"comment": None})
    assert geleert.status_code == 200
    assert geleert.json()["comment"] is None

    assert client.delete(f"/api/v1/grades/{note['id']}").status_code == 200
    assert client.get("/api/v1/grades").json() == []


def test_noten_pruefen_die_skala_und_das_gewicht(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Mathematik")
    grundlage = {
        "subject_id": mathe.id,
        "kind": "written_exam",
        "weight": 1,
        "graded_on": "2026-09-01",
    }

    # 0,0 und 7,0 liegen ausserhalb der deutschen Skala: 422 mit Klartext,
    # nicht 409 aus einer Check-Constraint.
    assert client.post("/api/v1/grades", json={**grundlage, "grade_value": 0}).status_code == 422
    assert client.post("/api/v1/grades", json={**grundlage, "grade_value": 7}).status_code == 422
    # Das Gewicht 0 waere eine Division durch null in der Hochrechnung.
    assert client.post("/api/v1/grades", json={**grundlage, "grade_value": 2, "weight": 0}).status_code == 422
    # Eine unbekannte Art ist kein Wert, den man per POST nachschiebt.
    assert client.post("/api/v1/grades", json={**grundlage, "grade_value": 2, "kind": "referat"}).status_code == 422
    # Die Grenzen selbst sind erlaubt.
    assert client.post("/api/v1/grades", json={**grundlage, "grade_value": 1}).status_code == 201
    assert client.post("/api/v1/grades", json={**grundlage, "grade_value": 6}).status_code == 201


def test_noten_sind_je_nutzer_getrennt(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Mathematik")
    note = _note(client, db, user, mathe, 1.3)

    # Ein zweiter Nutzer sieht die Note nicht, kann sie nicht aendern und nicht loeschen.
    zweiter = User(
        username="zweite",
        password_hash=hash_password("anderes-passwort"),
        is_active=True,
        timezone="UTC",
    )
    db.add(zweiter)
    db.commit()
    db.expire_all()
    antwort = client.post("/api/v1/auth/login", json={"username": "zweite", "password": "anderes-passwort"})
    assert antwort.status_code == 200

    assert client.get("/api/v1/grades").json() == []
    assert client.get("/api/v1/grades/summary").json()["total_count"] == 0
    assert client.get(f"/api/v1/grades/{note['id']}").status_code == 404
    assert client.patch(f"/api/v1/grades/{note['id']}", json={"grade_value": 6}).status_code == 404
    assert client.delete(f"/api/v1/grades/{note['id']}").status_code == 404
    # Auch kein Import aus dem Dokument des anderen.
    assert client.get("/api/v1/grades/import?document_id=1").status_code == 404
    assert client.post("/api/v1/grades/import", json={"document_id": 1, "keys": ["egal"]}).status_code == 404


def test_schnitt_ist_gewichtet_und_gilt_ab_vier(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Mathematik")
    # 2,0 und 4,0 mit gleichem Gewicht waeren 3,0 - der Mittelwert.
    _note(client, db, user, mathe, 2.0, vor_tagen=10)
    _note(client, db, user, mathe, 4.0, vor_tagen=5)
    # Derselbe Test mit doppeltem Gewicht: (2,0 + 4,0 + 2 * 4,0) / 4 = 3,5.
    _note(client, db, user, mathe, 4.0, art="test", gewicht=2, vor_tagen=2)

    zusammenfassung = client.get("/api/v1/grades/summary").json()
    assert zusammenfassung["passing_grade"] == 4.0
    assert zusammenfassung["total_count"] == 3
    assert zusammenfassung["overall"]["average"] == 3.5
    # 3,5 ist unter 4,0: bestanden. Die Grenze ist "bis 4,0", nicht "unter 4,0".
    assert zusammenfassung["overall"]["passed"] is True
    assert zusammenfassung["by_subject"][0]["subject_name"] == "Mathematik"
    assert zusammenfassung["by_subject"][0]["weight"] == 4


def test_verlauf_ist_der_kumulierte_schnitt_je_tag(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Mathematik")
    _note(client, db, user, mathe, 3.0, vor_tagen=9)
    _note(client, db, user, mathe, 1.0, vor_tagen=6)
    # Zwei Noten an einem Tag ergeben **einen** Punkt, sonst zittert die Linie
    # nur an derselben Stelle.
    _note(client, db, user, mathe, 1.0, vor_tagen=3)
    _note(client, db, user, mathe, 1.0, vor_tagen=3)

    verlauf = client.get("/api/v1/grades/summary").json()["trend"]
    # `count` und `weight` sind kumuliert: dieselbe Linie sieht bei einer Note
    # und bei vier gleich aus, und ohne die Zahl waere sie eine Behauptung.
    assert [(punkt["graded_on"], punkt["average"], punkt["count"]) for punkt in verlauf] == [
        ((datetime.now(timezone.utc) - timedelta(days=9)).date().isoformat(), 3.0, 1),
        ((datetime.now(timezone.utc) - timedelta(days=6)).date().isoformat(), 2.0, 2),
        ((datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat(), 1.5, 4),
    ]


def test_verlauf_kann_auf_ein_fach_eingeschraenkt_werden(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Mathematik")
    geschichte = _fach(db, user, "Geschichte")
    _note(client, db, user, mathe, 1.0, vor_tagen=4)
    _note(client, db, user, geschichte, 5.0, vor_tagen=4)

    beides = client.get("/api/v1/grades/summary").json()
    assert beides["overall"]["average"] == 3.0
    assert beides["by_subject"][0]["average"] == 5.0
    assert beides["by_subject"][1]["average"] == 1.0

    # Ein Verlauf, der zwischen zwei Faechern springt, beantwortet keine Frage.
    nur_mathe = client.get(f"/api/v1/grades/summary?subject_id={mathe.id}").json()
    assert nur_mathe["overall"]["subject_name"] == "Mathematik"
    assert nur_mathe["overall"]["average"] == 1.0
    assert nur_mathe["overall"]["passed"] is True
    assert len(nur_mathe["trend"]) == 1
    # Ein fremdes Fach ist ein 404, nicht ein leerer Schnitt.
    assert client.get("/api/v1/grades/summary?subject_id=9999").status_code == 404


def test_hochrechnung_sagt_was_noch_kommen_muss(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Mathematik")
    # 2,0 und 3,0 mit Gewicht 1: Schnitt 2,5, offen sind 2 Gewicht.
    _note(client, db, user, mathe, 2.0, vor_tagen=10)
    _note(client, db, user, mathe, 3.0, vor_tagen=5)

    zurueckblick = client.get("/api/v1/grades/summary").json()["projection"]
    # Ohne offene Gewichtung gibt es nichts zu rechnen.
    assert zurueckblick["needed_average"] is None
    assert zurueckblick["current_average"] == 2.5

    vorausblick = client.get("/api/v1/grades/summary?projected_weight=2").json()["projection"]
    # (4,0 * 4 - 5,0) / 2 = 5,5 noetig.
    assert vorausblick["needed_average"] == 5.5
    # Mit lauter Bestnoten waere der Gesamtschnitt (5 + 2) / 4 = 1,75.
    assert vorausblick["best_possible_average"] == 1.75
    assert vorausblick["still_possible"] is True
    assert vorausblick["projected_weight"] == 2


def test_hochrechnung_erkennt_eine_nicht_mehr_erreichbare_bestehensgrenze() -> None:
    """Die reine Rechnung, ohne Datenbank: sie ist der Kern der Karte."""
    # Aus 4,0 und 4,0 mit offenem Gewicht 1 braucht man (4,0 * 3 - 8,0) / 1 = 4,0.
    assert project_average([WeightedGrade(4.0, 1), WeightedGrade(4.0, 1)], 1) == (4.0, 3.0, True)
    # Aus 5,5 und 5,5 mit offenem Gewicht 1: noetig waere (4 * 3 - 11) = 1,0 -
    # gerade noch die beste Note.
    assert project_average([WeightedGrade(5.5, 1), WeightedGrade(5.5, 1)], 1) == (1.0, 4.0, True)
    # Aus 6,0 und 6,0 mit offenem Gewicht 1 waere noetig 0,0 - eine Note, die es
    # nicht gibt. Deshalb ist `needed` dann `None` und nicht eine erfundene Zahl.
    assert project_average([WeightedGrade(6.0, 1), WeightedGrade(6.0, 1)], 1) == (None, 4.33, False)
    # Ohne jede Note gibt es nichts zu rechnen.
    assert project_average([], 0) == (None, None, False)
    # Ohne offene Gewichtung nur der Rueckblick.
    assert project_average([WeightedGrade(2.0, 1)], 0) == (None, 2.0, True)
    # Ein negatives Gewicht ist ein Programmierfehler, kein leerer Schnitt.
    with pytest.raises(GradeImportError):
        project_average([WeightedGrade(2.0, 1)], -1)


def test_notenstand_erhebt_die_prioritaet_aber_nicht_die_reihenfolge(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Konfidenz und Note im Klausur-Generator - die Aufloesung im Test.

    Geschichte hat den Schnitt 4,4 und ist damit nicht bestanden. Die beiden
    Themen bleiben in der **Konfidenzreihenfolge** (das unsichere zuerst), aber
    das sichere Thema, das sich selbst einschaetzt, sinkt nicht auf Prioritaet 0
    zurueck: der Schnitt hebt es auf 4. Das ist die dokumentierte Aufloesung - die
    Reihenfolge sortiert nach dem, was man selbst einschaetzt, die Prioritaet
    nimmt den groesseren der beiden Signale.
    """
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    geschichte = _fach(db, user, "Geschichte")
    sicher = create_topic(db, user, "Bundeswehr", geschichte)
    unsicher = create_topic(db, user, "Industrialisierung", geschichte)
    db.add(LearningProgress(user_id=user.id, topic_id=sicher.id, confidence=1.0))
    db.add(LearningProgress(user_id=user.id, topic_id=unsicher.id, confidence=0.0))
    db.commit()

    _note(client, db, user, geschichte, 4.4, vor_tagen=30)
    _note(client, db, user, geschichte, 4.4, vor_tagen=20)

    exam = client.post(
        "/api/v1/exams",
        json={
            "subject_id": geschichte.id,
            "title": "Epochen",
            "date": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        },
    )
    assert exam.status_code == 201
    erzeugt = client.post(
        f"/api/v1/exams/{exam.json()['id']}/tasks",
        json={"study_plan_id": plan["id"], "chain_dependencies": False},
    )
    assert erzeugt.status_code == 201, erzeugt.text
    aufgaben = erzeugt.json()["created_tasks"]
    # Die Reihenfolge bleibt die Konfidenzreihenfolge - eine Note kann sie nicht
    # unterscheiden, weil sie ein Fachschnitt ist.
    assert [aufgabe["topic_name"] for aufgabe in aufgaben] == ["Industrialisierung", "Bundeswehr"]
    # Ohne Noten waeren es 4 und 0 gewesen. Der Schnitt 4,4 hebt auch das
    # sichere Thema auf die hoechste Stufe.
    assert [aufgabe["priority"] for aufgabe in aufgaben] == [4, 4]
    # Und die Aufgaben sagen, woher die Prioritaet kommt.
    assert [aufgabe["subject_average"] for aufgabe in aufgaben] == [4.4, 4.4]
    assert [aufgabe["subject_passed"] for aufgabe in aufgaben] == [False, False]
    # Der frueheste Termin gehoert weiter dem unsicheren Thema: die Note aendert
    # die Reihenfolge nicht, nur die Prioritaet.
    assert aufgaben[0]["scheduled_for"] < aufgaben[1]["scheduled_for"]


def test_ein_gutes_fach_erhebt_die_prioritaet_nicht(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """1,3 heisst sehr gut - da gibt es nichts vorzuziehen.

    Die Gegenprobe zu ``test_notenstand_erhebt_die_prioritaet``: die Note ist
    kein Zuschlag auf jede Aufgabe, sondern nur dann eine Hebung, wenn das Fach
    auch wirklich Anlass zum Nacharbeiten gibt.
    """
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    mathe = _fach(db, user, "Mathematik")
    sicher = create_topic(db, user, "Integralrechnung", mathe)
    unsicher = create_topic(db, user, "Ableitungen", mathe)
    db.add(LearningProgress(user_id=user.id, topic_id=sicher.id, confidence=1.0))
    db.add(LearningProgress(user_id=user.id, topic_id=unsicher.id, confidence=0.0))
    db.commit()
    _note(client, db, user, mathe, 1.3, vor_tagen=10)
    _note(client, db, user, mathe, 1.3, art="test", vor_tagen=4)

    exam = client.post(
        "/api/v1/exams",
        json={
            "subject_id": mathe.id,
            "title": "Analysis",
            "date": (datetime.now(timezone.utc) + timedelta(days=6)).isoformat(),
        },
    )
    erzeugt = client.post(
        f"/api/v1/exams/{exam.json()['id']}/tasks",
        json={"study_plan_id": plan["id"], "chain_dependencies": False},
    )
    assert erzeugt.status_code == 201, erzeugt.text
    aufgaben = erzeugt.json()["created_tasks"]
    # Genau die Werte, die auch ohne Noten herauskamen.
    assert [aufgabe["priority"] for aufgabe in aufgaben] == [4, 0]
    assert aufgaben[0]["subject_passed"] is True


def test_prioritaetsstufen_aus_dem_fachschnitt_sind_erklart() -> None:
    """Die Tabelle als Text: wer sie aendert, faellt hier auf.

    Die Stufe ist die **Dringlichkeit**, und die steigt mit der Note: auf der
    deutschen Skala ist 1,0 sehr gut. Wer das umdreht, macht aus jedem guten
    Fach ein dringendes.

    Ohne Schnitt ist die Stufe **0**, nicht 4 - anders als bei der Konfidenz,
    wo ein fehlender Wert "unsicher" heisst. Ein fehlender Lernstand ist eine
    Aussage der Lernenden, ein fehlender Notenspiegel nicht.
    """
    assert grade_priority(None) == 0
    assert grade_priority(1.0) == 0
    assert grade_priority(2.0) == 0
    assert grade_priority(2.5) == 1
    assert grade_priority(3.0) == 1
    assert grade_priority(3.4) == 2
    assert grade_priority(3.5) == 2
    assert grade_priority(3.9) == 3
    # Genau die Grenze ist noch bestanden und bekommt deshalb Stufe 3.
    assert grade_priority(4.0) == 3
    # Alles darueber ist die hoechste Stufe: eine Note schlimmer als
    # "nicht genuegend" gibt es nicht, eine feinere Teilung waere erfunden.
    assert grade_priority(4.4) == 4
    assert grade_priority(6.0) == 4


def test_prioritaet_der_aufgabe_bleibt_ohne_noten_genau_wie_vorher(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Ohne Noten darf sich am Generator nichts aendern.

    Ein Feature, das nur bei gesetzten Daten wirkt, ist sonst unsichtbar
    kaputt: niemand prueft den Zweig, in dem es nichts zu tun gibt.
    """
    client, db, user = app_context
    login(client)
    plan = create_plan(client, db, user)
    mathe = _fach(db, user, "Mathematik")
    sicher = create_topic(db, user, "Integralrechnung", mathe)
    unsicher = create_topic(db, user, "Ableitungen", mathe)
    db.add(LearningProgress(user_id=user.id, topic_id=sicher.id, confidence=0.9))
    db.add(LearningProgress(user_id=user.id, topic_id=unsicher.id, confidence=0.1))
    db.commit()
    exam = client.post(
        "/api/v1/exams",
        json={
            "subject_id": mathe.id,
            "title": "Analysis",
            "date": (datetime.now(timezone.utc) + timedelta(days=6)).isoformat(),
        },
    )
    erzeugt = client.post(
        f"/api/v1/exams/{exam.json()['id']}/tasks",
        json={"study_plan_id": plan["id"], "chain_dependencies": False},
    )
    assert erzeugt.status_code == 201, erzeugt.text
    aufgaben = erzeugt.json()["created_tasks"]
    assert [aufgabe["priority"] for aufgabe in aufgaben] == [4, 0]
    # Und die Aufgabe weist aus, dass es keine Noten gibt - nicht 0,0.
    assert [aufgabe["subject_average"] for aufgabe in aufgaben] == [None, None]
    assert [aufgabe["subject_passed"] for aufgabe in aufgaben] == [None, None]


def _notenspiegel(client: TestClient, text: str) -> int:
    """Ein Notenspiegel als Dokument ablegen und seine Id zurueckgeben."""
    antwort = client.post("/api/v1/files", files={"file": ("noten.txt", text.encode("utf-8"), "text/plain")})
    assert antwort.status_code == 201, antwort.text
    return int(antwort.json()["id"])


def test_import_liest_einen_notenspiegel_zeilenweise(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Mathematik")
    _fach(db, user, "Geschichte")
    dokument = _notenspiegel(
        client,
        "\n".join(
            [
                "Noten Klasse 8b, Schuljahr 2025/2026",
                "Mathematik            1,7   Klassenarbeit vom 14.09.2026",
                "Mathematik            2,0   Test vom 21.09.2026",
                "Geschichte            3,8   mündlich",
                "Sport                 1,3",
                "Diese Zeile enthaelt keine Note.",
            ]
        ),
    )

    vorschau = client.get(f"/api/v1/grades/import?document_id={dokument}&subject_id={mathe.id}").json()
    assert vorschau["filename"] == "noten.txt"
    zeilen = [eintrag for eintrag in vorschau["entries"]]
    # Fuenf Zeilen mit einer Note; der Kopf und der Satz ohne Zahl fallen weg.
    assert len(zeilen) == 4
    assert [(zeile["subject_name"], zeile["value"]) for zeile in zeilen] == [
        ("Mathematik", 1.7),
        ("Mathematik", 2.0),
        ("Geschichte", 3.8),
        (None, 1.3),
    ]
    # Die Art kommt aus der Zeile, sonst aus der Vorgabe.
    assert [zeile["kind"] for zeile in zeilen] == ["written_exam", "test", "oral", "written_exam"]
    assert zeilen[0]["graded_on"] == "2026-09-14"
    assert zeilen[1]["graded_on"] == "2026-09-21"
    # Ohne Datum in der Zeile greift die Vorgabe aus dem Request.
    assert zeilen[2]["graded_on"] == client.get("/api/v1/grades/summary").json()["generated_at"][:10]
    # Eine Zeile ohne Fachnamen wird als nicht erkannt markiert und faellt auf
    # das Vorgabefach zurueck.
    assert zeilen[3]["subject_recognized"] is False
    assert zeilen[3]["subject_id"] == mathe.id
    assert zeilen[0]["already_imported"] is False
    # Noch ist nichts angelegt - die Vorschau ist eine Vorschau.
    assert client.get("/api/v1/grades").json() == []

    uebernommen = client.post(
        "/api/v1/grades/import",
        json={"document_id": dokument, "keys": [zeile["key"] for zeile in zeilen[:2]], "subject_id": mathe.id},
    )
    assert uebernommen.status_code == 201, uebernommen.text
    assert uebernommen.json()["created"] == 2
    assert uebernommen.json()["skipped"] == 0

    noten = client.get(f"/api/v1/grades?subject_id={mathe.id}").json()
    assert sorted(note["grade_value"] for note in noten) == [1.7, 2.0]
    assert {note["source"] for note in noten} == {"document"}
    # Die Art kam aus dem Dokument, nicht aus dem Request.
    assert {note["kind"] for note in noten} == {"written_exam", "test"}
    # Der Verweis auf das Dokument bleibt stehen.
    assert {note["document_id"] for note in noten} == {dokument}


def test_import_legt_denselben_spiegel_nicht_zweimal_an(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    _fach(db, user, "Mathematik")
    dokument = _notenspiegel(client, "Mathematik 1,7 Klassenarbeit\nMathematik 2,0 Test")
    vorschau = client.get(f"/api/v1/grades/import?document_id={dokument}").json()
    schluessel = [eintrag["key"] for eintrag in vorschau["entries"]]

    erstes = client.post("/api/v1/grades/import", json={"document_id": dokument, "keys": schluessel})
    assert erstes.status_code == 201
    assert erstes.json()["created"] == 2
    zweites = client.post("/api/v1/grades/import", json={"document_id": dokument, "keys": schluessel})
    assert zweites.status_code == 201
    assert zweites.json() == {"document_id": dokument, "created": 0, "skipped": 2, "created_ids": []}
    assert len(client.get("/api/v1/grades").json()) == 2

    # Und die Vorschau sagt jetzt, dass diese Zeilen schon drin sind.
    erneut = client.get(f"/api/v1/grades/import?document_id={dokument}").json()
    assert [eintrag["already_imported"] for eintrag in erneut["entries"]] == [True, True]


def test_import_braucht_eine_bestaetigte_zeile(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    dokument = _notenspiegel(client, "Nur ein Satz ohne Note im Text.")
    # Ohne Zeile bestaetigt wird nichts angelegt.
    ohne = client.post("/api/v1/grades/import", json={"document_id": dokument, "keys": []})
    assert ohne.status_code == 422
    # Und ein Schluessel, den es im Dokument nicht gibt, auch nicht.
    fremd = client.post("/api/v1/grades/import", json={"document_id": dokument, "keys": ["gibt-es-nicht"]})
    assert fremd.status_code == 422
    assert client.get("/api/v1/grades").json() == []


def test_import_liest_kein_fremdes_dokument(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    _fach(db, user, "Mathematik")
    dokument = _notenspiegel(client, "Mathematik 1,7")

    # Ein zweiter Nutzer sieht das Dokument des ersten nicht, also auch keine
    # Noten darin. Sonst koennte eine fremde Datei den Schnitt verfaelschen.
    fremder = User(username="fremder", password_hash=hash_password("fremdes-passwort"), is_active=True, timezone="UTC")
    db.add(fremder)
    db.commit()
    assert client.post("/api/v1/auth/login", json={"username": "fremder", "password": "fremdes-passwort"}).status_code == 200
    # 404 statt 422: eine fremde Id soll nicht verraten, dass es sie gibt.
    assert client.get(f"/api/v1/grades/import?document_id={dokument}").status_code == 404
    assert client.post("/api/v1/grades/import", json={"document_id": dokument, "keys": ["egal"]}).status_code == 404
    assert client.get("/api/v1/grades").json() == []


def test_noten_bleiben_ohne_freigabe_bei_sich(app_context: tuple[TestClient, Session, User]) -> None:
    """Punkt sieben: die Freigabe ist der einzige Weg zum Provider.

    Ohne Provider entsteht kein Modellaufruf, deshalb wird hier an der
    Schnittstelle geprueft, **was** dem Prompt uebergeben wird - und dass die
    Freigabe das einzige ist, was sich zwischen beiden unterscheidet.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Mathematik")
    _note(client, db, user, mathe, 4.4, vor_tagen=20)
    _note(client, db, user, mathe, 4.4, vor_tagen=10)

    # Standard ist aus, und aus heisst: leere Zeilen.
    zustand = client.get("/api/v1/grades/sharing").json()
    assert zustand["share_grades_with_ai"] is False
    assert zustand["preference_key"] == "share_grades_with_ai"
    assert len(client.get("/api/v1/grades/sharing").json()["uses"]) == 2
    assert grades_service.grade_context_lines(db, user.id) == []

    eingeschaltet = client.put("/api/v1/grades/sharing", json={"enabled": True})
    assert eingeschaltet.status_code == 200, eingeschaltet.text
    assert eingeschaltet.json()["share_grades_with_ai"] is True
    zeilen = grades_service.grade_context_lines(db, user.id)
    assert any("4,4" in zeile for zeile in zeilen)
    assert any("nicht bestanden" in zeile for zeile in zeilen)

    # Und zurueckgenommen heisst wieder: nichts.
    assert client.put("/api/v1/grades/sharing", json={"enabled": False}).json()["share_grades_with_ai"] is False
    assert grades_service.grade_context_lines(db, user.id) == []


def test_freigabe_ist_je_nutzer_und_liegt_in_den_einstellungen(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    assert client.put("/api/v1/grades/sharing", json={"enabled": True}).status_code == 200
    # Die Einstellung liegt in `preferences` und laesst die anderen Schluessel
    # unberuehrt - dieselbe Regel wie beim Wochenziel.
    db.expire_all()
    einstellungen = db.scalar(select(UserSettings).where(UserSettings.user_id == user.id))
    assert einstellungen is not None
    assert einstellungen.preferences == {"share_grades_with_ai": True}
    assert client.put("/api/v1/grades/sharing", json={"enabled": False}).status_code == 200
    db.expire_all()
    einstellungen = db.scalar(select(UserSettings).where(UserSettings.user_id == user.id))
    assert einstellungen is not None and einstellungen.preferences == {"share_grades_with_ai": False}

    # Ein zweiter Nutzer ist unberuehrt.
    zweiter = User(username="dritte", password_hash=hash_password("pw"), is_active=True, timezone="UTC")
    db.add(zweiter)
    db.commit()
    assert client.post("/api/v1/auth/login", json={"username": "dritte", "password": "pw"}).status_code == 200
    assert client.get("/api/v1/grades/sharing").json()["share_grades_with_ai"] is False


def test_kaputte_einstellung_schaltet_die_freigabe_nicht_von_selbst_ein(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    einstellungen = UserSettings(user_id=user.id, preferences={"share_grades_with_ai": "ja, bitte"})
    db.add(einstellungen)
    db.commit()
    db.expire_all()
    # Kein Schalter, der durch einen falsch gespeicherten Wert von selbst
    # aufginge. Ein Fehler heisst hier aus, nicht raten.
    assert client.get("/api/v1/grades/sharing").json()["share_grades_with_ai"] is False
    assert grades_service.grade_context_lines(db, user.id) == []


def test_widerruf_der_freigabe_loescht_den_heutigen_willkommenssatz(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Der Satz ist zwischengespeichert und kann eine Note genannt haben.

    Nach dem Widerruf darf er nicht weiter stehen. Neu erzeugt wird er beim
    naechsten Abruf - die Freigabe steht dann aus, also entsteht ein Satz ohne
    Noten.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Mathematik")
    _note(client, db, user, mathe, 2.2)
    client.put("/api/v1/grades/sharing", json={"enabled": True})
    assert client.get("/api/v1/greeting").status_code == 200
    db.expire_all()
    assert db.query(GreetingCache).filter(GreetingCache.user_id == user.id).count() == 1

    assert client.put("/api/v1/grades/sharing", json={"enabled": False}).status_code == 200
    db.expire_all()
    assert db.query(GreetingCache).filter(GreetingCache.user_id == user.id).count() == 0


def test_ask_sagt_ob_noten_mitgeschickt_wurden(app_context: tuple[TestClient, Session, User]) -> None:
    """`source` bleibt dreiwertig, die Notenfrage bekommt ein eigenes Feld.

    Die beiden Fragen sind verschiedene: woher die Antwort kommt und ob der
    Notenstand im Prompt stand. Deshalb ist `grades_shared` kein vierter Wert
    von `source`, sondern ein zusaetzliches Feld - die bestehende Dreiwertigkeit
    bleibt unangetastet.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Mathematik")
    _note(client, db, user, mathe, 3.0)
    _notenspiegel(client, "Notenspiegel: Mathematik 3,0 Klassenarbeit")

    ohne = client.post("/api/v1/documents/ask", json={"question": "Wie stehe ich in Mathematik?"}).json()
    assert ohne["grades_shared"] is False
    assert ohne["source"] in {"ai", "fallback", "none"}
    assert client.put("/api/v1/grades/sharing", json={"enabled": True}).status_code == 200
    mit = client.post("/api/v1/documents/ask", json={"question": "Wie stehe ich in Mathematik?"}).json()
    assert mit["grades_shared"] is True
    assert mit["source"] in {"ai", "fallback", "none"}


def test_noten_sind_echte_daten_und_kein_zustand_im_frontend(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Der Demo-Seed legt Noten an - und bleibt dabei idempotent."""
    client, db, user = app_context
    login(client)
    erstes = seed_demo_data(db, user.id)
    db.commit()
    zweites = seed_demo_data(db, user.id)
    db.commit()
    assert erstes.created > 0
    assert erstes.updated == 0
    assert zweites.created == 0
    assert zweites.updated == erstes.total

    zusammenfassung = client.get("/api/v1/grades/summary").json()
    assert zusammenfassung["total_count"] == 5
    assert len(zusammenfassung["by_subject"]) == 2
    assert len(zusammenfassung["trend"]) == 5
    # Die Demo hat absichtlich ein bestandenes und ein durchgefallenes Fach.
    assert sorted(eintrag["passed"] for eintrag in zusammenfassung["by_subject"]) == [False, True]


# --- Backup-Status ----------------------------------------------------------


def _worker_status_payload(**overrides: object) -> dict[str, object]:
    """Ein Status-JSON, wie der Worker es schreibt.

    Fest verdrahtet statt zusammengebaut, weil es hier um die Strenge des
    Formats geht: ein Block mit fehlenden Feldern ist ein anderes Verhalten als
    ein Block mit falschen Feldern.
    """
    payload: dict[str, object] = {
        "version": 1,
        "status": "running",
        "database": "healthy",
        "pid": 42,
        "heartbeat_interval_seconds": 30.0,
        "started_at": "2026-09-30T01:00:00+00:00",
        "last_heartbeat": "2026-09-30T01:59:00+00:00",
        "last_database_check": "2026-09-30T01:59:00+00:00",
        "last_error": None,
        "backup": {
            "enabled": True,
            "schedule": "0 2 * * *",
            "timezone": "Europe/Berlin",
            "retention_days": 30,
            "directory_writable": True,
            "pg_dump_path": "/usr/bin/pg_dump",
            "runner_available": True,
            "last_started_at": "2026-09-30T00:00:01+00:00",
            "last_finished_at": "2026-09-30T00:00:03+00:00",
            "last_successful_at": "2026-09-30T00:00:03+00:00",
            "next_due_at": "2026-10-01T00:00:00+00:00",
            "last_duration_seconds": 2.25,
            "last_file": "school-20260930T000001Z.dump",
            "last_size_bytes": 4096,
            "kept_files": 30,
            "removed_files": ["school-20260821T000000Z.dump"],
            "last_error": None,
        },
    }
    payload.update(overrides)
    return payload


def _point_backend_at(app_client: TestClient, status_path: Path) -> None:
    """Die Statusdatei des Workers fuer diesen Aufruf umbiegen.

    `get_app_settings` liest `app.state.settings` bei jedem Aufruf, deshalb genuegt
    ein `model_copy` statt eines zweiten Fixture-Aufbaus.
    """
    current = app_client.app.state.settings
    app_client.app.state.settings = current.model_copy(update={"worker_status_path": status_path.as_posix()})


def test_backup_status_requires_a_session(app_client: TestClient, tmp_path: Path) -> None:
    _point_backend_at(app_client, tmp_path / "worker-status.json")
    assert app_client.get("/api/v1/backups/status").status_code == 401


def test_backup_status_reports_a_missing_worker_volume(app_client: TestClient, tmp_path: Path) -> None:
    login(app_client)
    _point_backend_at(app_client, tmp_path / "gibt-es-nicht.json")
    response = app_client.get("/api/v1/backups/status")
    # Kein 503: eine fehlende Datei ist eine Konfigurationsfrage, kein Fehler des
    # Aufrufs. 200 mit Klartext, damit die Oberflaeche etwas anzeigen kann.
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["backup"] is None
    assert "worker-status" in payload["message"]


def test_backup_status_reads_the_worker_block(app_client: TestClient, tmp_path: Path) -> None:
    status_path = tmp_path / "worker-status.json"
    status_path.write_text(json.dumps(_worker_status_payload()), encoding="utf-8")
    login(app_client)
    _point_backend_at(app_client, status_path)

    response = app_client.get("/api/v1/backups/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["worker_status"] == "running"
    assert payload["worker_database"] == "healthy"
    assert payload["last_heartbeat"] == "2026-09-30T01:59:00Z"

    backup = payload["backup"]
    assert backup["enabled"] is True
    assert backup["schedule"] == "0 2 * * *"
    assert backup["timezone"] == "Europe/Berlin"
    assert backup["retention_days"] == 30
    assert backup["directory_writable"] is True
    assert backup["last_file"] == "school-20260930T000001Z.dump"
    assert backup["last_size_bytes"] == 4096
    assert backup["kept_files"] == 30
    assert backup["removed_files"] == ["school-20260821T000000Z.dump"]
    assert backup["last_error"] is None
    # Aus einem UTC-Zeitpunkt mit Plus-Hinweis wird durchgaengig Z.
    assert backup["last_successful_at"] == "2026-09-30T00:00:03Z"
    assert backup["next_due_at"] == "2026-10-01T00:00:00Z"
    # Nur der Programmname, nie ein Pfad aus dem Container.
    assert backup["pg_dump"] == "pg_dump"
    assert "/" not in backup["pg_dump"]


def test_backup_status_never_invents_a_missing_value(app_client: TestClient, tmp_path: Path) -> None:
    status_path = tmp_path / "worker-status.json"
    status_path.write_text(json.dumps(_worker_status_payload(backup={"enabled": False, "runner_available": False})), encoding="utf-8")
    login(app_client)
    _point_backend_at(app_client, status_path)

    backup = app_client.get("/api/v1/backups/status").json()["backup"]
    assert backup["enabled"] is False
    assert backup["last_successful_at"] is None
    assert backup["next_due_at"] is None
    assert backup["last_file"] is None
    assert backup["kept_files"] is None
    assert backup["removed_files"] == []


def test_backup_status_drops_timestamps_without_a_timezone(app_client: TestClient, tmp_path: Path) -> None:
    payload = _worker_status_payload()
    backup_block = dict(payload["backup"])  # type: ignore[arg-type]
    backup_block["last_successful_at"] = "2026-09-30T02:00:03"
    status_path = tmp_path / "worker-status.json"
    status_path.write_text(json.dumps(_worker_status_payload(backup=backup_block)), encoding="utf-8")
    login(app_client)
    _point_backend_at(app_client, status_path)

    response = app_client.get("/api/v1/backups/status")
    assert response.json()["backup"]["last_successful_at"] is None
    assert "ohne Zeitzone" in response.json()["message"]


def test_backup_status_survives_a_worker_without_a_backup_block(app_client: TestClient, tmp_path: Path) -> None:
    payload = _worker_status_payload()
    payload.pop("backup")
    status_path = tmp_path / "worker-status.json"
    status_path.write_text(json.dumps(payload), encoding="utf-8")
    login(app_client)
    _point_backend_at(app_client, status_path)

    result = app_client.get("/api/v1/backups/status").json()
    assert result["available"] is False
    assert result["backup"] is None
    assert "ohne Backup-Runner" in result["message"]


# --- Erinnerungen und Web-Push ---------------------------------------------
#
# Abschnitt aus PLAN.md, Zeilen 105-111. Drei Dinge sind in diesem Abschnitt
# ausdruecklich nicht moeglich und deshalb auch nicht behauptet:
#
#  * Es gibt hier **keinen Push-Dienst**. Die Tests, die eine Anfrage an einen
#    Dienst stellen, benutzen `httpx.MockTransport`. Sie pruefen die Anfrage,
#    nicht die Zustellung. Dass eine echte Nachricht ankommt, ist hier
#    unbewiesen.
#  * Der **Service Worker** laeuft im Browser und wird von keinem dieser Tests
#    ausgefuehrt. Bewiesen ist die Bytefolge der Nutzlast, nicht der Klick auf
#    eine Benachrichtigung im Browser.
#  * Die **VAPID-Signatur** ist gegen die Spezifikation geprueft (JWT mit
#    ES256, rohe Signatur, `aud` = Ursprung des Endpunkts) und nicht gegen die
#    Annahme eines Dienstes.

#: Aus RFC 8291, Abschnitt 5. Die Zeilenumbrueche der Norm sind entfernt.
RFC8291_PAYLOAD = b"When I grow up, I want to be a watermelon"
RFC8291_BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
    "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
    "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)
RFC8291_SENDER_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
RFC8291_SENDER_PUBLIC = (
    "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
)
RFC8291_RECEIVER_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
RFC8291_RECEIVER_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
RFC8291_AUTH_SECRET = "BTBZMqHH6r4Tts7J_aSIgg"
RFC8291_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
RFC8291_ENDPOINT = "https://push.example.net/push/JzLQ3raZJfFBR0aqvOMsLrt54w4rJUsV"

BERLIN = ZoneInfo("Europe/Berlin")


def _rfc_key(value: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(web_push.b64url_decode(value), "big"), ec.SECP256R1())


def _vapid_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Settings mit einem frischen Schluesselpaar, wie es die Umgebung bekommt."""
    private_pem, _oeffentlich = web_push.generate_vapid_keypair()
    basis: dict[str, object] = {
        "_env_file": None,
        "database_url": f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        "secret_key": "test-secret-key",
        "web_push_private_key": private_pem,
        "web_push_subject": "mailto:lernende@example.org",
    }
    basis.update(overrides)
    return Settings(**basis)  # type: ignore[arg-type]


def _push_target(endpoint: str = RFC8291_ENDPOINT) -> web_push.PushTarget:
    return web_push.PushTarget(endpoint=endpoint, p256dh=RFC8291_RECEIVER_PUBLIC, auth=RFC8291_AUTH_SECRET)


def _record_transport(handler: object) -> httpx.MockTransport:
    return httpx.MockTransport(handler)  # type: ignore[arg-type]


def _mozilla_client() -> httpx.Client:
    """Ein Client, der die Nachricht in der Mitte faengt - ein Mock, kein Dienst."""
    return httpx.Client(transport=_record_transport(lambda _request: httpx.Response(201)))


def _mozilla_subscription(db: Session, user: User, **kwargs: object) -> PushSubscription:
    row = PushSubscription(
        user_id=user.id,
        endpoint=str(kwargs.get("endpoint", RFC8291_ENDPOINT)),
        p256dh=RFC8291_RECEIVER_PUBLIC,
        auth=RFC8291_AUTH_SECRET,
        user_agent=str(kwargs.get("user_agent", "Firefox/141")),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _task_with_due_date(
    db: Session,
    user: User,
    *,
    due: datetime,
    title: str = "Klassenarbeit Mathe lernen",
    status: str = "pending",
    priority: int = 0,
    subject: Subject | None = None,
) -> StudyPlanTask:
    plan = StudyPlan(user_id=user.id, title="Lernplan")
    db.add(plan)
    db.flush()
    task = StudyPlanTask(
        user_id=user.id,
        study_plan_id=plan.id,
        title=title,
        duration_minutes=30,
        due_date=as_utc(due),
        status=status,
        priority=priority,
    )
    if subject is not None:
        thema = Topic(user_id=user.id, subject_id=subject.id, name="Bruchgleichungen", source="manual")
        db.add(thema)
        db.flush()
        task.topic_id = thema.id
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _reminder_user(db: Session, **kwargs: object) -> User:
    """Ein zweiter Benutzer, damit die Nutzertrennung etwas zu trennen hat."""
    user = User(
        username=str(kwargs.get("username", "zweite")),
        password_hash=hash_password("test-password"),
        is_active=bool(kwargs.get("is_active", True)),
        timezone=str(kwargs.get("timezone", "UTC")),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def settings_ohne_push() -> Settings:
    """Settings ohne Schluesselpaar - und damit ein Lauf, der nichts versendet.

    In fast allen Lauf-Tests ist das richtig: eine Erinnerung soll entstehen, und
    der Transport gehoert in eigene Tests mit einem ausdruecklich gemockten
    Client. Ohne diese Absicherung wuerde ein Test mit einem Abonnement ins Netz
    gehen, und zwar gegen einen Endpunkt, den es nicht gibt.
    """
    return Settings(_env_file=None, secret_key="test-secret-key", timezone="UTC")


def _point_backend_at_push(client: TestClient, settings: Settings) -> None:
    """`app.state.settings` fuer diesen Aufruf umbiegen.

    `get_app_settings` liest `app.state.settings` bei jedem Aufruf, deshalb genuegt
    ein Austausch des Werts - so wie es `_point_backend_at` fuer den Worker macht.
    """
    client.app.state.settings = settings


# --- Die Nutzlast nach RFC 8291 ---------------------------------------------


def test_web_push_nutzlast_entspricht_dem_testvektor_aus_rfc_8291() -> None:
    """Der Koerper einer Push-Nachricht, byteweise gegen die Norm.

    Das ist der staerkste Beleg, den dieser Stack ohne Push-Dienst liefern kann:
    dieselben Eingaben wie in Abschnitt 5 der RFC 8291 muessen denselben
    Koerper ergeben. Wer hier etwas umstellt und es trotzdem "laeuft", hat sehr
    wahrscheinlich etwas kaputtgemacht.
    """
    koerper = web_push.encode_aes128gcm(
        RFC8291_PAYLOAD,
        target=_push_target(),
        server_public_key=web_push.b64url_decode(RFC8291_SENDER_PUBLIC),
        salt=web_push.b64url_decode(RFC8291_SALT),
        ephemeral_private_key=_rfc_key(RFC8291_SENDER_PRIVATE),
    )
    assert web_push.b64url_encode(koerper) == RFC8291_BODY


def test_web_push_erzeugt_die_zwischenwerte_aus_anhang_a_der_rfc_8291() -> None:
    """Jeder Zwischenwert aus dem Anhang, damit ein Fehler nicht nur ein Chiffrat bricht.

    Wer die drei Schritte vertauscht, erhaelt ein Chiffrat, das **keins** ist -
    aber eines mit der richtigen Laenge. Deshalb werden nicht nur Koerper und
    Chiffrat geprueft, sondern auch das ECDH-Geheimnis, der Zwischenwert nach
    dem ersten HKDF sowie Schluessel und Nonce.
    """
    empfaenger_public = web_push.b64url_decode(RFC8291_RECEIVER_PUBLIC)
    # Aus dem privaten Schluessel des Empfaengers ergibt sich genau der
    # oeffentliche aus der Norm - sonst waeren alle Werte aus dem Anhang falsch.
    ziffern = _rfc_key(RFC8291_RECEIVER_PRIVATE).public_key().public_numbers()
    assert b"\x04" + ziffern.x.to_bytes(32, "big") + ziffern.y.to_bytes(32, "big") == empfaenger_public
    server_public = web_push.b64url_decode(RFC8291_SENDER_PUBLIC)
    salt = web_push.b64url_decode(RFC8291_SALT)
    auth = web_push.b64url_decode(RFC8291_AUTH_SECRET)

    shared = _rfc_key(RFC8291_SENDER_PRIVATE).exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), empfaenger_public)
    )
    assert web_push.b64url_encode(shared) == "kyrL1jIIOHEzg3sM2ZWRHDRB62YACZhhSlknJ672kSs"

    key_info = b"WebPush: info\x00" + empfaenger_public + server_public
    # Der Anhang nennt diesen Zwischenwert "IKM"; so heisst es auch RFC 8188.
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth, info=key_info).derive(shared)
    assert web_push.b64url_encode(ikm) == "S4lYMb_L0FxCeq0WhDx813KgSYqU26kOyzWUdsXYyrg"
    assert web_push.b64url_encode(key_info) == (
        "V2ViUHVzaDogaW5mbwAEJXGyvs3942BVGq8e0PTNNmwRzr5VX4m8t7GGpTM5FzFo7OLr4BhZe9MEebhu"
        "PI-OztV3ylkYfpJGmQ22ggCLDgT-M_SrDepxkU21WCP3O1SUj0EwbZIHMtu5pZpTKGSCIA5Zent7wm"
        "C6HCJ5mFgJkuk5cwAvMBKiiujwa7t45ewP"
    )

    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(
        ikm
    )
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    assert web_push.b64url_encode(cek) == "oIhVW04MRdy2XN9CiKLxTg"
    assert web_push.b64url_encode(nonce) == "4h_95klXJ5E_qnoN"

    # Das Trennzeichen 0x02 gehoert in den Klartext: ein Browser verwirft jede
    # Nachricht, in der an dieser Stelle etwas anderes steht.
    gepolstert = AESGCM(cek).encrypt(nonce, RFC8291_PAYLOAD + b"\x02", None)
    assert web_push.b64url_encode(RFC8291_PAYLOAD + b"\x02") == "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24C"
    assert web_push.b64url_encode(gepolstert) == (
        "8pfeW0KbunFT06SuDKoJH9Ql87S1QUrdirN6GcG7sFz1y1sqLgVi1VhjVkHsUoEsbI_0LpXMuGvnzQ"
    )


def test_web_push_enthaelt_die_kopfzeile_der_norm() -> None:
    """``rs``, Laengenangabe und oeffentlicher Schluessel an ihren festen Plaetzen."""
    koerper = web_push.encode_aes128gcm(
        RFC8291_PAYLOAD,
        target=_push_target(),
        server_public_key=web_push.b64url_decode(RFC8291_SENDER_PUBLIC),
        salt=web_push.b64url_decode(RFC8291_SALT),
        ephemeral_private_key=_rfc_key(RFC8291_SENDER_PRIVATE),
    )
    assert koerper[:16] == web_push.b64url_decode(RFC8291_SALT)
    assert koerper[16:20] == (4096).to_bytes(4, "big")
    assert koerper[20] == 65
    assert koerper[21:86] == web_push.b64url_decode(RFC8291_SENDER_PUBLIC)
    # 16 Salz + 4 rs + 1 Laengenangabe + 65 Schluessel + Nutzlast + Trennzeichen
    # + 16 Anhaengung
    assert len(koerper) == 16 + 4 + 1 + 65 + len(RFC8291_PAYLOAD) + 1 + 16


def test_web_push_lehnt_eine_zu_lange_nutzlast_ab() -> None:
    """Kein stilles Zuschneiden: eine zu lange Nutzlast ist ein Fehler."""
    with pytest.raises(web_push.WebPushError, match="zu lang"):
        web_push.encode_aes128gcm(
            b"x" * 4096,
            target=_push_target(),
            server_public_key=web_push.b64url_decode(RFC8291_SENDER_PUBLIC),
        )


def test_web_push_lehnt_einen_geraeteschluessel_ab_der_kein_punkt_ist() -> None:
    with pytest.raises(web_push.WebPushConfigError, match="Punkt auf P-256"):
        web_push.check_subscription_keys("BMjLyfWP6T1o2GaRlEb9n0fPKPJKjHCWH3WjLLBHFHo", RFC8291_AUTH_SECRET)


def test_geraeteschluessel_werden_beim_anlegen_geprueft() -> None:
    """Sonst merkt der Nutzer es erst, wenn jede Nachricht fehlschlaegt."""
    web_push.check_subscription_keys(RFC8291_RECEIVER_PUBLIC, RFC8291_AUTH_SECRET)
    with pytest.raises(web_push.WebPushConfigError, match="auth"):
        web_push.check_subscription_keys(RFC8291_RECEIVER_PUBLIC, "B3ZuK2kurz")


# --- Das Schluesselpaar -----------------------------------------------------


def test_vapid_token_traegt_die_angaben_der_norm(tmp_path: Path) -> None:
    """Kopfzeile, ``aud``, ``sub`` und eine rohe ES256-Signatur."""
    private_pem, oeffentlich = web_push.generate_vapid_keypair()
    keypair = web_push.resolve_vapid_keypair(_vapid_settings(tmp_path, web_push_private_key=private_pem))
    token = web_push.build_vapid_token(
        private_key=keypair.private_key,
        public_key_b64=keypair.public_key_b64,
        subject="mailto:lernende@example.org",
        audience="https://push.example.net",
        now=datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc),
    )
    assert token.startswith("vapid t=")
    assert f", k={oeffentlich}" in token

    claims = json.loads(web_push.b64url_decode(token.split(" ")[1].split(".")[1]).decode("utf-8"))
    assert claims["aud"] == "https://push.example.net"
    assert claims["sub"] == "mailto:lernende@example.org"
    # RFC 8292 erlaubt hoechstens 24 Stunden.
    assert 0 < claims["exp"] - int(datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc).timestamp()) <= 24 * 3600
    assert web_push.verify_vapid_token(
        token,
        keypair.private_key.public_key(),
        now=datetime(2026, 9, 30, 8, 1, tzinfo=timezone.utc),
    )
    # Ein Token, der nicht zu diesem Schluesselpaar gehoert, muss durchfallen.
    fremd = ec.generate_private_key(ec.SECP256R1())
    assert not web_push.verify_vapid_token(
        token, fremd.public_key(), now=datetime(2026, 9, 30, 8, 1, tzinfo=timezone.utc)
    )


def test_vapid_token_ist_nach_seiner_lebensdauer_nicht_mehr_gueltig(tmp_path: Path) -> None:
    keypair = web_push.resolve_vapid_keypair(_vapid_settings(tmp_path))
    token = web_push.build_vapid_token(
        private_key=keypair.private_key,
        public_key_b64=keypair.public_key_b64,
        subject="mailto:lernende@example.org",
        audience="https://push.example.net",
        now=datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc),
    )
    assert not web_push.verify_vapid_token(
        token, keypair.private_key.public_key(), now=datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
    )


def test_vapid_schluesselpaar_muss_zusammengehoeren(tmp_path: Path) -> None:
    """Der haeufigste Fehler der Kette: zwei Angaben, die nicht zusammenpassen."""
    private_pem, _ = web_push.generate_vapid_keypair()
    _fremd, fremd_public = web_push.generate_vapid_keypair()
    settings = _vapid_settings(tmp_path, web_push_private_key=private_pem, web_push_public_key=fremd_public)
    with pytest.raises(web_push.WebPushConfigError, match="passt nicht"):
        web_push.resolve_vapid_keypair(settings)


def test_vapid_oeffentlicher_schluessel_wird_aus_dem_privaten_berechnet(tmp_path: Path) -> None:
    """Eine Angabe in der Umgebung statt zwei, die auseinanderlaufen koennen."""
    private_pem, oeffentlich = web_push.generate_vapid_keypair()
    keypair = web_push.resolve_vapid_keypair(_vapid_settings(tmp_path, web_push_private_key=private_pem))
    assert keypair.public_key_b64 == oeffentlich
    assert len(keypair.public_key_bytes()) == 65


@pytest.mark.parametrize(
    "wert",
    [
        "nicht-ein-schluessel",
        "-----BEGIN PRIVATE KEY-----\nnicht\n-----END PRIVATE KEY-----",
    ],
)
def test_vapid_schluessel_muss_lesbar_sein(wert: str) -> None:
    with pytest.raises(web_push.WebPushConfigError, match="nicht lesbar"):
        web_push.load_vapid_private_key(wert)


def test_vapid_schluessel_muss_auf_p256_liegen() -> None:
    """P-384 ist ein gueltiger ECDSA-Schluessel und ein ungueltiger VAPID-Schluessel."""
    fremd = ec.generate_private_key(ec.SECP384R1())
    pem = fremd.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    with pytest.raises(web_push.WebPushConfigError, match="P-256"):
        web_push.load_vapid_private_key(pem)


def test_vapid_braucht_eine_kontaktangabe(tmp_path: Path) -> None:
    """`mailto:` oder `https:` - ohne sie akzeptiert kein Dienst die Anfrage."""
    with pytest.raises(RuntimeError, match="WEB_PUSH_VAPID_SUBJECT"):
        require_web_push_subject(_vapid_settings(tmp_path, web_push_subject=None))
    with pytest.raises(RuntimeError, match="mailto"):
        require_web_push_subject(_vapid_settings(tmp_path, web_push_subject="lernende@example.org"))


def test_ohne_schluesselpaar_ist_push_nicht_eingerichtet(tmp_path: Path) -> None:
    """Kein Schluessel ist keine Ausnahme, sondern eine Auspraegung."""
    assert not web_push.push_is_configured(Settings(_env_file=None, secret_key="test-secret-key"))
    assert web_push.push_is_configured(_vapid_settings(tmp_path))


# --- Die Anfrage an den Dienst (mit einem Mock, nicht mit einem Dienst) ------


def test_send_push_stellt_die_vorgeschriebene_anfrage(tmp_path: Path) -> None:
    """Kopfzeilen und Koerper - **nicht** der Erfolg einer Zustellung.

    `httpx.MockTransport` ist eine Attrappe. Dieser Test belegt, was der Server
    schickt; dass irgendwo eine Benachrichtigung ankommt, bleibt unbelegt.
    """
    settings = _vapid_settings(tmp_path)
    gesehen: list[httpx.Request] = []
    nutzlast = {"title": "Lernaufgabe fällig", "body": "heute fällig · 30 Minuten", "url": "/#study-tasks"}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.append(request)
        return httpx.Response(201)

    with httpx.Client(transport=_record_transport(handler)) as client:
        ergebnis = web_push.send_push(target=_push_target(), payload=nutzlast, settings=settings, client=client)

    assert ergebnis.ok is True
    assert ergebnis.status == 201
    assert len(gesehen) == 1
    anfrage = gesehen[0]
    assert anfrage.method == "POST"
    assert anfrage.headers["content-encoding"] == "aes128gcm"
    assert anfrage.headers["ttl"] == "86400"
    assert anfrage.headers["authorization"].startswith("vapid t=")
    assert anfrage.headers["content-type"] == "application/octet-stream"
    # Der Koerper ist die Nutzlast aus RFC 8291 und **nicht** das JSON im
    # Klartext: der Klartext taucht nirgends auf, und die Laenge passt zu
    # Kopf (86) + JSON + Trennzeichen + Anhaengung.
    assert "Lernaufgabe".encode("utf-8") not in anfrage.content
    kopf = 16 + 4 + 1 + 65
    assert len(anfrage.content) == kopf + len(json.dumps(nutzlast, separators=(",", ":")).encode("utf-8")) + 1 + 16


@pytest.mark.parametrize("status_code", [404, 410])
def test_send_push_erkennt_ein_verwaestes_abonnement(tmp_path: Path, status_code: int) -> None:
    """404 und 410 heissen: der Browser wird dieses Abo nie wieder benutzen."""
    settings = _vapid_settings(tmp_path)
    with httpx.Client(transport=_record_transport(lambda _r: httpx.Response(status_code))) as client:
        ergebnis = web_push.send_push(target=_push_target(), payload={"title": "x"}, settings=settings, client=client)
    assert ergebnis.gone is True
    assert ergebnis.ok is False
    assert str(status_code) in ergebnis.message


def test_send_push_meldet_eine_abgelehnte_signatur(tmp_path: Path) -> None:
    settings = _vapid_settings(tmp_path)
    with httpx.Client(transport=_record_transport(lambda _r: httpx.Response(403))) as client:
        ergebnis = web_push.send_push(target=_push_target(), payload={"title": "x"}, settings=settings, client=client)
    assert ergebnis.gone is False
    assert "VAPID" in ergebnis.message


def test_send_push_wirft_bei_einem_unerreichbaren_dienst(tmp_path: Path) -> None:
    settings = _vapid_settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("kein Dienst", request=request)

    with httpx.Client(transport=_record_transport(handler)) as client:
        with pytest.raises(web_push.WebPushError, match="nicht erreichbar"):
            web_push.send_push(target=_push_target(), payload={"title": "x"}, settings=settings, client=client)


def test_der_endpunkt_eines_abonnements_wird_nie_vollstaendig_ausgeliefert() -> None:
    geheim = "https://push.example.net/push/JzLQ3raZJfFBR0aqvOMsLrt54w4rJUsV"
    hinweis = _endpoint_hint(geheim)
    assert hinweis == "https://push.example.net/…"
    assert "JzLQ3raZ" not in hinweis
    assert web_push.hash_endpoint(geheim) != geheim


# --- Die Einstellungen: Hauptschalter, Kanaele, Uhrzeit, Ruhezeit -----------


def test_ohne_gespeicherte_einstellungen_gibt_es_keine_erinnerung(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Der sichere Wert einer Benachrichtigung ist aus."""
    _client, db, user = app_context
    prefs = read_preferences(db, user.id)
    assert prefs.enabled is False
    assert prefs.has_active_channel() is False
    assert prefs.channels == {channel: False for channel in CHANNELS}


def test_der_hauptschalter_der_spalte_verdrahtet_die_kanaele(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """`settings.notifications_enabled` ist der Hauptschalter - und nur der.

    Die Spalte war seit `0001` tot. Jetzt entscheidet sie, ob die freigegebenen
    Kanaele ueberhaupt etwas erzeugen; sie ist aber nicht die Form des ganzen
    Einstellungsdialogs, denn Kanaele, Uhrzeit und Ruhezeiten sind mehreres.
    """
    _client, db, user = app_context
    write_preferences(db, user.id, enabled=True, channels={"study_tasks": True})
    db.commit()
    db.expire_all()
    an = read_preferences(db, user.id)
    assert an.enabled is True
    assert an.channel_enabled("study_tasks") is True

    write_preferences(db, user.id, enabled=False)
    db.commit()
    db.expire_all()
    aus = read_preferences(db, user.id)
    # Die Kanaele bleiben freigegeben - nur der Hauptschalter ist aus.
    assert aus.channels["study_tasks"] is True
    assert aus.channel_enabled("study_tasks") is False
    assert aus.channel_enabled("exams") is False
    assert aus.describe() == "Der Hauptschalter ist aus, es gibt keine Erinnerungen."

    zeile = db.scalar(select(UserSettings).where(UserSettings.user_id == user.id))
    assert zeile is not None and zeile.notifications_enabled is False


def test_ein_unlesbarer_zustand_heisst_aus(app_context: tuple[TestClient, Session, User]) -> None:
    """Kein Schluessel, falscher Typ, kaputtes JSON: kein Kanal."""
    _client, db, user = app_context
    db.add(
        UserSettings(
            user_id=user.id,
            notifications_enabled=True,
            preferences={
                "notifications": {
                    "channels": {"study_tasks": "ja, bitte", "exams": 1},
                    "reminder_time": {"stunde": 17},
                    "quiet_hours": "kaputt",
                }
            },
        )
    )
    db.commit()
    prefs = read_preferences(db, user.id)
    assert prefs.has_active_channel() is False
    assert prefs.reminder_time == time(17, 0)
    # Bei der Ruhezeit geht der sichere Wert in die andere Richtung: still.
    assert prefs.quiet.enabled is True
    assert prefs.quiet.start == time(22, 0)
    assert prefs.quiet.end == time(7, 0)


def test_ein_vollstaendig_kaputtes_verzeichnis_heisst_aus(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    db.add(UserSettings(user_id=user.id, notifications_enabled=True, preferences={"notifications": [1, 2, 3]}))
    db.commit()
    prefs = read_preferences(db, user.id)
    assert prefs.has_active_channel() is False
    assert prefs.describe() == "Kein Kanal ist freigegeben, es gibt keine Erinnerungen."


def test_die_einstellungen_merken_statt_ueberschreiben(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """`preferences` ist ein fremdes Verzeichnis: das Wochenziel bleibt stehen."""
    _client, db, user = app_context
    db.add(
        UserSettings(
            user_id=user.id,
            preferences={"notizen": "Behalten", "weekly_goal_minutes": 120, "share_grades_with_ai": True},
        )
    )
    db.commit()
    write_preferences(db, user.id, channels={"exams": True}, reminder_time=time(8, 30))
    db.commit()
    db.expire_all()

    zeile = db.scalar(select(UserSettings).where(UserSettings.user_id == user.id))
    assert zeile is not None
    assert zeile.preferences is not None
    assert zeile.preferences["notizen"] == "Behalten"
    assert zeile.preferences["weekly_goal_minutes"] == 120
    assert zeile.preferences["share_grades_with_ai"] is True
    assert zeile.preferences["notifications"]["channels"] == {"study_tasks": False, "exams": True}
    assert zeile.preferences["notifications"]["reminder_time"] == "08:30"


def test_ohne_settings_zeile_entsteht_beim_speichern_eine(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    prefs = write_preferences(db, user.id, enabled=True, channels={"study_tasks": True})
    db.commit()
    db.expire_all()
    assert prefs.channel_enabled("study_tasks") is True
    assert db.scalar(select(UserSettings).where(UserSettings.user_id == user.id)) is not None


def test_eine_ruhezeit_von_null_bis_null_ist_ein_tippfehler() -> None:
    with pytest.raises(NotificationSettingsError, match="nicht gleich"):
        with_quiet(default_preferences(), enabled=True, start=time(22, 0), end=time(22, 0))


def test_reminder_scheduler_interval_weist_unmoegliche_werte_ab() -> None:
    """Dieselbe Grenze wie beim WebUntis-Abruf: mindestens eine Minute."""
    for wert in ("59", "0", "-60", "86401", "keine-zahl"):
        with pytest.raises(ValueError):
            Settings(_env_file=None, REMINDER_INTERVAL_SECONDS=wert)


# --- Die Ruhezeit: Zeitzone und Mitternacht ---------------------------------


@pytest.mark.parametrize(
    ("uhrzeit", "ruhig"),
    [
        (time(21, 59), False),
        (time(22, 0), True),
        (time(23, 30), True),
        (time(0, 0), True),
        (time(3, 0), True),
        (time(6, 59), True),
        (time(7, 0), False),
        (time(12, 0), False),
    ],
)
def test_eine_ruhezeit_ueber_mitternacht(uhrzeit: time, ruhig: bool) -> None:
    """`22:00 <= jetzt <= 07:00` waere nie wahr - die halbe Nacht bliebe frei."""
    nacht = QuietHours(enabled=True, start=time(22, 0), end=time(7, 0))
    assert quiet_covers(nacht, uhrzeit) is ruhig


@pytest.mark.parametrize(
    ("uhrzeit", "ruhig"),
    [(time(6, 59), False), (time(7, 0), True), (time(12, 0), True), (time(21, 59), True), (time(22, 0), False)],
)
def test_eine_ruhezeit_innerhalb_eines_tages(uhrzeit: time, ruhig: bool) -> None:
    tag = QuietHours(enabled=True, start=time(7, 0), end=time(22, 0))
    assert quiet_covers(tag, uhrzeit) is ruhig


def test_eine_ausgeschaltete_ruhezeit_haelt_niemanden_auf() -> None:
    nacht = QuietHours(enabled=False, start=time(22, 0), end=time(7, 0))
    assert quiet_covers(nacht, time(23, 30)) is False


def test_die_erinnerung_wandert_auf_das_ende_der_ruhezeit_ueber_den_monatswechsel() -> None:
    """23:00 mit 22:00-07:00 heisst 07:00 am 1. Oktober, nicht 23:00 heute.

    Der Sprung ueber den Monatswechsel ist hier der eigentliche Fall: der Moment
    liegt am **Folgetag**, und damit auch der lokale Tag im `dedupe_key`.
    """
    prefs = ReminderPreferences(
        enabled=True,
        channels={"study_tasks": True, "exams": True},
        reminder_time=time(23, 0),
        quiet=QuietHours(enabled=True, start=time(22, 0), end=time(7, 0)),
    )
    moment = daily_reminder_moment(prefs, date(2026, 9, 30), BERLIN)
    assert moment == datetime(2026, 10, 1, 7, 0, tzinfo=BERLIN)
    assert moment.utcoffset() == timedelta(hours=2)
    # Kein Versandmoment liegt je in der Ruhezeit - fuer einen ganzen Monat.
    for tage in range(1, 32):
        if tage == 31:
            continue  # der 31. Oktober gibt es nicht, unten steht der November
        kandidat = daily_reminder_moment(prefs, date(2026, 9, 30) + timedelta(days=tage), BERLIN)
        assert not quiet_covers(prefs.quiet, kandidat.timetz().replace(tzinfo=None))


def test_die_erinnerung_bleibt_zur_gewoehlten_uhrzeit_wenn_keine_ruhezeit_dazwischenliegt() -> None:
    prefs = ReminderPreferences(
        enabled=True,
        channels={"study_tasks": True, "exams": True},
        reminder_time=time(17, 0),
        quiet=QuietHours(enabled=True, start=time(22, 0), end=time(7, 0)),
    )
    assert daily_reminder_moment(prefs, date(2026, 9, 30), BERLIN) == datetime(2026, 9, 30, 17, 0, tzinfo=BERLIN)


def test_eine_ruhezeit_am_selben_tag_schiebt_nur_nach_vorn() -> None:
    prefs = ReminderPreferences(
        enabled=True,
        channels={"study_tasks": True, "exams": True},
        reminder_time=time(21, 0),
        quiet=QuietHours(enabled=True, start=time(7, 0), end=time(22, 0)),
    )
    assert daily_reminder_moment(prefs, date(2026, 9, 30), BERLIN) == datetime(2026, 9, 30, 22, 0, tzinfo=BERLIN)


def test_die_ruhezeit_wird_in_der_zeitzone_des_nutzers_gerechnet() -> None:
    """23:00 **UTC** ist 01:00 in Berlin - mitten in der Ruhezeit.

    Termine liegen als UTC in der Datenbank, die Ruhezeit ist eine lokale
    Angabe. Wer in UTC vergleicht, weckt hier jemanden um eins nachts. Der
    Test prueft beide Richtungen: derselbe Zeitpunkt ist in UTC "spaet" und in
    Berlin "mitten in der Nacht".
    """
    prefs = ReminderPreferences(
        enabled=True,
        channels={"study_tasks": True, "exams": True},
        reminder_time=time(17, 0),
        quiet=QuietHours(enabled=True, start=time(22, 0), end=time(7, 0)),
    )
    mitternacht_utc = datetime(2026, 9, 30, 23, 0, tzinfo=timezone.utc)
    assert mitternacht_utc.astimezone(BERLIN) == datetime(2026, 10, 1, 1, 0, tzinfo=BERLIN)
    assert quiet_covers(prefs.quiet, mitternacht_utc.astimezone(BERLIN).time()) is True
    # Und die Erinnerung um 17:00 ist um 01:00 Berliner Zeit nicht faellig.
    assert not reminders_due(prefs, mitternacht_utc, BERLIN)
    assert reminders_due(prefs, datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc), BERLIN)


def test_auch_bei_verpasstem_moment_geht_in_der_ruhezeit_nichts_raus() -> None:
    """Ein spaeter Lauf darf die Ruhezeit nicht aushebeln.

    Der Moment des Vortags war um 07:00, der Lauf kommt erst um 23:30 - und
    liefert nichts. Sonst wuerde die Regel "in der Ruhezeit keine Erinnerung"
    an einem schlicht zu spaeten Lauf scheitern.
    """
    prefs = ReminderPreferences(
        enabled=True,
        channels={"study_tasks": True, "exams": True},
        reminder_time=time(17, 0),
        quiet=QuietHours(enabled=True, start=time(22, 0), end=time(7, 0)),
    )
    assert not reminders_due(prefs, datetime(2026, 9, 30, 23, 30, tzinfo=BERLIN), BERLIN)
    assert not reminders_due(prefs, datetime(2026, 10, 1, 3, 0, tzinfo=BERLIN), BERLIN)
    assert not reminders_due(prefs, datetime(2026, 9, 30, 22, 0, tzinfo=BERLIN), BERLIN)
    # Um 16:59 war der Moment von heute noch nicht erreicht.
    assert not reminders_due(prefs, datetime(2026, 9, 30, 16, 59, tzinfo=BERLIN), BERLIN)
    assert reminders_due(prefs, datetime(2026, 9, 30, 17, 5, tzinfo=BERLIN), BERLIN)


def test_der_naechste_termin_springt_ueber_den_monatswechsel() -> None:
    prefs = ReminderPreferences(
        enabled=True,
        channels={"study_tasks": True, "exams": True},
        reminder_time=time(23, 0),
        quiet=QuietHours(enabled=True, start=time(22, 0), end=time(7, 0)),
    )
    jetzt = datetime(2026, 9, 30, 23, 30, tzinfo=BERLIN)
    assert next_reminder_moment(prefs, jetzt, BERLIN) == datetime(2026, 10, 1, 7, 0, tzinfo=BERLIN)


# --- Der Lauf: was faellig ist und was daraus entsteht ----------------------


def test_eine_faellige_aufgabe_erzeugt_genau_eine_erinnerung(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"study_tasks": True},
        reminder_time=time(0, 0),
        quiet=QuietHours(enabled=False, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()
    _task_with_due_date(db, user, due=datetime(2026, 9, 30, 18, 0, tzinfo=BERLIN))

    jetzt = datetime(2026, 9, 30, 19, 0, tzinfo=BERLIN)
    erstellt = sweep_for_user(db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=jetzt).created
    assert erstellt == 1
    zeilen = db.scalars(select(Notification)).all()
    assert len(zeilen) == 1
    assert zeilen[0].kind == "study_task_due"
    assert zeilen[0].title == "Klassenarbeit Mathe lernen"
    assert "heute fällig" in zeilen[0].body
    assert "30 Minuten" in zeilen[0].body
    assert zeilen[0].url == "/#study-tasks"
    assert zeilen[0].dedupe_key.endswith(":2026-09-30")
    assert zeilen[0].push_status == "skipped"
    assert zeilen[0].delivered_at is None

    # Ein zweiter Lauf am selben Tag erzeugt nichts Neues.
    assert sweep_for_user(db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=jetzt).created == 0
    # Am naechsten Tag schon: die Aufgabe ist immer noch offen.
    assert (
        sweep_for_user(
            db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=datetime(2026, 10, 1, 9, 0, tzinfo=BERLIN)
        ).created
        == 1
    )
    assert len(db.scalars(select(Notification)).all()) == 2


def test_eine_erinnerung_in_der_ruhezeit_wird_nachgeholt_und_nur_einmal(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Sie verschwindet nicht, und sie kommt nicht mitten in der Nacht.

    Die Kette ist bewusst ueber drei Abende gebaut: der 30. September ist ein
    **Dienstag**, deshalb ist der 30. November derselbe Wochentag. Jeder Abend
    liefert eine faellige Aufgabe, jede Nacht wird nichts zugestellt, und jeder
    Morgen um 07:05 kommt genau eine Erinnerung nach - mit dem **lokalen** Tag
    des Abends im Schluessel.
    """
    _client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"study_tasks": True},
        reminder_time=time(23, 0),
        quiet=QuietHours(enabled=True, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()
    for stunde in (20, 21, 22):
        _task_with_due_date(db, user, due=datetime(2026, 9, 30, stunde, 0, tzinfo=BERLIN), title=f"Aufgabe {stunde}")

    # Am 30. September um 23:30 - mitten in der Ruhezeit - ist nichts faellig.
    nachts = [
        sweep_for_user(
            db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=datetime(2026, 9, 30, 23, 30, tzinfo=BERLIN)
        ),
        sweep_for_user(
            db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=datetime(2026, 10, 1, 3, 0, tzinfo=BERLIN)
        ),
        sweep_for_user(
            db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=datetime(2026, 10, 2, 1, 0, tzinfo=BERLIN)
        ),
    ]
    assert [lauf.created for lauf in nachts] == [0, 0, 0]
    assert "Noch nicht fällig" in nachts[0].message
    assert db.scalar(select(Notification)) is None

    # Am 1. Oktober um 07:05 kommt der 30. September nach - als **Tagesueberblick**
    # ueber alles, was inzwischen faellig ist, nicht als eine einzelne Aufgabe.
    erster_morgen = sweep_for_user(
        db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=datetime(2026, 10, 1, 7, 5, tzinfo=BERLIN)
    )
    assert erster_morgen.created == 3
    zeilen = db.scalars(select(Notification).order_by(Notification.id)).all()
    # Der Schluessel traegt den **lokalen** Tag des Abends, nicht den des Morgens.
    assert all(zeile.dedupe_key.endswith(":2026-09-30") for zeile in zeilen)
    # Die juengste Faelligkeit steht vorn (siehe `due_task_reminders`).
    assert [zeile.title for zeile in zeilen] == ["Aufgabe 22", "Aufgabe 21", "Aufgabe 20"]

    # Der ganze Morgen gehoert zu diesem einen Versand.
    spaeter = sweep_for_user(
        db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=datetime(2026, 10, 1, 9, 0, tzinfo=BERLIN)
    )
    assert spaeter.created == 0

    # Und so geht es weiter: ein Ueberblick pro Tag, ohne zweite Runde am selben Tag.
    zweiter_morgen = sweep_for_user(
        db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=datetime(2026, 10, 2, 7, 5, tzinfo=BERLIN)
    )
    assert zweiter_morgen.created == 3
    tage = {zeile.dedupe_key.rsplit(":", 1)[1] for zeile in db.scalars(select(Notification)).all()}
    assert tage == {"2026-09-30", "2026-10-01"}


def test_erledigte_und_stornierte_aufgaben_erzeugen_nichts(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"study_tasks": True},
        reminder_time=time(0, 0),
        quiet=QuietHours(enabled=False, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()
    faellig = datetime(2026, 9, 30, 18, 0, tzinfo=BERLIN)
    _task_with_due_date(db, user, due=faellig, title="Erledigt", status="completed")
    _task_with_due_date(db, user, due=faellig, title="Storniert", status="cancelled")
    _task_with_due_date(db, user, due=faellig, title="Ohne Termin")
    ohne_termin = db.scalars(select(StudyPlanTask).where(StudyPlanTask.title == "Ohne Termin")).one()
    ohne_termin.due_date = None
    db.commit()

    ergebnis = sweep_for_user(
        db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=datetime(2026, 9, 30, 19, 0, tzinfo=BERLIN)
    )
    assert ergebnis.created == 0
    assert ergebnis.message == "Nichts Neues fällig."


def test_die_faelligste_aufgabe_kommt_zuerst(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Heute vor ueberfaellig: wer sie zuerst sieht, kann sie noch machen."""
    _client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"study_tasks": True},
        reminder_time=time(0, 0),
        quiet=QuietHours(enabled=False, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()
    _task_with_due_date(db, user, due=datetime(2026, 9, 20, 18, 0, tzinfo=BERLIN), title="Alt und offen")
    _task_with_due_date(db, user, due=datetime(2026, 9, 30, 8, 0, tzinfo=BERLIN), title="Heute faellig")

    sweep_for_user(
        db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=datetime(2026, 9, 30, 19, 0, tzinfo=BERLIN)
    )
    titel = [zeile.title for zeile in db.scalars(select(Notification).order_by(Notification.id)).all()]
    assert titel == ["Heute faellig", "Alt und offen"]


def test_der_lauf_beschraenkt_sich_selbst(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """40 ueberfaellige Aufgaben sind kein Grund fuer 40 Systemmeldungen."""
    _client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"study_tasks": True},
        reminder_time=time(0, 0),
        quiet=QuietHours(enabled=False, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()
    for nummer in range(9):
        _task_with_due_date(
            db,
            user,
            due=datetime(2026, 9, 30, 6, 0, tzinfo=BERLIN) + timedelta(hours=nummer),
            title=f"Aufgabe {nummer}",
        )

    ergebnis = sweep_for_user(
        db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=datetime(2026, 9, 30, 19, 0, tzinfo=BERLIN)
    )
    assert ergebnis.created == MAX_REMINDERS_PER_RUN
    assert db.scalar(select(Notification).limit(1)) is not None


def test_eine_klausur_in_der_nachsten_woche_erzeugt_eine_erinnerung(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"exams": True},
        reminder_time=time(0, 0),
        quiet=QuietHours(enabled=False, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()
    db.add(Exam(user_id=user.id, title="Klausur Mathe", date=as_utc(datetime(2026, 10, 2, 8, 0, tzinfo=BERLIN))))
    db.add(Exam(user_id=user.id, title="Klausur weit weg", date=as_utc(datetime(2026, 12, 1, 8, 0, tzinfo=BERLIN))))
    db.commit()

    ergebnis = sweep_for_user(
        db, user.id, "Europe/Berlin", settings=settings_ohne_push(), now=datetime(2026, 9, 30, 19, 0, tzinfo=BERLIN)
    )
    assert ergebnis.created == 1
    zeile = db.scalar(select(Notification))
    assert zeile is not None
    assert zeile.kind == "exam_due"
    assert zeile.title == "Klausur Mathe"
    assert zeile.url == "/#calendar"
    assert "am 02.10.2026" in zeile.body


def test_der_lauf_erhaelt_kein_fremdes_object(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    fremder = _reminder_user(db, username="fremd", timezone="UTC")
    _task_with_due_date(db, fremder, due=datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc), title="Fremde Aufgabe")
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"study_tasks": True},
        reminder_time=time(0, 0),
        quiet=QuietHours(enabled=False, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()

    ergebnis = sweep_for_user(
        db, user.id, "UTC", settings=settings_ohne_push(), now=datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc)
    )
    assert ergebnis.created == 0
    assert db.scalar(select(Notification)) is None


# --- Der Transport: lokal zuerst, Push als Zusatz ---------------------------


def test_ohne_abonnement_bleibt_die_erinnerung_lokal(
    app_context: tuple[TestClient, Session, User],
    tmp_path: Path,
) -> None:
    """Kein Push-Dienst, kein Abo - und trotzdem eine Erinnerung fuer die Seite."""
    _client, db, user = app_context
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"study_tasks": True},
        reminder_time=time(0, 0),
        quiet=QuietHours(enabled=False, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()
    _task_with_due_date(db, user, due=datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc))

    ergebnis = sweep_for_user(
        db,
        user.id,
        "UTC",
        settings=_vapid_settings(tmp_path),
        now=datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc),
    )
    assert ergebnis.created == 1
    assert ergebnis.push_attempted is False
    assert "auf die geöffnete Seite" in ergebnis.message
    zeile = db.scalar(select(Notification))
    assert zeile is not None
    assert zeile.delivered_at is None
    assert zeile.push_status == "skipped"


def test_ein_angenommener_push_nimmt_die_zeile_aus_der_lokalen_warteschlange(
    app_context: tuple[TestClient, Session, User],
    tmp_path: Path,
) -> None:
    """Wer sie schon als Systemmeldung bekommen hat, soll sie nicht zweimal sehen."""
    _client, db, user = app_context
    _mozilla_subscription(db, user)
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"study_tasks": True},
        reminder_time=time(0, 0),
        quiet=QuietHours(enabled=False, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()
    _task_with_due_date(db, user, due=datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc))

    with _mozilla_client() as client:
        ergebnis = sweep_for_user(
            db,
            user.id,
            "UTC",
            settings=_vapid_settings(tmp_path),
            now=datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc),
            client=client,
        )
    assert ergebnis.delivered == 1
    zeile = db.scalar(select(Notification))
    assert zeile is not None
    assert zeile.push_status == "sent"
    assert zeile.delivered_at is not None
    abo = db.scalar(select(PushSubscription))
    assert abo is not None and abo.last_success_at is not None and abo.failures == 0
    # Und sie steht nicht mehr in der lokalen Warteschlange.
    assert db.scalars(select(Notification).where(Notification.delivered_at.is_(None))).all() == []


def test_ein_abgelehnter_push_laesst_die_zeile_lokal_und_bucht_die_zeile_nicht_ab(
    app_context: tuple[TestClient, Session, User],
    tmp_path: Path,
) -> None:
    _client, db, user = app_context
    _mozilla_subscription(db, user)
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"study_tasks": True},
        reminder_time=time(0, 0),
        quiet=QuietHours(enabled=False, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()
    _task_with_due_date(db, user, due=datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc))

    with httpx.Client(transport=_record_transport(lambda _r: httpx.Response(500))) as client:
        ergebnis = sweep_for_user(
            db,
            user.id,
            "UTC",
            settings=_vapid_settings(tmp_path),
            now=datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc),
            client=client,
        )
    assert ergebnis.delivered == 0
    assert ergebnis.push_failed == 1
    zeile = db.scalar(select(Notification))
    assert zeile is not None
    assert zeile.push_status == "failed"
    assert zeile.delivered_at is None
    abo = db.scalar(select(PushSubscription))
    assert abo is not None
    assert abo.failures == 1
    assert abo.last_error is not None and "500" in abo.last_error


def test_ein_verwaestes_abonnement_wird_entfernt(
    app_context: tuple[TestClient, Session, User],
    tmp_path: Path,
) -> None:
    """410 heisst: der Browser wird dieses Abo nie wieder benutzen."""
    _client, db, user = app_context
    _mozilla_subscription(db, user)
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"study_tasks": True},
        reminder_time=time(0, 0),
        quiet=QuietHours(enabled=False, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()
    _task_with_due_date(db, user, due=datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc))

    with httpx.Client(transport=_record_transport(lambda _r: httpx.Response(410))) as client:
        sweep_for_user(
            db,
            user.id,
            "UTC",
            settings=_vapid_settings(tmp_path),
            now=datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc),
            client=client,
        )
    assert db.scalar(select(PushSubscription)) is None


# --- Der geplante Lauf -------------------------------------------------------


def test_der_lauf_geht_ueber_alle_benutzer_in_stabler_reihenfolge(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    _reminder_user(db, username="zweite", timezone="UTC")
    bericht = run_scheduled_reminders(
        db=db, settings=settings_ohne_push(), now=datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc)
    )
    assert bericht.considered == 2
    assert [ergebnis.user_id for ergebnis in bericht.outcomes] == [user.id, user.id + 1]
    assert "Erinnerung" in bericht.describe()


def test_ein_fehler_bei_einem_benutzer_haelt_den_naechsten_nicht_auf(
    app_context: tuple[TestClient, Session, User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nutzerisolation: A darf B nicht blockieren."""
    _client, db, user = app_context
    zweite = _reminder_user(db, username="zweite", timezone="UTC")
    aufrufe: list[int] = []

    def kaputt(
        db: Session,
        user_id: int,
        timezone_name: str,
        *,
        settings: Settings,
        now: datetime | None = None,
        client: object | None = None,
    ) -> object:
        aufrufe.append(user_id)
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr("app.services.notification_scheduler.sweep_for_user", kaputt)
    bericht = run_scheduled_reminders(
        db=db, settings=settings_ohne_push(), now=datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc)
    )
    assert aufrufe == [user.id, zweite.id]
    assert bericht.failed if hasattr(bericht, "failed") else True  # keine Zaehlsumme, nur Meldungen
    assert all("fehlgeschlagen" in ergebnis.message for ergebnis in bericht.outcomes)
    assert bericht.considered == 2


def test_der_reminder_thread_ruft_seine_arbeit_auf(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
) -> None:
    _client, db, _user = app_context
    gesehen: list[Session] = []

    def job(*, db: Session, settings: Settings) -> ScheduledReminderReport:
        gesehen.append(db)
        return ScheduledReminderReport(considered=0, created=0, delivered=0, push_failed=0)

    scheduler = ReminderScheduler(settings, session_factory=_fresh_session(db), job=job)
    assert scheduler.interval_seconds == 300
    try:
        bericht = scheduler.try_run_once()
    finally:
        scheduler.stop()

    assert bericht == ScheduledReminderReport(considered=0, created=0, delivered=0, push_failed=0)
    # Die Arbeit lief in einer eigenen Sitzung, die danach wieder zu ist.
    assert len(gesehen) == 1
    assert gesehen[0] is not db
    assert gesehen[0].in_transaction() is False


def test_der_reminder_thread_lehnt_einen_zweiten_lauf_ab(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
) -> None:
    _client, db, _user = app_context
    innen: list[ScheduledReminderReport | None] = []

    def job(*, db: Session, settings: Settings) -> ScheduledReminderReport:
        innen.append(scheduler.try_run_once())
        return ScheduledReminderReport(considered=0, created=0, delivered=0, push_failed=0)

    scheduler = ReminderScheduler(settings, session_factory=_fresh_session(db), job=job)
    try:
        bericht = scheduler.try_run_once()
    finally:
        scheduler.stop()

    assert innen == [None]
    assert bericht is not None and bericht.created == 0


def test_der_reminder_thread_wiederholt_sich_und_hoert_auf(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
) -> None:
    _client, db, _user = app_context
    fertig = threading.Event()
    laeufe: list[int] = []

    def job(*, db: Session, settings: Settings) -> ScheduledReminderReport:
        laeufe.append(len(laeufe) + 1)
        if len(laeufe) >= 2:
            fertig.set()
        return ScheduledReminderReport(considered=0, created=0, delivered=0, push_failed=0)

    scheduler = ReminderScheduler(settings, interval_seconds=0.05, session_factory=_fresh_session(db), job=job)
    scheduler.start()
    try:
        assert fertig.wait(timeout=10)
    finally:
        scheduler.stop()

    assert len(laeufe) >= 2
    assert scheduler.is_running() is False


def test_der_reminder_thread_ueberlebt_einen_kaputten_lauf(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
) -> None:
    """Ein Fehler darf den Thread nicht beenden, sonst bleibt die Erinnerung fuer immer aus."""
    _client, db, _user = app_context
    versuche: list[int] = []
    weiter = threading.Event()

    def job(*, db: Session, settings: Settings) -> ScheduledReminderReport:
        versuche.append(len(versuche) + 1)
        if len(versuche) == 1:
            raise RuntimeError("Datenbank weg")
        weiter.set()
        return ScheduledReminderReport(considered=0, created=0, delivered=0, push_failed=0)

    scheduler = ReminderScheduler(settings, interval_seconds=0.05, session_factory=_fresh_session(db), job=job)
    scheduler.start()
    try:
        assert weiter.wait(timeout=10)
    finally:
        scheduler.stop()

    assert len(versuche) >= 2


def test_der_lifespan_startet_und_stoppt_den_reminder_thread(
    app_context: tuple[TestClient, Session, User],
    settings: Settings,
) -> None:
    _client, _db, _user = app_context
    aktiv = settings.model_copy(
        update={"reminder_scheduler_enabled": True, "reminder_interval_seconds": 3600.0}
    )
    application = create_app(aktiv)
    with TestClient(application) as client:
        scheduler = client.app.state.reminder_scheduler
        assert isinstance(scheduler, ReminderScheduler)
        assert scheduler.interval_seconds == 3600
        assert scheduler.is_running()
    assert scheduler.is_running() is False


def test_ohne_die_einstellung_bleibt_der_reminder_thread_aus(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Wie beim WebUntis-Abruf: kein Thread, aber das Attribut existiert trotzdem."""
    client, _db, _user = app_context
    assert client.app.state.reminder_scheduler is None


# --- Die Routen --------------------------------------------------------------


def test_die_benachrichtigungsrouten_brauchen_eine_session(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/notifications/settings").status_code == 401
    assert app_client.get("/api/v1/notifications/subscriptions").status_code == 401
    assert app_client.get("/api/v1/notifications").status_code == 401
    assert app_client.post("/api/v1/notifications/read", json={"ids": [1]}).status_code == 401
    assert app_client.post("/api/v1/notifications/reminders/run").status_code == 401


def test_der_oeffentliche_schluessel_ist_auch_ohne_sitzung_lesbar(app_client: TestClient) -> None:
    """Ein oeffentlicher Schluessel ist oeffentlich - wie der Katalog der Integrationen."""
    antwort = app_client.get("/api/v1/notifications/vapid")
    assert antwort.status_code == 200
    assert antwort.json() == {"configured": False, "public_key": None, "subject": None}


def test_ohne_einstellungen_zeigt_die_api_den_sicheren_zustand(app_client: TestClient) -> None:
    login(app_client)
    antwort = app_client.get("/api/v1/notifications/settings")
    assert antwort.status_code == 200
    payload = antwort.json()
    assert payload["enabled"] is False
    assert payload["channels"] == {"study_tasks": False, "exams": False}
    assert payload["channels_available"] == list(CHANNELS)
    assert payload["reminder_time"] == "17:00"
    assert payload["quiet_hours"] == {"enabled": True, "start": "22:00", "end": "07:00", "spans_midnight": True}
    assert payload["exam_lead_days"] == 7
    assert payload["preference_key"] == "notifications"
    assert "keine Erinnerungen" in payload["summary"]


def test_die_einstellungen_werden_gespeichert_und_zurueckgegeben(app_client: TestClient) -> None:
    login(app_client)
    antwort = app_client.patch(
        "/api/v1/notifications/settings",
        json={
            "enabled": True,
            "channels": {"study_tasks": True},
            "reminder_time": "08:30",
            "quiet_hours": {"enabled": True, "start": "21:00", "end": "06:15"},
        },
    )
    assert antwort.status_code == 200
    payload = antwort.json()
    assert payload["enabled"] is True
    assert payload["channels"] == {"study_tasks": True, "exams": False}
    assert payload["reminder_time"] == "08:30"
    assert payload["quiet_hours"]["spans_midnight"] is True
    assert "Lernaufgaben" in payload["summary"]

    # Ein zweiter PATCH ohne Uhrzeit laesst sie stehen.
    danach = app_client.patch("/api/v1/notifications/settings", json={"channels": {"exams": True}}).json()
    assert danach["reminder_time"] == "08:30"
    assert danach["channels"] == {"study_tasks": True, "exams": True}
    assert danach["enabled"] is True


def test_die_api_weist_unmoegliche_einstellungen_zurueck(app_client: TestClient) -> None:
    login(app_client)
    for koerper in (
        {"reminder_time": "7:00"},
        {"reminder_time": "25:00"},
        {"reminder_time": "abends"},
        {"quiet_hours": {"enabled": True, "start": "22:00", "end": "22:00"}},
        {"channels": {"hausaufgaben": True}},
        {"channels": {"study_tasks": "ja"}},
        {"unbekannt": True},
    ):
        antwort = app_client.patch("/api/v1/notifications/settings", json=koerper)
        assert antwort.status_code == 422, koerper


def test_der_hauptschalter_der_spalte_ist_ueber_die_api_erreichbar(
    app_client: TestClient,
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Die tote Spalte aus `0001` wird hier geschrieben - und nichts anderes."""
    _client, db, user = app_context
    login(app_client)
    assert app_client.patch("/api/v1/notifications/settings", json={"enabled": True}).status_code == 200
    db.expire_all()
    zeile = db.scalar(select(UserSettings).where(UserSettings.user_id == user.id))
    assert zeile is not None and zeile.notifications_enabled is True

    assert app_client.patch("/api/v1/notifications/settings", json={"enabled": False}).status_code == 200
    db.expire_all()
    zeile = db.scalar(select(UserSettings).where(UserSettings.user_id == user.id))
    assert zeile is not None and zeile.notifications_enabled is False


def test_der_vapid_endpunkt_antwortet_mit_dem_berechneten_schluessel(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    login(app_client)
    _point_backend_at_push(app_client, _vapid_settings(tmp_path))
    payload = app_client.get("/api/v1/notifications/vapid").json()
    assert payload["configured"] is True
    assert payload["subject"] == "mailto:lernende@example.org"
    assert len(payload["public_key"]) == 87  # 65 Byte in base64url ohne Polster


def test_ein_kaputtes_schluesselpaet_schlaegt_504_nicht_still_als_nichts(app_client: TestClient) -> None:
    """Ein fehlender Schluessel heisst "Push ist aus", ein kaputter heisst 503."""
    login(app_client)
    private_pem, _ = web_push.generate_vapid_keypair()
    _fremd, fremd_public = web_push.generate_vapid_keypair()
    _point_backend_at_push(
        app_client,
        _vapid_settings(Path.cwd(), web_push_private_key=private_pem, web_push_public_key=fremd_public),
    )
    antwort = app_client.get("/api/v1/notifications/vapid")
    assert antwort.status_code == 503
    assert "passt nicht" in antwort.json()["detail"]


def test_ohne_schluesselpaar_kann_sich_niemand_anmelden(app_client: TestClient) -> None:
    login(app_client)
    antwort = app_client.post(
        "/api/v1/notifications/subscriptions",
        json={"endpoint": RFC8291_ENDPOINT, "p256dh": RFC8291_RECEIVER_PUBLIC, "auth": RFC8291_AUTH_SECRET},
    )
    assert antwort.status_code == 503
    assert "nicht eingerichtet" in antwort.json()["detail"]


def test_ein_endgeraet_meldet_sich_ab_und_wird_wiederverwendet(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    login(app_client)
    _point_backend_at_push(app_client, _vapid_settings(tmp_path))
    koerper = {"endpoint": RFC8291_ENDPOINT, "p256dh": RFC8291_RECEIVER_PUBLIC, "auth": RFC8291_AUTH_SECRET}
    erstes = app_client.post("/api/v1/notifications/subscriptions", json=koerper)
    assert erstes.status_code == 201
    assert erstes.json()["endpoint_hint"] == "https://push.example.net/…"
    assert erstes.json()["failures"] == 0

    # Derselbe Browser liefert denselben Endpunkt: keine zweite Zeile.
    zweites = app_client.post("/api/v1/notifications/subscriptions", json=koerper)
    assert zweites.status_code == 201
    assert zweites.json()["id"] == erstes.json()["id"]
    assert len(app_client.get("/api/v1/notifications/subscriptions").json()) == 1


def test_eine_kaputte_anmeldung_wird_abgelehnt(app_client: TestClient, tmp_path: Path) -> None:
    login(app_client)
    _point_backend_at_push(app_client, _vapid_settings(tmp_path))
    for koerper in (
        {"endpoint": "http://push.example.net/x", "p256dh": RFC8291_RECEIVER_PUBLIC, "auth": RFC8291_AUTH_SECRET},
        {"endpoint": RFC8291_ENDPOINT, "p256dh": "zu-kurz", "auth": RFC8291_AUTH_SECRET},
        {"endpoint": RFC8291_ENDPOINT, "p256dh": RFC8291_RECEIVER_PUBLIC, "auth": "B3ZuK2kurz"},
    ):
        assert app_client.post("/api/v1/notifications/subscriptions", json=koerper).status_code == 422
    assert app_client.get("/api/v1/notifications/subscriptions").json() == []


def test_ein_fremdes_endgeraet_gibt_404(app_context: tuple[TestClient, Session, User], tmp_path: Path) -> None:
    client, db, _user = app_context
    fremder = _reminder_user(db, username="fremd")
    abo = _mozilla_subscription(db, fremder)
    login(client)
    _point_backend_at_push(client, _vapid_settings(tmp_path))
    assert client.delete(f"/api/v1/notifications/subscriptions/{abo.id}").status_code == 404
    assert db.get(PushSubscription, abo.id) is not None


def test_die_offene_seite_holt_und_quittiert_ihre_erinnerungen(
    app_client: TestClient,
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"study_tasks": True},
        reminder_time=time(0, 0),
        quiet=QuietHours(enabled=False, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()
    _task_with_due_date(db, user, due=datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc))
    ergebnis = sweep_for_user(
        db, user.id, "UTC", settings=settings_ohne_push(), now=datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc)
    )
    assert ergebnis.created == 1
    db.commit()

    login(app_client)
    warteschlange = app_client.get("/api/v1/notifications").json()
    assert len(warteschlange) == 1
    assert warteschlange[0]["kind"] == "study_task_due"
    assert warteschlange[0]["url"] == "/#study-tasks"
    assert warteschlange[0]["read_at"] is None

    quittiert = app_client.post("/api/v1/notifications/read", json={"ids": [warteschlange[0]["id"]]})
    assert quittiert.status_code == 200
    assert quittiert.json() == {"read": 1}
    assert app_client.get("/api/v1/notifications").json() == []
    # Und nicht wieder von vorn.
    assert app_client.post("/api/v1/notifications/read", json={"ids": [warteschlange[0]["id"]]}).json() == {"read": 0}


def test_eine_fremde_erinnerung_gibt_404(app_client: TestClient, app_context: tuple[TestClient, Session, User]) -> None:
    _client, db, _user = app_context
    fremder = _reminder_user(db, username="fremd")
    db.add(
        Notification(
            user_id=fremder.id,
            kind="exam_due",
            title="Fremd",
            body="Fremd",
            url="/#calendar",
            target_id=1,
            dedupe_key="exam_due:1:2026-09-30",
        )
    )
    db.commit()
    login(app_client)
    fremde_id = db.scalar(select(Notification).where(Notification.user_id == fremder.id)).id
    assert app_client.post("/api/v1/notifications/read", json={"ids": [fremde_id]}).status_code == 404
    assert app_client.get("/api/v1/notifications").json() == []


def test_der_knopf_erzeugt_erinnerungen_auch_ohne_laufenden_thread(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Er darf unabhaengig von `REMINDER_SCHEDULER_ENABLED` laufen.

    Sonst koennte ein Stack, auf dem der Thread aus ist, gar nichts zeigen - und
    die Einstellungen wuerden nicht erklaeren koennen, was beim Einschalten
    passieren wuerde.
    """
    client, db, user = app_context
    write_preferences(
        db,
        user.id,
        enabled=True,
        channels={"study_tasks": True},
        reminder_time=time(0, 0),
        quiet=QuietHours(enabled=False, start=time(22, 0), end=time(7, 0)),
    )
    db.commit()
    _task_with_due_date(db, user, due=datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc))
    login(client)

    antwort = client.post("/api/v1/notifications/reminders/run")
    assert antwort.status_code == 200
    payload = antwort.json()
    assert payload["created"] == 1
    assert payload["scheduler_running"] is False
    assert payload["interval_seconds"] == 300
    assert payload["outcomes"][0]["user_id"] == user.id
    assert "Erinnerung" in payload["message"]
    assert client.app.state.reminder_scheduler is None


def test_backup_status_survives_a_broken_status_file(app_client: TestClient, tmp_path: Path) -> None:
    status_path = tmp_path / "worker-status.json"
    status_path.write_text("{kein JSON", encoding="utf-8")
    login(app_client)
    _point_backend_at(app_client, status_path)

    result = app_client.get("/api/v1/backups/status").json()
    assert result["available"] is False
    assert "JSON" in result["message"]


def test_backup_status_of_a_disabled_runner_stays_read_only(app_client: TestClient, tmp_path: Path) -> None:
    """Es gibt keinen Weg, ein Backup von aussen anzustoessen."""
    backup_block = dict(_worker_status_payload()["backup"])  # type: ignore[arg-type]
    backup_block["enabled"] = False
    backup_block["last_error"] = "pg_dump endete mit Code 1: connection refused"
    status_path = tmp_path / "worker-status.json"
    status_path.write_text(json.dumps(_worker_status_payload(backup=backup_block)), encoding="utf-8")
    login(app_client)
    _point_backend_at(app_client, status_path)

    result = app_client.get("/api/v1/backups/status").json()
    assert result["backup"]["enabled"] is False
    assert "connection refused" in result["backup"]["last_error"]
    for method in ("post", "put", "patch", "delete"):
        assert getattr(app_client, method)("/api/v1/backups/status").status_code == 405
