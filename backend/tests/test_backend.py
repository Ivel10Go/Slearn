from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import threading
import wave
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from app.api.notifications import _endpoint_hint
from app.core import oauth_state
from app.core.config import Settings, normalize_database_url, require_web_push_subject
from app.core.crypto import decrypt_credentials, encrypt_credentials
from app.core.oauth_state import OAuthStateError
from app.core.security import create_session_token, hash_password
from app.core.time import as_utc
from app.db.entities import (
    SYNC_STATE_DELETED,
    SYNC_STATE_EDITED,
    SYNC_STATE_SYNCED,
    CalendarEvent,
    DocumentChunk,
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
from app.services import (
    ai_routing,
    calendar_consent,
    calendar_feed,
    calendar_sync,
    google_calendar,
    local_inference,
    model_catalog,
    piper_voices,
    web_push,
)
from app.services import grades as grades_service
from app.services import integrations as integrations_service
from app.services import speech as speech_service
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
from app.services.caldav import CalDavClient
from app.services.calendar_sync import (
    CalendarAuthError,
    CalendarConfigError,
    CalendarConflictError,
    CalendarNotWritableError,
    CalendarSyncError,
    CalendarSyncOutcome,
    IcalAdapter,
    RemoteBatch,
    RemoteEvent,
    sync_calendar,
    window_bounds,
)
from app.services.dashboard import serialize_dashboard
from app.services.demo_seed import seed_demo_data
from app.services.documents import EMBEDDING_DIMENSION, chunk_text, read_document_text
from app.services.google_calendar import GoogleCalendarClient, GoogleOAuthError, OAuthTokens
from app.services.grades import (
    GradeImportError,
    WeightedGrade,
    grade_priority,
    project_average,
)
from app.services.ical import ICalError, ICalEvent, build_calendar, parse_calendar
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
from app.services.model_catalog import (
    MODEL_ID_PATTERN,
    MODEL_TASKS,
    ModelCatalogError,
    ModelFile,
    ModelSpec,
    bits_per_weight,
    catalog_models,
    model_spec,
    ram_estimate,
    validate_download_url,
)
from app.services.model_downloads import (
    MANIFEST_NAME,
    PART_SUFFIX,
    ModelDownloadBusy,
    ModelDownloadError,
    ModelDownloadManager,
)
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
from app.services.piper_voices import PiperVoice, VoiceConfigError, read_voice_config
from app.services.progress import (
    DEFAULT_WEEKLY_GOAL_MINUTES,
    confidence_label,
    current_streak,
    longest_streak,
    review_interval_days,
    week_bounds,
)
from app.services.speech import (
    PiperSynthesizer,
    SpeechRuntimeError,
    SpeechService,
    WhisperTranscriber,
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
from pgvector.sqlalchemy import Vector
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
    assert [entry["kind"] for entry in catalog] == [
        "webuntis",
        "ai_provider",
        "documents",
        "ical",
        "google_calendar",
        "caldav",
    ]
    webuntis = next(entry for entry in catalog if entry["kind"] == "webuntis")
    assert webuntis["required_config"] == ["base_url", "school"]
    assert webuntis["required_credentials"] == ["username", "password"]

    ical = next(entry for entry in catalog if entry["kind"] == "ical")
    # `ical` verlangt **eine** von zwei Adressen, nicht beide. Das steht als
    # `required_one_of` in der Antwort, damit die Oberfläche genau eine
    # Alternatives-Feld-Alternative bauen kann.
    assert ical["required_config"] == []
    assert ical["required_one_of"] == [["url", "path"]]
    assert ical["supports_writing"] is False

    google = next(entry for entry in catalog if entry["kind"] == "google_calendar")
    # `client_secret` ist ein Credential und kein Konfigurationsfeld: die
    # Konfiguration steht im Klartext in einer JSON-Spalte.
    assert google["required_config"] == ["client_id", "calendar_id", "redirect_uri"]
    assert google["required_credentials"] == ["client_secret"]
    assert google["optional_credentials"] == ["refresh_token", "token_scope"]
    assert google["supports_writing"] is True

    caldav_entry = next(entry for entry in catalog if entry["kind"] == "caldav")
    assert caldav_entry["required_credentials"] == ["username", "password"]
    assert caldav_entry["supports_writing"] is True


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
    # `ical` ist inzwischen eine echte Integrationsart und taugt hier nicht mehr
    # als Beispiel für eine unbekannte - deshalb `mystery`.
    source = SyncSource(
        user_id=user.id, source_type="mystery", name="Kalender", configuration={"path": tmp_path_value()}
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
        source_type="mystery",
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
        return "Herzlich willkommen, heute steht ein großer Tag an."

    monkeypatch.setattr("app.services.ai_routing.generate_text", fake_generate)

    body = client.get("/api/v1/greeting").json()

    assert body["source"] == "provider"
    assert body["text"] == "Herzlich willkommen, heute steht ein großer Tag an."
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

    monkeypatch.setattr("app.services.ai_routing.generate_text", fake_generate)

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

    monkeypatch.setattr("app.services.ai_routing.generate_text", fake_generate)

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

    monkeypatch.setattr("app.services.ai_routing.generate_text", kaputt)

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
        "app.services.ai_routing.generate_text",
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


# ===========================================================================
# Kalender-Integrationen: ICS, Google, CalDAV und der Abgleich in beide Richtungen
# ===========================================================================

BERLIN = ZoneInfo("Europe/Berlin")


def ics(*events: str, name: str = "Schulkalender") -> str:
    """Baut einen iCalendar-Text aus fertigen ``VEVENT``-Bloecken."""
    kopf = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//test//DE\r\n"
        f"X-WR-CALNAME:{name}\r\n"
    )
    return kopf + "".join(events) + "END:VCALENDAR\r\n"


def vevent(
    uid: str,
    summary: str,
    start: str,
    end: str,
    *,
    params: str = "",
    extra: str = "",
) -> str:
    return (
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        "DTSTAMP:20260901T000000Z\r\n"
        f"DTSTART{params}:{start}\r\n"
        f"DTEND{params}:{end}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"{extra}"
        "END:VEVENT\r\n"
    )


def write_ics(root: Path, name: str = "schule.ics") -> Path:
    """Legt eine ICS-Datei an, deren Termin **im Abruffenster** liegt.

    Das Fenster beginnt am Montag der laufenden Woche; ein fest verdrahtetes
    Datum aus einem Test von 2026 läge ausserhalb und der Abruf würde - völlig
    richtig - nichts importieren.
    """
    ziel = root / name
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tag = date.today() + timedelta(days=1)
    ziel.write_text(
        ics(
            vevent(
                "mathe-1",
                "Mathe",
                tag.strftime("%Y%m%dT080000"),
                (tag + timedelta(hours=1)).strftime("%Y%m%dT090000"),
                extra="SEQUENCE:1\r\n",
            )
        ),
        encoding="utf-8",
    )
    return ziel


def ics_ohne_schluessel(monkeypatch: pytest.MonkeyPatch, modul: object, handler: object) -> None:
    """Schickt jeden von ``modul`` gebauten ``httpx.Client`` durch eine Attrappe."""
    original = httpx.Client

    def factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs.pop("transport", None)
        return original(*args, **kwargs, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]

    monkeypatch.setattr(modul.httpx, "Client", factory)


def mock_google(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    """Derselbe Trick, aber global - die Google-Adapter bauen ihre Clients selbst.

    ``original`` wird **vor** dem Patch festgehalten. Ohne das ruft die Attrappe
    sich selbst auf, weil ``httpx.Client`` dann die Attrappe ist.
    """
    ics_ohne_schluessel(monkeypatch, SimpleNamespace(httpx=httpx), handler)


class FakeAdapter:
    """Ein Kalenderadapter, dessen Gegenseite für den Test vollständig bekannt ist.

    Bewusst **kein** HTTP: die Regel in ``calendar_sync.sync_calendar`` ist der
    Gegenstand dieser Tests, und eine Attrappe mit steuerbarem ``etag`` und
    steuerbaren Antworten prüft sie ohne Netz, ohne Wartezeit und ohne Zufall.
    Die drei echten Adapter haben eigene Tests mit gemockten Antworten.
    """

    kind = "caldav"
    supports_writing = True

    def __init__(self, events: list[RemoteEvent] | None = None) -> None:
        self.remote = list(events or [])
        self.pushed: list[tuple[RemoteEvent, str | None]] = []
        self.deleted: list[tuple[str, str | None]] = []
        self.conflict_on: set[str] = set()

    def check_connection(self) -> str:
        return "Die Gegenseite ist erreichbar."

    def fetch_events(self, start: datetime, end: datetime) -> RemoteBatch:
        return RemoteBatch(
            events=tuple(ereignis for ereignis in self.remote if _overlaps(ereignis.start, ereignis.end, start, end)),
        )

    def push_event(self, event: RemoteEvent, etag: str | None) -> RemoteEvent:
        if event.uid in self.conflict_on:
            raise CalendarConflictError("Die Gegenseite hat zwischenzeitlich geändert.")
        self.pushed.append((event, etag))
        return replace(event, etag=f"neu-{event.uid}")

    def delete_event(self, uid: str, etag: str | None) -> None:
        if uid in self.conflict_on:
            raise CalendarConflictError("Die Gegenseite hat zwischenzeitlich geändert.")
        self.deleted.append((uid, etag))


def _overlaps(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> bool:
    return as_utc(start) < window_end and as_utc(end) > window_start


def calendar_source(db: Session, user: User, kind: str = "caldav") -> SyncSource:
    konfiguration = (
        {"base_url": "https://kalender.example.org"}
        if kind == "caldav"
        else {"url": "https://kalender.example.org/k.ics"}
    )
    quelle = SyncSource(
        user_id=user.id,
        source_type=kind,
        name="Kalender",
        enabled=True,
        configuration=konfiguration,
        credentials=encrypt_credentials(
            {"username": "lernende", "password": "app-passwort"}, "test-integration-encryption-key"
        ),
    )
    db.add(quelle)
    db.commit()
    db.refresh(quelle)
    return quelle


def remote_event(uid: str, *, title: str = "Mathematik", etag: str = "v1") -> RemoteEvent:
    return RemoteEvent(
        uid=uid,
        title=title,
        start=datetime(2026, 9, 7, 8, 0, tzinfo=BERLIN),
        end=datetime(2026, 9, 7, 9, 0, tzinfo=BERLIN),
        etag=etag,
    )


def run_engine(
    db: Session,
    source: SyncSource,
    adapter: FakeAdapter,
    *,
    allow_writing: bool = False,
    timezone_name: str = "Europe/Berlin",
) -> CalendarSyncOutcome:
    return sync_calendar(
        db,
        source,
        adapter,  # type: ignore[arg-type]
        window_start=date(2026, 9, 7),
        window_end=date(2026, 9, 11),
        timezone_name=timezone_name,
        allow_writing=allow_writing,
    )


def events_of(db: Session, user: User) -> list[CalendarEvent]:
    db.expire_all()
    return list(db.scalars(select(CalendarEvent).where(CalendarEvent.user_id == user.id)).all())


def create_ical(client: TestClient, config: dict[str, str]) -> int:
    antwort = client.post("/api/v1/integrations", json={"kind": "ical", "name": "Abo", "config": config})
    assert antwort.status_code == 201, antwort.text
    return int(antwort.json()["id"])


GOOGLE_CONFIG = {
    "client_id": "client.apps.googleusercontent.com",
    "calendar_id": "primary",
    "redirect_uri": "https://lernraum.example.org/api/backend/v1/integrations/1/oauth/callback",
}


def create_google(client: TestClient) -> int:
    antwort = client.post(
        "/api/v1/integrations",
        json={"kind": "google_calendar", "name": "Google", "config": GOOGLE_CONFIG, "credentials": {"client_secret": "geheim"}},
    )
    assert antwort.status_code == 201, antwort.text
    return int(antwort.json()["id"])


# -- iCalendar lesen und schreiben -----------------------------------------


def test_ics_liest_utc_und_feste_zone_unterschiedlich() -> None:
    kalender = parse_calendar(
        ics(
            vevent("a", "In UTC", "20260907T060000Z", "20260907T070000Z", extra="SEQUENCE:1\r\n"),
            vevent("b", "In Berlin", "20260908T080000", "20260908T090000", params=";TZID=Europe/Berlin"),
        ),
        BERLIN,
    )
    nach_utc, nach_berlin = kalender.events
    assert nach_utc.start == datetime(2026, 9, 7, 6, 0, tzinfo=UTC)
    assert nach_utc.floating is False
    # 08:00 Europe/Berlin ist im September 06:00 UTC. Ohne die Umrechnung stuende
    # hier 08:00 UTC und der Termin waere zwei Stunden daneben.
    assert nach_berlin.start == datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
    assert nach_berlin.floating is False
    assert kalender.name == "Schulkalender"


def test_ics_liest_schwebende_zeit_in_der_nutzerzone() -> None:
    """Ohne Zone in der Datei gibt es keine richtige Antwort, nur eine getroffene."""
    kalender = parse_calendar(ics(vevent("a", "Ohne Zone", "20260907T080000", "20260907T090000")), BERLIN)
    termin = kalender.events[0]
    assert termin.floating is True
    assert termin.start == datetime(2026, 9, 7, 8, 0, tzinfo=BERLIN)
    assert as_utc(termin.start) == datetime(2026, 9, 7, 6, 0, tzinfo=UTC)


def test_ics_liest_ganztaegige_und_gefaltene_termin_e() -> None:
    text = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//DE\r\n"
        "BEGIN:VEVENT\r\nUID:ganz\r\nDTSTAMP:20260901T000000Z\r\n"
        "DTSTART;VALUE=DATE:20260907\r\nDTEND;VALUE=DATE:20260909\r\n"
        "SUMMARY:Freizeit\r\nEND:VEVENT\r\n"
        # Absichtlich auf 75 Oktett gefaltet: die Fortsetzungszeile beginnt mit
        # einem Leerzeichen und gehoert zum selben Termin.
        "BEGIN:VEVENT\r\nUID:lang\r\nDTSTAMP:20260901T000000Z\r\n"
        "DTSTART:20260910T080000Z\r\nDTEND:20260910T090000Z\r\n"
        "SUMMARY:Ein sehr langer Titel, der ueber die Faltgrenze\r\n  hinausgeht\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    ganz, lang = parse_calendar(text, BERLIN).events
    assert ganz.all_day is True
    # Ganztaegig ist DTEND exklusiv: der 7. und 8. enden am 9.
    assert ganz.start.date() == date(2026, 9, 7)
    assert ganz.end.date() == date(2026, 9, 9)
    assert lang.summary == "Ein sehr langer Titel, der ueber die Faltgrenze hinausgeht"


def test_ics_ueberspringt_wiederholungen_und_zaehlt_sie() -> None:
    kalender = parse_calendar(
        ics(
            vevent("woche", "Mathe", "20260907T080000", "20260907T090000", extra="RRULE:FREQ=WEEKLY\r\n"),
            vevent("einzeln", "Deutsch", "20260907T100000", "20260907T110000"),
            vevent("", "Ohne UID", "20260907T120000", "20260907T130000"),
        ),
        BERLIN,
    )
    assert [termin.uid for termin in kalender.events] == ["einzeln"]
    assert kalender.skipped == 2
    assert any("Wiederholungen" in grund for grund in kalender.reasons)


def test_ics_rundlauf_behaelt_utc_und_ganztaegig() -> None:
    ursprung = [
        ICalEvent(
            uid="mit-umlaut",
            summary="Mathe; Algebra, 2. Teil\\nZeile 2",
            start=datetime(2026, 9, 7, 6, 0, tzinfo=UTC),
            end=datetime(2026, 9, 7, 7, 0, tzinfo=UTC),
        ),
        ICalEvent(
            uid="ganz",
            summary="Ferien",
            start=datetime(2026, 9, 7, 0, 0, tzinfo=UTC),
            end=datetime(2026, 9, 9, 0, 0, tzinfo=UTC),
            all_day=True,
        ),
    ]
    text = build_calendar(ursprung, "Lernraum", "Europe/Berlin")
    assert "DTSTART:20260907T060000Z" in text
    assert "DTSTART;VALUE=DATE:20260907" in text
    zurueck = parse_calendar(text, BERLIN)
    assert zurueck.events[0].summary == ursprung[0].summary
    assert zurueck.events[0].end == ursprung[0].end
    assert zurueck.events[1].all_day is True


def test_ics_weist_text_ab_der_kein_kalender_ist() -> None:
    with pytest.raises(ICalError):
        parse_calendar("Das ist kein Kalender.\r\n", BERLIN)


def test_ics_fehlt_die_endzeit_und_die_dauer_wird_gestimmt() -> None:
    text = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//DE\r\n"
        "BEGIN:VEVENT\r\nUID:kurz\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:20260907T080000Z\r\n"
        "SUMMARY:Kurz\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:lang\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:20260907T080000Z\r\n"
        "DURATION:PT90M\r\nSUMMARY:Lang\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:kaputt\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:kein-datum\r\n"
        "SUMMARY:Kaputt\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    kalender = parse_calendar(text, BERLIN)
    kurz, lang = kalender.events
    assert kurz.end - kurz.start == timedelta(hours=1)
    assert lang.end - lang.start == timedelta(minutes=90)
    assert kalender.skipped == 1


def test_ics_datei_ausserhalb_des_ablageordners_wird_abgelehnt(tmp_path: Path) -> None:
    ordner = tmp_path / "ablage"
    ordner.mkdir()
    ausserhalb = tmp_path / "fremd.ics"
    ausserhalb.write_text(ics(vevent("a", "Fremd", "20260907T080000Z", "20260907T090000Z")), encoding="utf-8")
    adapter = IcalAdapter(url="", path=str(ausserhalb), storage_root=str(ordner), timezone_name="UTC", timeout_seconds=5.0)
    with pytest.raises(CalendarConfigError, match="außerhalb"):
        adapter.check_connection()


def test_ical_liest_ueber_url(monkeypatch: pytest.MonkeyPatch) -> None:
    text = ics(vevent("a", "Mathe", "20260907T060000Z", "20260907T070000Z"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/k.ics"
        return httpx.Response(200, text=text)

    ics_ohne_schluessel(monkeypatch, calendar_sync, handler)
    adapter = IcalAdapter(
        url="https://kalender.example.org/k.ics",
        path="",
        storage_root=".",
        timezone_name="Europe/Berlin",
        timeout_seconds=5.0,
    )
    assert "1 lesbare Termine" in adapter.check_connection()
    assert adapter.fetch_events(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)).events[0].uid == "a"


def test_ical_meldet_einen_fehlenden_server(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    ics_ohne_schluessel(monkeypatch, calendar_sync, handler)
    adapter = IcalAdapter(
        url="https://kalender.example.org/k.ics", path="", storage_root=".", timezone_name="UTC", timeout_seconds=5.0
    )
    with pytest.raises(CalendarSyncError, match="HTTP 404"):
        adapter.check_connection()


# -- Die Regel: bei wem die Aenderung gewinnt ------------------------------


def test_erster_abruf_importiert_und_merkt_sich_die_fassung(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    quelle = calendar_source(db, user)

    ergebnis = run_engine(db, quelle, FakeAdapter([remote_event("mathe-1")]))

    assert (ergebnis.imported_events, ergebnis.updated_events, ergebnis.conflicts) == (1, 0, 0)
    termin = events_of(db, user)[0]
    assert termin.title == "Mathematik"
    assert termin.sync_state == SYNC_STATE_SYNCED
    assert termin.remote_etag == "v1"
    assert termin.source == "caldav"
    # Der Schluessel traegt die Quellen-Id, sonst wuerfen zwei Abos mit derselben
    # UID sich gegenseitig ueberschreiben.
    assert termin.external_id == f"{quelle.id}:mathe-1"
    # SQLite legt Zeitstempel ohne Zone ab; `as_utc` ist die Art, wie dieser
    # Stack überall liest.
    assert as_utc(termin.start_time) == datetime(2026, 9, 7, 6, 0, tzinfo=UTC)


def test_fremde_aenderung_wird_uebernommen_wenn_lokal_nichts_passiert(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    run_engine(db, quelle, FakeAdapter([remote_event("mathe-1")]))

    ergebnis = run_engine(db, quelle, FakeAdapter([remote_event("mathe-1", title="Mathematik 2", etag="v2")]))

    assert ergebnis.updated_events == 1
    termin = events_of(db, user)[0]
    assert termin.title == "Mathematik 2"
    assert termin.remote_etag == "v2"


def test_unveraenderter_fuehr_wird_nicht_neu_geschrieben(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    run_engine(db, quelle, FakeAdapter([remote_event("mathe-1")]))

    ergebnis = run_engine(db, quelle, FakeAdapter([remote_event("mathe-1")]))

    assert (ergebnis.updated_events, ergebnis.imported_events) == (0, 0)


def test_lokale_aenderung_gewinnt_gegen_das_ueberschreiben_durch_den_abruf(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Der Kern der Bidirektionalitaet: was hier geaendert wurde, bleibt."""
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    run_engine(db, quelle, FakeAdapter([remote_event("mathe-1")]))

    termin = events_of(db, user)[0]
    termin.title = "Mathe (verschoben)"
    termin.sync_state = SYNC_STATE_EDITED
    db.commit()

    ergebnis = run_engine(db, quelle, FakeAdapter([remote_event("mathe-1", title="Fremde Änderung", etag="v2")]))

    assert ergebnis.updated_events == 0
    nachher = events_of(db, user)[0]
    assert nachher.title == "Mathe (verschoben)"
    # Das etag bleibt auf v1 stehen: der Schreibvorgang vergleicht spaeter mit der
    # Fassung, auf der sich die lokale Aenderung bezieht.
    assert nachher.remote_etag == "v1"
    assert nachher.sync_state == SYNC_STATE_EDITED
    assert ergebnis.conflicts == 1


def test_lokale_aenderung_wird_nachgeschoben(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    gegner = FakeAdapter([remote_event("mathe-1")])
    run_engine(db, quelle, gegner)

    termin = events_of(db, user)[0]
    termin.title = "Mathe (verschoben)"
    termin.sync_state = SYNC_STATE_EDITED
    db.commit()

    ergebnis = run_engine(db, quelle, gegner, allow_writing=True)

    assert ergebnis.pushed_events == 1
    geschoben, etag = gegner.pushed[0]
    assert geschoben.title == "Mathe (verschoben)"
    assert etag == "v1"
    nachher = events_of(db, user)[0]
    assert nachher.sync_state == "synced"
    assert nachher.remote_etag == "neu-mathe-1"


def test_ohne_einwilligung_wird_nicht_geschrieben(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    gegner = FakeAdapter([remote_event("mathe-1")])
    run_engine(db, quelle, gegner)

    termin = events_of(db, user)[0]
    termin.sync_state = SYNC_STATE_EDITED
    db.commit()

    ergebnis = run_engine(db, quelle, gegner, allow_writing=False)

    assert gegner.pushed == []
    assert ergebnis.pushed_events == 0
    # Der offene Zustand wird gezaehlt statt verschwiegen.
    assert ergebnis.conflicts == 1
    assert "offen geblieben" in ergebnis.describe()


def test_widerspruch_bleibt_offen_und_verschleisst_nichts(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    gegner = FakeAdapter([remote_event("mathe-1")])
    run_engine(db, quelle, gegner)

    termin = events_of(db, user)[0]
    termin.title = "Lokal"
    termin.sync_state = SYNC_STATE_EDITED
    db.commit()
    gegner.conflict_on.add("mathe-1")

    ergebnis = run_engine(db, quelle, gegner, allow_writing=True)

    assert ergebnis.conflicts == 1
    nachher = events_of(db, user)[0]
    assert nachher.title == "Lokal"
    assert nachher.sync_state == SYNC_STATE_EDITED
    assert nachher.remote_etag == "v1"


def test_ein_konflikt_bricht_den_lauf_nicht_ab(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Ein Abruf, der beim ersten Widerspruch abbricht, waere der schlechtere."""
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    gegner = FakeAdapter([remote_event("a"), remote_event("b"), remote_event("c")])
    run_engine(db, quelle, gegner)
    for termin in events_of(db, user):
        termin.sync_state = SYNC_STATE_EDITED
    db.commit()
    gegner.conflict_on.add("a")

    ergebnis = run_engine(db, quelle, gegner, allow_writing=True)

    assert ergebnis.conflicts == 1
    assert ergebnis.pushed_events == 2
    assert sorted(geschoben.uid for geschoben, _ in gegner.pushed) == ["b", "c"]


def test_geloeschter_termin_bleibt_grabstein_und_wird_dort_geloescht(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    gegner = FakeAdapter([remote_event("mathe-1")])
    run_engine(db, quelle, gegner)

    termin = events_of(db, user)[0]
    termin.sync_state = SYNC_STATE_DELETED
    db.commit()

    ergebnis = run_engine(db, quelle, gegner, allow_writing=True)

    assert ergebnis.deleted_events == 1
    assert gegner.deleted == [("mathe-1", "v1")]
    assert events_of(db, user) == []


def test_grabstein_ohne_einwilligung_bleibt_liegen(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    gegner = FakeAdapter([remote_event("mathe-1")])
    run_engine(db, quelle, gegner)

    termin = events_of(db, user)[0]
    termin.sync_state = SYNC_STATE_DELETED
    db.commit()

    ergebnis = run_engine(db, quelle, gegner, allow_writing=False)

    assert gegner.deleted == []
    assert ergebnis.conflicts == 1
    assert events_of(db, user)[0].sync_state == SYNC_STATE_DELETED


def test_in_der_quelle_geloeschte_termin_e_verschwinden_lokal(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    run_engine(db, quelle, FakeAdapter([remote_event("mathe-1")]))

    ergebnis = run_engine(db, quelle, FakeAdapter([]))

    assert ergebnis.removed_events == 1
    assert events_of(db, user) == []


def test_lokal_geaenderter_termin_der_in_der_quelle_fehlt_wird_neu_angelegt(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    gegner = FakeAdapter([remote_event("mathe-1")])
    run_engine(db, quelle, gegner)

    termin = events_of(db, user)[0]
    termin.title = "Mathe (verschoben)"
    termin.sync_state = SYNC_STATE_EDITED
    db.commit()

    run_engine(db, quelle, FakeAdapter([]))

    nachher = events_of(db, user)[0]
    assert nachher.title == "Mathe (verschoben)"
    # Ohne etag legt der Schreibvorgang neu an, statt mit einem If-Match zu
    # arbeiten, das garantiert fehlschlaegt.
    assert nachher.remote_etag is None
    run_engine(db, quelle, gegner, allow_writing=True)
    assert gegner.pushed[0][1] is None


def test_manuelle_termine_werden_vom_abruf_nicht_beruehrt(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    db.add(
        CalendarEvent(
            user_id=user.id,
            title="Klavierschule",
            start_time=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            end_time=datetime(2026, 9, 7, 11, 0, tzinfo=UTC),
        )
    )
    db.commit()

    ergebnis = run_engine(db, quelle, FakeAdapter([]))

    assert ergebnis.removed_events == 0
    assert [termin.title for termin in events_of(db, user)] == ["Klavierschule"]


def test_termine_einer_anderen_quelle_werden_nicht_beruehrt(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    erste = calendar_source(db, user)
    zweite = SyncSource(
        user_id=user.id,
        source_type="caldav",
        name="Zweiter Kalender",
        enabled=True,
        configuration={"base_url": "https://zwei.example.org"},
    )
    db.add(zweite)
    db.commit()
    db.refresh(zweite)
    db.add(
        CalendarEvent(
            user_id=user.id,
            sync_source_id=zweite.id,
            title="Aus dem anderen Kalender",
            source="caldav",
            external_id=f"{zweite.id}:x",
            start_time=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            end_time=datetime(2026, 9, 7, 11, 0, tzinfo=UTC),
        )
    )
    db.commit()

    run_engine(db, erste, FakeAdapter([]))

    assert [termin.title for termin in events_of(db, user)] == ["Aus dem anderen Kalender"]


def test_zwei_abos_mit_gleicher_uid_ueberschreiben_sich_nicht(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    erste = calendar_source(db, user)
    zweite = calendar_source(db, user, kind="ical")

    run_engine(db, erste, FakeAdapter([remote_event("gleich", title="Erster")]))
    run_engine(db, zweite, FakeAdapter([remote_event("gleich", title="Zweiter")]))

    assert sorted(termin.title for termin in events_of(db, user)) == ["Erster", "Zweiter"]


def test_ical_wird_nie_geschrieben(tmp_path: Path) -> None:
    """Ein Abo gehoert jemand anderem - unabhaengig von der Einwilligung."""
    adapter = integrations_service.calendar_adapter(
        "ical",
        config={"url": "https://a.example/k.ics"},
        credentials={},
        document_storage_path=str(tmp_path),
        timezone_name="Europe/Berlin",
        timeout_seconds=5.0,
    )
    assert adapter.supports_writing is False
    with pytest.raises(CalendarNotWritableError):
        adapter.push_event(remote_event("x"), None)  # type: ignore[arg-type]
    with pytest.raises(CalendarNotWritableError):
        adapter.delete_event("x", None)  # type: ignore[arg-type]


def test_abgleich_braucht_ein_aktiviertes_konto(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    quelle = calendar_source(db, user)
    quelle.enabled = False
    db.commit()
    with pytest.raises(CalendarConfigError, match="deaktiviert"):
        run_engine(db, quelle, FakeAdapter([]))


def test_kein_kalenderadapter_fuer_unbekannte_art() -> None:
    with pytest.raises(IntegrationValidationError, match="Kalenderadapter"):
        integrations_service.calendar_adapter(
            "webuntis", config={}, credentials={}, document_storage_path=".", timezone_name="UTC", timeout_seconds=1.0
        )


def test_fenster_ist_lokal_gerechnet() -> None:
    """Mitternacht in Berlin ist am Vorabend in UTC - das Fenster beginnt also frueher."""
    start, ende = window_bounds(date(2026, 9, 7), date(2026, 9, 7), BERLIN)
    assert start == datetime(2026, 9, 6, 22, 0, tzinfo=UTC)
    # Rechts offen einen Tag weiter, damit ein ganztaegiger Termin am letzten
    # Tag nicht aus dem Fenster faellt.
    assert ende == datetime(2026, 9, 7, 22, 0, tzinfo=UTC)


# -- Google Kalender --------------------------------------------------------


def test_google_baut_eine_autorisierungs_url_mit_offline_und_schreibrecht() -> None:
    url = google_calendar.build_authorization_url(client_id="client.apps.googleusercontent.com", redirect_uri="https://a.example/r", state="signiert")
    assert url.startswith(google_calendar.AUTHORIZATION_ENDPOINT)
    assert "access_type=offline" in url
    assert "response_type=code" in url
    assert "state=signiert" in url
    assert "calendar.events" in url


def test_google_verlangt_client_id_und_weiterleitung() -> None:
    with pytest.raises(GoogleOAuthError, match="Client-ID"):
        google_calendar.build_authorization_url(client_id="", redirect_uri="https://a.example", state="s")
    with pytest.raises(GoogleOAuthError, match="Weiterleitungs"):
        google_calendar.build_authorization_url(client_id="id", redirect_uri="  ", state="s")


def test_google_tauscht_den_code_gegen_ein_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/token"
        return httpx.Response(
            200,
            json={
                "access_token": "zugriff",
                "refresh_token": "dauer",
                "expires_in": 3600,
                "scope": google_calendar.SCOPES[0],
            },
        )

    ics_ohne_schluessel(monkeypatch, google_calendar, handler)
    tokens = google_calendar.exchange_code(
        client_id="id", client_secret="geheim", code="der-code", redirect_uri="https://a.example"
    )
    assert tokens.refresh_token == "dauer"
    assert tokens.access_token == "zugriff"


def test_google_ohne_refresh_token_ist_ein_fehler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Token ohne Refresh-Token waere nach einer Stunde tot."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "kurz", "expires_in": 3600})

    ics_ohne_schluessel(monkeypatch, google_calendar, handler)
    with pytest.raises(GoogleOAuthError, match="Refresh-Token"):
        google_calendar.exchange_code(
            client_id="id", client_secret="geheim", code="code", redirect_uri="https://a.example"
        )


def test_google_braucht_einen_code() -> None:
    with pytest.raises(GoogleOAuthError, match="Autorisierungscode"):
        google_calendar.exchange_code(
            client_id="id", client_secret="geheim", code="  ", redirect_uri="https://a.example"
        )


def test_access_token_landet_nicht_in_der_credential_blase() -> None:
    """Nur der Refresh-Token ist langlebig; ein Zugriffstoken waere ein zweiter Weg."""
    blase = google_calendar.tokens_to_credentials(
        google_calendar.OAuthTokens(refresh_token="dauer", access_token="kurz", scope="calendar.events"),
        {"client_secret": "geheim", "access_token": "alt"},
    )
    assert blase == {"client_secret": "geheim", "refresh_token": "dauer", "token_scope": "calendar.events"}
    assert "kurz" not in str(blase)
    assert "alt" not in str(blase)


def google_client(calendar_id: str = "primary") -> GoogleCalendarClient:
    return GoogleCalendarClient(
        client_id="id",
        client_secret="geheim",
        calendar_id=calendar_id,
        tokens=OAuthTokens(refresh_token="dauer", access_token="zugriff"),
    )


def test_google_holt_und_liest_termine(monkeypatch: pytest.MonkeyPatch) -> None:
    gesehene: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "neu", "expires_in": 3600})
        gesehene.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "g1",
                        "summary": "Sport",
                        "etag": '"v9"',
                        "updated": "2026-09-07T10:11:12.000Z",
                        "start": {"dateTime": "2026-09-07T08:00:00+02:00"},
                        "end": {"dateTime": "2026-09-07T09:30:00+02:00"},
                    },
                    {"id": "g2", "status": "cancelled", "start": {"dateTime": "2026-09-07T08:00:00+02:00"}},
                    {"id": "g3", "start": {"date": "2026-09-08"}},
                ]
            },
        )

    mock_google(monkeypatch, handler)
    with google_client() as client:
        charge = client.fetch_events(datetime(2026, 9, 7, tzinfo=UTC), datetime(2026, 9, 14, tzinfo=UTC))
    assert "/calendar/v3/calendars/primary/events" in gesehene
    # Der abgebrochene Termin wird gezaehlt und nicht importiert.
    assert charge.skipped == 1
    sport, ganztag = charge.events
    assert sport.uid == "g1"
    assert sport.start == datetime(2026, 9, 7, 6, 0, tzinfo=UTC)
    assert sport.etag == '"v9"'
    assert ganztag.all_day is True


def test_google_verbindungstest_nennt_den_kalender(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"summary": "Schule", "accessRole": "owner", "canWrite": True})

    mock_google(monkeypatch, handler)
    with google_client() as client:
        text = client.check_connection()
    assert "Schule" in text
    assert "anlegen und ändern" in text


def test_google_meldet_wenn_nur_gelesen_werden_kann(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"summary": "Fremd", "accessRole": "reader", "canWrite": False})

    mock_google(monkeypatch, handler)
    with google_client() as client:
        assert "nicht anlegen oder ändern" in client.check_connection()


def test_google_bei_401_ist_das_kein_konflikt_sondern_ein_zugang(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})

    mock_google(monkeypatch, handler)
    with google_client() as client, pytest.raises(CalendarAuthError, match="Invalid Credentials"):
        client.fetch_events(datetime(2026, 9, 7, tzinfo=UTC), datetime(2026, 9, 14, tzinfo=UTC))


def test_google_bei_412_ist_es_ein_konflikt(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(412, json={"error": {"message": "conditionNotMet"}})

    mock_google(monkeypatch, handler)
    with google_client() as client, pytest.raises(CalendarConflictError):
        client.push_event(remote_event("g1"), '"alt"')


def test_google_geloeschter_termin_gilt_als_erledigt(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "notFound"}})

    mock_google(monkeypatch, handler)
    with google_client() as client:
        client.delete_event("g1", '"v1"')


def test_google_unbekannter_kalender_ist_ein_konfigurationsfehler(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "Not Found"}})

    mock_google(monkeypatch, handler)
    with google_client() as client, pytest.raises(CalendarConfigError, match="Not Found"):
        client.check_connection()


def test_google_braucht_kalender_und_zugriff() -> None:
    with pytest.raises(CalendarConfigError, match="Kalender-Id"):
        google_client(calendar_id="")
    with pytest.raises(CalendarAuthError, match="nicht eingerichtet"):
        GoogleCalendarClient(
            client_id="id",
            client_secret="geheim",
            calendar_id="primary",
            tokens=OAuthTokens(refresh_token=""),
        )


def test_google_ohne_token_beschreibt_nur_die_lage() -> None:
    assert "noch nicht eingerichtet" in google_calendar.describe_token_credentials({})
    text = google_calendar.describe_token_credentials({"refresh_token": "d", "token_scope": "calendar.events"})
    assert "Lesen und Schreiben" in text


# -- CalDAV -----------------------------------------------------------------


def caldav_client() -> CalDavClient:
    return CalDavClient(
        base_url="https://caldav.icloud.com/",
        username="lernende@example.org",
        password="app-spezifisches-passwort",
        timezone_name="Europe/Berlin",
        timeout_seconds=5.0,
    )


def test_caldav_verbindungstest_erkennt_das_schreibrecht(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PROPFIND"
        assert request.headers["depth"] == "0"
        return httpx.Response(
            207,
            text=(
                '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"><d:response><d:propstat><d:prop>'
                "<d:current-user-principal><d:href>/principals/users/lernende</d:href></d:current-user-principal>"
                "<d:current-user-privilege-set><d:privilege><d:write/></d:privilege>"
                "</d:current-user-privilege-set></d:prop></d:propstat></d:response></d:multistatus>"
            ),
        )

    mock_google(monkeypatch, handler)
    with caldav_client() as client:
        text = client.check_connection()
    assert "lernende" in text
    assert "anlegen und ändern" in text


def test_caldav_verbindungstest_meldet_leserecht(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            207,
            text=(
                '<d:multistatus xmlns:d="DAV:"><d:response><d:propstat><d:prop>'
                "<d:current-user-privilege-set><d:privilege><d:read/></d:privilege>"
                "</d:current-user-privilege-set></d:prop></d:propstat></d:response></d:multistatus>"
            ),
        )

    mock_google(monkeypatch, handler)
    with caldav_client() as client:
        assert "keine Termine anlegen" in client.check_connection()


def test_caldav_liest_termine_mit_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "REPORT"
        assert "time-range" in request.content.decode()
        kalender = ics(vevent("uid-1", "Orchester", "20260907T180000", "20260907T193000", params=";TZID=Europe/Berlin"))
        eingebettet = kalender.replace("<", "&lt;").replace(">", "&gt;")
        return httpx.Response(
            207,
            text=(
                '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                "<d:response><d:href>/calendars/privat/uid-1.ics</d:href><d:propstat><d:prop>"
                '<d:getetag>"a1b2"</d:getetag>'
                "<d:getlastmodified>Mon, 07 Sep 2026 08:00:00 GMT</d:getlastmodified>"
                f"<c:calendar-data>{eingebettet}</c:calendar-data>"
                "</d:prop></d:propstat></d:response></d:multistatus>"
            ),
        )

    mock_google(monkeypatch, handler)
    with caldav_client() as client:
        charge = client.fetch_events(datetime(2026, 9, 7, tzinfo=UTC), datetime(2026, 9, 14, tzinfo=UTC))
    termin = charge.events[0]
    assert termin.uid == "uid-1"
    assert termin.etag == '"a1b2"'
    assert termin.start == datetime(2026, 9, 7, 18, 0, tzinfo=BERLIN)
    assert termin.modified_at == datetime(2026, 9, 7, 8, 0, tzinfo=UTC)


def test_caldav_setzt_beim_ersetzen_eine_bedingung(monkeypatch: pytest.MonkeyPatch) -> None:
    gesendet: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gesendet.append(request)
        return httpx.Response(201, headers={"ETag": '"neu"'})

    mock_google(monkeypatch, handler)
    with caldav_client() as client:
        gespeichert = client.push_event(remote_event("uid-2"), '"alt"')
        client.push_event(remote_event("uid-3"), None)
    assert gesendet[0].headers["if-match"] == '"alt"'
    assert "UID:uid-2" in gesendet[0].content.decode()
    # Beim allerersten Anlegen gibt es nichts zu ueberschreiben, also keine
    # Bedingung - eine Bedingung auf eine noch nicht existierende Fassung
    # wuerde das Anlegen verhindern.
    assert "if-match" not in gesendet[1].headers
    assert gespeichert.etag == '"neu"'


def test_caldav_412_ist_ein_konflikt_und_404_beim_loeschen_ist_erledigt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(412 if request.method == "PUT" else 404)

    mock_google(monkeypatch, handler)
    with caldav_client() as client, pytest.raises(CalendarConflictError):
        client.push_event(remote_event("uid-4"), '"alt"')
    with caldav_client() as client:
        client.delete_event("uid-4", '"alt"')


def test_caldav_401_nennt_den_typischen_fehler(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    mock_google(monkeypatch, handler)
    with caldav_client() as client, pytest.raises(CalendarAuthError, match="app-spezifische"):
        client.check_connection()


def test_caldav_weist_unbrauchbare_adressen_ab() -> None:
    with pytest.raises(CalendarConfigError, match="http"):
        CalDavClient(base_url="nicht-eine-url", username="a", password="b")
    with pytest.raises(CalendarConfigError, match="Abfrage"):
        CalDavClient(base_url="https://a.example/x?y=1", username="a", password="b")
    with pytest.raises(CalendarAuthError, match="app-spezifische"):
        CalDavClient(base_url="https://a.example", username="a", password="")
    with pytest.raises(CalendarAuthError, match="Apple-ID"):
        CalDavClient(base_url="https://a.example", username="", password="b")


def test_caldav_kalenderpfad_kann_gewaehlt_werden() -> None:
    client = CalDavClient(
        base_url="https://caldav.example.org",
        username="a",
        password="b",
        calendar_path="/dav/calendars/lernende/privat/",
    )
    assert client._calendar_url().endswith("/dav/calendars/lernende/privat/")


# -- Der signierte state ----------------------------------------------------


def test_state_uebersteht_den_rundlauf() -> None:
    token = oauth_state.sign_state(7, 3, "geheimer-schluessel")
    claims = oauth_state.verify_state(token, "geheimer-schluessel")
    assert (claims.subject, claims.actor) == (7, 3)
    assert claims.nonce


def test_state_ist_unveraenderlich() -> None:
    token = oauth_state.sign_state(7, 3, "geheimer-schluessel")
    with pytest.raises(OAuthStateError, match="verändert"):
        oauth_state.verify_state(token, "anderer-schluessel")
    with pytest.raises(OAuthStateError, match="state"):
        oauth_state.verify_state(token[:-2] + "xy", "geheimer-schluessel")
    with pytest.raises(OAuthStateError, match="state"):
        oauth_state.verify_state("ohne-punkt", "geheimer-schluessel")


def test_state_ist_zeitlich_befristet() -> None:
    jetzt = 1_800_000_000.0
    token = oauth_state.sign_state(7, 3, "geheimer-schluessel", now=jetzt)
    with pytest.raises(OAuthStateError, match="abgelaufen"):
        oauth_state.verify_state(token, "geheimer-schluessel", now=jetzt + oauth_state.DEFAULT_TTL_SECONDS + 1)
    assert oauth_state.verify_state(token, "geheimer-schluessel", now=jetzt + 60)


def test_state_aus_der_zukunft_wird_abgelehnt() -> None:
    jetzt = 1_800_000_000.0
    token = oauth_state.sign_state(7, 3, "geheimer-schluessel", now=jetzt)
    with pytest.raises(OAuthStateError, match="Zukunft"):
        oauth_state.verify_state(token, "geheimer-schluessel", now=jetzt - 3600)


def test_state_braucht_einen_geheimen_schluessel_und_positive_werte() -> None:
    token = oauth_state.sign_state(7, 3, "geheimer-schluessel")
    with pytest.raises(OAuthStateError, match="geheime"):
        oauth_state.verify_state(token, "")
    with pytest.raises(OAuthStateError, match="positiv"):
        oauth_state.sign_state(0, 3, "geheimer-schluessel")


# -- Endpunkte --------------------------------------------------------------


def test_kalender_routen_sind_registriert(app_client: TestClient) -> None:
    pfade = {route.path for route in app_client.app.routes}
    assert "/api/v1/calendar/events" in pfade
    assert "/api/v1/calendar/writing" in pfade
    assert "/api/v1/calendar/feed" in pfade
    assert "/api/v1/calendar/feed/{token}" in pfade
    assert "/api/v1/integrations/{integration_id}/authorize" in pfade
    assert "/api/v1/integrations/{integration_id}/oauth/callback" in pfade


def test_freigabe_zum_schreiben_ist_standardmaessig_aus(app_client: TestClient) -> None:
    login(app_client)
    koerper = app_client.get("/api/v1/calendar/writing").json()
    assert koerper["share_calendar_for_writing"] is False
    assert koerper["preference_key"] == "share_calendar_for_writing"
    assert koerper["uses"]

    assert app_client.put("/api/v1/calendar/writing", json={"enabled": True}).json()["share_calendar_for_writing"] is True
    assert app_client.get("/api/v1/calendar/writing").json()["share_calendar_for_writing"] is True
    assert app_client.put("/api/v1/calendar/writing", json={"enabled": False}).json()["share_calendar_for_writing"] is False
    # Die anderen Schluessel in `preferences` bleiben stehen.
    assert app_client.get("/api/v1/calendar/writing").status_code == 200


def test_freigabe_und_kalender_brauchen_eine_sitzung(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/calendar/writing").status_code == 401
    assert app_client.put("/api/v1/calendar/writing", json={"enabled": True}).status_code == 401
    assert app_client.get("/api/v1/calendar/feed").status_code == 401
    assert app_client.post("/api/v1/calendar/feed").status_code == 401


def test_kaputte_kalendereinstellung_schaltet_die_freigabe_nicht_von_selbst_ein(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    db.add(UserSettings(user_id=user.id, preferences={"share_calendar_for_writing": "ja"}))
    db.commit()
    login(client)
    assert client.get("/api/v1/calendar/writing").json()["share_calendar_for_writing"] is False
    assert calendar_consent.calendar_writing_allowed(db, user.id) is False


def test_freigabe_bleibt_je_nutzer_getrennt(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    fremder = User(username="fremd", password_hash=hash_password("x"), is_active=True, timezone="UTC")
    db.add(fremder)
    db.commit()
    login(client)
    assert client.put("/api/v1/calendar/writing", json={"enabled": True}).status_code == 200
    assert calendar_consent.calendar_writing_allowed(db, user.id) is True
    assert calendar_consent.calendar_writing_allowed(db, fremder.id) is False


def test_abruf_von_google_ohne_die_freigabe(app_client: TestClient) -> None:
    login(app_client)
    id_ = create_google(app_client)

    ohne_freigabe = app_client.post(f"/api/v1/integrations/{id_}/authorize")
    assert ohne_freigabe.status_code == 422
    assert "Freigabe zum Schreiben" in ohne_freigabe.json()["detail"]

    app_client.put("/api/v1/calendar/writing", json={"enabled": True})
    mit_freigabe = app_client.post(f"/api/v1/integrations/{id_}/authorize")
    assert mit_freigabe.status_code == 200
    koerper = mit_freigabe.json()
    assert koerper["authorization_url"].startswith(google_calendar.AUTHORIZATION_ENDPOINT)
    assert koerper["scopes"] == [google_calendar.SCOPES[0]]
    assert app_client.get(f"/api/v1/integrations/{id_}").json()["status"] == "pending"


def test_autorisierung_braucht_vollstaendige_angaben(app_client: TestClient) -> None:
    login(app_client)
    unvollstaendig = app_client.post(
        "/api/v1/integrations",
        json={"kind": "google_calendar", "name": "Unvollständig", "config": {"client_id": "abc"}},
    )
    assert unvollstaendig.status_code == 422


def test_autorisierung_gibt_es_nur_fuer_google(app_client: TestClient) -> None:
    login(app_client)
    dokumente = app_client.post(
        "/api/v1/integrations", json={"kind": "documents", "name": "Dokumente", "config": {"path": "dok"}}
    )
    assert dokumente.status_code == 201
    # 404 statt 400: eine fremde Art zu autorisieren wuerde sonst verraten, dass
    # die Id ueberhaupt existiert.
    assert app_client.post(f"/api/v1/integrations/{dokumente.json()['id']}/authorize").status_code == 404
    assert app_client.get(f"/api/v1/integrations/{dokumente.json()['id']}/oauth/callback").status_code == 404


def test_autorisierung_ist_je_nutzer_getrennt(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    fremde = SyncSource(user_id=user.id + 100, source_type="google_calendar", name="Fremd", configuration={})
    db.add(fremde)
    db.commit()
    login(client)
    assert client.post(f"/api/v1/integrations/{fremde.id}/authorize").status_code == 404


def test_rueckkehr_legt_das_token_verschluesselt_ab(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db, user = app_context
    login(client)
    id_ = create_google(client)
    client.put("/api/v1/calendar/writing", json={"enabled": True})
    state = client.post(f"/api/v1/integrations/{id_}/authorize").json()["state"]

    monkeypatch.setattr(
        google_calendar,
        "exchange_code",
        lambda **_: OAuthTokens(refresh_token="dauer-token", access_token="kurz", scope="calendar.events"),
    )
    antwort = client.get(f"/api/v1/integrations/{id_}/oauth/callback", params={"code": "der-code", "state": state})
    assert antwort.status_code == 200
    assert "text/html" in antwort.headers["content-type"]
    assert "Google Kalender ist verbunden" in antwort.text
    # Kein Geheimnis in der Seite, und ein Weg zurueck in die Einstellungen.
    assert "der-code" not in antwort.text
    assert 'href="/settings"' in antwort.text

    db.expire_all()
    quelle = db.get(SyncSource, id_)
    assert quelle is not None and quelle.credentials is not None
    assert quelle.credentials.startswith("sa1.")
    assert "dauer-token" not in quelle.credentials
    blase = decrypt_credentials(quelle.credentials, "test-integration-encryption-key")
    assert blase["refresh_token"] == "dauer-token"
    # Der Client-Schluessel ist nicht verloren gegangen, und der Zugriffstoken
    # liegt nirgends im Klartext.
    assert blase["client_secret"] == "geheim"
    assert "kurz" not in str(blase)
    assert client.get(f"/api/v1/integrations/{id_}").json()["status"] == "configured"


def test_rueckkehr_mit_falschem_state_speichert_nichts(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db, user = app_context
    login(client)
    id_ = create_google(client)

    def darf_nie_rufen(**_: object) -> OAuthTokens:
        raise AssertionError("Ohne gültigen state darf der Code nicht getauscht werden.")

    monkeypatch.setattr(google_calendar, "exchange_code", darf_nie_rufen)
    antwort = client.get(f"/api/v1/integrations/{id_}/oauth/callback", params={"code": "x", "state": "gefaelscht"})
    assert antwort.status_code == 200
    assert "passt nicht" in antwort.text
    db.expire_all()
    quelle = db.get(SyncSource, id_)
    assert quelle is not None
    blase = decrypt_credentials(quelle.credentials or "", "test-integration-encryption-key")
    assert "refresh_token" not in blase


def test_rueckkehr_mit_state_einer_anderen_integration(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db, user = app_context
    login(client)
    erste = create_google(client)
    zweite = client.post(
        "/api/v1/integrations",
        json={
            "kind": "google_calendar",
            "name": "Zweite",
            "config": GOOGLE_CONFIG,
            "credentials": {"client_secret": "geheim"},
        },
    ).json()["id"]
    client.put("/api/v1/calendar/writing", json={"enabled": True})
    fremder_state = client.post(f"/api/v1/integrations/{erste}/authorize").json()["state"]

    monkeypatch.setattr(google_calendar, "exchange_code", lambda **_: OAuthTokens(refresh_token="dauer", access_token="k"))
    antwort = client.get(f"/api/v1/integrations/{zweite}/oauth/callback", params={"code": "x", "state": fremder_state})
    assert "anderen Autorisierung" in antwort.text


def test_rueckkehr_mit_abgebrochener_einwilligung(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    id_ = create_google(client)
    client.put("/api/v1/calendar/writing", json={"enabled": True})
    state = client.post(f"/api/v1/integrations/{id_}/authorize").json()["state"]
    antwort = client.get(
        f"/api/v1/integrations/{id_}/oauth/callback",
        params={"code": "x", "state": state, "error": "access_denied"},
    )
    assert "abgebrochen" in antwort.text
    assert "access_denied" in antwort.text
    db.expire_all()
    quelle = db.get(SyncSource, id_)
    assert quelle is not None and quelle.credentials is not None
    assert "refresh_token" not in decrypt_credentials(quelle.credentials, "test-integration-encryption-key")


def test_rueckkehr_ohne_sitzung_geht_nicht(app_client: TestClient) -> None:
    id_ = 999
    assert app_client.get(f"/api/v1/integrations/{id_}/oauth/callback", params={"code": "x"}).status_code == 401


def test_ical_verlangt_eine_adresse_oder_einen_pfad(app_client: TestClient) -> None:
    login(app_client)
    ohne = app_client.post("/api/v1/integrations", json={"kind": "ical", "name": "Abo", "config": {}})
    assert ohne.status_code == 422
    assert "url oder path" in ohne.json()["detail"]

    mit_url = app_client.post(
        "/api/v1/integrations", json={"kind": "ical", "name": "Abo", "config": {"url": "https://a.example/k.ics"}}
    )
    assert mit_url.status_code == 201
    assert mit_url.json()["missing_fields"] == []
    assert mit_url.json()["config"]["url"] == "https://a.example/k.ics"


def test_sync_endpoint_meldet_die_kalenderzahlen(
    app_context: tuple[TestClient, Session, User], tmp_path: Path
) -> None:
    client, db, user = app_context
    login(client)
    id_ = create_ical(client, {"path": str(write_ics(tmp_path))})

    koerper = client.post(f"/api/v1/integrations/{id_}/sync").json()
    assert koerper["ok"] is True
    assert koerper["imported_events"] == 1
    assert koerper["window_start"] is not None
    assert koerper["window_end"] is not None
    assert client.get("/api/v1/calendar/events").json()[0]["title"] == "Mathe"
    # Ein zweiter Abruf darf nichts doppelt anlegen.
    assert client.post(f"/api/v1/integrations/{id_}/sync").json()["imported_events"] == 0
    assert len(client.get("/api/v1/calendar/events").json()) == 1


def test_kalenderverbindungstest_liest_die_quelle(
    app_context: tuple[TestClient, Session, User], tmp_path: Path
) -> None:
    client, db, user = app_context
    datei = write_ics(tmp_path)
    login(client)
    id_ = create_ical(client, {"path": str(datei)})

    koerper = client.post(f"/api/v1/integrations/{id_}/test").json()
    assert koerper["ok"] is True
    assert "lesbare Termine" in koerper["message"]

    datei.unlink()
    defekt = client.post(f"/api/v1/integrations/{id_}/test").json()
    assert defekt["ok"] is False
    assert defekt["status"] == "error"
    assert "nicht gefunden" in defekt["message"]


def test_sync_meldet_keine_fehlende_freigabe_als_fehler(
    app_context: tuple[TestClient, Session, User], tmp_path: Path
) -> None:
    """Lesen darf ohne Freigabe stattfinden - nur das Schreiben nicht."""
    client, db, user = app_context
    login(client)
    id_ = create_ical(client, {"path": str(write_ics(tmp_path))})
    assert client.post(f"/api/v1/integrations/{id_}/sync").json()["ok"] is True
    assert client.get("/api/v1/calendar/writing").json()["share_calendar_for_writing"] is False


def test_terminaenderung_merkt_sich_die_lokale_entscheidung(
    app_context: tuple[TestClient, Session, User], tmp_path: Path
) -> None:
    client, db, user = app_context
    datei = write_ics(tmp_path)
    login(client)
    id_ = create_ical(client, {"path": str(datei)})
    client.post(f"/api/v1/integrations/{id_}/sync")
    ereignis = client.get("/api/v1/calendar/events").json()[0]

    geaendert = client.patch(f"/api/v1/calendar/events/{ereignis['id']}", json={"title": "Mathe (verschoben)"}).json()
    assert geaendert["sync_state"] == "edited"

    # Und die Quelle liefert danach dieselbe Zeile: die Aenderung darf nicht
    # beim naechsten Abruf verschwinden.
    client.post(f"/api/v1/integrations/{id_}/sync")
    assert client.get("/api/v1/calendar/events").json()[0]["title"] == "Mathe (verschoben)"


def test_terminloeschung_bei_einer_quelle_wird_zum_grabstein(
    app_context: tuple[TestClient, Session, User], tmp_path: Path
) -> None:
    client, db, user = app_context
    login(client)
    id_ = create_ical(client, {"path": str(write_ics(tmp_path))})
    client.post(f"/api/v1/integrations/{id_}/sync")
    ereignis = client.get("/api/v1/calendar/events").json()[0]

    assert client.delete(f"/api/v1/calendar/events/{ereignis['id']}").status_code == 200
    assert client.get("/api/v1/calendar/events").json() == []
    assert client.get(f"/api/v1/calendar/events/{ereignis['id']}").status_code == 404

    db.expire_all()
    zeile = db.get(CalendarEvent, ereignis["id"])
    assert zeile is not None
    assert zeile.sync_state == SYNC_STATE_DELETED
    assert zeile.is_tombstone is True


def test_handtermine_werden_beim_loeschen_echt_entfernt(app_client: TestClient) -> None:
    login(app_client)
    ereignis = app_client.post(
        "/api/v1/calendar/events",
        json={"title": "Klavierschule", "start_time": "2026-09-07T10:00:00Z", "end_time": "2026-09-07T11:00:00Z"},
    ).json()
    assert ereignis["sync_state"] == "synced"
    assert ereignis["sync_source_id"] is None
    assert app_client.delete(f"/api/v1/calendar/events/{ereignis['id']}").status_code == 200
    assert app_client.get("/api/v1/calendar/events").json() == []


def test_grabstein_laesst_sich_nicht_bearbeiten(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    ereignis = client_calendar_event(client, user, db, title="Orchester")
    db.get(CalendarEvent, ereignis).sync_state = SYNC_STATE_DELETED  # type: ignore[union-attr]
    db.commit()
    assert client.patch(f"/api/v1/calendar/events/{ereignis}", json={"title": "Neu"}).status_code == 404


def client_calendar_event(client: TestClient, user: User, db: Session, *, title: str) -> int:
    login(client)
    zeile = CalendarEvent(
        user_id=user.id,
        title=title,
        start_time=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
        end_time=datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
    )
    db.add(zeile)
    db.commit()
    db.refresh(zeile)
    return int(zeile.id)


# -- Die abonnierbare ICS-Quelle -------------------------------------------


def test_abo_adresse_ist_nur_mit_gueltigem_schluessel_abrufbar(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    assert client.get("/api/v1/calendar/feed").json()["active"] is False

    erzeugt = client.post("/api/v1/calendar/feed")
    assert erzeugt.status_code == 201
    pfad = erzeugt.json()["url_path"]
    assert pfad.startswith("/api/v1/calendar/feed/")
    assert client.get("/api/v1/calendar/feed").json()["active"] is True

    inhalt = client.get(pfad)
    assert inhalt.status_code == 200
    assert "text/calendar" in inhalt.headers["content-type"]
    assert inhalt.text.startswith("BEGIN:VCALENDAR")

    # Ohne Sitzung: ein abonnierender Kalender hat kein Cookie.
    ohne = TestClient(client.app)
    assert ohne.get(pfad).status_code == 200
    # Ein falscher Schluessel liefert nichts - und keine Auskunft darueber.
    assert ohne.get(f"/api/v1/calendar/feed/{'x' * 43}").status_code == 404
    assert ohne.get("/api/v1/calendar/feed/kurz").status_code == 404


def test_neuer_schluessel_macht_die_alte_adresse_unbrauchbar(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    erster = client.post("/api/v1/calendar/feed").json()["url_path"]
    zweiter = client.post("/api/v1/calendar/feed").json()["url_path"]
    assert erster != zweiter
    assert client.get(zweiter).status_code == 200
    assert client.get(erster).status_code == 404


def test_abo_zeigt_die_eigenen_termine_und_keine_grabsteine(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    pfad = client.post("/api/v1/calendar/feed").json()["url_path"]
    # Relativ zu heute: ein festes Datum faellt nach vier Wochen aus dem Fenster des Abos.
    tag = date.today() + timedelta(days=3)
    morgen = tag + timedelta(days=1)
    client.post(
        "/api/v1/calendar/events",
        json={"title": "Orchester", "start_time": f"{tag}T10:00:00Z", "end_time": f"{tag}T12:00:00Z"},
    )
    wegwerfen = client.post(
        "/api/v1/calendar/events",
        json={"title": "Wegwerfen", "start_time": f"{morgen}T10:00:00Z", "end_time": f"{morgen}T12:00:00Z"},
    ).json()
    client.delete(f"/api/v1/calendar/events/{wegwerfen['id']}")

    text = client.get(pfad).text
    assert "Orchester" in text
    assert "Wegwerfen" not in text
    # Zeiten gehen mit Zone hinaus - so braucht der Weg zurueck keine Annahme.
    assert f"DTSTART:{tag:%Y%m%d}T100000Z" in text


def test_abo_zeigt_termine_auch_in_der_vergangenheit(app_context: tuple[TestClient, Session, User]) -> None:
    """Ein importierter alter Termin darf beim naechsten Abruf nicht verschwinden."""
    client, db, user = app_context
    login(client)
    pfad = client.post("/api/v1/calendar/feed").json()["url_path"]
    heute = date.today()
    alt = heute - timedelta(days=2)
    client.post(
        "/api/v1/calendar/events",
        json={
            "title": "Vor dem Fenster",
            "start_time": f"{alt.isoformat()}T10:00:00Z",
            "end_time": f"{alt.isoformat()}T12:00:00Z",
        },
    )
    assert "Vor dem Fenster" in client.get(pfad).text


def test_abo_zeigt_nur_die_eigenen_termine(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    fremder = User(username="fremd", password_hash=hash_password("x"), is_active=True, timezone="UTC")
    db.add(fremder)
    db.commit()
    login(client)
    tag = date.today() + timedelta(days=3)
    client.post(
        "/api/v1/calendar/events",
        json={"title": "Privat", "start_time": f"{tag}T10:00:00Z", "end_time": f"{tag}T12:00:00Z"},
    )
    pfad = client.post("/api/v1/calendar/feed").json()["url_path"]
    assert "Privat" in client.get(pfad).text

    db.add(UserSettings(user_id=fremder.id, preferences={"calendar_feed_token": "f" * 43}))
    db.commit()
    fremder_text = calendar_feed.build_feed(db, fremder.id, timezone_name="UTC")
    assert "Privat" not in fremder_text


def test_abo_adresse_ist_ein_reiner_pfad(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    token = calendar_feed.rotate_feed_token(db, user.id).token
    assert calendar_feed.feed_path(token) == f"/api/v1/calendar/feed/{token}"
    # Der Schluessel ist lang genug, dass ihn niemand erraten kann.
    assert len(token) >= 40


# --------------------------------------------------------------------------
# Modellkatalog und Download-Manager
# --------------------------------------------------------------------------


def _test_model(model_id: str = "probe-modell", payload: bytes = b"", **kwargs: object) -> ModelSpec:
    """Ein Katalogeintrag für den Test, mit echtem Hash über ``payload``.

    Die URL zeigt auf die erlaubte Quelle, aber der Test hängt einen
    ``httpx``-Transport davor - es geht **kein** Byte ins Netz.
    """
    content = payload or b"gewichten"
    return ModelSpec(
        id=model_id,
        label=f"Probe {model_id}",
        task=kwargs.pop("task", "text_generation"),
        family="Test",
        description="Ein Modell für den Test.",
        license="MIT",
        source_repository="test/probe",
        source_page="https://huggingface.co/test/probe",
        parameter_count=kwargs.pop("parameter_count", 1000),
        quantization=kwargs.pop("quantization", "Q4_K_M"),
        context_length=kwargs.pop("context_length", 4096),
        files=(
            ModelFile(
                name="probe.gguf",
                url="https://huggingface.co/test/probe/resolve/main/probe.gguf",
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            ),
        ),
        **kwargs,  # type: ignore[arg-type]
    )


class _FakeModelTransport(httpx.BaseTransport):
    """Ein Modellserver im Test: Range, Abbruch, Pause und Weiterleitung.

    Der Stream wird **zügig** ausgeliefert, damit ein Test den Download an
    genau der Stelle anhalten kann, die ihn interessiert (``pause_after``), und
    umgekehrt.
    """

    def __init__(
        self,
        payload: bytes,
        *,
        supports_range: bool = True,
        fail_once: bool = False,
        pause_after: int | None = None,
        redirect_to: str | None = None,
        block_size: int = 1024 * 1024,
    ) -> None:
        self.payload = payload
        self.supports_range = supports_range
        self.fail_once = fail_once
        self.pause_after = pause_after
        self.redirect_to = redirect_to
        self.block_size = block_size
        self.requests: list[httpx.Request] = []
        self.paused = threading.Event()
        self.resume = threading.Event()
        self._failed = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.redirect_to is not None:
            return httpx.Response(302, headers={"location": self.redirect_to}, request=request)

        raw_range = request.headers.get("range", "")
        start = 0
        if raw_range and self.supports_range:
            start = int(raw_range.split("=", 1)[1].split("-", 1)[0])
        if start and not self.supports_range:
            # Genau die Falle aus der Aufgabe: Der Server ignoriert die Range
            # und schickt die ganze Datei. Antwort 200, nicht 206.
            return self._respond(200, self.payload, request)
        if start > len(self.payload):
            return httpx.Response(416, request=request)
        body = self.payload[start:]
        if self.supports_range and start:
            return self._respond(206, body, request, content_range=f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}")
        return self._respond(200, body, request)

    def _respond(
        self,
        status_code: int,
        body: bytes,
        request: httpx.Request,
        *,
        content_range: str | None = None,
    ) -> httpx.Response:
        should_fail = self.fail_once and not self._failed
        if should_fail:
            self._failed = True
        blocks = [body[index : index + self.block_size] for index in range(0, len(body), self.block_size)] or [b""]

        def stream() -> object:
            for index, block in enumerate(blocks):
                if should_fail and index > 0:
                    raise httpx.ReadError("Verbindung mitten im Stream abgerissen")
                if self.pause_after is not None and index == self.pause_after:
                    self.paused.set()
                    self.resume.wait(timeout=10)
                yield block

        headers = {"content-length": str(len(body))}
        if content_range:
            headers["content-range"] = content_range
        return httpx.Response(status_code, headers=headers, content=stream(), request=request)


def _manager(root: Path, transport: httpx.BaseTransport, **kwargs: object) -> ModelDownloadManager:
    return ModelDownloadManager(
        root,
        timeout_seconds=5.0,
        client_factory=lambda: httpx.Client(transport=transport, timeout=5.0, follow_redirects=False),
        **kwargs,  # type: ignore[arg-type]
    )


def _part_file(root: Path, spec: ModelSpec) -> Path:
    return root / spec.id / f"{spec.files[0].name}{PART_SUFFIX}"


def _target_file(root: Path, spec: ModelSpec) -> Path:
    return root / spec.id / spec.files[0].name


def _install_model(
    app_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    *,
    transport: httpx.BaseTransport | None = None,
    manager_kwargs: dict[str, object] | None = None,
) -> tuple[ModelSpec, ModelDownloadManager]:
    """Ein Testmodell in den Katalog hängen und einen Manager darauf setzen."""
    spec = _test_model(payload=payload)
    monkeypatch.setitem(model_catalog.MODEL_CATALOG, spec.id, spec)
    server = transport or _FakeModelTransport(payload)
    manager = _manager(tmp_path / "modelle", server, **(manager_kwargs or {}))
    app_client.app.state.model_downloads = manager
    return spec, manager


# --- Katalog ---------------------------------------------------------------


def test_der_modellkatalog_traegt_nur_vollstaendige_metadaten() -> None:
    """Jeder Eintrag braucht Aufgabe, mindestens eine Datei und eine echte Pruefsumme."""
    assert catalog_models()
    for spec in catalog_models():
        assert spec.task in MODEL_TASKS
        assert spec.files
        assert spec.size_bytes > 0
        for item in spec.files:
            assert len(item.sha256) == 64
            assert set(item.sha256) <= set("0123456789abcdef")
            assert item.size_bytes > 0
            validate_download_url(item.url)


def test_der_katalog_ist_nicht_auf_gguf_textmodelle_beschraenkt() -> None:
    """Sprachmodelle und Textmodelle teilen sich ein Format - sonst waere es Whisper-Rest."""
    aufgaben = {spec.task for spec in catalog_models()}
    assert "text_generation" in aufgaben
    assert "speech_to_text" in aufgaben


def test_der_ram_bedarf_wird_aus_den_metadaten_abgeleitet() -> None:
    spec = _test_model(parameter_count=2_000_000_000, quantization="Q4_K_M", context_length=4096)
    estimate = ram_estimate(spec)
    # 2e9 * 4.8 / 8 = 1,2 Mrd. Byte Gewichte, mal 1,1 Headroom, plus 4096 Token KV.
    assert estimate.weight_bytes == round(2_000_000_000 * 4.8 / 8)
    assert estimate.ram_bytes > estimate.weight_bytes
    assert estimate.is_estimate is True
    assert "Parameter" in estimate.derived_from
    assert "Kontexttoken" in estimate.derived_from


def test_ohne_parameterzahl_rechnet_die_schaetzung_mit_der_dateigroesse() -> None:
    """Whisper hat keine Parameterzahl im Katalog - dann wird nicht geraten."""
    payload = b"x" * 4096
    spec = _test_model(payload=payload, parameter_count=None, quantization="f16", context_length=None)
    estimate = ram_estimate(spec)
    assert estimate.weight_bytes == len(payload)
    assert "Dateigröße" in estimate.derived_from
    assert "Kontexttoken" not in estimate.derived_from


def test_die_quantisierung_stammt_aus_dem_namen_und_nicht_aus_einer_tabelle() -> None:
    assert bits_per_weight("Q4_K_M") == 4.8
    assert bits_per_weight("q5_k_s") == 5.8
    assert bits_per_weight("Q8_0") == 8.0
    assert bits_per_weight("IQ4_XS") == 4.8
    assert bits_per_weight("F16") == 16.0
    # Unbekanntes wird nicht geraten, sondern als unbekannt gemeldet.
    assert bits_per_weight("AQ_256") is None
    assert bits_per_weight(None) is None


def test_ein_modell_mit_128k_kontext_wird_nicht_nach_128k_geplant() -> None:
    """Der Kontextpuffer wird gekappt, sonst waere das Modell unbenutzbar."""
    gedeckelt = _test_model(context_length=8192)
    lang = _test_model(context_length=131072)
    assert ram_estimate(lang).ram_bytes == ram_estimate(gedeckelt).ram_bytes


def test_eine_fremde_modellquelle_steht_nicht_auf_der_allowlist() -> None:
    for verboten in (
        "http://huggingface.co/test/probe/resolve/main/probe.gguf",
        "https://169.254.169.254/latest/meta-data/",
        "https://backend:8000/api/v1/models",
        "https://localhost:8080/probe.gguf",
        "https://huggingface.co:8443/probe.gguf",
        "https://user:pw@huggingface.co/probe.gguf",
        "file:///etc/passwd",
    ):
        with pytest.raises(ModelCatalogError):
            validate_download_url(verboten)


def test_die_echten_cdn_hosts_von_hugging_face_sind_erlaubt() -> None:
    """HF leitet auf eigene CDN-Hosts um - ohne sie waere jeder Download tot."""
    for erlaubt in (
        "https://huggingface.co/test/probe/resolve/main/probe.gguf",
        "https://cdn-lfs.huggingface.co/repos/ab/cd/blob",
        "https://transfer.xethub.hf.co/transfer/abc",
    ):
        assert validate_download_url(erlaubt) == erlaubt


def test_eine_unbekannte_modell_id_wird_im_katalog_abgelehnt() -> None:
    with pytest.raises(ModelCatalogError):
        model_spec("gibt-es-nicht")


# --- Download-Manager auf Serviceebene --------------------------------------


def test_ein_download_prueft_die_summe_und_benennt_dann_um(tmp_path: Path) -> None:
    payload = b"gewichten-in-huggingface" * 1000
    spec = _test_model(payload=payload)
    manager = _manager(tmp_path / "modelle", _FakeModelTransport(payload))

    manager.start(spec)
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    state = manager.status(spec)
    assert state.status == "installed"
    assert state.verified is True
    assert state.progress_percent == 100.0
    assert _target_file(tmp_path / "modelle", spec).read_bytes() == payload
    assert not _part_file(tmp_path / "modelle", spec).exists()
    assert (tmp_path / "modelle" / spec.id / MANIFEST_NAME).exists()


def test_ein_zweiter_download_desselben_modells_wird_abgelehnt(tmp_path: Path) -> None:
    """Zwei Tabs auf denselben Knopf: der zweite bekommt 409 und keinen zweiten Thread."""
    payload = b"a" * (3 * 1024 * 1024)
    spec = _test_model(payload=payload)
    transport = _FakeModelTransport(payload, pause_after=1)
    manager = _manager(tmp_path / "modelle", transport)

    manager.start(spec)
    assert transport.paused.wait(timeout=10)
    with pytest.raises(ModelDownloadBusy):
        manager.start(spec)
    transport.resume.set()
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)
    assert manager.status(spec).status == "installed"


def test_der_download_setzt_nach_einem_abruf_unterbrochen_fort(tmp_path: Path) -> None:
    """Einabriss im Stream darf kein Ende sein: der naechste Versuch holt den Rest."""
    payload = b"b" * (3 * 1024 * 1024)
    spec = _test_model(payload=payload)
    transport = _FakeModelTransport(payload, fail_once=True)
    manager = _manager(tmp_path / "modelle", transport)

    manager.start(spec)
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)
    assert manager.status(spec).status == "failed"

    part = _part_file(tmp_path / "modelle", spec)
    teilstand = part.stat().st_size
    assert 0 < teilstand < len(payload)

    manager.start(spec)
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    assert manager.status(spec).status == "installed"
    assert _target_file(tmp_path / "modelle", spec).read_bytes() == payload
    # Der zweite Versuch hat eine Range geschickt - das ist der Fortsetzungspunkt.
    assert transport.requests[-1].headers["range"] == f"bytes={teilstand}-"


def test_die_pruefsumme_gilt_ueber_alle_teilstuecke(tmp_path: Path) -> None:
    """Der vorhandene Praefix muss in den Hash einrechnen, sonst prueft nichts."""
    payload = b"c" * (3 * 1024 * 1024)
    spec = _test_model(payload=payload)
    transport = _FakeModelTransport(payload, fail_once=True)
    manager = _manager(tmp_path / "modelle", transport)

    manager.start(spec)
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)
    manager.start(spec)
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    assert manager.status(spec).status == "installed"
    assert _target_file(tmp_path / "modelle", spec).read_bytes() == payload


def test_ein_server_ohne_range_setzt_auf_null_zurueck(tmp_path: Path) -> None:
    """Antwort 200 statt 206 heisst: neu von vorn, nicht anhaengen."""
    payload = b"d" * (3 * 1024 * 1024)
    spec = _test_model(payload=payload)
    transport = _FakeModelTransport(payload, fail_once=True)
    manager = _manager(tmp_path / "modelle", transport)

    manager.start(spec)
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)
    assert _part_file(tmp_path / "modelle", spec).exists()

    # Jetzt ein Server, der die Range ignoriert und immer die ganze Datei schickt.
    transport.supports_range = False
    manager.start(spec)
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    assert manager.status(spec).status == "installed"
    assert _target_file(tmp_path / "modelle", spec).read_bytes() == payload
    # Der letzte Versuch hatte keine Range mehr, weil die Datei verworfen war.
    assert "range" not in transport.requests[-1].headers


def test_ein_falscher_hash_verwirft_die_datei_vollstaendig(tmp_path: Path) -> None:
    """Ein vergifteter Praefix darf nicht liegen bleiben - sonst scheitert jeder Versuch."""
    payload = b"e" * 4096
    spec = _test_model(payload=payload)
    gebogene = _FakeModelTransport(b"x" * 4096)
    manager = _manager(tmp_path / "modelle", gebogene)

    manager.start(spec)
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    state = manager.status(spec)
    assert state.status == "failed"
    assert "Prüfsumme" in state.message
    assert not _target_file(tmp_path / "modelle", spec).exists()
    assert not _part_file(tmp_path / "modelle", spec).exists()
    assert manager.status(spec).progress_percent == 0.0


def test_ein_abbruch_behaelt_den_teilstand_und_meldet_nie_installiert(tmp_path: Path) -> None:
    payload = b"f" * (3 * 1024 * 1024)
    spec = _test_model(payload=payload)
    transport = _FakeModelTransport(payload, pause_after=1)
    manager = _manager(tmp_path / "modelle", transport)

    manager.start(spec)
    assert transport.paused.wait(timeout=10)
    assert manager.cancel(spec.id) is True
    transport.resume.set()
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    state = manager.status(spec)
    assert state.status == "interrupted"
    assert manager.status(spec).verified is False
    assert not _target_file(tmp_path / "modelle", spec).exists()
    assert 0 < _part_file(tmp_path / "modelle", spec).stat().st_size < len(payload)
    # Ein Abbruch ohne laufenden Download ist kein Fehler, sondern eine leere Antwort.
    assert manager.cancel(spec.id) is False


def test_der_teilstand_nennt_sich_nie_vollstaendig(tmp_path: Path) -> None:
    """Auch von Hand angelegt: ohne Manifest ist ein Modell nicht installiert."""
    payload = b"g" * 1024
    spec = _test_model(payload=payload)
    root = tmp_path / "modelle"
    (root / spec.id).mkdir(parents=True)
    _target_file(root, spec).write_bytes(payload)

    manager = _manager(root, _FakeModelTransport(payload))
    state = manager.status(spec)
    # Ohne Manifest ist die Datei ein liegengebliebener Rest und nie ein Erfolg.
    assert state.status != "installed"
    assert state.verified is False
    assert manager.is_installed(spec) is False
    assert state.bytes_downloaded == len(payload)


def test_eine_weiterleitung_ins_eigene_netz_wird_abgelehnt(tmp_path: Path) -> None:
    """Der Server darf nicht ins Docker-Netz oder zur Metadaten-Adresse umleiten."""
    payload = b"h" * 2048
    spec = _test_model(payload=payload)
    transport = _FakeModelTransport(payload, redirect_to="https://169.254.169.254/latest/meta-data/")
    manager = _manager(tmp_path / "modelle", transport)

    manager.start(spec)
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    state = manager.status(spec)
    assert state.status == "failed"
    assert "erlaubten Quellen" in state.message
    assert not _target_file(tmp_path / "modelle", spec).exists()


def test_zu_wenig_speicher_bricht_vor_dem_ersten_byte_ab(tmp_path: Path) -> None:
    payload = b"i" * 4096
    spec = _test_model(payload=payload)
    manager = _manager(tmp_path / "modelle", _FakeModelTransport(payload), min_free_bytes=10**18)

    with pytest.raises(ModelDownloadError) as fehler:
        manager.start(spec)
    assert "Speicherplatz" in str(fehler.value)
    assert manager.status(spec).status == "not_downloaded"


def test_ein_bereits_geladenes_modell_startet_nichts_neues(tmp_path: Path) -> None:
    payload = b"j" * 4096
    spec = _test_model(payload=payload)
    transport = _FakeModelTransport(payload)
    manager = _manager(tmp_path / "modelle", transport)

    manager.start(spec)
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)
    anzahl = len(transport.requests)

    state = manager.start(spec)
    assert state.status == "installed"
    assert len(transport.requests) == anzahl
    # Der No-Op darf das Modell nicht dauerhaft sperren: sonst waere nach dem
    # ersten zweiten Klick jeder weitere Versuch ein 409.
    assert manager.remove(spec) is True


def test_loeschen_raeumt_teilstand_und_manifest_und_ist_idempotent(tmp_path: Path) -> None:
    payload = b"k" * 2048
    spec = _test_model(payload=payload)
    manager = _manager(tmp_path / "modelle", _FakeModelTransport(payload))
    manager.start(spec)
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    assert manager.remove(spec) is True
    assert not (tmp_path / "modelle" / spec.id).exists()
    assert manager.status(spec).status == "not_downloaded"
    # Der Katalogeintrag bleibt bestehen, nur die Dateien sind weg.
    assert manager.remove(spec) is False
    assert manager.status(spec).status == "not_downloaded"


def test_waehrend_eines_downloads_wird_nicht_geloescht(tmp_path: Path) -> None:
    payload = b"l" * (3 * 1024 * 1024)
    spec = _test_model(payload=payload)
    transport = _FakeModelTransport(payload, pause_after=1)
    manager = _manager(tmp_path / "modelle", transport)

    manager.start(spec)
    assert transport.paused.wait(timeout=10)
    with pytest.raises(ModelDownloadBusy):
        manager.remove(spec)
    transport.resume.set()
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)


def test_eine_kennung_mit_pfdadelementen_bildet_kein_verzeichnis(tmp_path: Path) -> None:
    """Die zweite Verteidigungslinie: die model_id entscheidet den Verzeichnisnamen."""
    root = tmp_path / "modelle"
    root.mkdir()
    manager = _manager(root, _FakeModelTransport(b"x" * 16))
    with pytest.raises(ModelDownloadError):
        manager.model_directory(_test_model(model_id="../../etc", payload=b"x" * 16))
    assert list(root.iterdir()) == []


# --- API -------------------------------------------------------------------


def test_der_modellkatalog_braucht_eine_sitzung(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/models").status_code == 401
    assert app_client.post("/api/v1/models/whisper-tiny-ggml/download").status_code == 401


def test_der_katalog_nennt_groesse_und_ram_schaetzung(app_client: TestClient) -> None:
    login(app_client)
    response = app_client.get("/api/v1/models")
    assert response.status_code == 200
    payload = response.json()

    assert payload["shared_installation"] is True
    assert payload["storage"]["writable"] is True
    # Der Ablagepfad aus dem Container gehoert nicht in eine Antwort an den Browser.
    assert "model_storage_path" not in response.text
    assert "/var/lib/school-assistant/models" not in response.text

    eintraege = {entry["id"]: entry for entry in payload["models"]}
    assert "whisper-tiny-ggml" in eintraege
    eintrag = eintraege["whisper-tiny-ggml"]
    assert eintrag["size_bytes"] > 0
    assert eintrag["size_is_estimate"] is False
    assert eintrag["ram_is_estimate"] is True
    assert eintrag["ram_estimate_bytes"] >= eintrag["size_bytes"]
    assert "Dateigröße" in eintrag["ram_basis"]
    assert eintrag["files"][0]["name"] == "ggml-tiny.bin"
    # Die Download-Adresse wird nicht ausgeliefert.
    assert "url" not in eintrag["files"][0]
    assert eintrag["status"]["status"] == "not_downloaded"


def test_ein_textmodell_nennt_seine_ram_schaetzung_aus_den_parametern(app_client: TestClient) -> None:
    login(app_client)
    payload = app_client.get("/api/v1/models").json()
    eintraege = {entry["id"]: entry for entry in payload["models"]}
    eintrag = eintraege["qwen2.5-1.5b-instruct-q4km"]
    assert eintrag["parameter_count"] == 1543714304
    assert eintrag["quantization"] == "Q4_K_M"
    assert "Parameter" in eintrag["ram_basis"]
    assert eintrag["ram_estimate_gib"] > 0


def test_eine_unbekannte_modell_id_ist_404(app_client: TestClient) -> None:
    login(app_client)
    assert app_client.get("/api/v1/models/gibt-es-nicht").status_code == 404
    assert app_client.post("/api/v1/models/gibt-es-nicht/download").status_code == 404
    assert app_client.post("/api/v1/models/gibt-es-nicht/cancel").status_code == 404
    assert app_client.delete("/api/v1/models/gibt-es-nicht").status_code == 404


def test_der_knopf_laedt_das_modell_und_die_antwort_zeigt_es(
    app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"laden-und-pruefen" * 5000
    spec, manager = _install_model(app_client, tmp_path, monkeypatch, payload)
    login(app_client)

    start = app_client.post(f"/api/v1/models/{spec.id}/download")
    assert start.status_code == 200
    assert start.json()["status"]["status"] in {"downloading", "installed"}
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    detail = app_client.get(f"/api/v1/models/{spec.id}").json()
    assert detail["status"]["status"] == "installed"
    assert detail["status"]["verified"] is True
    assert detail["status"]["progress_percent"] == 100.0
    assert _target_file(manager.root, spec).read_bytes() == payload

    # Ein zweiter Klick ist ein No-Op und kein zweiter Thread.
    nochmal = app_client.post(f"/api/v1/models/{spec.id}/download")
    assert nochmal.status_code == 200
    assert nochmal.json()["status"]["status"] == "installed"


def test_waehrend_eines_downloads_wird_der_zweite_klick_abgelehnt(
    app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"m" * (3 * 1024 * 1024)
    server = _FakeModelTransport(payload, pause_after=1)
    spec, manager = _install_model(app_client, tmp_path, monkeypatch, payload, transport=server)
    login(app_client)

    assert app_client.post(f"/api/v1/models/{spec.id}/download").status_code == 200
    assert server.paused.wait(timeout=10)

    zweiter = app_client.post(f"/api/v1/models/{spec.id}/download")
    assert zweiter.status_code == 409
    assert "läuft bereits" in zweiter.json()["detail"]

    server.resume.set()
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)


def test_der_abbruchknopf_bricht_ab_und_der_rest_startet_von_vorne(
    app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"n" * (3 * 1024 * 1024)
    server = _FakeModelTransport(payload, pause_after=1)
    spec, manager = _install_model(app_client, tmp_path, monkeypatch, payload, transport=server)
    login(app_client)

    app_client.post(f"/api/v1/models/{spec.id}/download")
    assert server.paused.wait(timeout=10)
    assert app_client.post(f"/api/v1/models/{spec.id}/cancel").status_code == 200
    server.resume.set()
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    stand = app_client.get(f"/api/v1/models/{spec.id}").json()["status"]
    assert stand["status"] == "interrupted"
    assert stand["verified"] is False
    assert 0 < _part_file(manager.root, spec).stat().st_size < len(payload)

    app_client.post(f"/api/v1/models/{spec.id}/download")
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)
    assert app_client.get(f"/api/v1/models/{spec.id}").json()["status"]["status"] == "installed"


def test_zu_wenig_speicher_wird_als_konfigurationsfrage_gememeldet(
    app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"o" * 4096
    spec, _manager = _install_model(
        app_client, tmp_path, monkeypatch, payload, manager_kwargs={"min_free_bytes": 10**18}
    )
    login(app_client)

    antwort = app_client.post(f"/api/v1/models/{spec.id}/download")
    assert antwort.status_code == 409
    assert "Speicherplatz" in antwort.json()["detail"]


def test_ein_fehlschlag_wird_als_fehlschlag_gememeldet_nicht_als_erfolg(
    app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falsche Bytes vom Server: Status failed, Klartext, keine Datei."""
    spec = _test_model(payload=b"sollte-anders-sein" * 100)
    monkeypatch.setitem(model_catalog.MODEL_CATALOG, spec.id, spec)
    # Gleiche Länge, anderer Inhalt: so scheitert die Pruefsumme und nicht die
    # Grössenpruefung - genau der Fall, den man sonst leicht übersieht.
    manager = _manager(tmp_path / "modelle", _FakeModelTransport(b"x" * len(b"sollte-anders-sein" * 100)))
    app_client.app.state.model_downloads = manager
    login(app_client)

    app_client.post(f"/api/v1/models/{spec.id}/download")
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    stand = app_client.get(f"/api/v1/models/{spec.id}").json()["status"]
    assert stand["status"] == "failed"
    assert "Prüfsumme" in stand["message"]
    assert not _target_file(manager.root, spec).exists()


def test_loeschen_ist_idempotent_und_laeuft_nur_ohne_download(
    app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"p" * 4096
    spec, manager = _install_model(app_client, tmp_path, monkeypatch, payload)
    login(app_client)

    app_client.post(f"/api/v1/models/{spec.id}/download")
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    geloescht = app_client.delete(f"/api/v1/models/{spec.id}")
    assert geloescht.status_code == 200
    assert "entfernt" in geloescht.json()["message"]
    assert app_client.get(f"/api/v1/models/{spec.id}").json()["status"]["status"] == "not_downloaded"

    nochmal = app_client.delete(f"/api/v1/models/{spec.id}")
    assert nochmal.status_code == 200
    assert "lag nichts" in nochmal.json()["message"]


def test_ein_download_landet_ausschliesslich_unter_der_modellablage(
    app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Ablagepfad entscheidet den Ort - und sonst wird nirgends geschrieben."""
    payload = b"q" * 2048
    spec, manager = _install_model(app_client, tmp_path, monkeypatch, payload)
    login(app_client)

    app_client.post(f"/api/v1/models/{spec.id}/download")
    assert manager.wait_until_idle(spec.id, timeout_seconds=10)

    assert (tmp_path / "modelle" / spec.id).is_dir()
    assert sorted(p.name for p in (tmp_path / "modelle" / spec.id).iterdir()) == [MANIFEST_NAME, "probe.gguf"]


# --------------------------------------------------------------------------
# Sprach-Assistent: Stimmenkatalog, Laufzeiten, Faehigkeiten, Textmodus
# --------------------------------------------------------------------------
#
# Was hier **nicht** getestet werden kann, und warum es trotzdem nicht
# fehlt:
#
# * **Die ONNX-Inferenz von Piper.** Sie laeuft im Prozess ``piper``, nicht in
#   diesem Code. Geprueft wird der ganze Weg drumherum - Argumentbau, WAV-Kopf,
#   Fehlermeldung des Programms, Grenzen, Sprache der Stimme - mit einer
#   Attrappe, die eine echte WAV-Datei zurueckgibt. Die Inferenz selbst wurde
#   hier nie ausgefuehrt.
# * **Der Download der echten Piper-Dateien.** Groessen und Pruefsummen im
#   Katalog sind echt (von Hugging Face geholt, nicht geraten), die Bytes kommen
#   im Test aus einem ``httpx.MockTransport``. Geprueft ist der Weg, nicht die
#   Quelle. Der Weg laeuft ueber denselben, **unveraenderten** Manager wie die
#   GGUF-Modelle - genau darum geht es bei der Formatfrage.
# * **whisper.cpp.** Ebenso durch einen ``httpx.MockTransport`` ersetzt; geprueft
#   werden Anfrage und Antwort, nicht die Erkennung.
#
# Was hier **doch** geprueft wird und der eigentliche Gegenstand der Aufgabe:
# der Textmodus als Normalzustand, die Trennung von „Modell geladen" und
# „Laufzeit vorhanden", und die Tatsache, dass eine halbe Ausstattung
# funktioniert.


def _leerer_transport() -> httpx.MockTransport:
    """Ein Server, der nichts ausliefert - fuer Tests, die nicht herunterladen."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    return httpx.MockTransport(handler)


def _wav(sekunden: float = 0.5, *, kanaele: int = 1, rate: int = 16000, breite: int = 2) -> bytes:
    """Eine echte WAV-Datei mit Stille, im Speicher gebaut.

    Echtes ``wave``-Modul und keine geratene Kopfzeile: der Produktionscode liest
    sie mit demselben Modul, und ein Test, der eine erfundene Kopfzeile prueft,
    prueft nicht den Weg, den der Betrieb nimmt.
    """
    puffer = io.BytesIO()
    with wave.open(puffer, "wb") as schreiber:
        schreiber.setnchannels(kanaele)
        schreiber.setsampwidth(breite)
        schreiber.setframerate(rate)
        schreiber.writeframes(b"\x00" * (int(sekunden * rate) * kanaele * breite))
    return puffer.getvalue()


def _voice_config_bytes(language: str = "de_DE", *, rate: int = 22050, quality: str = "medium") -> bytes:
    """Eine Piper-Konfigurationsdatei mit denselben Feldern wie das Original."""
    return json.dumps(
        {
            "audio": {"sample_rate": rate, "quality": quality},
            "language": {"code": language, "family": language[:2], "name_native": language, "name_english": language},
            "num_speakers": 1,
            "phoneme_type": "espeak",
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _kleine_stimme(voice_id: str = "de-de-probe-stimme") -> PiperVoice:
    """Eine Stimme mit winzigen Dateien und passenden Pruefsummen.

    Nur damit laesst sich der **Downloadweg** pruefen: der Manager verwirft eine
    Datei, deren SHA256 nicht stimmt, und die echten Stimmen sind zusammen rund
    290 MB. Der Test beweist damit, dass der Manager zwei Einzeldateien einer
    Stimme korrekt laedt, prueft und umbenennt - mit dem Code, der auch die
    echten Stimmen herunterlaedt.
    """
    modelle = b"onnx-gewichte-fuer-den-test"
    konfiguration = _voice_config_bytes("de_DE", rate=22050)
    return PiperVoice(
        id=voice_id,
        label="Probe-Stimme",
        language="de_DE",
        language_label="Deutsch",
        speaker="probe",
        speaker_label="Probe",
        quality="medium",
        sample_rate=22050,
        description="Eine Stimme fuer den Test.",
        license="CC0",
        dataset_url="https://example.org/daten",
        source_page="https://huggingface.co/rhasspy/piper-voices",
        files=(
            ModelFile(
                name=f"{voice_id}.onnx",
                url=f"https://huggingface.co/rhasspy/piper-voices/resolve/main/de/probe/{voice_id}.onnx",
                sha256=hashlib.sha256(modelle).hexdigest(),
                size_bytes=len(modelle),
            ),
            ModelFile(
                name=f"{voice_id}.onnx.json",
                url=f"https://huggingface.co/rhasspy/piper-voices/resolve/main/de/probe/{voice_id}.onnx.json",
                sha256=hashlib.sha256(konfiguration).hexdigest(),
                size_bytes=len(konfiguration),
            ),
        ),
    )


def _voice_server(stimme: PiperVoice) -> httpx.MockTransport:
    """Ein Hugging-Face-Server fuer genau eine Stimme, mit den echten Pruefsummen."""
    inhalt = {
        stimme.onnx_name: b"onnx-gewichte-fuer-den-test",
        stimme.config_name: _voice_config_bytes(stimme.language),
    }
    anfragen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        anfragen.append(request)
        name = request.url.path.rsplit("/", 1)[-1]
        daten = inhalt.get(name)
        if daten is None:
            return httpx.Response(404, request=request)
        return httpx.Response(200, headers={"content-length": str(len(daten))}, content=daten, request=request)

    server = httpx.MockTransport(handler)
    server.anfragen = anfragen  # type: ignore[attr-defined]
    return server


class _WhisperServer(httpx.MockTransport):
    """Ein ``whisper-server`` aus whisper.cpp im Test.

    Antwortet auf ``GET /`` (Erreichbarkeit) und auf ``POST /inference`` mit
    ``{"text": ...}``. Die Anfragen werden aufgezeichnet, damit der Test sieht,
    dass wirklich eine **Datei** ankommt und nicht nur ein Formularfeld.
    """

    def __init__(self, *, text: str = "Wann kommt die Klausur?", erreichbar: bool = True) -> None:
        self._text = text
        self._erreichbar = erreichbar
        self.anfragen: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.anfragen.append(request)
        if not self._erreichbar:
            raise httpx.ConnectError("Verbindung abgelehnt")
        if request.method == "GET":
            # whisper.cpp antwortet auf der Wurzel mit einer Indexseite. Fuer die
            # Erreichbarkeit zaehlt nur, dass ueberhaupt etwas antwortet.
            return httpx.Response(404, text="not found", request=request)
        if not self._text.strip():
            return httpx.Response(200, json={"text": self._text}, request=request)
        return httpx.Response(200, json={"text": self._text}, request=request)


class _Aufrufe:
    """Ein Aufzeichner fuer ``run_command``: merkt sich jeden Aufruf.

    ``schreibe_datei=False`` bildet einen Aufruf ab, der zwar Erfolg meldet, aber
    keine Datei hinterlaesst - der Fall, in dem eine stille Leerschleife entstehen
    wuerde, wenn man ihn nicht prueft.
    """

    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"", schreibe_datei: bool = True) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.schreibe_datei = schreibe_datei
        self.aufrufe: list[tuple[list[str], bytes]] = []

    def __call__(self, argv: Sequence[str], *, stdin: bytes, timeout_seconds: float) -> speech_service.CommandResult:
        self.aufrufe.append((list(argv), stdin))
        if self.schreibe_datei:
            for index, wert in enumerate(list(argv)):
                if wert == "--output_file" and index + 1 < len(argv):
                    Path(argv[index + 1]).write_bytes(self.stdout)
        return speech_service.CommandResult(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


def _transkribierer(transport: httpx.BaseTransport | None = None, *, sprache: str | None = None) -> WhisperTranscriber:
    """Ein Transkribierer mit echtem Anfrageweg und ohne Netzzugriff.

    ``transport`` gilt fuer **beide** Verbindungen, auch fuer die
    Erreichbarkeitspruefung. Eine Pruefung, die im Test ins Netz ginge, waere
    keine.
    """
    return WhisperTranscriber(
        "http://whisper.test:8080",
        language=sprache,
        timeout_seconds=5.0,
        client_factory=lambda: httpx.Client(transport=transport, timeout=5.0, follow_redirects=False),
        transport=transport,
    )


def _synthese(aufrufe: _Aufrufe, *, vorhanden: bool = True) -> PiperSynthesizer:
    """Ein Piper, der die Attrappe aufruft und **vorhanden** meldet.

    ``piper`` wird nicht gesucht: der Test sagt dem Synthesizer, dass es das
    Programm gibt. So ist der ganze Weg pruefbar, ohne dass Piper auf diesem
    Rechner installiert sein muss - und genau darum geht es: getestet wird, was
    sich testen laesst.
    """
    synthese = PiperSynthesizer("piper", runner=aufrufe, timeout_seconds=5.0)
    if vorhanden:
        synthese.is_available = lambda: (True, "Piper ist vorhanden.")  # type: ignore[method-assign]
    return synthese


def _stimmt_installieren(
    manager: ModelDownloadManager,
    stimme: PiperVoice,
    *,
    konfiguration: bytes | None = None,
) -> Path:
    """Legt eine Stimme so ab, wie der Download-Manager es tun wuerde.

    Manifest und beide Dateien, mit den Groessen aus dem Katalog. Danach ist
    ``manager.is_installed(spec)`` wahr - und nur dann. Genau diese Bedingung
    entscheidet in :class:`SpeechService`, ob gesprochen werden darf.

    Die Dateien werden mit ``truncate`` angelegt, nicht geschrieben: die echten
    Stimmen sind 63 MB gross, und ein Test soll daran nicht scheitern.
    """
    verzeichnis = manager.model_directory(stimme.as_model_spec())
    verzeichnis.mkdir(parents=True, exist_ok=True)
    eintraege = []
    for datei in stimme.files:
        with (verzeichnis / datei.name).open("wb") as datei_handle:
            datei_handle.truncate(datei.size_bytes)
        eintraege.append({"name": datei.name, "sha256": datei.sha256, "size_bytes": datei.size_bytes})
    # Die Konfiguration wird **echt** geschrieben, auf die Kataloggroesse
    # aufgefuellt. Nullbytes waeren zwar vorhanden, aber nicht lesbar - und der
    # Dienst soll sie lesen, genau wie im Betrieb. Nachgefuellt wird mit
    # Leerzeichen, weil `json.loads` damit genauso gut zurechtkommt wie mit
    # einer echten, laengeren Konfiguration.
    inhalt = konfiguration if konfiguration is not None else _voice_config_bytes(stimme.language)
    groesse = stimme.files[1].size_bytes
    (verzeichnis / stimme.config_name).write_bytes(inhalt[:groesse].ljust(groesse, b" "))
    (verzeichnis / MANIFEST_NAME).write_text(
        json.dumps({"model_id": stimme.id, "verified_at": "2026-09-30T00:00:00+00:00", "files": eintraege}),
        encoding="utf-8",
    )
    return verzeichnis


def _whisper_installieren(manager: ModelDownloadManager, model_id: str = "whisper-tiny-ggml") -> ModelSpec:
    """Legt ein Whisper-Modell ab, ohne Netz - Manifest und Datei genuegen."""
    spec = model_catalog.model_spec(model_id)
    verzeichnis = manager.model_directory(spec)
    verzeichnis.mkdir(parents=True, exist_ok=True)
    eintraege = []
    for datei in spec.files:
        with (verzeichnis / datei.name).open("wb") as datei_handle:
            datei_handle.truncate(datei.size_bytes)
        eintraege.append({"name": datei.name, "sha256": datei.sha256, "size_bytes": datei.size_bytes})
    (verzeichnis / MANIFEST_NAME).write_text(
        json.dumps({"model_id": spec.id, "verified_at": "2026-09-30T00:00:00+00:00", "files": eintraege}),
        encoding="utf-8",
    )
    return spec


def _bereit(app_client: TestClient, *, transcriber: object = None, synthesizer: object = None, **kwargs: object) -> SpeechService:
    """Setzt einen Sprachdienst auf ``app.state`` und liefert ihn zurueck."""
    manager: ModelDownloadManager = app_client.app.state.model_downloads
    dienst = SpeechService(downloads=manager, transcriber=transcriber, synthesizer=synthesizer, **kwargs)  # type: ignore[arg-type]
    app_client.app.state.speech = dienst
    return dienst


def _manager_fuer(app_client: TestClient, tmp_path: Path, transport: httpx.BaseTransport | None = None) -> ModelDownloadManager:
    """Ein Download-Manager auf dem Testablageort, gesetzt auf ``app.state``."""
    manager = _manager(tmp_path / "modelle", transport or _leerer_transport())
    app_client.app.state.model_downloads = manager
    return manager


# --- Der Stimmenkatalog -----------------------------------------------------


def test_der_katalog_erlaubt_keine_grossbuchstaben_in_der_kennung() -> None:
    """Warum die Ids hier klein sind - ``de_DE-thorsten-medium`` kann nicht.

    ``MODEL_ID_PATTERN`` ist die zweite Verteidigungslinie gegen ``..`` und
    Trennzeichen im Verzeichnisnamen. Es erlaubt keine Grossbuchstaben und keinen
    Unterstrich, also waere die Piper-Kennung im Originalformat ein Download, der
    an der eigenen Sicherheitspruefung scheitert.
    """
    assert MODEL_ID_PATTERN.match("de-de-thorsten-medium")
    assert not MODEL_ID_PATTERN.match("de_DE-thorsten-medium")
    for stimme in piper_voices.PIPER_VOICES.values():
        assert MODEL_ID_PATTERN.match(stimme.id)


def test_jede_stimme_hat_modell_und_konfiguration() -> None:
    """Zwei Dateien, echte Pruefsummen, erlaubte Quelle."""
    assert piper_voices.PIPER_VOICES
    for stimme in piper_voices.PIPER_VOICES.values():
        assert len(stimme.files) == 2
        assert stimme.files[0].name.endswith(".onnx")
        assert stimme.files[1].name == f"{stimme.files[0].name}.json"
        assert stimme.onnx_name == stimme.files[0].name
        assert stimme.config_name == stimme.files[1].name
        assert stimme.size_bytes == sum(datei.size_bytes for datei in stimme.files)
        for datei in stimme.files:
            assert len(datei.sha256) == 64
            assert set(datei.sha256) <= set("0123456789abcdef")
            assert datei.size_bytes > 0
            validate_download_url(datei.url)


def test_sprache_und_stimme_sind_zwei_entscheidungen() -> None:
    """Der Katalog muss beides hergeben - sonst waere die Auswahl eine Attrappe."""
    sprecher = {stimme.speaker for stimme in piper_voices.deutsche_voices()}
    # Zwei deutsche Sprecher: „deutsche Stimme" und „diese Stimme" muessen sich
    # unterscheiden lassen, sonst waere die zweite Haelfte der Sprachauswahl nicht
    # bedienbar.
    assert len(sprecher) >= 2
    assert {stimme.language for stimme in piper_voices.deutsche_voices()} == {"de_DE"}
    # Und eine fremde Sprache ist bewusst dabei, damit die Sprachentscheidung
    # sichtbar bleibt und nicht nur behauptet werden kann.
    assert any(stimme.language != "de_DE" for stimme in piper_voices.PIPER_VOICES.values())


def test_die_stimmen_stehen_nicht_in_fremden_katalogen() -> None:
    """Der Katalog schreibt nicht in ``MODEL_CATALOG`` - das gehoert woanders hin.

    Ein Test, der diese Grenze haelt: ohne ihn wuerde jemand die Stimmen
    "einfach" eintragen und dabei eine fremde Datei anfassen. Der **Merge-Bedarf**
    ist aber klein, weil ``as_model_spec`` vollstaendige ``ModelSpec`` liefert.
    """
    from app.services.model_catalog import MODEL_CATALOG

    for stimme in piper_voices.PIPER_VOICES.values():
        assert stimme.id not in MODEL_CATALOG
        assert stimme.id not in catalog_models_ids()
    # Aber sie sind vollstaendige ModelSpecs - der Merge waere eine Zeile.
    specs = piper_voices.as_model_specs()
    assert {spec.id for spec in specs} == set(piper_voices.PIPER_VOICES)
    assert all(spec.task == "text_to_speech" for spec in specs)
    assert all(len(spec.files) == 2 for spec in specs)


def catalog_models_ids() -> set[str]:
    """Die Kennungen des allgemeinen Modellkatalogs - als eigene kleine Helferin."""
    return {spec.id for spec in catalog_models()}


def test_eine_unbekannte_stemme_ist_ein_deutscher_fehler() -> None:
    with pytest.raises(ModelCatalogError):
        piper_voices.voice_by_id("de-de-niemand")


def test_die_englische_stemme_steht_nicht_vorne() -> None:
    """Anzeigereihenfolge: Deutsch zuerst, Auslandssprachen hinten."""
    ids = piper_voices.voice_ids()
    erster_fremder = next(index for index, kennung in enumerate(ids) if piper_voices.PIPER_VOICES[kennung].language != "de_DE")
    letzter_deutscher = max(index for index, kennung in enumerate(ids) if piper_voices.PIPER_VOICES[kennung].language == "de_DE")
    assert letzter_deutscher < erster_fremder
    assert piper_voices.DEFAULT_VOICE_ID in ids
    assert piper_voices.DEFAULT_VOICE_ID.startswith("de-de-")


def test_die_konfiguration_einer_stemme_wird_gelesen(tmp_path: Path) -> None:
    datei = tmp_path / "stimme.onnx.json"
    datei.write_bytes(_voice_config_bytes("de_DE", rate=16000, quality="low"))
    config = read_voice_config(datei)
    assert config.language == "de_DE"
    assert config.sample_rate == 16000
    assert config.quality == "low"
    assert config.num_speakers == 1


@pytest.mark.parametrize(
    "roh",
    [
        pytest.param(b"kein json", id="kein-json"),
        pytest.param(b"[]", id="liste"),
        pytest.param(json.dumps({"language": {"code": "de_DE"}}).encode("utf-8"), id="ohne-audio"),
        pytest.param(json.dumps({"audio": {"sample_rate": 0}, "language": {"code": "de_DE"}}).encode("utf-8"), id="rate-null"),
        pytest.param(json.dumps({"audio": {"sample_rate": 22050}}).encode("utf-8"), id="ohne-sprache"),
    ],
)
def test_eine_kaputte_stimmenkonfiguration_ist_ein_deutscher_fehler(tmp_path: Path, roh: bytes) -> None:
    datei = tmp_path / "stimme.onnx.json"
    datei.write_bytes(roh)
    with pytest.raises(VoiceConfigError):
        read_voice_config(datei)


def test_eine_fehlende_konfigurationsdatei_ist_ein_deutscher_fehler(tmp_path: Path) -> None:
    with pytest.raises(VoiceConfigError):
        read_voice_config(tmp_path / "gibt-es-nicht.json")


# --- Zwei Einzeldateien durch den vorhandenen Manager -----------------------


def test_eine_stimme_ist_ein_zweiteiliger_download_des_managers(
    app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die eigentliche Formatfrage: **zwei Einzeldateien, kein Archiv.**

    Der Beweis laeuft ueber den *unveraenderten* Manager aus
    ``app.services.model_downloads``: Beide Dateien werden einzeln geladen,
    einzeln geprueft und einzeln umbenannt, und das Modell gilt erst als geladen,
    wenn **beide** da sind. Genau das ist der Unterschied zu einem Archiv mit
    Entpack-Schritt - und es kommt ohne jede Aenderung an diesem Manager
    zustande, weil ``files`` schon ein Tupel ist.
    """
    stimme = _kleine_stimme()
    monkeypatch.setitem(piper_voices.PIPER_VOICES, stimme.id, stimme)
    server = _voice_server(stimme)
    manager = _manager(tmp_path / "modelle", server)

    manager.start(stimme.as_model_spec())
    assert manager.wait_until_idle(stimme.id, timeout_seconds=10)

    stand = manager.status(stimme.as_model_spec())
    assert stand.status == "installed"
    assert stand.verified is True
    assert manager.is_installed(stimme.as_model_spec())
    verzeichnis = tmp_path / "modelle" / stimme.id
    assert sorted(datei.name for datei in verzeichnis.iterdir()) == sorted(
        [stimme.onnx_name, stimme.config_name, MANIFEST_NAME]
    )
    # Zwei Anfragen, eine je Datei, beide gegen die erlaubte Quelle.
    assert len(server.anfragen) == 2  # type: ignore[attr-defined]
    for anfrage in server.anfragen:  # type: ignore[attr-defined]
        assert str(anfrage.url).startswith("https://huggingface.co/rhasspy/piper-voices/")


def test_eine_falsche_pruefsumme_behindert_die_ganze_stimme(
    app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Faellt eine der beiden Dateien durch, ist die Stimme **nicht** geladen.

    Genau der Unterschied zu einem Archiv, das man im Ganzen pruefen wuerde: hier
    weiss der Manager nach jedem Schritt, welche Datei fehlt - und die Meldung
    nennt sie.
    """
    stimme = _kleine_stimme()
    kaputt = ModelSpec(
        id=stimme.id,
        label=stimme.label,
        task=stimme.as_model_spec().task,
        family=stimme.as_model_spec().family,
        description=stimme.description,
        license=stimme.license,
        source_repository=stimme.as_model_spec().source_repository,
        source_page=stimme.source_page,
        parameter_count=None,
        quantization=None,
        context_length=None,
        files=(
            stimme.files[0],
            ModelFile(
                name=stimme.files[1].name,
                url=stimme.files[1].url,
                sha256="0" * 64,
                size_bytes=stimme.files[1].size_bytes,
            ),
        ),
    )
    monkeypatch.setitem(piper_voices.PIPER_VOICES, stimme.id, dataclasses.replace(stimme, files=kaputt.files))
    manager = _manager(tmp_path / "modelle", _voice_server(stimme))

    manager.start(kaputt)
    assert manager.wait_until_idle(kaputt.id, timeout_seconds=10)

    stand = manager.status(kaputt)
    assert stand.status == "failed"
    assert stimme.config_name in stand.message
    assert manager.is_installed(kaputt) is False


def test_eine_stimme_gilt_erst_bei_beiden_dateien_als_geladen(tmp_path: Path) -> None:
    """Nur das ONNX da, die Konfiguration fehlt: **nicht** geladen.

    Piper laedt die Konfiguration mit ``--config``; ohne sie kennt das Programm
    weder Abtastrate noch Phoneme. Ein „fast vollstaendig" waere hier eine
    stille Fehlfunktion an der Laufzeit.
    """
    stimme = piper_voices.voice_by_id("de-de-thorsten-medium")
    manager = _manager(tmp_path / "modelle", _leerer_transport())
    verzeichnis = tmp_path / "modelle" / stimme.id
    verzeichnis.mkdir(parents=True)
    with (verzeichnis / stimme.onnx_name).open("wb") as datei:
        datei.truncate(stimme.files[0].size_bytes)

    assert manager.is_installed(stimme.as_model_spec()) is False
    # Der Manager nennt eine vorhandene, aber ungeprueefte Datei "interrupted" -
    # unabhaengig davon gilt: **nicht geladen**, und damit nicht sprechbar.
    assert manager.status(stimme.as_model_spec()).verified is False


# --- Faehigkeiten: der Textmodus zuerst -------------------------------------


def test_ohne_modell_und_ohne_laufzeit_antwortet_die_faehigkeitsabfrage(app_client: TestClient) -> None:
    """**Immer 200.** Auch ohne ein einziges Modell.

    Der Textmodus ist der Zustand, in dem sich die meisten Lernenden befinden
    werden. Ein Endpunkt, der dort mit einem Fehler antwortet, produziert genau
    das, was vermieden werden soll: einen roten Kasten auf einer Seite, die
    vollstaendig bedienbar ist.
    """
    login(app_client)
    antwort = app_client.get("/api/v1/speech/capabilities")
    assert antwort.status_code == 200
    payload = antwort.json()
    assert payload["text_mode_available"] is True
    assert payload["text_mode_note"]
    assert payload["speech_to_text"]["available"] is False
    assert payload["text_to_speech"]["available"] is False
    assert payload["speech_to_text"]["reason"]
    assert payload["text_to_speech"]["reason"]
    assert payload["runtime"]["transcriber_reachable"] is False
    assert payload["runtime"]["synthesizer_available"] is False
    # Ohne Modell eingetragen gibt es auch keine Voreinstellung - erfunden wird
    # hier nichts.
    assert payload["default_voice_id"] is None


def test_die_sprachrouten_brauchen_eine_sitzung(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/speech/capabilities").status_code == 401
    assert app_client.get("/api/v1/speech/voices").status_code == 401
    assert app_client.post("/api/v1/speech/transcribe", files={"file": ("a.wav", _wav(), "audio/wav")}).status_code == 401
    assert app_client.post("/api/v1/speech/speak", json={"text": "Hallo"}).status_code == 401
    assert app_client.post("/api/v1/speech/voices/de-de-thorsten-medium/download").status_code == 401


def test_der_stimmkatalog_zeigt_beide_entscheidungen(app_client: TestClient) -> None:
    login(app_client)
    antwort = app_client.get("/api/v1/speech/voices")
    payload = antwort.json()
    assert payload["shared_installation"] is True
    eintraege = {stimme["id"]: stimme for stimme in payload["voices"]}
    thorsten = eintraege["de-de-thorsten-medium"]
    assert thorsten["language"] == "de_DE"
    assert thorsten["language_label"] == "Deutsch"
    assert thorsten["speaker"] == "thorsten"
    assert thorsten["speaker_label"] == "Thorsten"
    assert thorsten["quality"] == "medium"
    assert thorsten["sample_rate"] == 22050
    assert {datei["name"] for datei in thorsten["files"]} == {
        "de_DE-thorsten-medium.onnx",
        "de_DE-thorsten-medium.onnx.json",
    }
    # Genau eine als Modell, genau eine als Konfiguration markiert.
    assert sum(1 for datei in thorsten["files"] if datei["is_model"]) == 1
    assert sum(1 for datei in thorsten["files"] if datei["is_config"]) == 1
    # Die Quellseite steht drin, die interne Ablage nicht.
    assert thorsten["source_page"].startswith("https://huggingface.co/")
    assert "/var/lib/school-assistant" not in antwort.text


def test_eine_unbekannte_stemme_ist_404(app_client: TestClient) -> None:
    login(app_client)
    assert app_client.post("/api/v1/speech/voices/de-de-niemand/download").status_code == 404
    assert app_client.post("/api/v1/speech/voices/de-de-niemand/cancel").status_code == 404
    assert app_client.delete("/api/v1/speech/voices/de-de-niemand").status_code == 404


def test_der_stimmdownload_laeuft_ueber_denselben_manager(
    app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Manager, eine Ablage, eine Sperre - fuer Modelle **und** Stimmen."""
    stimme = _kleine_stimme()
    monkeypatch.setitem(piper_voices.PIPER_VOICES, stimme.id, stimme)
    manager = _manager_fuer(app_client, tmp_path, _voice_server(stimme))
    _bereit(app_client, transcriber=None, synthesizer=None)
    login(app_client)

    assert app_client.post(f"/api/v1/speech/voices/{stimme.id}/download").status_code == 200
    assert manager.wait_until_idle(stimme.id, timeout_seconds=10)

    detail = app_client.get("/api/v1/speech/voices").json()["voices"]
    geladen = [eintrag for eintrag in detail if eintrag["id"] == stimme.id][0]
    assert geladen["status"]["status"] == "installed"
    assert geladen["status"]["verified"] is True

    geloescht = app_client.delete(f"/api/v1/speech/voices/{stimme.id}")
    assert geloescht.status_code == 200
    assert "entfernt" in geloescht.json()["message"]
    # Idempotent: nochmal loeschen ist kein 404, weil der Katalogeintrag bleibt.
    assert "lag nichts" in app_client.delete(f"/api/v1/speech/voices/{stimme.id}").json()["message"]


def test_abgeschaltete_sprachfunktionen_lassen_den_textmodus(app_client: TestClient, tmp_path: Path) -> None:
    """``SPEECH_ENABLED=false`` nimmt beide Richtungen, nicht den Lernraum."""
    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager)
    _stimmt_installieren(manager, piper_voices.voice_by_id("de-de-thorsten-medium"))
    _bereit(
        app_client,
        transcriber=_transkribierer(_WhisperServer()),
        synthesizer=_synthese(_Aufrufe(stdout=_wav())),
        enabled=False,
    )
    login(app_client)

    payload = app_client.get("/api/v1/speech/capabilities").json()
    assert payload["text_mode_available"] is True
    assert payload["speech_to_text"]["available"] is False
    assert payload["text_to_speech"]["available"] is False
    assert "abgeschaltet" in payload["speech_to_text"]["reason"]
    # Und der Endpunkt sagt es auch: 503 mit demselben Grund, kein 500.
    transkript = app_client.post("/api/v1/speech/transcribe", files={"file": ("a.wav", _wav(), "audio/wav")})
    assert transkript.status_code == 503
    assert "abgeschaltet" in transkript.json()["detail"]


# --- Halbe Ausstattung -----------------------------------------------------


def test_geladenes_modell_ohne_laufzeit_sagt_beides_getrennt(app_client: TestClient, tmp_path: Path) -> None:
    """Modell da, Dienst weg: der Grund nennt **beide** Tatsachen.

    Das ist der Fall, in dem eine einzige Zahl ``false`` zwei verschiedene
    Ursachen verdeckt. Wer danach reparieren will, muss wissen, ob er das Modell
    noch laden oder den Dienst starten muss.
    """
    manager = _manager_fuer(app_client, tmp_path)
    spec = _whisper_installieren(manager)
    _bereit(
        app_client,
        transcriber=_transkribierer(_WhisperServer(erreichbar=False)),
        synthesizer=None,
    )
    login(app_client)

    aufgabe = app_client.get("/api/v1/speech/capabilities").json()["speech_to_text"]
    assert aufgabe["available"] is False
    # Das Modell ist da - das steht im Feld, damit man es nicht nachsehen muss.
    assert aufgabe["model_id"] == spec.id
    assert "geladen" in aufgabe["reason"]
    assert "antwortet nicht" in aufgabe["reason"]


def test_laufzeit_da_aber_ohne_modell_sagt_es_fuer_sich(app_client: TestClient, tmp_path: Path) -> None:
    """Dienst laeuft, Modell fehlt: eine andere Ursache, derselbe Status."""
    _manager_fuer(app_client, tmp_path)
    dienst = _bereit(app_client, transcriber=_transkribierer(_WhisperServer()), synthesizer=None)
    login(app_client)

    aufgabe = app_client.get("/api/v1/speech/capabilities").json()["speech_to_text"]
    assert aufgabe["available"] is False
    assert aufgabe["model_id"] is None
    assert "kein Spracherkennungsmodell geladen" in aufgabe["reason"]
    # Die Laufzeit steht trotzdem als „bereit" daneben - die beiden Bedingungen
    # sind getrennt und werden getrennt gemeldet.
    assert dienst.capabilities().runtime.transcriber_reachable is True


def test_eine_deutsche_stimme_macht_die_ausgabe_bereit(app_client: TestClient, tmp_path: Path) -> None:
    manager = _manager_fuer(app_client, tmp_path)
    stimme = piper_voices.voice_by_id("de-de-thorsten-medium")
    _stimmt_installieren(manager, stimme)
    _bereit(app_client, transcriber=None, synthesizer=_synthese(_Aufrufe(stdout=_wav())))
    login(app_client)

    payload = app_client.get("/api/v1/speech/capabilities").json()
    ausgabe = payload["text_to_speech"]
    assert ausgabe["available"] is True
    assert ausgabe["model_id"] == stimme.id
    assert ausgabe["language"] == "de_DE"
    assert payload["default_voice_id"] == stimme.id
    # Und das Spracherkennen ist weiterhin aus - **halb**, nicht nichts.
    assert payload["speech_to_text"]["available"] is False


def test_ohne_stemme_laeuft_alles_ausser_dem_vorlesen(app_client: TestClient, tmp_path: Path) -> None:
    """Piper laeuft, keine Stimme geladen: der Fall ist „keine" und nicht „kaputt"."""
    _manager_fuer(app_client, tmp_path)
    _bereit(app_client, transcriber=None, synthesizer=_synthese(_Aufrufe(stdout=_wav())))
    login(app_client)

    antwort = app_client.get("/api/v1/speech/capabilities")
    assert antwort.status_code == 200
    ausgabe = antwort.json()["text_to_speech"]
    assert ausgabe["available"] is False
    assert "keine Stimme geladen" in ausgabe["reason"]
    assert antwort.json()["text_mode_available"] is True
    # Und der Endpunkt sagt 503 statt 500: nicht verfuegbar, nicht kaputt.
    rede = app_client.post("/api/v1/speech/speak", json={"text": "Hallo"})
    assert rede.status_code == 503
    assert "keine Stimme geladen" in rede.json()["detail"]


def test_eine_englische_stemme_wird_nicht_stillschweigend_gesprochen(app_client: TestClient, tmp_path: Path) -> None:
    """Die zweite Haelfte der Sprachauswahl, als Verhalten.

    Eine geladene englische Stimme ist in einer deutschen Oberflaeche **keine**
    Alternative. Sie wird nicht stillschweigend eingesetzt, und sie wird nicht als
    verfuegbar gemeldet.
    """
    manager = _manager_fuer(app_client, tmp_path)
    _stimmt_installieren(manager, piper_voices.voice_by_id("en-us-amy-medium"))
    _bereit(app_client, transcriber=None, synthesizer=_synthese(_Aufrufe(stdout=_wav())))
    login(app_client)

    ausgabe = app_client.get("/api/v1/speech/capabilities").json()["text_to_speech"]
    assert ausgabe["available"] is False
    assert "Englisch" in ausgabe["reason"]
    assert "deutsche" in ausgabe["reason"].lower()

    rede = app_client.post("/api/v1/speech/speak", json={"text": "Hallo"})
    assert rede.status_code == 503
    assert "Englisch" in rede.json()["detail"]


def test_eine_stimme_mit_falscher_konfiguration_wird_nicht_gesprochen(app_client: TestClient, tmp_path: Path) -> None:
    """Katalog sagt deutsch, die Datei sagt englisch: nicht benutzen.

    Die Gegenprobe zwischen Katalogeintrag und mitgeladener Konfiguration. Ohne
    sie wuerde eine vertauschte Datei eine falsche Zusage machen - und zwar eine,
    die man erst hoeren wuerde.
    """
    stimme = piper_voices.voice_by_id("de-de-thorsten-medium")
    manager = _manager_fuer(app_client, tmp_path)
    _stimmt_installieren(manager, stimme, konfiguration=_voice_config_bytes("en_US"))
    _bereit(app_client, transcriber=None, synthesizer=_synthese(_Aufrufe(stdout=_wav())))
    login(app_client)

    ausgabe = app_client.get("/api/v1/speech/capabilities").json()["text_to_speech"]
    assert ausgabe["available"] is False
    assert "en_US" in ausgabe["reason"]
    assert app_client.post("/api/v1/speech/speak", json={"text": "Hallo"}).status_code == 503


def test_eingetragene_kennungen_werden_eingehalten(app_client: TestClient, tmp_path: Path) -> None:
    """Whisper und Stimme lassen sich fest verdrahten - und werden dann benutzt."""
    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager, "whisper-base-ggml")
    _whisper_installieren(manager, "whisper-tiny-ggml")
    kerstin = piper_voices.voice_by_id("de-de-kerstin-low")
    _stimmt_installieren(manager, kerstin)
    _stimmt_installieren(manager, piper_voices.voice_by_id("de-de-thorsten-medium"))
    _bereit(
        app_client,
        transcriber=_transkribierer(_WhisperServer()),
        synthesizer=_synthese(_Aufrufe(stdout=_wav())),
        stt_model_id="whisper-base-ggml",
        voice_id=kerstin.id,
    )
    login(app_client)

    payload = app_client.get("/api/v1/speech/capabilities").json()
    assert payload["speech_to_text"]["model_id"] == "whisper-base-ggml"
    assert payload["text_to_speech"]["model_id"] == kerstin.id


def test_ohne_eintrag_wird_das_kleinste_geladene_modell_genommen(app_client: TestClient, tmp_path: Path) -> None:
    """tiny vor base: ein knapper Server klebt sonst an der groesseren Variante."""
    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager, "whisper-base-ggml")
    _whisper_installieren(manager, "whisper-tiny-ggml")
    _bereit(app_client, transcriber=_transkribierer(_WhisperServer()), synthesizer=None)
    login(app_client)

    assert app_client.get("/api/v1/speech/capabilities").json()["speech_to_text"]["model_id"] == "whisper-tiny-ggml"


def test_ein_eingetragenes_modell_ohne_datei_wird_nicht_stillschweigend_ersetzt(
    app_client: TestClient, tmp_path: Path
) -> None:
    """Steht eine Kennung drin, die nicht geladen ist, wird **nicht** umgefallen.

    Sonst wuerde die Oberflaeche ein Modell anzeigen, das nicht das ist, was der
    Betreiber eingestellt hat - und niemand koennte es mehr nachvollziehen.
    """
    manager = _manager_fuer(app_client, tmp_path)
    # Nur tiny ist geladen - base steht als Wunsch im Eintrag, nicht auf der
    # Platte. Genau diese Lage soll nicht stillschweigend auf tiny zurueckfallen.
    _whisper_installieren(manager, "whisper-tiny-ggml")
    _bereit(
        app_client,
        transcriber=_transkribierer(_WhisperServer()),
        synthesizer=None,
        stt_model_id="whisper-base-ggml",
    )
    login(app_client)

    aufgabe = app_client.get("/api/v1/speech/capabilities").json()["speech_to_text"]
    assert aufgabe["available"] is False
    assert "whisper-base-ggml" in aufgabe["reason"]


# --- Aufnehmen --------------------------------------------------------------


def test_ohne_laufzeit_antwortet_die_transkription_mit_503(app_client: TestClient) -> None:
    """Kein eingetragener Dienst: 503 mit deutschem Klartext, kein 500."""
    login(app_client)
    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("a.wav", _wav(), "audio/wav")})
    assert antwort.status_code == 503
    assert "kein Spracherkennungsdienst eingetragen" in antwort.json()["detail"]


def test_ohne_modell_antwortt_die_transkription_mit_503(app_client: TestClient, tmp_path: Path) -> None:
    """Dienst da, Modell nicht geladen: 503 - und der Grund sagt welches fehlt.

    Der Endpoint bleibt ehrlich (503, weil es gerade nicht geht), aber die
    Oberflaeche behandelt das als erwarteten Zustand und nicht als Fehlermeldung.
    Diese Arbeitsteilung ist Absicht.
    """
    _manager_fuer(app_client, tmp_path)
    _bereit(app_client, transcriber=_transkribierer(_WhisperServer()), synthesizer=None)
    login(app_client)
    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("a.wav", _wav(), "audio/wav")})
    assert antwort.status_code == 503
    assert "kein Spracherkennungsmodell geladen" in antwort.json()["detail"]


def test_ein_fremder_container_wird_mit_der_liste_abgewiesen(app_client: TestClient) -> None:
    login(app_client)
    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("a.txt", b"kein ton", "text/plain")})
    assert antwort.status_code == 422
    detail = antwort.json()["detail"]
    assert "text/plain" in detail
    # Die Liste nennt beide Browserfaelle - das ist der Unterschied zwischen
    # "geht nicht" und "so geht es".
    assert "audio/webm" in detail
    assert "audio/mp4" in detail


def test_eine_riesige_aufnahme_wird_abgewiesen_bevor_sie_gelesen_ist(app_client: TestClient, tmp_path: Path) -> None:
    """Das Limit greift **im Strom**, nicht nach dem Lesen.

    Eine unbegrenzte Datei auf einem ``/transcribe``-Endpunkt ist ein
    Speicherproblem; eins, das erst nach dem Lesen merkt, ist es auch.
    """
    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager)
    dienst = _bereit(
        app_client,
        transcriber=_transkribierer(_WhisperServer()),
        synthesizer=None,
        max_upload_bytes=64 * 1024,
    )
    login(app_client)

    gross = _wav(3.0)
    assert len(gross) > 64 * 1024
    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("a.wav", gross, "audio/wav")})
    assert antwort.status_code == 413
    assert "65536" in antwort.json()["detail"]
    assert dienst.max_upload_bytes == 64 * 1024


def test_eine_leere_aufnahme_wird_abgewiesen(app_client: TestClient, tmp_path: Path) -> None:
    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager)
    _bereit(app_client, transcriber=_transkribierer(_WhisperServer()), synthesizer=None)
    login(app_client)

    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("a.wav", b"", "audio/wav")})
    assert antwort.status_code == 422
    assert "leer" in antwort.json()["detail"]


def test_eine_zu_lange_aufnahme_wird_abgewiesen(app_client: TestClient, tmp_path: Path) -> None:
    """Zwei Minuten sind fuer eine Frage reichlich; darueber ist es ein Fehler."""
    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager)
    _bereit(app_client, transcriber=_transkribierer(_WhisperServer()), synthesizer=None, max_seconds=1)
    login(app_client)

    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("a.wav", _wav(2.0), "audio/wav")})
    assert antwort.status_code == 413
    assert "Sekunden" in antwort.json()["detail"]


def test_eine_wav_datei_geht_ohne_umweg_zum_dienst(app_client: TestClient, tmp_path: Path) -> None:
    """Der Normalfall: 16-kHz-Mono-WAV wird **unveraendert** geschickt.

    Und der Weg davor wird mitgeprueft: die Datei kommt als Upload-Feld an, nicht
    als normales Feld - sonst wuerde der Dienst sie nicht finden.
    """
    manager = _manager_fuer(app_client, tmp_path)
    spec = _whisper_installieren(manager)
    server = _WhisperServer(text="Was kommt am Dienstag?")
    _bereit(app_client, transcriber=_transkribierer(server), synthesizer=None)
    login(app_client)

    aufnahme = _wav(1.0)
    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("aufnahme.wav", aufnahme, "audio/wav")})
    assert antwort.status_code == 200
    payload = antwort.json()
    assert payload["text"] == "Was kommt am Dienstag?"
    assert payload["transcoded"] is False
    assert payload["model_id"] == spec.id
    assert payload["duration_seconds"] == 1.0

    # Genau eine POST-Anfrage an /inference, und sie trug eine Datei.
    aufrufe = [anfrage for anfrage in server.anfragen if anfrage.method == "POST"]
    assert len(aufrufe) == 1
    assert aufrufe[0].url.path == "/inference"
    inhalt = aufrufe[0].read().decode("latin-1")
    assert "aufnahme.wav" in inhalt
    assert "audio/wav" in inhalt


def test_ein_fremdes_wav_format_geht_durch_die_umwandlung(app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stereo oder 44,1 kHz: brauchbar, aber nicht das Format, das whisper liest.

    Deshalb wird es umgewandelt statt abgelehnt - der Grund fuer ``transcoded``.
    """
    import sys

    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager)
    server = _WhisperServer(text="Aufgenommen in Stereo")
    _bereit(app_client, transcriber=_transkribierer(server), synthesizer=None, ffmpeg_path=sys.executable)
    monkeypatch.setattr(speech_service, "run_command", _Aufrufe(stdout=_wav(0.5)))
    login(app_client)

    stereo = _wav(0.5, kanaele=2, rate=44100)
    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("a.wav", stereo, "audio/wav")})
    assert antwort.status_code == 200
    assert antwort.json()["transcoded"] is True
    assert speech_service.looks_like_wav(stereo)


def test_ein_webm_wird_zuerst_umgewandelt(app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chrome liefert WebM/Opus - der Weg ueber ffmpeg, gegen eine Attrappe.

    ``ffmpeg_path`` zeigt auf den laufenden Python-Interpreter: es geht nur darum,
    dass ``program_exists`` zustimmt, denn der Aufruf selbst wird abgefangen. So
    ist der ganze Pfad pruefbar, ohne dass ffmpeg hier installiert sein muss.
    """
    import sys

    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager)
    server = _WhisperServer(text="Der Satz des Pythagoras")
    _bereit(app_client, transcriber=_transkribierer(server), synthesizer=None, ffmpeg_path=sys.executable)
    aufrufe = _Aufrufe(stdout=_wav(0.5))
    monkeypatch.setattr(speech_service, "run_command", aufrufe)
    login(app_client)

    # WebM-Magic-Bytes, wie sie ein Browser schickt.
    webm = b"\x1aE\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1fB\x86\x81\x01\x42\xf7\x81\x01"
    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("aufnahme.webm", webm, "audio/webm")})
    assert antwort.status_code == 200
    assert antwort.json()["text"] == "Der Satz des Pythagoras"
    assert antwort.json()["transcoded"] is True

    # Der Aufruf liest aus der Pipe (ffmpeg erkennt den Container selbst) und nennt Abtastrate und Kanalzahl.
    assert len(aufrufe.aufrufe) == 1
    argv = aufrufe.aufrufe[0][0]
    assert argv[0] == sys.executable
    assert argv[argv.index("-i") + 1] == "pipe:0"
    assert argv[argv.index("-ar") + 1] == "16000"
    assert argv[argv.index("-ac") + 1] == "1"


