"""Wochenplaner: Stundenplan + offene Aufgaben + Klausuren -> Lernplan.

Die Regeln stehen in ``app/services/planner.py``. Die meisten Tests rufen den
Dienst direkt mit festem ``now`` auf - Montag, 5. Oktober 2026, 07:00 UTC -,
damit kein Test am Wochentag haengt, an dem er laeuft. Die API-Tests am Ende
pruefen Vorschau, Uebernahme und Idempotenz ueber HTTP.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.db.entities import (
    CalendarEvent,
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
from app.schemas.planner import PlannerRequest
from app.services.planner import build_plan
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

UTC_ZONE = ZoneInfo("UTC")
MONDAY = datetime(2026, 10, 5, 7, 0, tzinfo=UTC)


def at(day_offset: int, hour: int, minute: int = 0) -> datetime:
    return (MONDAY + timedelta(days=day_offset)).replace(hour=hour, minute=minute)


def login(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "tester", "password": "test-password"})
    assert response.status_code == 200


def make_plan(db: Session, user: User, title: str = "Lernplan") -> StudyPlan:
    plan = StudyPlan(user_id=user.id, title=title)
    db.add(plan)
    db.commit()
    return plan


def make_task(db: Session, user: User, plan: StudyPlan, title: str, minutes: int = 45, **fields: object) -> StudyPlanTask:
    index = len(db.scalars(select(StudyPlanTask).where(StudyPlanTask.study_plan_id == plan.id)).all())
    task = StudyPlanTask(
        user_id=user.id, study_plan_id=plan.id, title=title, duration_minutes=minutes, order_index=index, **fields
    )
    db.add(task)
    db.commit()
    return task


def make_lesson(db: Session, user: User, start: datetime, end: datetime, cancelled: bool = False) -> None:
    db.add(Lesson(user_id=user.id, title="Mathe", start_time=start, end_time=end, is_cancelled=cancelled))
    db.commit()


def plan_for(db: Session, user: User, **options: object) -> dict:
    request = PlannerRequest.model_validate(options)
    return build_plan(db, user.id, UTC_ZONE, request, apply=False, now=MONDAY).model_dump()


def all_blocks(result: dict) -> list[dict]:
    return [block for day in result["days"] for block in day["blocks"]]


def test_unterricht_und_pause_nach_der_schule_verschieben_den_ersten_block(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _, db, user = app_context
    plan = make_plan(db, user)
    make_task(db, user, plan, "Vokabeln")
    make_lesson(db, user, at(0, 8), at(0, 15, 30))
    # Eine ausgefallene Stunde am Nachmittag blockiert nichts.
    make_lesson(db, user, at(0, 15, 30), at(0, 17), cancelled=True)

    result = plan_for(db, user, days=1)

    blocks = all_blocks(result)
    assert len(blocks) == 1
    # 15:30 Schulschluss + 30 Minuten Pause, nicht 14:00.
    assert blocks[0]["start"] == at(0, 16)
    assert blocks[0]["kind"] == "task"
    assert [busy["kind"] for busy in result["days"][0]["busy"]] == ["lesson"]


def test_termine_und_feste_aufgaben_sind_belegt(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    plan = make_plan(db, user)
    make_task(db, user, plan, "Schon geplant", minutes=60, scheduled_for=at(0, 14))
    make_task(db, user, plan, "Neu")
    db.add(CalendarEvent(user_id=user.id, title="Training", start_time=at(0, 15, 10), end_time=at(0, 16, 30)))
    # Ganztaegig blockiert nichts.
    db.add(CalendarEvent(user_id=user.id, title="Ferien", start_time=at(0, 0), end_time=at(1, 0), all_day=True))
    db.commit()

    result = plan_for(db, user, days=1)

    blocks = all_blocks(result)
    assert [block["title"] for block in blocks] == ["Neu"]
    # 14:00-15:00 fest, dann 10 Minuten Pause: bis zum Training um 15:10 ist
    # kein Platz mehr. Danach geht es um 16:30 weiter.
    assert blocks[0]["start"] == at(0, 16, 30)
    # Die feste Aufgabe zaehlt ins Tagesbudget.
    assert result["days"][0]["planned_minutes"] == 105


def test_frueheste_frist_zuerst_und_hausaufgaben_werden_eingeplant(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _, db, user = app_context
    plan = make_plan(db, user)
    make_task(db, user, plan, "Referat vorbereiten", due_date=at(4, 8), priority=5)
    db.add(Homework(user_id=user.id, title="Arbeitsblatt S. 12", due_date=at(1, 8)))
    # Erledigte Hausaufgaben und solche weit nach dem Zeitraum bleiben draussen.
    db.add(Homework(user_id=user.id, title="Erledigt", due_date=at(1, 8), completed=True))
    db.add(Homework(user_id=user.id, title="Viel spaeter", due_date=at(20, 8)))
    db.commit()

    result = plan_for(db, user, days=7, max_minutes_per_day=45)

    blocks = all_blocks(result)
    assert [block["title"] for block in blocks] == ["Hausaufgabe: Arbeitsblatt S. 12", "Referat vorbereiten"]
    assert blocks[0]["kind"] == "homework"
    assert blocks[0]["start"] == at(0, 14)
    assert blocks[0]["task_id"] is None
    # Budget 45 Minuten: das Referat rutscht trotz Prioritaet 5 auf Dienstag.
    assert blocks[1]["start"] == at(1, 14)


def test_klausurthemen_werden_verteilt_und_das_schwaechste_kommt_zuerst(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _, db, user = app_context
    subject = Subject(user_id=user.id, name="Biologie", color="#000000")
    db.add(subject)
    db.commit()
    for name, confidence in (("Zelle", 0.9), ("Genetik", 0.1), ("Evolution", 0.5)):
        topic = Topic(user_id=user.id, subject_id=subject.id, name=name)
        db.add(topic)
        db.commit()
        db.add(LearningProgress(user_id=user.id, topic_id=topic.id, confidence=confidence))
    db.add(Exam(user_id=user.id, subject_id=subject.id, title="Bio-Klausur", date=at(4, 8)))
    db.commit()

    result = plan_for(db, user, days=7)

    blocks = [block for block in all_blocks(result) if block["kind"] == "exam_topic"]
    assert [block["title"] for block in blocks] == ["Genetik lernen", "Evolution lernen", "Zelle lernen"]
    # Ein Thema pro Tag, nicht alles am Montag.
    assert [block["start"].date() for block in blocks] == [at(0, 0).date(), at(1, 0).date(), at(2, 0).date()]
    assert all(not block["late"] for block in blocks)
    assert blocks[0]["priority"] > blocks[2]["priority"]
    assert {block["subject_name"] for block in blocks} == {"Biologie"}


def test_verteilung_weicht_bevor_eine_klausurfrist_reisst(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    subject = Subject(user_id=user.id, name="Chemie", color="#000000")
    db.add(subject)
    db.commit()
    for name in ("Atome", "Bindungen", "Säuren"):
        db.add(Topic(user_id=user.id, subject_id=subject.id, name=name))
    # Klausur am Dienstagmorgen: nur der Montag liegt davor.
    db.add(Exam(user_id=user.id, subject_id=subject.id, title="Chemie-Test", date=at(1, 8)))
    db.commit()

    blocks = all_blocks(plan_for(db, user, days=3, max_minutes_per_day=300))

    assert len(blocks) == 3
    assert {block["start"].date() for block in blocks} == {at(0, 0).date()}
    assert not any(block["late"] for block in blocks)


def test_eine_klausur_ohne_themen_bekommt_einen_wiederholungsblock(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _, db, user = app_context
    db.add(Exam(user_id=user.id, title="Vokabeltest", date=at(2, 8)))
    # Zu weit weg: das Vorbereitungsfenster beginnt erst nach dem Zeitraum.
    db.add(Exam(user_id=user.id, title="Abschlussklausur", date=at(40, 8)))
    db.commit()

    result = plan_for(db, user, days=7, exam_lead_days=10)

    assert [(block["kind"], block["title"]) for block in all_blocks(result)] == [
        ("exam_review", "Für Vokabeltest wiederholen")
    ]


def test_tagesbudget_und_wochenende(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    plan = make_plan(db, user)
    for index in range(6):
        make_task(db, user, plan, f"Aufgabe {index}", minutes=60)

    result = plan_for(db, user, days=7, max_minutes_per_day=60, include_weekends=False)

    dates = [block["start"].date() for block in all_blocks(result)]
    # Montag bis Freitag je eine; Samstag und Sonntag sind frei.
    assert dates == [at(offset, 0).date() for offset in range(5)]
    assert [item["title"] for item in result["unplaced"]] == ["Aufgabe 5"]
    assert result["unplaced"][0]["reason"] == "Kein freier Platz im Zeitraum"


def test_abhaengigkeiten_bestimmen_die_reihenfolge(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    plan = make_plan(db, user)
    erst = make_task(db, user, plan, "Grundlagen", due_date=at(5, 8))
    dann = make_task(db, user, plan, "Aufbau", due_date=at(1, 8))
    db.add(StudyTaskDependency(user_id=user.id, task_id=dann.id, depends_on_task_id=erst.id))
    db.commit()

    blocks = all_blocks(plan_for(db, user, days=3))

    # Die fruehere Frist gewinnt nicht gegen die Abhaengigkeit.
    assert [block["title"] for block in blocks] == ["Grundlagen", "Aufbau"]
    assert blocks[1]["start"] >= blocks[0]["end"] + timedelta(minutes=10)


def test_ohne_platz_vor_der_frist_wird_der_block_als_spaet_markiert(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _, db, user = app_context
    make_lesson(db, user, at(0, 8), at(0, 15, 30))
    db.add(Homework(user_id=user.id, title="Kurzfristig", due_date=at(0, 16, 10)))
    db.commit()

    blocks = all_blocks(plan_for(db, user, days=2))

    assert len(blocks) == 1
    assert blocks[0]["late"] is True
    assert blocks[0]["start"] == at(0, 16)


def test_vorschau_schreibt_nichts_und_uebernahme_ist_idempotent(
    app_context: tuple[TestClient, Session, User],
) -> None:
    client, db, user = app_context
    login(client)
    plan = make_plan(db, user)
    task = make_task(db, user, plan, "Offen")
    due = (datetime.now(UTC) + timedelta(days=2)).replace(hour=8, minute=0, second=0, microsecond=0)
    homework = Homework(user_id=user.id, title="Aufsatz", due_date=due)
    db.add(homework)
    db.commit()
    body = {"days": 7, "earliest": "00:00", "weekend_earliest": "00:00", "latest": "23:55"}

    preview = client.post("/api/v1/study/planner/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["applied"] is False
    assert preview.json()["planned_count"] == 2
    db.expire_all()
    assert db.get(StudyPlanTask, task.id).scheduled_for is None  # type: ignore[union-attr]
    assert db.scalars(select(StudyPlan).where(StudyPlan.title == "Wochenplan")).first() is None

    applied = client.post("/api/v1/study/planner/apply", json=body)
    assert applied.status_code == 200, applied.text
    payload = applied.json()
    assert payload["applied"] is True
    assert payload["created_count"] == 1
    assert all(block["task_id"] is not None for day in payload["days"] for block in day["blocks"])
    db.expire_all()
    assert db.get(StudyPlanTask, task.id).scheduled_for is not None  # type: ignore[union-attr]
    block = db.scalars(select(StudyPlanTask).where(StudyPlanTask.homework_id == homework.id)).one()
    assert block.title == "Hausaufgabe: Aufsatz"
    assert db.get(StudyPlan, block.study_plan_id).title == "Wochenplan"  # type: ignore[union-attr]

    # Ein zweiter Lauf legt nichts doppelt an: beide sind jetzt fest terminiert.
    again = client.post("/api/v1/study/planner/apply", json=body)
    assert again.json()["created_count"] == 0
    assert again.json()["planned_count"] == 0
    assert len(db.scalars(select(StudyPlanTask).where(StudyPlanTask.homework_id == homework.id)).all()) == 1

    # Den Block erledigen hakt die Hausaufgabe mit ab.
    done = client.post(f"/api/v1/study/tasks/{block.id}/complete")
    assert done.status_code == 200, done.text
    assert done.json()["homework_id"] == homework.id
    db.expire_all()
    assert db.get(Homework, homework.id).completed is True  # type: ignore[union-attr]


def test_uebernahme_verkettet_klausurthemen(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    subject = Subject(user_id=user.id, name="Physik", color="#000000")
    db.add(subject)
    db.commit()
    for name in ("Kräfte", "Energie"):
        db.add(Topic(user_id=user.id, subject_id=subject.id, name=name))
    exam_date = (datetime.now(UTC) + timedelta(days=5)).replace(hour=8, minute=0, second=0, microsecond=0)
    db.add(Exam(user_id=user.id, subject_id=subject.id, title="Physik-Klausur", date=exam_date))
    db.commit()
    body = {"days": 7, "earliest": "00:00", "weekend_earliest": "00:00", "latest": "23:55"}

    applied = client.post("/api/v1/study/planner/apply", json=body)
    assert applied.status_code == 200, applied.text
    assert applied.json()["created_count"] == 2

    tasks = client.get("/api/v1/study/tasks", params={"order": "position"}).json()
    assert [task["title"] for task in tasks] == ["Energie lernen", "Kräfte lernen"]
    assert tasks[1]["blocked_by"][0]["title"] == "Energie lernen"
    assert {task["exam_title"] for task in tasks} == {"Physik-Klausur"}
    # Der Klausur-Generator erkennt die Themen als schon geplant.
    assert client.post("/api/v1/study/planner/apply", json=body).json()["created_count"] == 0


def make_topic(
    db: Session, user: User, name: str, confidence: float, reviewed: datetime | None, subject: Subject | None = None
) -> Topic:
    topic = Topic(user_id=user.id, subject_id=subject.id if subject is not None else None, name=name)
    db.add(topic)
    db.commit()
    db.add(LearningProgress(user_id=user.id, topic_id=topic.id, confidence=confidence, last_reviewed_at=reviewed))
    db.commit()
    return topic


def test_faellige_wiederholung_wird_eingeplant(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    subject = Subject(user_id=user.id, name="Geschichte", color="#000000")
    db.add(subject)
    db.commit()
    # "sicher" = 7 Tage: zuletzt am Mittwoch davor, also faellig am Mittwoch.
    make_topic(db, user, "Weimarer Republik", 0.5, at(-5, 10), subject)
    # "unsicher" = 1 Tag, vor zehn Tagen: laengst verpasst, also gleich am Montag.
    make_topic(db, user, "Vormärz", 0.0, at(-10, 10), subject)

    result = plan_for(db, user, days=7)

    reviews = [block for block in all_blocks(result) if block["kind"] == "review"]
    assert [(block["title"], block["start"].date()) for block in reviews] == [
        ("Vormärz wiederholen", at(0, 0).date()),
        ("Weimarer Republik wiederholen", at(2, 0).date()),
    ]
    assert all(block["priority"] == 0 for block in reviews)
    # Frist ist das Ende des Zeitraums, nicht der Faelligkeitstag.
    assert {block["deadline"].date() for block in reviews} <= {at(6, 0).date(), at(7, 0).date()}
    assert len({block["deadline"] for block in reviews}) == 1
    assert {block["subject_name"] for block in reviews} == {"Geschichte"}
    # Mit abgeschalteter Option keine Wiederholungen.
    assert not [block for block in all_blocks(plan_for(db, user, days=7, include_reviews=False)) if block["kind"] == "review"]


def test_nicht_faellige_wiederholung_wird_nicht_eingeplant(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    # "gefestigt" = 30 Tage: faellig erst weit nach dem Zeitraum.
    make_topic(db, user, "Bruchrechnung", 1.0, at(-1, 10))
    # Ohne Wiederholungsdatum gibt es keinen Termin - und damit keinen Block.
    make_topic(db, user, "Prozente", 0.0, None)

    result = plan_for(db, user, days=7)

    assert all_blocks(result) == []
    assert result["unplaced"] == []


def test_wiederholung_wird_beim_zweiten_uebernehmen_nicht_doppelt_angelegt(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _, db, user = app_context
    topic = make_topic(db, user, "Photosynthese", 0.25, at(-5, 10))
    request = PlannerRequest.model_validate({"days": 7})

    first = build_plan(db, user.id, UTC_ZONE, request, apply=True, now=MONDAY)
    db.commit()
    assert first.created_count == 1
    task = db.scalars(select(StudyPlanTask).where(StudyPlanTask.topic_id == topic.id)).one()
    assert task.exam_id is None and task.homework_id is None
    assert task.title == "Photosynthese wiederholen"

    second = build_plan(db, user.id, UTC_ZONE, request, apply=True, now=MONDAY)
    db.commit()
    assert second.created_count == 0
    assert len(db.scalars(select(StudyPlanTask).where(StudyPlanTask.topic_id == topic.id)).all()) == 1


def test_wiederholung_entfaellt_wenn_das_thema_fuer_eine_klausur_geplant_wird(
    app_context: tuple[TestClient, Session, User],
) -> None:
    _, db, user = app_context
    subject = Subject(user_id=user.id, name="Physik", color="#000000")
    db.add(subject)
    db.commit()
    make_topic(db, user, "Optik", 0.0, at(-3, 10), subject)
    db.add(Exam(user_id=user.id, subject_id=subject.id, title="Physik-Test", date=at(4, 8)))
    db.commit()

    kinds = [block["kind"] for block in all_blocks(plan_for(db, user, days=7))]

    assert kinds == ["exam_topic"]


def test_fremder_lernstand_wird_ignoriert(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    other = User(username="fremd", password_hash="x", is_active=True)
    db.add(other)
    db.commit()
    make_topic(db, other, "Fremdes Thema", 0.0, at(-3, 10))

    assert all_blocks(plan_for(db, user, days=7)) == []


def test_fremder_plan_ist_404_und_ungueltige_zeiten_422(app_context: tuple[TestClient, Session, User]) -> None:
    client, db, user = app_context
    login(client)
    other = User(username="fremd", password_hash="x", is_active=True)
    db.add(other)
    db.commit()
    foreign = make_plan(db, other)

    assert client.post("/api/v1/study/planner/preview", json={"study_plan_id": foreign.id}).status_code == 404
    assert client.post("/api/v1/study/planner/preview", json={"earliest": "20:00", "latest": "19:00"}).status_code == 422
    assert client.post("/api/v1/study/planner/preview", json={"earliest": "25:00"}).status_code == 422
