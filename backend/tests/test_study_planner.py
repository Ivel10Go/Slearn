"""Der automatische Lernplan (``app/services/study_planner.py``, ``POST /study/plans/generate``).

Die Tests rechnen mit einem festen "heute" (Montag, 12.10.2026, Berlin) und
geben dem Planer ``today`` direkt - die Route nimmt das echte Datum und wird
deshalb nur auf Gestalt und Idempotenz geprueft.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.time import as_utc
from app.db.entities import (
    Exam,
    Homework,
    LearningProgress,
    Lesson,
    StudyPlan,
    StudyPlanTask,
    StudyTaskDependency,
    Subject,
    Topic,
    User,
)
from app.services import study_planner
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

BERLIN = ZoneInfo("Europe/Berlin")
MONTAG = date(2026, 10, 12)


def login(client: TestClient) -> None:
    antwort = client.post("/api/v1/auth/login", json={"username": "tester", "password": "test-password"})
    assert antwort.status_code == 200


def berlin(day: date, hour: int, minute: int = 0) -> datetime:
    return as_utc(datetime(day.year, day.month, day.day, hour, minute, tzinfo=BERLIN))


def fach_mit_themen(db: Session, user: User, namen_und_konfidenz: list[tuple[str, float | None]]) -> Subject:
    fach = Subject(user_id=user.id, name="Mathematik")
    db.add(fach)
    db.flush()
    for name, konfidenz in namen_und_konfidenz:
        thema = Topic(user_id=user.id, subject_id=fach.id, name=name)
        db.add(thema)
        db.flush()
        if konfidenz is not None:
            db.add(LearningProgress(user_id=user.id, topic_id=thema.id, confidence=konfidenz))
    db.commit()
    return fach


def test_lernzeit_beginnt_nach_der_letzten_stunde(app_context: tuple[TestClient, Session, User]) -> None:
    _client, db, user = app_context
    db.add(Lesson(user_id=user.id, title="Mathe", start_time=berlin(MONTAG, 13, 0), end_time=berlin(MONTAG, 14, 30)))
    db.add(
        Lesson(
            user_id=user.id,
            title="Ausfall",
            start_time=berlin(MONTAG, 15, 0),
            end_time=berlin(MONTAG, 16, 30),
            is_cancelled=True,
        )
    )
    db.commit()

    slots = study_planner.day_slots(db, user.id, BERLIN, MONTAG, 7, 90)

    # 14:30 + 60 min = 15:30; der ausgefallene Block zaehlt nicht.
    assert slots[0].start.astimezone(BERLIN).strftime("%H:%M") == "15:30"
    # Dienstag ohne Unterricht: Vormittag.
    assert slots[1].start.astimezone(BERLIN).strftime("%H:%M") == "10:00"
    assert all(slot.capacity == 90 for slot in slots)


def test_hausaufgabe_liegt_vor_der_abgabe_und_klausurthemen_in_reihenfolge(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    fach = fach_mit_themen(db, user, [("Integrale", 0.9), ("Ableitungen", None), ("Kurven", 0.5)])
    db.add(Homework(user_id=user.id, title="Aufgabe 3", due_date=berlin(MONTAG + timedelta(days=3), 8)))
    db.add(Exam(user_id=user.id, subject_id=fach.id, title="Mathe-Klausur", date=berlin(MONTAG + timedelta(days=6), 8)))
    db.commit()

    vorschlag = study_planner.propose(db, user.id, BERLIN, 14, 90, today=MONTAG)

    hausaufgabe = next(item for item in vorschlag.items if item.kind == "homework")
    assert hausaufgabe.day is not None and hausaufgabe.day <= MONTAG + timedelta(days=2)
    themen = [item for item in vorschlag.items if item.kind == "exam"]
    assert [item.title for item in themen] == ["Ableitungen lernen", "Kurven lernen", "Integrale lernen"]
    tage = [item.day for item in themen]
    assert all(day is not None and day < MONTAG + timedelta(days=6) for day in tage)
    assert tage == sorted(tage)  # type: ignore[type-var]
    assert vorschlag.overbooked == []


def test_zu_viel_fuer_die_zeit_wird_als_ueberbucht_gemeldet_und_nicht_verworfen(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _client, db, user = app_context
    for nummer in range(5):
        db.add(Homework(user_id=user.id, title=f"Blatt {nummer}", due_date=berlin(MONTAG + timedelta(days=1), 8)))
    db.commit()

    vorschlag = study_planner.propose(db, user.id, BERLIN, 7, 60, today=MONTAG)

    assert len([item for item in vorschlag.items if item.kind == "homework"]) == 5
    assert vorschlag.overbooked == [MONTAG]


def test_faellige_wiederholung_wird_geplant(app_context: tuple[TestClient, Session, User]) -> None:
    _client, db, user = app_context
    fach = Subject(user_id=user.id, name="Englisch")
    db.add(fach)
    db.flush()
    thema = Topic(user_id=user.id, subject_id=fach.id, name="Vokabeln Unit 2")
    db.add(thema)
    db.flush()
    # "geuebt" = 3 Tage Intervall, zuletzt vor 5 Tagen: faellig.
    db.add(
        LearningProgress(
            user_id=user.id, topic_id=thema.id, confidence=0.25, last_reviewed_at=berlin(MONTAG - timedelta(days=5), 18)
        )
    )
    db.commit()

    vorschlag = study_planner.propose(db, user.id, BERLIN, 7, 90, today=MONTAG)

    assert [item.title for item in vorschlag.items] == ["Vokabeln Unit 2 wiederholen"]
    assert vorschlag.items[0].day == MONTAG


def test_route_vorschlag_schreibt_nichts_und_anwenden_ist_idempotent(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    morgen = datetime.now(BERLIN).date() + timedelta(days=4)
    fach = fach_mit_themen(db, user, [("Bruchrechnen", 0.2), ("Prozente", 0.6)])
    db.add(Homework(user_id=user.id, title="Seite 12", due_date=berlin(morgen, 8)))
    db.add(Exam(user_id=user.id, subject_id=fach.id, title="Test", date=berlin(morgen + timedelta(days=2), 8)))
    db.commit()
    login(client)

    vorschlag = client.post("/api/v1/study/plans/generate", json={"days": 14})
    assert vorschlag.status_code == 200, vorschlag.text
    assert vorschlag.json()["applied"] is False
    assert len(vorschlag.json()["items"]) == 3
    assert len(vorschlag.json()["days"]) == 14
    assert db.scalar(select(func.count()).select_from(StudyPlanTask)) == 0

    angewandt = client.post("/api/v1/study/plans/generate", json={"days": 14, "apply": True})
    assert angewandt.status_code == 200
    plan_id = angewandt.json()["study_plan_id"]
    assert db.get(StudyPlan, plan_id) is not None
    db.expire_all()
    aufgaben = db.scalars(select(StudyPlanTask).order_by(StudyPlanTask.scheduled_for)).all()
    assert len(aufgaben) == 3
    assert all(aufgabe.scheduled_for is not None for aufgabe in aufgaben)
    assert sum(1 for aufgabe in aufgaben if aufgabe.homework_id is not None) == 1
    # Die zwei Klausurthemen sind verkettet: das schwaechere zuerst.
    assert db.scalar(select(func.count()).select_from(StudyTaskDependency)) == 1

    nochmal = client.post("/api/v1/study/plans/generate", json={"days": 14, "apply": True})
    assert nochmal.json()["items"] == []
    assert nochmal.json()["skipped"] == 3
    assert db.scalar(select(func.count()).select_from(StudyPlanTask)) == 3


def test_fremder_plan_ist_404(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, _user = app_context
    andere = User(username="andere", password_hash="x", is_active=True, timezone="UTC")
    db.add(andere)
    db.flush()
    fremd = StudyPlan(user_id=andere.id, title="Fremd")
    db.add(fremd)
    db.commit()
    login(client)

    antwort = client.post("/api/v1/study/plans/generate", json={"apply": True, "study_plan_id": fremd.id})

    assert antwort.status_code == 404