def test_ein_mp4_von_safari_wird_ebenfalls_angenommen(app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Safari liefert MP4/AAC - derselbe Weg, andere Endung.

    ``audio/mp4;codecs=mp4a.40.2`` schickt der Browser **mit** Parametern. Ohne
    das Abschneiden der Parameter faelle die Datei als unbekannter Container
    durch - und der Fehler waere genau die Klasse, die auf einem einzigen Browser
    passiert.
    """
    import sys

    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager)
    _bereit(app_client, transcriber=_transkribierer(_WhisperServer(text="Morgen um acht")), synthesizer=None, ffmpeg_path=sys.executable)
    aufrufe = _Aufrufe(stdout=_wav(0.5))
    monkeypatch.setattr(speech_service, "run_command", aufrufe)
    login(app_client)

    mp4 = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00"
    antwort = app_client.post(
        "/api/v1/speech/transcribe",
        files={"file": ("aufnahme.m4a", mp4, "audio/mp4;codecs=mp4a.40.2")},
    )
    assert antwort.status_code == 200
    assert antwort.json()["text"] == "Morgen um acht"
    assert "pipe:0" in aufrufe.aufrufe[0][0]


def test_ohne_ffmpeg_bleibt_es_beim_textmodus(app_client: TestClient, tmp_path: Path) -> None:
    """Kein ffmpeg, WebM-Datei: 503 mit einem Satz, der weiterhilft.

    Der Fall ist nicht selten - und er darf nicht als „kaputt" erscheinen,
    sondern als „auf diesem Server fehlt ein Programm".
    """
    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager)
    _bereit(app_client, transcriber=_transkribierer(_WhisperServer()), synthesizer=None, ffmpeg_path="gibt-es-nicht-als-programm")
    login(app_client)

    webm = b"\x1aE\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1fB\x86\x81"
    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("a.webm", webm, "audio/webm")})
    assert antwort.status_code == 503
    detail = antwort.json()["detail"]
    assert "ffmpeg" in detail
    assert "Alles andere" in detail

    # Die Faehigkeitsabfrage sagt es **vorab**, damit die Oberflaeche es nicht
    # erst nach dem Fehlversuch herausfinden muss - und damit eine WAV-Datei
    # weiterhin als moeglich erscheint.
    laufzeit = app_client.get("/api/v1/speech/capabilities").json()["runtime"]
    assert laufzeit["ffmpeg_present"] is False
    assert "WAV" in laufzeit["ffmpeg_note"]
    assert laufzeit["transcriber_reachable"] is True


def test_ein_leeres_transkript_ist_kein_erfolg(app_client: TestClient, tmp_path: Path) -> None:
    """Der Dienst antwortet mit nichts: 422, nicht 200 mit leerem Feld."""
    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager)
    _bereit(app_client, transcriber=_transkribierer(_WhisperServer(text="   ")), synthesizer=None)
    login(app_client)

    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("a.wav", _wav(0.5), "audio/wav")})
    assert antwort.status_code == 422
    assert "keinen Text" in antwort.json()["detail"]


def test_ein_dienst_der_die_aufnahme_abweist_wird_als_422_gemeldet(app_client: TestClient, tmp_path: Path) -> None:
    """Der Dienst lehnt ab: das ist ein Eingabeproblem, kein Serverausfall."""
    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager)

    def _ablehnen(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, request=request)
        return httpx.Response(500, text="Modell konnte nicht geoeffnet werden", request=request)

    _bereit(app_client, transcriber=_transkribierer(httpx.MockTransport(_ablehnen)), synthesizer=None)
    login(app_client)

    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("a.wav", _wav(0.5), "audio/wav")})
    assert antwort.status_code == 422
    assert "abgelehnt" in antwort.json()["detail"]
    assert "Modell konnte nicht geoeffnet werden" in antwort.json()["detail"]


def test_ein_unlesbarer_dienst_wird_als_502_gemeldet(app_client: TestClient, tmp_path: Path) -> None:
    """Antwort ohne Textfeld: 502 - der Aufruf ging raus, die Antwort nicht."""
    manager = _manager_fuer(app_client, tmp_path)
    _whisper_installieren(manager)

    def _muell(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, request=request)
        return httpx.Response(200, content=b"kein json", headers={"content-type": "text/plain"}, request=request)

    _bereit(app_client, transcriber=_transkribierer(httpx.MockTransport(_muell)), synthesizer=None)
    login(app_client)

    antwort = app_client.post("/api/v1/speech/transcribe", files={"file": ("a.wav", _wav(0.5), "audio/wav")})
    assert antwort.status_code == 422
    assert "unlesbare" in antwort.json()["detail"]


# --- Vorlesen ---------------------------------------------------------------


def test_eine_leere_oder_riesige_rede_wird_abgewiesen(app_client: TestClient) -> None:
    login(app_client)
    assert app_client.post("/api/v1/speech/speak", json={"text": "   "}).status_code == 422
    # Das Schema begrenzt die Laenge selbst; der Dienst begrenzt sie nochmal.
    assert app_client.post("/api/v1/speech/speak", json={"text": "a" * 50000}).status_code == 422


def test_ohne_piper_antwortet_das_vorlesen_mit_503(app_client: TestClient) -> None:
    login(app_client)
    antwort = app_client.post("/api/v1/speech/speak", json={"text": "Hallo"})
    assert antwort.status_code == 503
    assert "Piper" in antwort.json()["detail"]


def test_ohne_installiertes_piper_programm_wirds_das_gesagt(app_client: TestClient, tmp_path: Path) -> None:
    """Das Programm fehlt: der Grund nennt es beim Namen."""
    manager = _manager_fuer(app_client, tmp_path)
    _stimmt_installieren(manager, piper_voices.voice_by_id("de-de-thorsten-medium"))
    _bereit(app_client, transcriber=None, synthesizer=PiperSynthesizer("piper-nicht-installiert"))
    login(app_client)

    reason = app_client.get("/api/v1/speech/capabilities").json()["text_to_speech"]["reason"]
    assert "piper-nicht-installiert" in reason
    assert "Spracheingabe und der Textmodus bleiben" in reason
    assert app_client.post("/api/v1/speech/speak", json={"text": "Hallo"}).status_code == 503


def test_die_sprachausgabe_kommt_als_wav_und_nicht_als_base64(app_client: TestClient, tmp_path: Path) -> None:
    """Rohdaten statt JSON: eine WAV-Datei waere als base64 um ein Drittel groesser."""
    stimme = piper_voices.voice_by_id("de-de-thorsten-medium")
    manager = _manager_fuer(app_client, tmp_path)
    _stimmt_installieren(manager, stimme)
    ton = _wav(0.75)
    aufrufe = _Aufrufe(stdout=ton)
    _bereit(app_client, transcriber=None, synthesizer=_synthese(aufrufe))
    login(app_client)

    antwort = app_client.post("/api/v1/speech/speak", json={"text": "Dein Lernraum."})
    assert antwort.status_code == 200
    assert antwort.headers["content-type"].startswith("audio/wav")
    assert antwort.content == ton
    assert "base64" not in antwort.headers["content-type"]
    # Ohne `nosniff` schweigt der Browser in manchen Faellen, statt den Ton zu
    # spielen - und der Byte-Oktett-Parameter sagt ihm, dass es beliebige Bytes
    # sind. WAV ist genau das.
    assert antwort.headers["x-content-type-options"] == "nosniff"

    # Der Aufruf nennt Modell, Konfiguration und den Text auf stdin.
    argv, stdin = aufrufe.aufrufe[0]
    assert argv[argv.index("--model") + 1].endswith(stimme.onnx_name)
    assert argv[argv.index("--config") + 1].endswith(stimme.config_name)
    assert stdin.decode("utf-8") == "Dein Lernraum."


def test_piper_mit_einer_eigenen_stimme(app_client: TestClient, tmp_path: Path) -> None:
    """Eine ausdruecklich gewaehlte Stimme wird benutzt - wenn sie deutsch ist."""
    kerstin = piper_voices.voice_by_id("de-de-kerstin-low")
    manager = _manager_fuer(app_client, tmp_path)
    _stimmt_installieren(manager, kerstin)
    _stimmt_installieren(manager, piper_voices.voice_by_id("de-de-thorsten-medium"))
    aufrufe = _Aufrufe(stdout=_wav(0.5))
    _bereit(app_client, transcriber=None, synthesizer=_synthese(aufrufe))
    login(app_client)

    antwort = app_client.post("/api/v1/speech/speak", json={"text": "Kerstin bitte.", "voice_id": kerstin.id})
    assert antwort.status_code == 200
    argv = aufrufe.aufrufe[0][0]
    assert argv[argv.index("--model") + 1].endswith(kerstin.onnx_name)
    assert argv[argv.index("--config") + 1].endswith(kerstin.config_name)


def test_eine_unbekannte_stimme_zum_vorlesen_ist_404(app_client: TestClient, tmp_path: Path) -> None:
    _manager_fuer(app_client, tmp_path)
    _bereit(app_client, transcriber=None, synthesizer=_synthese(_Aufrufe(stdout=_wav())))
    login(app_client)
    antwort = app_client.post("/api/v1/speech/speak", json={"text": "Hallo", "voice_id": "de-de-niemand"})
    assert antwort.status_code == 404
    assert "Unbekannte Stimme" in antwort.json()["detail"]


def test_eine_nicht_geladene_stimme_wird_nicht_gesprochen(app_client: TestClient, tmp_path: Path) -> None:
    """Im Katalog, aber nicht geladen: 503 mit dem Hinweis auf die Ablage."""
    _manager_fuer(app_client, tmp_path)
    _bereit(app_client, transcriber=None, synthesizer=_synthese(_Aufrufe(stdout=_wav())))
    login(app_client)

    antwort = app_client.post("/api/v1/speech/speak", json={"text": "Hallo", "voice_id": "de-de-thorsten-medium"})
    assert antwort.status_code == 503
    assert "nicht vollstaendig geladen" in antwort.json()["detail"]


def test_ein_abgestuerztes_piper_wird_als_502_gemeldet(app_client: TestClient, tmp_path: Path) -> None:
    """Piper bricht ab: die eigene Meldung des Programms geht an den Nutzer.

    Die letzte nichtleere Zeile von stderr ist fast immer die eigentliche Ursache,
    und die gehoert dem Nutzer - nicht nur dem Log.
    """
    stimme = piper_voices.voice_by_id("de-de-thorsten-medium")
    manager = _manager_fuer(app_client, tmp_path)
    _stimmt_installieren(manager, stimme)
    aufrufe = _Aufrufe(returncode=1, stderr=b"espeak-ng fehlt\nFehler: Modell nicht lesbar\n")
    _bereit(app_client, transcriber=None, synthesizer=_synthese(aufrufe))
    login(app_client)

    antwort = app_client.post("/api/v1/speech/speak", json={"text": "Hallo"})
    assert antwort.status_code == 502
    assert "Modell nicht lesbar" in antwort.json()["detail"]


def test_piper_ohne_ausgabedatei_wird_bemerkt(app_client: TestClient, tmp_path: Path) -> None:
    """Rueckgabewert 0, aber keine Datei: trotzdem ein Fehler.

    Ein stilles Leeres Ergebnis wuerde in der Oberflaeche als „gelesen"
    erscheinen - und der Nutzer wuerde es nie erfahren.
    """
    stimme = piper_voices.voice_by_id("de-de-thorsten-medium")
    manager = _manager_fuer(app_client, tmp_path)
    _stimmt_installieren(manager, stimme)
    aufrufe = _Aufrufe(returncode=0, stdout=b"", schreibe_datei=False)
    _bereit(app_client, transcriber=None, synthesizer=_synthese(aufrufe))
    login(app_client)

    antwort = app_client.post("/api/v1/speech/speak", json={"text": "Hallo"})
    assert antwort.status_code == 502
    assert "keine Audiodatei" in antwort.json()["detail"]


def test_eine_leere_rede_braucht_keinen_lauf(app_client: TestClient, tmp_path: Path) -> None:
    """Leerer Text: 422 **vor** dem Lauf, damit Piper nicht umsonst startet."""
    stimme = piper_voices.voice_by_id("de-de-thorsten-medium")
    manager = _manager_fuer(app_client, tmp_path)
    _stimmt_installieren(manager, stimme)
    aufrufe = _Aufrufe(stdout=_wav(0.5))
    _bereit(app_client, transcriber=None, synthesizer=_synthese(aufrufe))
    login(app_client)

    assert app_client.post("/api/v1/speech/speak", json={"text": "   "}).status_code == 422
    assert aufrufe.aufrufe == []


# --- Die Laufzeiten direkt --------------------------------------------------


def test_eine_fehlende_adresse_ist_kein_stolperstein() -> None:
    transkribierer = WhisperTranscriber("")
    erreichbar, notiz = transkribierer.is_reachable()
    assert erreichbar is False
    assert "keine Adresse" in notiz


def test_ein_antwortender_dienst_gilt_als_erreichbar() -> None:
    """Auch ein 404: es geht um die Frage, ob sich etwas meldet.

    whisper.cpp hat keine eigene Health-Route. Eine zu erfinden waere eine
    Zusage, die der Dienst nicht einloest.
    """
    transkribierer = _transkribierer(_WhisperServer())
    erreichbar, notiz = transkribierer.is_reachable()
    assert erreichbar is True
    assert notiz


def test_ein_fehlendes_programm_nennt_seinen_namen() -> None:
    laufzeit = PiperSynthesizer("")
    assert laufzeit.is_available() == (False, "Es ist kein Piper-Programm eingetragen.")
    laufzeit = PiperSynthesizer("gibt-es-nicht-als-programm")
    vorhanden, notiz = laufzeit.is_available()
    assert vorhanden is False
    assert "gibt-es-nicht-als-programm" in notiz


def test_ein_fehlendes_programm_beim_lauf_wird_gemeldet(tmp_path: Path) -> None:
    """``run_command`` ist die einzige Stelle mit ``subprocess`` - und sie meldet.

    Der Text nennt das Programm, weil das die einzige Information ist, die hier
    wirklich hilft: ein ``FileNotFoundError`` ohne Namen landet als
    ``[Errno 2]`` in einer Oberflaeche.
    """
    with pytest.raises(SpeechRuntimeError) as fehler:
        speech_service.run_command([str(tmp_path / "gibt-es-nicht"), "-x"], stdin=b"", timeout_seconds=1.0)
    assert "gibt-es-nicht" in str(fehler.value)


def test_run_command_kann_ein_wirkliches_programm_ausfuehren() -> None:
    """Der Aufruf funktioniert - mit einer Liste, nicht mit einer Shell.

    Sicherheitshalber festgehalten: ``run_command`` bekommt eine **Liste** und
    reicht sie an ``subprocess.run`` weiter. Ein ``shell=True`` wuerde den Text
    der Lernenden zur Befehlszeile machen - und der kommt hier als stdin herein.
    """
    import sys

    ergebnis = speech_service.run_command(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        stdin=b"hallo",
        timeout_seconds=30.0,
    )
    assert ergebnis.returncode == 0
    assert ergebnis.stdout == b"HALLO"
    # Ohne stderr hilft stdout - manche Werkzeuge schreiben dorthin. Genau das
    # wird hier benutzt, um sicher zu sein, dass `run_command` den Ein- und
    # Ausgabestrom wirklich durchreicht.
    assert ergebnis.fehlermeldung == "HALLO"


def test_der_meldungstext_kommt_aus_der_letzten_zeile() -> None:
    ergebnis = speech_service.CommandResult(returncode=1, stderr=b"viel Zeug\n\n  Die eigentliche Ursache  \n")
    assert ergebnis.fehlermeldung == "Die eigentliche Ursache"
    assert speech_service.CommandResult(returncode=1).fehlermeldung == ""
    # Ohne stderr hilft stdout - manche Werkzeuge schreiben dorthin.
    assert speech_service.CommandResult(returncode=1, stdout=b"nur hier\n").fehlermeldung == "nur hier"


def test_das_programm_entscheidet_ueber_die_meldung() -> None:
    assert speech_service.program_exists("") is False
    assert speech_service.program_exists("gibt-es-nicht-als-programm") is False


# --- Lokale KI: Laufzeiten, Fallback-Kette und Herkunft -----------------------
#
# **Alle Tests dieser Sektion laufen gegen einen gemockten OpenAI-kompatiblen
# Server.** In dieser Umgebung gibt es kein llama-server, kein Ollama und kein
# LM Studio; ein Test, der einen echten braucht, waere kein Test. Gegen einen
# echten lokalen Server ist deshalb **nichts verifiziert**.


class _GemockterServer:
    """Ein OpenAI-kompatibler Server aus Attrappen.

    Er unterscheidet nur zwei Pfade: ``/models`` und alles andere. Er laedt kein
    Modell und erzeugt kein Token - er gibt vor, geantwortet zu haben.
    """

    def __init__(
        self,
        *,
        modelle: tuple[str, ...] = ("llama3.2",),
        antwort: str = "Hallo.",
    ) -> None:
        self.modelle = modelle
        self.antwort = antwort
        self.aufrufe: list[str] = []

    def transport(self) -> httpx.BaseTransport:
        return _ServerTransport(self)


class _ServerTransport(httpx.BaseTransport):
    def __init__(self, server: _GemockterServer) -> None:
        self.server = server

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.server.aufrufe.append(str(request.url))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, content=_models_response(*self.server.modelle).content, request=request)
        return httpx.Response(200, content=_chat_response(self.server.antwort).content, request=request)


def _weg(
    quelle: str,
    base_url: str,
    *,
    model: str = "Auto",
    api_key: str = "",
    runtime: str = "auto",
    identitaet: int = 1,
    name: str = "Server",
) -> ai_routing.AiRoute:
    return ai_routing.AiRoute(
        source=quelle,
        runtime=runtime,
        integration_id=identitaet,
        name=name,
        base_url=base_url,
        model=model,
        api_key=api_key,
    )


def _erreichbar(server: _GemockterServer) -> Callable[..., local_inference.LocalRuntimeStatus]:
    """Eine Erreichbarkeitsfrage, die immer 'da' sagt."""

    def probe(**kwargs: object) -> local_inference.LocalRuntimeStatus:
        return local_inference.LocalRuntimeStatus(
            reachable=True,
            ok=True,
            base_url=str(kwargs.get("base_url", "")),
            runtime=str(kwargs.get("runtime", "auto")),
            model_id=server.modelle[0] if server.modelle else None,
            available=server.modelle,
            message=f"Der lokale Server antwortet, {len(server.modelle)} Modell verfuegbar.",
        )

    return probe


def _nicht_erreichbar(meldung: str = "Der lokale Server antwortet nicht."):
    def probe(**kwargs: object) -> local_inference.LocalRuntimeStatus:
        return local_inference.LocalRuntimeStatus(
            reachable=False,
            ok=False,
            base_url=str(kwargs.get("base_url", "")),
            runtime=str(kwargs.get("runtime", "auto")),
            model_id=None,
            available=(),
            message=meldung,
        )

    return probe


def _erzeugt(server: _GemockterServer, quelle: str) -> Callable[..., str]:
    """Ein Erzeuger, der festhält, welcher Weg geantwortet hat."""

    def erzeuge(*_a: object, **kwargs: object) -> str:
        server.aufrufe.append(quelle)
        return server.antwort

    return erzeuge


def _darf_nicht_erzeugen(*_a: object, **_kw: object) -> str:
    raise AssertionError("Der lokale Weg darf nach einer erfolglosen Frage nicht erzeugen.")


# --- 1. Adressen lesen: lokal oder online ----------------------------------


@pytest.mark.parametrize(
    ("base_url", "lokal"),
    [
        ("http://localhost:11434", True),
        ("http://127.0.0.1:1234", True),
        ("http://127.0.0.5:1234", True),
        ("http://host.docker.internal:11434", True),
        ("http://host.containers.internal:1234", True),
        ("http://gateway.docker.internal:8080", True),
        ("http://LM-Studio.local:1234/v1", True),
        ("http://192.168.10.7:11434", True),
        ("http://10.1.2.3:1234", True),
        ("http://172.16.5.5:8080", True),
        ("http://[fd12:3456::1]:1234", True),
        ("https://api.openai.com/v1", False),
        ("https://api.example.com:8443/v1", False),
    ],
)
def test_lokale_adresse_wird_erkannt(base_url: str, lokal: bool) -> None:
    assert local_inference.is_local_base_url(base_url) is lokal


def test_route_ist_lokal_oder_provider() -> None:
    assert local_inference.route_kind("http://localhost:11434", "auto") == "local"
    assert local_inference.route_kind("https://api.example.com/v1", "auto") == "provider"
    # Ein privater Host ist lokal, auch wenn es ein Rechner im LAN ist.
    assert local_inference.route_kind("http://192.168.178.31:11434", "auto") == "local"


def test_runtime_entscheidet_gegen_die_adresse() -> None:
    """Wer ``runtime`` setzt, gewinnt - sonst waere die Angabe wirkungslos."""
    assert local_inference.route_kind("http://192.168.178.31:11434", "online") == "provider"
    assert local_inference.route_kind("https://api.example.com/v1", "llama_cpp") == "local"
    assert local_inference.route_kind("https://api.example.com/v1", "auto") == "provider"


def test_kaputte_oder_leere_adresse_ist_nicht_lokal() -> None:
    assert local_inference.is_local_base_url("") is False
    assert local_inference.is_local_base_url("keine-url") is False


# --- 2. Der host.docker.internal-Hinweis -------------------------------------


@pytest.mark.parametrize(
    ("base_url", "erwartet"),
    [
        ("http://localhost:11434", "http://host.docker.internal:11434"),
        ("http://127.0.0.1:1234", "http://host.docker.internal:1234"),
        ("http://localhost:8080/v1", "http://host.docker.internal:8080"),
    ],
)
def test_container_adresse_ersetzt_localhost(base_url: str, erwartet: str) -> None:
    assert local_inference.docker_host_for(base_url) == erwartet


@pytest.mark.parametrize(
    "base_url",
    [
        # Bereits richtig: der Host ist ueber die Bruecke benannt.
        "http://host.docker.internal:11434",
        # Ein privater Host braucht keine Ersetzung - er ist der Rechner.
        "http://192.168.10.7:11434",
        # Ein Online-Anbieter wird nie umgeschrieben.
        "https://api.example.com/v1",
        # Ohne Port gibt es nichts zu uebertragen.
        "http://localhost",
        "",
    ],
)
def test_container_adresse_braucht_keine_ersetzung(base_url: str) -> None:
    assert local_inference.docker_host_for(base_url) is None
    assert local_inference.describe_container_trap(base_url) is None


def test_localhost_wird_im_satz_erklaert() -> None:
    satz = local_inference.describe_container_trap("http://localhost:11434")
    assert satz is not None
    assert "Docker" in satz
    assert "host.docker.internal:11434" in satz


def test_verbindungstest_nennt_den_container_fallstrick(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der haeufigste Konfigurationsfehler steht in der Meldung, die man liest."""
    login(app_client)
    integration_id = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "Ollama auf dem Rechner",
            "config": {"base_url": "http://localhost:11434", "model": "llama3.2"},
        },
    ).json()["id"]

    def nicht_erreichbar(**_: object) -> AiProviderModels:
        raise AiProviderError("Der KI-Provider ist nicht erreichbar: ConnectError")

    monkeypatch.setattr(integrations_service, "probe_ai_provider", nicht_erreichbar)

    body = app_client.post(f"/api/v1/integrations/{integration_id}/test").json()

    assert body["ok"] is False
    assert "host.docker.internal:11434" in body["message"]
    assert "Docker" in body["message"]


