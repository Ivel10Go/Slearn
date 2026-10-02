"""Parallelstunden: erkennen, auswaehlen, beim Import beachten."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from app.db.entities import Lesson, Subject, SyncSource, User
from app.services.timetable_import import import_timetable
from app.services.webuntis import WebUntisEntity, WebUntisLessonEntry, WebUntisTimetable
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def login(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "tester", "password": "test-password"})
    assert response.status_code == 200


def _next_monday() -> date:
    today = date.today()
    return today + timedelta(days=7 - today.weekday())


def _lesson(user: User, subject: Subject, day: date, start: time, end: time) -> Lesson:
    return Lesson(
        user_id=user.id,
        subject_id=subject.id,
        start_time=datetime.combine(day, start, tzinfo=UTC),
        end_time=datetime.combine(day, end, tzinfo=UTC),
        source="webuntis",
        external_id=f"{subject.name}-{day.isoformat()}-{start.isoformat()}",
    )


def _parallel_setup(db: Session, user: User) -> tuple[Subject, Subject, Subject]:
    ethik = Subject(user_id=user.id, name="Ethik")
    religion = Subject(user_id=user.id, name="Religion")
    mathe = Subject(user_id=user.id, name="Mathematik")
    db.add_all([ethik, religion, mathe])
    db.flush()
    monday = _next_monday()
    for week in range(2):
        day = monday + timedelta(weeks=week)
        db.add(_lesson(user, ethik, day, time(9, 35), time(10, 20)))
        db.add(_lesson(user, religion, day, time(9, 35), time(10, 20)))
        db.add(_lesson(user, mathe, day, time(8, 0), time(8, 45)))
    db.commit()
    return ethik, religion, mathe


def test_parallele_faecher_werden_einmal_je_zeitfenster_gemeldet(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    _parallel_setup(db, user)

    groups = client.get("/api/v1/timetable/conflicts").json()
    assert len(groups) == 1
    group = groups[0]
    assert (group["weekday"], group["start"], group["end"]) == (0, "09:35", "10:20")
    assert group["decided"] is False
    assert [option["name"] for option in group["options"]] == ["Ethik", "Religion"]
    assert all(option["selected"] for option in group["options"])


def test_auswahl_entfernt_die_anderen_stunden_und_bleibt_aenderbar(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    _, religion, mathe = _parallel_setup(db, user)
    key = client.get("/api/v1/timetable/conflicts").json()[0]["key"]

    saved = client.put("/api/v1/timetable/choices", json={"selections": [{"key": key, "attended": [religion.id]}]})
    assert saved.status_code == 200
    group = saved.json()["groups"][0]
    assert group["decided"] is True
    assert {option["name"]: option["selected"] for option in group["options"]} == {"Ethik": False, "Religion": True}

    db.expire_all()
    subjects = set(db.scalars(select(Lesson.subject_id).where(Lesson.user_id == user.id)).all())
    assert subjects == {religion.id, mathe.id}


def test_auswahl_braucht_mindestens_ein_fach_aus_der_gruppe(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    _, _, mathe = _parallel_setup(db, user)
    key = client.get("/api/v1/timetable/conflicts").json()[0]["key"]

    fremd = client.put("/api/v1/timetable/choices", json={"selections": [{"key": key, "attended": [mathe.id]}]})
    assert fremd.status_code == 422
    unbekannt = client.put(
        "/api/v1/timetable/choices", json={"selections": [{"key": "9|0@00:00-00:01", "attended": [mathe.id]}]}
    )
    assert unbekannt.status_code == 422


def test_import_ueberspringt_abgewaehlte_parallelstunden(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    _, religion, _ = _parallel_setup(db, user)
    key = client.get("/api/v1/timetable/conflicts").json()[0]["key"]
    client.put("/api/v1/timetable/choices", json={"selections": [{"key": key, "attended": [religion.id]}]})

    source = SyncSource(user_id=user.id, source_type="webuntis", name="Portal", enabled=True, configuration={})
    db.add(source)
    db.commit()
    monday = _next_monday()

    def entry(external_id: str, subject_id: int, subject_name: str) -> WebUntisLessonEntry:
        return WebUntisLessonEntry(
            external_id=external_id,
            day=monday,
            start=time(9, 35),
            end=time(10, 20),
            activity_type="Unterricht",
            subjects=(WebUntisEntity(id=subject_id, name=subject_name, longname=subject_name),),
        )

    timetable = WebUntisTimetable(
        entries=(entry("a", 901, "Ethik"), entry("b", 902, "Religion")),
        window_start=monday,
        window_end=monday + timedelta(days=6),
    )
    outcome = import_timetable(db, source, timetable, "UTC")
    assert outcome.imported_lessons == 1

    names = set(
        db.scalars(
            select(Subject.name).join(Lesson, Lesson.subject_id == Subject.id).where(Lesson.sync_source_id == source.id)
        ).all()
    )
    assert names == {"Religion"}