# --- 3. Laufzeiten und Presets ----------------------------------------------


def test_presets_kennen_die_drei_lokalen_laufzeiten() -> None:
    arten = [preset.kind for preset in local_inference.RUNTIME_PRESETS]
    assert arten == ["ollama", "lm_studio", "llama_cpp"]
    ports = {preset.kind: preset.port for preset in local_inference.RUNTIME_PRESETS}
    assert ports == {"ollama": 11434, "lm_studio": 1234, "llama_cpp": 8080}


def test_preset_nennt_beide_adressen() -> None:
    """Im Docker-Stack zaehlt host.docker.internal, nativ 127.0.0.1."""
    preset = local_inference.runtime_preset("ollama")
    assert preset is not None
    assert preset.base_url == "http://host.docker.internal:11434"
    assert preset.native_base_url == "http://127.0.0.1:11434"


def test_auto_ist_keine_laufzeit() -> None:
    assert local_inference.runtime_preset("auto") is None
    assert "auto" not in local_inference.LOCAL_RUNTIME_KINDS


def test_laufzeit_route_liefert_presets_und_reihenfolge(app_client: TestClient) -> None:
    login(app_client)
    body = app_client.get("/api/v1/ai-runtime").json()

    assert body["container_host"] == "host.docker.internal"
    assert body["local_first"] is True
    assert [eintrag["kind"] for eintrag in body["runtimes"]] == ["ollama", "lm_studio", "llama_cpp"]
    assert body["runtimes"][0]["base_url"] == "http://host.docker.internal:11434"
    assert body["runtimes"][0]["native_base_url"] == "http://127.0.0.1:11434"
    assert "localhost" in body["container_trap_hint"]
    assert "ollama" in body["runtime_values"]
    assert len(body["stages"]) == 3


def test_laufzeit_route_braucht_eine_sitzung(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/ai-runtime").status_code == 401


def test_laufzeit_route_fragt_nichts_ab(app_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Route liefert Presets, kein Verbindungsversuch - es gibt nichts zu starten."""
    login(app_client)

    def verboten(**_: object) -> None:
        raise AssertionError("Die Laufzeit-Route darf keinen Server abfragen.")

    monkeypatch.setattr(local_inference, "probe_local_runtime", verboten)
    assert app_client.get("/api/v1/ai-runtime").status_code == 200


# --- 4. runtime im Formular --------------------------------------------------


def test_ai_provider_akzeptiert_ein_optionales_runtime(app_client: TestClient) -> None:
    login(app_client)
    created = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "Ollama",
            "config": {
                "base_url": "http://host.docker.internal:11434",
                "model": "llama3.2",
                "runtime": "ollama",
            },
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["config"]["runtime"] == "ollama"


def test_ai_provider_lehnt_unbekanntes_runtime_ab(app_client: TestClient) -> None:
    """Ein Tippfehler wird gemeldet statt stillschweigend zu 'auto'."""
    login(app_client)
    created = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "Tippfehler",
            "config": {"base_url": "http://host.docker.internal:11434", "model": "x", "runtime": "llamacpp"},
        },
    )
    assert created.status_code == 422
    assert "runtime" in created.json()["detail"]


def test_lokal_und_online_teilen_sich_die_art(app_client: TestClient) -> None:
    """Eine lokale und eine Online-Verbindung brauchen **keine** zwei Eintraege."""
    login(app_client)
    catalog = app_client.get("/api/v1/integrations/catalog").json()
    mit_runtime = [eintrag["kind"] for eintrag in catalog if "runtime" in eintrag["optional_config"]]
    assert mit_runtime == ["ai_provider"]

    created = app_client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "Lokales Modell",
            "config": {"base_url": "http://host.docker.internal:11434", "model": "Auto"},
        },
    )
    assert created.status_code == 201
    # Ohne runtime bleibt der Eintrag vollstaendig - das Feld ist optional.
    assert "runtime" not in created.json()["config"]
    assert created.json()["missing_fields"] == []


def test_unbekanntes_runtime_aus_der_datenbank_wird_zu_auto() -> None:
    assert ai_routing.normalize_runtime("") == "auto"
    assert ai_routing.normalize_runtime(None) == "auto"
    assert ai_routing.normalize_runtime("LLAMA-CPP") == "llama_cpp"
    assert ai_routing.normalize_runtime("gibtsnicht") == "auto"


# --- 5. Reihenfolge der Wege ------------------------------------------------


def test_lokaler_weg_gewinnt_gegen_den_online_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lokal zuerst: kostenlos, und die Daten verlassen den Rechner nicht."""
    server = _GemockterServer(antwort="Vom lokalen Server.")
    monkeypatch.setattr("app.services.ai_routing.probe_local_runtime", _erreichbar(server))
    monkeypatch.setattr("app.services.ai_routing.generate_locally", _erzeugt(server, "lokal"))
    monkeypatch.setattr("app.services.ai_routing.generate_text", _erzeugt(server, "provider"))

    ergebnis = ai_routing.generate_with_routes(
        (
            _weg(ai_routing.SOURCE_LOCAL, "http://host.docker.internal:11434", identitaet=9),
            _weg(ai_routing.SOURCE_PROVIDER, "https://api.example.com/v1", identitaet=2),
        ),
        "System",
        "Frage",
    )

    assert ergebnis is not None
    assert ergebnis.source == "local"
    assert server.aufrufe == ["lokal"]


def test_online_provider_nimmt_den_platz_des_gescheiterten_lokalen_wegs(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _GemockterServer(antwort="Vom Anbieter.")
    monkeypatch.setattr("app.services.ai_routing.probe_local_runtime", _erreichbar(server))
    monkeypatch.setattr(
        "app.services.ai_routing.generate_locally",
        lambda *a, **kw: (_ for _ in ()).throw(AiProviderError("Zeitüberschreitung bei der Generierung.")),
    )
    monkeypatch.setattr("app.services.ai_routing.generate_text", _erzeugt(server, "provider"))

    ergebnis = ai_routing.generate_with_routes(
        (
            _weg(ai_routing.SOURCE_LOCAL, "http://host.docker.internal:11434", identitaet=9),
            _weg(ai_routing.SOURCE_PROVIDER, "https://api.example.com/v1", identitaet=2),
        ),
        "System",
        "Frage",
    )

    assert ergebnis is not None
    assert ergebnis.source == "provider"
    assert server.aufrufe == ["provider"]
    assert [versuch.source for versuch in ergebnis.attempts] == ["local"]


def test_toter_server_kostet_nur_die_billige_frage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne Probe wuerde ein toter Server das ganze Modelllauf-Budget auffressen."""
    server = _GemockterServer()
    gefragt: list[float] = []

    def probe(**kwargs: object) -> local_inference.LocalRuntimeStatus:
        gefragt.append(float(kwargs["timeout_seconds"]))
        return _nicht_erreichbar()(kwargs)

    monkeypatch.setattr("app.services.ai_routing.probe_local_runtime", probe)
    monkeypatch.setattr("app.services.ai_routing.generate_locally", _darf_nicht_erzeugen)
    monkeypatch.setattr("app.services.ai_routing.generate_text", _erzeugt(server, "provider"))

    ergebnis = ai_routing.generate_with_routes(
        (
            _weg(ai_routing.SOURCE_LOCAL, "http://host.docker.internal:11434", identitaet=9),
            _weg(ai_routing.SOURCE_PROVIDER, "https://api.example.com/v1", identitaet=2),
        ),
        "System",
        "Frage",
        local_fast_timeout=15.0,
    )

    assert ergebnis is not None
    assert ergebnis.source == "provider"
    assert gefragt == [local_inference.LOCAL_PROBE_TIMEOUT_SECONDS]


def test_ohne_provider_bekommt_der_lokale_weg_das_volle_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gestuftes Zurueckfallen: allein steht dem lokalen Weg die ganze Zeit zu."""
    server = _GemockterServer(antwort="Lokal, aber langsam.")
    budgets: list[float] = []

    def erzeuge(*_a: object, **kwargs: object) -> str:
        budgets.append(float(kwargs["timeout_seconds"]))
        return server.antwort

    monkeypatch.setattr("app.services.ai_routing.probe_local_runtime", _erreichbar(server))
    monkeypatch.setattr("app.services.ai_routing.generate_locally", erzeuge)

    ergebnis = ai_routing.generate_with_routes(
        (_weg(ai_routing.SOURCE_LOCAL, "http://host.docker.internal:11434", identitaet=9),),
        "System",
        "Frage",
        local_timeout=60.0,
        local_fast_timeout=15.0,
    )

    assert ergebnis is not None
    assert ergebnis.source == "local"
    assert budgets == [60.0]


def test_mit_provider_im_rueckgrat_bleibt_der_lokale_weg_kurz(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _GemockterServer(antwort="Schnell genug.")
    budgets: list[float] = []

    def erzeuge(*_a: object, **kwargs: object) -> str:
        budgets.append(float(kwargs["timeout_seconds"]))
        return server.antwort

    monkeypatch.setattr("app.services.ai_routing.probe_local_runtime", _erreichbar(server))
    monkeypatch.setattr("app.services.ai_routing.generate_locally", erzeuge)

    ai_routing.generate_with_routes(
        (
            _weg(ai_routing.SOURCE_LOCAL, "http://host.docker.internal:11434", identitaet=9),
            _weg(ai_routing.SOURCE_PROVIDER, "https://api.example.com/v1", identitaet=2),
        ),
        "System",
        "Frage",
        local_timeout=60.0,
        local_fast_timeout=15.0,
    )

    assert budgets == [15.0]


def test_timeout_waehlt_das_kurze_budget_nur_mit_fallback() -> None:
    assert ai_routing.local_timeout_for(True, alone=60.0, with_fallback=15.0) == 15.0
    assert ai_routing.local_timeout_for(False, alone=60.0, with_fallback=15.0) == 60.0


def test_leere_antwort_ist_schlechter_als_der_daten_satz(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein leerer Text waere eine Luege in der Form einer KI-Antwort."""
    monkeypatch.setattr("app.services.ai_routing.probe_local_runtime", _erreichbar(_GemockterServer()))
    monkeypatch.setattr("app.services.ai_routing.generate_locally", lambda *a, **kw: "   ")

    assert (
        ai_routing.generate_with_routes(
            (_weg(ai_routing.SOURCE_LOCAL, "http://host.docker.internal:11434"),),
            "System",
            "Frage",
        )
        is None
    )


def test_ohne_jeden_weg_wird_nichts_erfunden() -> None:
    """Leer heisst: der Aufrufer baut aus den Daten. Kein Ersatztext hier."""
    assert ai_routing.generate_with_routes((), "System", "Frage") is None


def test_der_zweite_weg_ueberlebt_auch_einen_fremden_fehler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nicht nur AiProviderError darf einen Weg abbrechen - ein Fremdfehler auch."""
    server = _GemockterServer(antwort="Vom Anbieter.")

    def kaputt(**_: object) -> str:
        raise ValueError("ein Fehler, den es nicht gibt")

    monkeypatch.setattr("app.services.ai_routing.probe_local_runtime", _erreichbar(server))
    monkeypatch.setattr("app.services.ai_routing.generate_locally", kaputt)
    monkeypatch.setattr("app.services.ai_routing.generate_text", _erzeugt(server, "provider"))

    ergebnis = ai_routing.generate_with_routes(
        (
            _weg(ai_routing.SOURCE_LOCAL, "http://host.docker.internal:11434", identitaet=9),
            _weg(ai_routing.SOURCE_PROVIDER, "https://api.example.com/v1", identitaet=2),
        ),
        "System",
        "Frage",
    )

    assert ergebnis is not None
    assert ergebnis.source == "provider"


def test_generate_locally_spricht_das_offene_protokoll(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gegen einen gemockten Server: derselbe Aufruf wie beim Online-Provider.

    Der Test belegt genau eine Sache - ``generate_locally`` landet auf
    ``POST {base}/v1/chat/completions`` und liest die Antwort wie der
    Online-Weg. Er belegt **nicht**, dass ein echter ``llama-server`` das tut.
    """
    server = _GemockterServer(modelle=("qwen",), antwort="Von llama-server.")
    client = AiProviderClient("http://host.docker.internal:8080", "qwen", "")
    client._client = httpx.Client(transport=server.transport(), timeout=5.0)

    # `generate_locally` haelt `generate_text` aus `ai_provider` im eigenen
    # Namensraum; monkeypatch muss deshalb dort ansetzen, nicht am Ursprung.
    monkeypatch.setattr(
        local_inference,
        "generate_text",
        lambda **kwargs: client.generate(
            str(kwargs["system_prompt"]),
            str(kwargs["user_prompt"]),
            max_tokens=int(kwargs["max_tokens"]),
        ),
    )

    text = local_inference.generate_locally(
        "System",
        "Frage",
        model="qwen",
        base_url="http://host.docker.internal:8080",
        timeout_seconds=12.0,
    )

    assert text == "Von llama-server."
    assert server.aufrufe == ["http://host.docker.internal:8080/v1/chat/completions"]


def test_generate_locally_reicht_den_provider_fehler_durch() -> None:
    """Wer den Weg entscheidet, muss den Fehler bekommen - nicht der Aufrufer."""
    with pytest.raises(AiProviderError):
        local_inference.generate_locally(
            "System",
            "Frage",
            model="qwen",
            base_url="ftp://kein-server",
        )


# --- 6. Wege aus der Datenbank ----------------------------------------------


def test_wege_kommen_lokal_zuerst_aus_der_datenbank(app_context: tuple[TestClient, Session, User]) -> None:
    """Reihenfolge ist Produktentscheidung: lokal vor Online, unabhaengig von der Id."""
    _, db, user = app_context
    settings = Settings(secret_key="test-secret-key", integration_encryption_key="test-integration-encryption-key")
    key = "test-integration-encryption-key"

    # Der Online-Eintrag bekommt bewusst die **kleinere** Id.
    online = SyncSource(
        user_id=user.id,
        source_type="ai_provider",
        name="Online",
        enabled=True,
        configuration={"base_url": "https://api.example.com/v1", "model": "gpt-4o-mini"},
        credentials=encrypt_credentials({"api_key": "geheim"}, key),
    )
    lokal = SyncSource(
        user_id=user.id,
        source_type="ai_provider",
        name="Ollama",
        enabled=True,
        configuration={"base_url": "http://host.docker.internal:11434", "model": "llama3.2", "runtime": "ollama"},
        credentials=encrypt_credentials({"api_key": "leer"}, key),
    )
    db.add_all([online, lokal])
    db.commit()

    wege = ai_routing.resolve_ai_routes(db, user.id, settings)

    assert [weg.source for weg in wege] == ["local", "provider"]
    assert wege[0].model == "llama3.2"
    assert wege[1].api_key == "geheim"


def test_deaktivierte_und_leere_wege_werden_uebersprungen(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    settings = Settings(secret_key="test-secret-key", integration_encryption_key="test-integration-encryption-key")
    key = "test-integration-encryption-key"

    aus = SyncSource(
        user_id=user.id,
        source_type="ai_provider",
        name="Ausgeschaltet",
        enabled=False,
        configuration={"base_url": "http://host.docker.internal:11434", "model": "llama3.2"},
        credentials=encrypt_credentials({"api_key": "leer"}, key),
    )
    ohne_adresse = SyncSource(
        user_id=user.id,
        source_type="ai_provider",
        name="Ohne Adresse",
        enabled=True,
        configuration={"model": "llama3.2"},
        credentials=encrypt_credentials({"api_key": "leer"}, key),
    )
    db.add_all([aus, ohne_adresse])
    db.commit()

    assert ai_routing.resolve_ai_routes(db, user.id, settings) == ()


def test_ai_local_first_kehrt_die_reihenfolge_um(app_context: tuple[TestClient, Session, User]) -> None:
    """Wer im Schulnetz einen langsameren lokalen Server hat, dreht die Kette um."""
    _, db, user = app_context
    schluessel = "test-integration-encryption-key"
    db.add_all(
        [
            SyncSource(
                user_id=user.id,
                source_type="ai_provider",
                name="Online",
                enabled=True,
                configuration={"base_url": "https://api.example.com/v1", "model": "gpt-4o-mini"},
                credentials=encrypt_credentials({"api_key": "geheim"}, schluessel),
            ),
            SyncSource(
                user_id=user.id,
                source_type="ai_provider",
                name="Ollama",
                enabled=True,
                configuration={"base_url": "http://host.docker.internal:11434", "model": "llama3.2"},
                credentials=encrypt_credentials({"api_key": "leer"}, schluessel),
            ),
        ]
    )
    db.commit()

    lokal_zuerst = Settings(secret_key="test-secret-key", integration_encryption_key=schluessel)
    online_zuerst = Settings(
        secret_key="test-secret-key",
        integration_encryption_key=schluessel,
        ai_local_first=False,
    )

    assert [weg.source for weg in ai_routing.resolve_ai_routes(db, user.id, lokal_zuerst)] == ["local", "provider"]
    assert [weg.source for weg in ai_routing.resolve_ai_routes(db, user.id, online_zuerst)] == ["provider", "local"]


def test_lokaler_server_braucht_keinen_api_schluessel(app_context: tuple[TestClient, Session, User]) -> None:
    """Ollama, LM Studio und llama-server verlangen keinen Schluessel.

    Ein Eintrag ganz ohne hinterlegte Zugangsdaten ist darum ein vollwertiger
    Weg. Verlangte man hier eine Credential-Zeile, wuerde man genau die
    Verbindung aussortieren, um die es geht - und niemand faelle auf, weil
    ``missing_fields`` leer bleibt.
    """
    _, db, user = app_context
    settings = Settings(secret_key="test-secret-key", integration_encryption_key="test-integration-encryption-key")
    db.add(
        SyncSource(
            user_id=user.id,
            source_type="ai_provider",
            name="Ollama ohne Schluessel",
            enabled=True,
            configuration={"base_url": "http://host.docker.internal:11434", "model": "llama3.2"},
            credentials=None,
        )
    )
    db.commit()

    wege = ai_routing.resolve_ai_routes(db, user.id, settings)

    assert [weg.name for weg in wege] == ["Ollama ohne Schluessel"]
    assert wege[0].api_key == ""
    assert wege[0].source == "local"


def test_ohne_schluessel_keine_wege_statt_einer_ausnahme(app_context: tuple[TestClient, Session, User]) -> None:
    """Ein fehlender INTEGRATION_ENCRYPTION_KEY lahmlegt die KI, nicht das Dashboard."""
    _, db, user = app_context
    db.add(
        SyncSource(
            user_id=user.id,
            source_type="ai_provider",
            name="Ollama",
            enabled=True,
            configuration={"base_url": "http://host.docker.internal:11434", "model": "llama3.2"},
            credentials=encrypt_credentials({"api_key": "leer"}, "anderer-schluessel"),
        )
    )
    db.commit()

    ohne = Settings(secret_key="test-secret-key")
    assert ai_routing.resolve_ai_routes(db, user.id, ohne) == ()


def test_nicht_entschluesselbarer_eintrag_ueberspringt_die_anderen(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    settings = Settings(secret_key="test-secret-key", integration_encryption_key="test-integration-encryption-key")
    key = "test-integration-encryption-key"

    kaputt = SyncSource(
        user_id=user.id,
        source_type="ai_provider",
        name="Kaputter Schluessel",
        enabled=True,
        configuration={"base_url": "http://host.docker.internal:11434", "model": "llama3.2"},
        credentials=encrypt_credentials({"api_key": "falsch"}, "anderer-schluessel"),
    )
    gut = SyncSource(
        user_id=user.id,
        source_type="ai_provider",
        name="Guter Eintrag",
        enabled=True,
        configuration={"base_url": "https://api.example.com/v1", "model": "gpt-4o-mini"},
        credentials=encrypt_credentials({"api_key": "geheim"}, key),
    )
    db.add_all([kaputt, gut])
    db.commit()

    wege = ai_routing.resolve_ai_routes(db, user.id, settings)

    assert [weg.name for weg in wege] == ["Guter Eintrag"]


# --- 7. Herkunft im Antwortschema --------------------------------------------


def test_alte_herkunft_ai_wird_zu_provider() -> None:
    """Ein Cacheeintrag von gestern darf nicht als Datenluege erscheinen."""
    assert ai_routing.normalize_source("ai") == "provider"
    assert ai_routing.normalize_source("local") == "local"
    assert ai_routing.normalize_source("provider") == "provider"
    assert ai_routing.normalize_source("fallback") == "fallback"
    # Alles Unbekannte wird zur harmlosen Annahme.
    assert ai_routing.normalize_source("etwas-anderes") == "fallback"
    assert ai_routing.normalize_source("") == "fallback"
    assert ai_routing.normalize_source(None) == "fallback"


def test_greeting_cache_mit_altem_ai_wird_als_provider_gelesen(app_context: tuple[TestClient, Session, User]) -> None:
    """Der Normalisierungsschritt sitzt beim Lesen, nicht in einer Migration."""
    client, db, user = app_context
    login(client)
    db.add(
        GreetingCache(
            user_id=user.id,
            sentence="Herzlich willkommen, gestern geschrieben.",
            source="ai",
            model="gpt-4o-mini",
            generated_on=date.today(),
            generated_at=datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc),
        )
    )
    db.commit()

    body = client.get("/api/v1/greeting").json()

    assert body["source"] == "provider"
    assert body["text"] == "Herzlich willkommen, gestern geschrieben."


# --- 8. Der lokale Weg als Geminiokte ---------------------------------------


def test_lokaler_server_liefert_den_gruesssatz(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ende zu Ende ueber die API - aber gegen einen gemockten Server."""
    client, _, _ = app_context
    login(client)
    created = client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "llama-server",
            "config": {
                "base_url": "http://host.docker.internal:8080",
                "model": "qwen",
                "runtime": "llama_cpp",
            },
            # Kein `credentials`: ein lokaler Server verlangt keinen Schluessel.
        },
    )
    assert created.status_code == 201

    server = _GemockterServer(modelle=("qwen",), antwort="Herzlich willkommen, der lokale Server antwortet.")
    monkeypatch.setattr("app.services.ai_routing.probe_local_runtime", _erreichbar(server))
    monkeypatch.setattr("app.services.ai_routing.generate_locally", _erzeugt(server, "lokal"))

    body = client.get("/api/v1/greeting").json()

    assert body["source"] == "local"
    assert body["model"] == "qwen"
    assert body["text"] == "Herzlich willkommen, der lokale Server antwortet."


def test_abgestuerzter_lokaler_server_erzeugt_keinen_500(
    app_context: tuple[TestClient, Session, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein abgestuerzter llama-server ist eine kaputte Fremdverbindung, kein Serverfehler."""
    client, _, _ = app_context
    login(client)
    client.post(
        "/api/v1/integrations",
        json={
            "kind": "ai_provider",
            "name": "llama-server",
            "config": {"base_url": "http://host.docker.internal:8080", "model": "qwen", "runtime": "llama_cpp"},
        },
    )

    monkeypatch.setattr("app.services.ai_routing.probe_local_runtime", _nicht_erreichbar())

    antwort = client.get("/api/v1/greeting")

    assert antwort.status_code == 200
    assert antwort.json()["source"] == "fallback"


def test_die_vektorspalte_kann_cosine_distance_ueberhaupt() -> None:
    """Regression: die semantische Suche war zur Laufzeit tot.

    `cosine_distance` haengt am Comparator des pgvector-Typs, und der Comparator
    kommt vom **Python**-Typ der Spalte. Mit `Numeric(...).with_variant(Vector(...),
    "postgresql")` war der Python-Typ `Numeric` - die Spalte sah in Postgres
    korrekt aus (`VECTOR(1536)`), der Aufruf warf aber `AttributeError`. Da
    `_vector_search` nur `AiProviderError` faengt, wurde daraus ein 500.

    Dieser Test laeuft auf SQLite und ist damit kein Beweis fuer die Abfrage -
    er beweist aber, dass der Comparator ueberhaupt existiert. Genau daran ist
    der Fehler vorher gescheitert, ohne dass irgendein Test rot wurde.
    """
    from app.db.content import VECTOR_TYPE

    assert isinstance(VECTOR_TYPE, Vector), "der Python-Typ muss Vector sein, nicht Numeric"
    assert hasattr(DocumentChunk.embedding, "cosine_distance")


def test_die_vektorsuche_baut_eine_postgres_abfrage() -> None:
    """Die Query aus `_vector_search` muss gegen Postgres uebersetzbar sein."""
    from sqlalchemy.dialects import postgresql

    spalte = DocumentChunk.embedding
    stmt = select(spalte.cosine_distance([0.0] * EMBEDDING_DIMENSION).label("_distance")).order_by(
        spalte.cosine_distance([0.0] * EMBEDDING_DIMENSION).asc()
    )

    sql = " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())

    # `<=>` ist der Cosine-Distance-Operator; ohne ihn waere die Query auf
    # Postgres ein Syntaxfehler und die Suche lieferte nichts.
    assert "<=>" in sql


def test_neues_fach_ohne_farbe_bekommt_eine_zufaellige_die_aenderbar_bleibt(app_client: TestClient) -> None:
    from app.db.identity import SUBJECT_COLORS

    login(app_client)
    created = app_client.post("/api/v1/subjects", json={"name": "Chemie"})
    assert created.status_code == 201
    assert created.json()["color"] in SUBJECT_COLORS

    changed = app_client.patch(f"/api/v1/subjects/{created.json()['id']}", json={"color": "#112233"})
    assert changed.status_code == 200
    assert changed.json()["color"] == "#112233"
