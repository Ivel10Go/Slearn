"""Punkte und Auszeichnungen.

Eine eigene Datei und kein Abschnitt in ``test_backend.py``. Zwei Gruende: die
Datei dort ist ein Sammelbecken fuer jede Welle, und ein gemeinsamer Block waere
die Stelle, an der sich zwei Wellen am schlimmsten in die Quere kommen. Und
dieser Bereich hat eine eigene, scharf abgegrenzte Zusicherung - die Trennung
zwischen Punkten und Noten -, die man beim Lesen nur findet, wenn sie nicht
zwischen 400 anderen Tests steht.

Die Tests, die zuerst gelesen werden, sind diese vier:

* ``test_ein_doppelter_abschluss_verguest_genau_einmal`` - die Idempotenz, weil
  ``POST /complete`` ausdruecklich idempotent ist und der zweifache Aufruf hier
  kein Sonderfall sein darf.
* ``test_ohne_thema_gibt_es_keine_punkte`` - der Inhaltsbezug, ohne den die
  Belohnung ein Zaehler waere.
* ``test_belohnungen_veraendern_keine_prioritaet_und_keinen_schnitt`` - die Grenze
  zu den Noten, mit Werten *und* mit dem KI-Kontext.
* ``test_das_reward_modul_kennt_weder_noten_noch_ki_noch_prompt`` - dieselbe Grenze
  als Zusage ueber die Importliste, weil ein Refactoring sie sonst unbemerkt aufweichen
  kann.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.security import hash_password
from app.core.time import as_utc
from app.db.entities import (
    LearningProgress,
    RewardBadge,
    RewardEntry,
    StudyPlan,
    StudyPlanTask,
    StudySession,
    Subject,
    Topic,
    User,
)
from app.services import rewards as rewards_service
from app.services.grades import WeightedGrade, grade_context_lines, grade_priority, project_average
from app.services.study import exam_priority
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

BERLIN = ZoneInfo("Europe/Berlin")
UTC_ZONE = ZoneInfo("UTC")


def login(client: TestClient) -> None:
    """Anmelden. Bewusst lokal und nicht aus ``test_backend`` importiert."""
    antwort = client.post("/api/v1/auth/login", json={"username": "tester", "password": "test-password"})
    assert antwort.status_code == 200


# --- Aufbauhelfer -----------------------------------------------------------


def _fach(db: Session, user: User, name: str = "Mathematik") -> Subject:
    fach = Subject(user_id=user.id, name=name, color="#3B82F6")
    db.add(fach)
    db.commit()
    db.refresh(fach)
    return fach


def _thema(db: Session, user: User, name: str, fach: Subject | None) -> Topic:
    thema = Topic(user_id=user.id, subject_id=fach.id if fach else None, name=name)
    db.add(thema)
    db.commit()
    db.refresh(thema)
    return thema


def _plan(db: Session, user: User) -> int:
    if not db.scalar(select(StudyPlan).where(StudyPlan.user_id == user.id)):
        db.add(StudyPlan(user_id=user.id, title="Lernplan"))
        db.commit()
    return int(db.scalar(select(StudyPlan.id).where(StudyPlan.user_id == user.id).order_by(StudyPlan.id)))


def _aufgabe(client: TestClient, db: Session, user: User, thema: Topic | None, *, minuten: int = 30, titel: str = "Lernen") -> dict:
    """Eine Aufgabe anlegen. Mit Thema zahlt sie spaeter Punkte, ohne Thema nie."""
    antwort = client.post(
        "/api/v1/study/tasks",
        json={
            "study_plan_id": _plan(db, user),
            "topic_id": thema.id if thema is not None else None,
            "title": titel,
            "duration_minutes": minuten,
        },
    )
    assert antwort.status_code == 201, antwort.text
    return antwort.json()


def _task(db: Session, task_id: int) -> StudyPlanTask:
    task = db.get(StudyPlanTask, task_id)
    assert task is not None
    return task


def _session_am_tag(
    db: Session,
    user: User,
    task: StudyPlanTask,
    *,
    tag: date,
    minuten: int,
    zone: ZoneInfo = UTC_ZONE,
    abgeschlossen: bool = True,
) -> StudySession:
    """Eine Lernsession an einem festen Kalendertag, ohne HTTP.

    So lassen sich Lerntage, Faecher, Wochen und Zeitzonen bestimmen, ohne vom
    Zufall des Tages abhaengig zu sein. Fuer den geprueften Endpunkt reicht der Weg
    ueber die API, fuer die Regeln-diesen nicht. Die Session beginnt um zehn Uhr
    des Tages - ein Block um Mitternacht wuerde sonst von der Tagesgrenze
    abhaengen und den Test nachts anders laufen lassen als tagsueber.
    """
    start = as_utc(datetime.combine(tag, time(10, 0), tzinfo=zone))
    session = StudySession(
        user_id=user.id,
        task_id=task.id,
        started_at=start,
        completed_at=start + timedelta(minutes=minuten) if abgeschlossen else None,
        duration_minutes=minuten if abgeschlossen else None,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _heute() -> date:
    return as_utc(datetime.now(UTC)).date()


def _montag_dieser_woche() -> date:
    heute = _heute()
    return heute - timedelta(days=heute.weekday())


def _punkte(client: TestClient) -> int:
    antwort = client.get("/api/v1/rewards")
    assert antwort.status_code == 200, antwort.text
    return int(antwort.json()["summary"]["total_points"])


def _buchungen(db: Session, user_id: int) -> list[RewardEntry]:
    return list(db.scalars(select(RewardEntry).where(RewardEntry.user_id == user_id).order_by(RewardEntry.id)).all())


def _badge(code: str, antwort: dict) -> dict:
    return next(badge for badge in antwort["badges"] if badge["code"] == code)


def _vormerken(
    db: Session,
    user: User,
    *,
    session: StudySession | None = None,
    task: StudyPlanTask | None = None,
    zone: ZoneInfo = UTC_ZONE,
    now: datetime | None = None,
) -> rewards_service.RewardOutcome:
    """Den Buchungslauf direkt aufrufen, wie die Endpunkte es tun."""
    ergebnis = rewards_service.record_progress(
        db,
        user.id,
        zone=zone,
        task=task,
        sessions=tuple([session] if session is not None else ()),
        now=now,
    )
    db.commit()
    return ergebnis


# --- Die Form und der Zugang ------------------------------------------------


def test_reward_routen_sind_registriert(app_client: TestClient) -> None:
    assert "/api/v1/rewards" in {route.path for route in app_client.app.routes}


def test_die_belohnungsantwort_nennt_die_felder_der_oberflaeche(app_context: tuple[TestClient, Session, User]) -> None:
    """Die Antwortform ist ein Vertrag - und wird als solcher festgehalten.

    Auf der anderen Seite steht in ``lib/rewards.test.ts`` ein Test, der genau diese
    Felder einliest. Benennt eines der beiden etwas um, faellt eines der beiden auf -
    und nicht erst ein Typfehler zur Laufzeit oder eine stille `null`, weil das
    Zod-Schema ein fehlendes Feld verschluckt.

    Geprueft werden die **Schluessel**, nicht die Werte: der Wert wird an drei Stellen
    geprueft, der Name an zweien.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Analysis", mathe)
    aufgabe = _aufgabe(client, db, user, thema, minuten=30)
    assert client.post(f"/api/v1/study/tasks/{aufgabe['id']}/complete").status_code == 200

    antwort = client.get("/api/v1/rewards").json()
    assert set(antwort) == {"summary", "rules", "catalog", "badges", "entries"}

    assert set(antwort["summary"]) == {
        "generated_at",
        "timezone",
        "today_points",
        "today_cap",
        "week_points",
        "total_points",
        "today",
        "week_starts_on",
        "week_ends_on",
        "awarded_badges",
        "pending_badges",
    }
    assert set(antwort["rules"]) == {
        "task_points",
        "day_cap",
        "subject_day_cap",
        "min_subject_day_minutes",
        "subject_day_steps",
    }
    assert set(antwort["rules"]["subject_day_steps"][0]) == {"minutes", "points"}
    assert set(antwort["catalog"][0]) == {
        "code",
        "title",
        "description",
        "target",
        "unit",
        "unit_plural",
        "scope_kind",
        "repeatable",
    }
    assert set(antwort["entries"][0]) == {
        "id",
        "kind",
        "points",
        "minutes",
        "subject_id",
        "subject_name",
        "topic_id",
        "topic_name",
        "detail",
        "earned_on",
        "earned_at",
    }
    for badge in antwort["badges"]:
        assert set(badge) == {
            "id",
            "code",
            "title",
            "description",
            "scope",
            "scope_kind",
            "subject_id",
            "subject_name",
            "topic_id",
            "topic_name",
            "progress",
            "target",
            "awarded",
            "awarded_at",
            "repeatable",
            "unit",
            "unit_plural",
        }
    # Und die geschlossenen Listen, damit kein Tippfehler unbemerkt durchrutscht.
    assert antwort["entries"][0]["kind"] in {"task_completed", "subject_day"}
    assert {badge["scope_kind"] for badge in antwort["badges"]} <= {"global", "subject", "topic", "week"}


def test_die_belohnungskarte_braucht_eine_anmeldung(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/rewards").status_code == 401


def test_ohne_punkte_kommt_eine_leere_und_vollstaendige_antwort(app_client: TestClient) -> None:
    """Ein frisches Konto bekommt Nullen, Regeln und Katalog - keine Fehlermeldung.

    Die Karte muss etwas anzeigen koennen, auch bevor gelernt wurde. Eine leere
    Antwort, die die Regel mitbringt, ist die Grundlage dafuer: sonst muesste die
    Oberflaeche die Punkte selbst erklaeren und waere die zweite Wahrheit.
    """
    login(app_client)
    antwort = app_client.get("/api/v1/rewards").json()
    assert antwort["summary"]["total_points"] == 0
    assert antwort["summary"]["today_points"] == 0
    assert antwort["summary"]["today_cap"] == rewards_service.MAX_DAY_POINTS
    assert antwort["entries"] == []
    assert antwort["badges"] == []
    assert antwort["rules"]["task_points"] == rewards_service.TASK_POINTS
    assert antwort["rules"]["subject_day_steps"][0] == {"minutes": 10, "points": 5}
    assert len(antwort["catalog"]) == len(rewards_service.BADGE_DEFINITIONS)


# --- Idempotenz: der Test, den ein Reviewer zuerst liest -------------------


def test_ein_doppelter_abschluss_verguest_genau_einmal(app_context: tuple[TestClient, Session, User]) -> None:
    """``POST /complete`` ist idempotent, also darf es auch nur einmal punkten.

    Der Aufruf ist ausdruecklich wiederholbar - der erste schliesst eine offene
    Session ab, ein spaeterer koennte eine zweite schliessen. Beide Wege enden in
    ``record_progress``. Die Eindeutigkeit sitzt dabei in der Datenbank
    (``uq_reward_entries_user_kind_event`` auf ``task:{id}``), nicht in einer Abfrage
    "habe ich schon gepunktet": eine solche Abfrage ist genau die Stelle, an der ein
    zweiter Klick durchrutscht, sobald zwei Anfragen gleichzeitig kommen.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Integralrechnung", mathe)
    aufgabe = _aufgabe(client, db, user, thema, minuten=30)

    erster = client.post(f"/api/v1/study/tasks/{aufgabe['id']}/complete")
    assert erster.status_code == 200, erster.text
    stand_nach_dem_ersten = _punkte(client)
    assert stand_nach_dem_ersten > 0

    for _ in range(2):
        assert client.post(f"/api/v1/study/tasks/{aufgabe['id']}/complete").status_code == 200

    assert _punkte(client) == stand_nach_dem_ersten
    db.expire_all()
    zeilen = _buchungen(db, user.id)
    # Genau eine Zeile fuer die Aufgabe und genau eine fuer den Fachtag - auch
    # nach drei Aufrufen.
    assert [zeile.kind for zeile in zeilen].count("task_completed") == 1
    assert [zeile.kind for zeile in zeilen].count("subject_day") == 1
    assert sum(zeile.points for zeile in zeilen) == stand_nach_dem_ersten

    # Und derselbe Aufruf noch einmal **am Service**, damit die Zusage nicht an
    # der Verzweigung des Endpunkts haengt. Ein Endpunkt, der beim zweiten Mal
    # frueh zurueckkehrt, waere idempotent - die Datenbank trotzdem nicht, und ein
    # anderer Aufrufer (ein Skript, ein spaeterer Endpunkt) wuerde sie brechen.
    task = _task(db, int(aufgabe["id"]))
    sessions = tuple(task.sessions)
    assert sessions
    for _ in range(3):
        rewards_service.record_progress(
            db,
            user.id,
            zone=ZoneInfo("UTC"),
            task=task,
            sessions=sessions,
        )
    db.commit()

    db.expire_all()
    nachher = _buchungen(db, user.id)
    assert len(nachher) == len(zeilen)
    assert sum(zeile.points for zeile in nachher) == stand_nach_dem_ersten
    assert _punkte(client) == stand_nach_dem_ersten


def test_ein_wieder_eroeffneter_abschluss_zahlt_nicht_zweimal(app_context: tuple[TestClient, Session, User]) -> None:
    """Auf ``pending`` zurueck und wieder erledigt holt dieselben Punkte.

    Sonst waere "kurz zurueckstellen" eine Maschine zum Punktemachen. Die
    Eindeutigkeit liegt auf dem Ereignis, nicht auf dem Tag.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Ableitungen", mathe)
    aufgabe = _aufgabe(client, db, user, thema, minuten=25)

    client.post(f"/api/v1/study/tasks/{aufgabe['id']}/complete")
    stand = _punkte(client)
    assert client.patch(f"/api/v1/study/tasks/{aufgabe['id']}", json={"status": "pending"}).status_code == 200
    assert client.post(f"/api/v1/study/tasks/{aufgabe['id']}/complete").status_code == 200

    assert _punkte(client) == stand
    db.expire_all()
    assert len([z for z in _buchungen(db, user.id) if z.kind == "task_completed"]) == 1


def test_der_patch_zum_abschluss_zahlt_ebenfalls(app_context: tuple[TestClient, Session, User]) -> None:
    """Auch der Weg ohne den Abschluss-Endpunkt zahlt.

    ``PATCH {"status": "completed"}`` setzt dieselbe Tatsache. Zahlte nur der
    Endpunkt, waere der PATCH eine Nebenhand, an der die Punkte vorbeikommen.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Bruecken", mathe)
    aufgabe = _aufgabe(client, db, user, thema, minuten=30)

    assert client.patch(f"/api/v1/study/tasks/{aufgabe['id']}", json={"status": "completed"}).status_code == 200
    stand = _punkte(client)
    assert stand == rewards_service.TASK_POINTS
    assert client.patch(f"/api/v1/study/tasks/{aufgabe['id']}", json={"status": "completed"}).status_code == 200
    assert _punkte(client) == stand


# --- Der Inhaltsbezug: kein reines Zaehlerspiel -----------------------------


def test_ohne_thema_gibt_es_keine_punkte(app_context: tuple[TestClient, Session, User]) -> None:
    """Die Kernzusage: eine Aufgabe ohne Thema zahlt nichts.

    Ohne Thema gibt es keinen Weg zum Fach, und damit nichts, woran die Buchung den
    Inhalt binden koennte. Das ist der Unterschied zwischen einer Belohnung fuer
    Lernen und einem Zaehler fuer Klickerei.
    """
    client, db, user = app_context
    login(client)
    aufgabe = _aufgabe(client, db, user, None, minuten=30, titel="Etwas ohne Thema")

    assert client.post(f"/api/v1/study/tasks/{aufgabe['id']}/complete").status_code == 200
    assert _punkte(client) == 0
    db.expire_all()
    assert _buchungen(db, user.id) == []


def test_die_punkte_stufen_nach_der_zeit_und_sind_gedeckelt(app_context: tuple[TestClient, Session, User]) -> None:
    """Die Treppe eines Fachtags, als Tabelle - wer sie aendert, faellt hier auf.

    Zwei Aussagen stecken darin. Erstens ist der Verlauf **nicht linear**: 24 und 25
    Minuten liegen 10 Punkte auseinander, 49 und 50 noch einmal 15. Eine Minute mehr
    ist nicht durchgehend eine Minute wert, sonst waere das Sitzen so viel wert wie
    ein durchgearbeiteter Fachtag. Zweitens ist er **gedeckelt**: ab 75 Minuten waechst
    der Betrag nicht mehr, sonst loehnte sich ein zweiter Nachmittag am selben Fach.
    """
    assert rewards_service.subject_day_points(0) == 0
    assert rewards_service.subject_day_points(9) == 0
    assert rewards_service.subject_day_points(10) == 5
    assert rewards_service.subject_day_points(24) == 5
    assert rewards_service.subject_day_points(25) == 15
    assert rewards_service.subject_day_points(49) == 15
    assert rewards_service.subject_day_points(50) == 30
    assert rewards_service.subject_day_points(74) == 30
    assert rewards_service.subject_day_points(75) == rewards_service.MAX_SUBJECT_DAY_POINTS
    # Und ab hier bleibt es dort - auch bei absurd langen Tagen.
    assert rewards_service.subject_day_points(300) == rewards_service.MAX_SUBJECT_DAY_POINTS

    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Kurven", mathe)
    task = _task(db, int(_aufgabe(client, db, user, thema, minuten=30)["id"]))
    session = _session_am_tag(db, user, task, tag=_heute(), minuten=300)
    _vormerken(db, user, session=session)

    db.expire_all()
    zeilen = [z for z in _buchungen(db, user.id) if z.kind == "subject_day"]
    assert len(zeilen) == 1
    assert zeilen[0].minutes == 300
    assert zeilen[0].points == rewards_service.MAX_SUBJECT_DAY_POINTS


def test_unter_der_schwelle_wird_nichts_gebucht(app_context: tuple[TestClient, Session, User]) -> None:
    """Fuenf Minuten sind ein Einstieg, aber noch kein Lerntag.

    Unterhalb von ``MIN_SUBJECT_DAY_MINUTES`` entsteht **keine** Zeile - auch keine
    mit 0 Punkten. Eine Buchung ueber fuenf Minuten waere eine Buchung ueber nichts.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Lineare Algebra", mathe)
    task = _task(db, int(_aufgabe(client, db, user, thema, minuten=30)["id"]))
    session = _session_am_tag(db, user, task, tag=_heute(), minuten=5)
    _vormerken(db, user, session=session)
    db.expire_all()
    assert _buchungen(db, user.id) == []


def test_eine_offene_session_zaehlt_nicht(app_context: tuple[TestClient, Session, User]) -> None:
    """Eine laufende Session hat keine Dauer und wird nicht gebucht.

    Sonst waere jede laufende Session ein Zwischenstand *und* nach dem Beenden ein
    zweiter Eintrag fuer dieselbe Zeit.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Stochastik", mathe)
    aufgabe = _aufgabe(client, db, user, thema, minuten=30)
    assert client.post(f"/api/v1/study/tasks/{aufgabe['id']}/start").status_code == 200
    db.expire_all()
    offen = _task(db, int(aufgabe["id"])).sessions[-1]
    assert offen.completed_at is None

    _vormerken(db, user, session=offen)
    db.expire_all()
    assert _buchungen(db, user.id) == []


def test_der_stopp_bucht_die_gelernte_zeit_ohne_aufgabenpunkte(app_context: tuple[TestClient, Session, User]) -> None:
    """Ein gestoppter Block zaehlt als Zeit, nicht als erledigte Aufgabe.

    Der Block fand statt, er wurde nur nicht zu Ende gebracht. Genau das ist der
    Unterschied zwischen den beiden Buchungsarten: Punkte fuer den Abschluss gibt
    es nur ueber ``task_completed``.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user, "Physik")
    thema = _thema(db, user, "Optik", mathe)
    task_id = int(_aufgabe(client, db, user, thema, minuten=60)["id"])

    client.post(f"/api/v1/study/tasks/{task_id}/start")
    # Der Start in die Vergangenheit schieben, damit der Stopp eine echte Dauer
    # rechnet. Ein Block, den man im selben Augenblick startet und stoppt, hat
    # null Minuten - und wuerde zu Recht nichts buchen.
    offen = db.scalar(
        select(StudySession).where(StudySession.task_id == task_id, StudySession.completed_at.is_(None))
    )
    assert offen is not None
    offen.started_at = as_utc(datetime.now(UTC)) - timedelta(minutes=45)
    db.commit()

    assert client.post(f"/api/v1/study/tasks/{task_id}/stop").status_code == 200
    db.expire_all()
    zeilen = _buchungen(db, user.id)
    assert [zeile.kind for zeile in zeilen] == ["subject_day"]
    assert zeilen[0].points == rewards_service.subject_day_points(45)
    assert zeilen[0].minutes == 45


def test_zwei_blaecke_an_einem_tag_werden_ein_fachtag(app_context: tuple[TestClient, Session, User]) -> None:
    """Zwei Buchungen an einem Tag sind **eine** Buchung mit der Summe.

    Sonst stuende am Ende eine Zeile je Block, und die eindeutige Ereignisgrenze
    wuerde den zweiten Block zur Fehlschlag machen, statt ihn zusammenzufassen.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Analysis", mathe)
    erster = _task(db, int(_aufgabe(client, db, user, thema, minuten=30, titel="Erster Block")["id"]))
    zweiter = _task(db, int(_aufgabe(client, db, user, thema, minuten=30, titel="Zweiter Block")["id"]))

    _vormerken(db, user, session=_session_am_tag(db, user, erster, tag=_heute(), minuten=20))
    _vormerken(db, user, session=_session_am_tag(db, user, zweiter, tag=_heute(), minuten=20))

    db.expire_all()
    zeilen = [z for z in _buchungen(db, user.id) if z.kind == "subject_day"]
    assert len(zeilen) == 1
    assert zeilen[0].minutes == 40
    # Der Betrag waechst **mit** der Summe, aber nicht proportional: 20 Minuten
    # ergaben 5, 40 Minuten ergeben 15. Ohne dieses Nachziehen wuerde der zweite
    # Block am Tagesende umsonst dazugekommen sein.
    assert zeilen[0].points == rewards_service.subject_day_points(40)


def test_die_tagesobergrenze_halt_und_bucht_weiter(app_context: tuple[TestClient, Session, User]) -> None:
    """Ist das Tageskonto voll, wird weiter gebucht - mit 0 Punkten.

    Der Betrag ist gedeckelt, das **Protokoll** nicht. Eine Luecke waere keine
    Auskunft; eine Zeile mit 0 Punkten sagt, dass gelernt wurde und dass die Regel
    gegriffen hat. Und es gibt keine Nachzahlung am Folgetag.
    """
    client, db, user = app_context
    login(client)
    heute = _heute()
    mathe = _fach(db, user, "Analysis I")
    chemie = _fach(db, user, "Chemie")
    thema_mathe = _thema(db, user, "Integration", mathe)
    thema_chemie = _thema(db, user, "Saeure", chemie)
    task_mathe = _task(db, int(_aufgabe(client, db, user, thema_mathe, minuten=30, titel="Analysis")["id"]))
    task_mathe.status = "completed"
    db.commit()
    task_chemie = _task(db, int(_aufgabe(client, db, user, thema_chemie, minuten=30, titel="Chemie")["id"]))

    # Erst die Aufgabe (15) und ein sehr langer Tag (40): 55 von 80.
    _vormerken(db, user, task=task_mathe, session=_session_am_tag(db, user, task_mathe, tag=heute, minuten=300))
    assert _punkte(client) == rewards_service.TASK_POINTS + rewards_service.MAX_SUBJECT_DAY_POINTS

    # Dann ein zweites Fach: 40 waeren zu viel, also bekommt es den Rest.
    _vormerken(db, user, session=_session_am_tag(db, user, task_chemie, tag=heute, minuten=300))
    assert _punkte(client) == rewards_service.MAX_DAY_POINTS

    # Und ein weiterer Block an diesem Tag fuegt nichts hinzu.
    _vormerken(db, user, session=_session_am_tag(db, user, task_chemie, tag=heute, minuten=20))
    db.expire_all()
    assert sum(z.points for z in _buchungen(db, user.id)) == rewards_service.MAX_DAY_POINTS
    assert int(client.get("/api/v1/rewards").json()["summary"]["today_points"]) == rewards_service.MAX_DAY_POINTS

    # Ein drittes Fach an diesem Tag wird **mit 0 Punkten** gebucht: gelernt wurde
    # es, ausgezahlt wird nichts mehr. Genau das ist der Unterschied zwischen einer
    # gedeckelten Menge und einem abgeschalteten Protokoll.
    physik = _fach(db, user, "Physik")
    thema_physik = _thema(db, user, "Optik", physik)
    task_physik = _task(db, int(_aufgabe(client, db, user, thema_physik, minuten=30, titel="Physik")["id"]))
    _vormerken(db, user, session=_session_am_tag(db, user, task_physik, tag=heute, minuten=30))
    db.expire_all()
    assert sum(z.points for z in _buchungen(db, user.id)) == rewards_service.MAX_DAY_POINTS
    assert any(z.points == 0 and z.minutes == 30 for z in _buchungen(db, user.id))


def test_der_tagespunkt_ist_der_tag_des_nutzers(app_context: tuple[TestClient, Session, User]) -> None:
    """Ein Block kurz nach Mitternacht ist in Berlin heute und in UTC gestern.

    Die Lernsessions liegen als UTC in der Datenbank. Ohne die Zone des Nutzers
    waere derselbe Block ein *gestriger* Lerntag - und damit ein anderes
    Punktekonto, was bei einer Tagesobergrenze den Unterschied zwischen "heute voll"
    und "heute noch 40 frei" ausmacht.

    Der Block beginnt um 00:30 Ortszeit, weil nur in den ersten beiden Stunden der
    Ortszeit der Kalendertag vom UTC-Tag abweicht.
    """
    client, db, user = app_context
    user.timezone = "Europe/Berlin"
    db.commit()
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Logik", mathe)
    task = _task(db, int(_aufgabe(client, db, user, thema, minuten=30)["id"]))

    tag = _montag_dieser_woche()
    start = as_utc(datetime.combine(tag, time(0, 30), tzinfo=BERLIN))
    session = StudySession(
        user_id=user.id,
        task_id=task.id,
        started_at=start,
        completed_at=start + timedelta(minutes=30),
        duration_minutes=30,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    # Die Voraussetzung des Tests: in UTC ist derselbe Block am Vortag.
    assert session.started_at.astimezone(UTC).date() == tag - timedelta(days=1)

    _vormerken(db, user, session=session, zone=BERLIN, now=session.completed_at)
    db.expire_all()
    zeilen = [z for z in _buchungen(db, user.id) if z.kind == "subject_day"]
    assert len(zeilen) == 1
    assert zeilen[0].earned_on == tag


# --- Auszeichnungen ---------------------------------------------------------


def test_auszeichnungen_tragen_bedingung_fortschritt_und_zeitpunkt(app_context: tuple[TestClient, Session, User]) -> None:
    """Eine Auszeichnung ist ein Datenobjekt, kein Rechteck.

    Geprueft wird genau das, worum es im ToDo geht: Bedingung (``target`` und
    ``unit``), Fortschritt, Zeitpunkt - und die Frage der Wiederholbarkeit, die als
    Feld mitkommt statt in einer Konvention.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Matrizen", mathe)
    aufgabe = _aufgabe(client, db, user, thema, minuten=30)
    assert client.post(f"/api/v1/study/tasks/{aufgabe['id']}/complete").status_code == 200

    antwort = client.get("/api/v1/rewards").json()
    erster = _badge("erster_abschluss", antwort)
    assert erster["awarded"] is True
    assert erster["awarded_at"] is not None
    assert erster["progress"] == erster["target"] == 1
    assert erster["unit"] == "Aufgabe"
    assert erster["repeatable"] is False
    assert erster["scope"] == "global"
    assert erster["scope_kind"] == "global"
    assert erster["title"] and erster["description"]

    # Die fachgebundenen Auszeichnungen tragen ihren Bereich mit - daran haengt die
    # Eindeutigkeit in der Datenbank.
    fach = _badge("fach_durchgearbeitet", antwort)
    assert fach["scope"] == f"subject:{mathe.id}"
    assert fach["scope_kind"] == "subject"
    assert fach["subject_name"] == "Mathematik"
    assert fach["awarded"] is True

    themen_badge = _badge("regelmaessig_wiederholt", antwort)
    assert themen_badge["scope"] == f"topic:{thema.id}"
    assert themen_badge["topic_name"] == "Matrizen"
    # Ein Tag ist noch kein Muster.
    assert themen_badge["progress"] == 1
    assert themen_badge["target"] == 3
    assert themen_badge["awarded"] is False


def test_eine_auszeichnung_wird_nicht_zweimal_vergeben(app_context: tuple[TestClient, Session, User]) -> None:
    """Dieselbe Bedingung ergibt genau eine Zeile - und genau einen Zeitpunkt.

    Der Bereich ist der Schluessel, nicht der Code: dieselbe Auszeichnung kann je
    Fach oder Thema eine eigene Zeile haben, ohne dass sie sich gegenseitig ins
    Gehege kommen.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Statistik", mathe)
    aufgabe = _aufgabe(client, db, user, thema, minuten=30)
    for _ in range(3):
        assert client.post(f"/api/v1/study/tasks/{aufgabe['id']}/complete").status_code == 200

    db.expire_all()
    zeilen = list(db.scalars(select(RewardBadge).where(RewardBadge.user_id == user.id)).all())
    codes = [badge.code for badge in zeilen]
    assert codes.count("erster_abschluss") == 1
    assert codes.count("regelmaessig_wiederholt") == 1
    assert codes.count("fach_durchgearbeitet") == 1


def test_der_wochenrhythmus_ist_je_woche_eine_eigene_auszeichnung(app_context: tuple[TestClient, Session, User]) -> None:
    """Eine wiederholte Auszeichnung bekommt je Zeitraum eine eigene Zeile.

    Kein Zaehler in derselben Zeile: eine zurueckgesetzte Zahl waere eine Frage, die
    man der Oberflaeche stellen muesste, und zwei Zeilen fuer dieselbe Bedingung
    derselben Woche waeren doppelt gezaehlt. Der Test pinnt deshalb den ``now`` auf
    den Freitag der Woche - laeuft er an einem Montag, sind die vier anderen Tage
    noch in der Zukunft und die Aussage waere eine andere.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Topologie", mathe)
    task = _task(db, int(_aufgabe(client, db, user, thema, minuten=30)["id"]))
    montag = _montag_dieser_woche()

    for tage in range(5):
        _vormerken(db, user, session=_session_am_tag(db, user, task, tag=montag + timedelta(days=tage), minuten=15))
    # Der Freitag dieser Woche ist der Bezugspunkt der Auswertung.
    freitag = as_utc(datetime.combine(montag + timedelta(days=4), time(20, 0), tzinfo=UTC_ZONE))
    _vormerken(db, user, session=None, now=freitag)

    db.expire_all()
    zeilen = list(
        db.scalars(
            select(RewardBadge).where(RewardBadge.user_id == user.id, RewardBadge.code == "wochenrhythmus")
        ).all()
    )
    assert len(zeilen) == 1
    assert zeilen[0].awarded_at is not None
    assert zeilen[0].scope.startswith("week:")

    antwort = client.get("/api/v1/rewards").json()
    rhythmus = _badge("wochenrhythmus", antwort)
    assert rhythmus["repeatable"] is True
    assert rhythmus["scope_kind"] == "week"
    assert rhythmus["awarded"] is True


def test_der_fortschritt_einer_offenen_auszeichnung_wird_gepflegt(app_context: tuple[TestClient, Session, User]) -> None:
    """Eine offene Auszeichnung zeigt ihren Fortschritt, und er waechst.

    Das ist der Unterschied zwischen einem Datenobjekt und einem statischen
    Abzeichen: der Balken bewegt sich, weil die Zahl aus der Datenbank kommt.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Numerik", mathe)
    task = _task(db, int(_aufgabe(client, db, user, thema, minuten=30)["id"]))

    fortschritte: list[int] = []
    for tage in (2, 1):
        _vormerken(db, user, session=_session_am_tag(db, user, task, tag=_heute() - timedelta(days=tage), minuten=15))
        fortschritte.append(_badge("regelmaessig_wiederholt", client.get("/api/v1/rewards").json())["progress"])

    assert fortschritte == [1, 2]
    db.expire_all()
    zeile = db.scalar(
        select(RewardBadge).where(RewardBadge.user_id == user.id, RewardBadge.code == "regelmaessig_wiederholt")
    )
    assert zeile is not None
    assert zeile.awarded_at is None


def test_eine_erreichte_auszeichnung_verschwindet_nicht_wieder(app_context: tuple[TestClient, Session, User]) -> None:
    """Eine verdiente Auszeichnung bleibt, auch wenn neue Aufgaben dazukommen.

    Der Fortschritt friert ein. Eine Belohnung, die man wieder verlieren kann, ist
    eine Schuld - und genau dieser Unterschied trennt eine Auszeichnung von einer
    Aufgabe.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Compilerbau", mathe)
    aufgabe = _aufgabe(client, db, user, thema, minuten=30)
    assert client.post(f"/api/v1/study/tasks/{aufgabe['id']}/complete").status_code == 200

    # Eine zweite, offene Aufgabe im selben Fach aendert die Bedingung.
    _aufgabe(client, db, user, thema, minuten=30, titel="Noch offen")
    task = _task(db, int(aufgabe["id"]))
    _vormerken(db, user, task=task)

    badge = _badge("fach_durchgearbeitet", client.get("/api/v1/rewards").json())
    assert badge["awarded"] is True
    assert badge["progress"] == 1


def test_die_woche_und_heute_kommen_aus_denselben_zeilen(app_context: tuple[TestClient, Session, User]) -> None:
    """Heute, diese Woche und insgesamt sind drei Lesarten einer Abfrage.

    Drei Abfragen wuerden drei Moeglichkeiten schaffen, in denen die Karte eine Zahl
    aus dem einen Tag und eine Summe aus einer anderen zeigt - und die
    Tagesobergrenze waere dann gegen eine Zahl geprueft, die es so nicht gibt.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Diskrete", mathe)
    task = _task(db, int(_aufgabe(client, db, user, thema, minuten=30)["id"]))
    task.status = "completed"
    db.commit()

    montag = _montag_dieser_woche()
    heute_in_der_woche = montag <= _heute() <= montag + timedelta(days=6)
    if heute_in_der_woche:
        _vormerken(db, user, task=task, session=_session_am_tag(db, user, task, tag=_heute(), minuten=60))
    _vormerken(db, user, session=_session_am_tag(db, user, task, tag=montag - timedelta(days=7), minuten=60))

    summary = client.get("/api/v1/rewards").json()["summary"]
    db.expire_all()
    gesamt = sum(z.points for z in _buchungen(db, user.id))
    assert summary["total_points"] == gesamt
    assert summary["week_points"] >= 0
    assert summary["today_points"] >= 0
    assert summary["today_points"] <= summary["today_cap"]
    assert summary["week_starts_on"] <= summary["week_ends_on"]


# --- Nutzertrennung ---------------------------------------------------------


def test_punkte_eines_fremden_nutzers_sind_weder_sichtbar_noch_veraenderbar(
    app_context: tuple[TestClient, Session, User],
) -> None:
    """Punkte sind nutzerbezogenes Eigentum.

    Ein fremder Nutzer darf weder die Buchungen des einen sehen noch die Aufgaben des
    anderen veraendern. Der Weg davor ist derselbe wie bei allen Nutzdaten: die
    Abfrage filtert auf ``user_id`` und kennt gar keine fremde Id, die man raten
    koennte.
    """
    client, db, user = app_context
    login(client)
    mathe = _fach(db, user)
    thema = _thema(db, user, "Datenbanken", mathe)
    aufgabe = _aufgabe(client, db, user, thema, minuten=30)
    assert client.post(f"/api/v1/study/tasks/{aufgabe['id']}/complete").status_code == 200
    assert _punkte(client) > 0

    anderer = User(
        username="zweite",
        password_hash=hash_password("anderes-passwort"),
        is_admin=True,
        is_active=True,
        timezone="UTC",
    )
    db.add(anderer)
    db.commit()
    assert client.post("/api/v1/auth/login", json={"username": "zweite", "password": "anderes-passwort"}).status_code == 200

    stand = client.get("/api/v1/rewards").json()
    assert stand["summary"]["total_points"] == 0
    assert stand["entries"] == []
    assert stand["badges"] == []

    # Und die Aufgaben des ersten sind fuer den zweiten nicht erreichbar - 404,
    # nicht 403, weil eine 403 die Existenz der Id verraten wuerde.
    assert client.post(f"/api/v1/study/tasks/{aufgabe['id']}/complete").status_code == 404


# --- Die Grenze zu Noten und KI (Punkt 5) -----------------------------------


def test_belohnungen_veraendern_keine_prioritaet_und_keinen_schnitt(app_context: tuple[TestClient, Session, User]) -> None:
    """Punkte duerfen die Aufgabenprioritaet und den Notenstand nicht anfassen.

    Der Test macht beide Seiten messbar, statt sich auf eine Behauptung zu verlassen:

    1. Die Prioritaet einer erzeugten Aufgabe ist **vor und nach** dem Punkten
       identisch - mit schlechtem Fachschnitt, damit der Notenweg ueberhaupt aktiv ist.
    2. Schnitt und Hochrechnung sind dieselben Zahlen.
    3. Der freigegebene KI-Kontext enthaelt die Punktzahl nicht - weder als Zahl noch
       als Wort.

    Der dritte Punkt ist der, der zuerst bricht: eine Zeile Punkte in
    ``grade_context_lines()`` faellt hier auf, ohne dass jemand den Wert von Hand
    nachrechnen muss.
    """
    client, db, user = app_context
    login(client)
    plan_id = _plan(db, user)

    geschichte = _fach(db, user, "Geschichte")
    sicher = _thema(db, user, "Bundeswehr", geschichte)
    unsicher = _thema(db, user, "Industrialisierung", geschichte)
    db.add(LearningProgress(user_id=user.id, topic_id=sicher.id, confidence=1.0))
    db.add(LearningProgress(user_id=user.id, topic_id=unsicher.id, confidence=0.0))
    db.commit()

    # Ein schlechter Fachschnitt, damit `grade_priority` wirklich greift.
    for tage in (30, 20):
        tag = _heute() - timedelta(days=tage)
        assert (
            client.post(
                "/api/v1/grades",
                json={
                    "subject_id": geschichte.id,
                    "grade_value": 4.4,
                    "kind": "written_exam",
                    "graded_on": tag.isoformat(),
                },
            ).status_code
            == 201
        )

    exam = client.post(
        "/api/v1/exams",
        json={
            "subject_id": geschichte.id,
            "title": "Epochen",
            "date": (datetime.now(UTC) + timedelta(days=5)).isoformat(),
        },
    )
    assert exam.status_code == 201, exam.text
    erzeugt = client.post(
        f"/api/v1/exams/{exam.json()['id']}/tasks",
        json={"study_plan_id": plan_id, "chain_dependencies": False},
    )
    assert erzeugt.status_code == 201, erzeugt.text
    vorher = [(a["topic_name"], a["priority"]) for a in erzeugt.json()["created_tasks"]]
    # Ohne Punkte ist die Prioritaet die aus Konfidenz und Notenschnitt, wie im
    # bestehenden Test zur Aufloesung: das sichere Thema wird vom Schnitt gehoben.
    assert vorher == [("Industrialisierung", 4), ("Bundeswehr", 4)]
    schnitt_vorher = client.get("/api/v1/grades/summary").json()["overall"]
    assert schnitt_vorher["average"] == 4.4

    # Jetzt Punkte sammeln: Aufgaben erledigen, Lernzeit, Auszeichnungen.
    for name, thema_id in (("Bundeswehr", sicher.id), ("Industrialisierung", unsicher.id)):
        aufgabe = _aufgabe(client, db, user, None, minuten=45, titel=f"Wiederholung {name}")
        assert client.patch(f"/api/v1/study/tasks/{aufgabe['id']}", json={"topic_id": thema_id}).status_code == 200
        assert client.post(f"/api/v1/study/tasks/{aufgabe['id']}/complete").status_code == 200
    assert _punkte(client) > 0

    # 1. Die Prioritaet ist unveraendert. Der zweite Lauf geht in einen eigenen
    #    Plan, damit die Aufgaben des ersten nicht dazwischenfunken.
    db.add(StudyPlan(user_id=user.id, title="Vergleichsplan"))
    db.commit()
    zweiter_plan = int(db.scalar(select(StudyPlan.id).where(StudyPlan.user_id == user.id).order_by(StudyPlan.id.desc())))
    vergleich = client.post(
        f"/api/v1/exams/{exam.json()['id']}/tasks",
        json={"study_plan_id": zweiter_plan, "chain_dependencies": False},
    )
    assert vergleich.status_code == 201, vergleich.text
    assert [(a["topic_name"], a["priority"]) for a in vergleich.json()["created_tasks"]] == vorher

    # 2. Schnitt und Hochrechnung sind dieselben Zahlen.
    schnitt_nachher = client.get("/api/v1/grades/summary").json()
    assert schnitt_nachher["overall"] == schnitt_vorher
    assert schnitt_nachher["by_subject"] == client.get("/api/v1/grades/summary").json()["by_subject"]
    # Und die reinen Funktionen antworten unveraendert.
    assert grade_priority(4.4) == 4
    assert exam_priority(1.0, 4.4) == 4
    assert exam_priority(0.0, 4.4) == 4
    assert exam_priority(1.0, None) == 0
    assert project_average([WeightedGrade(value=4.4, weight=1)], 1) == (3.6, 2.7, True)

    # 3. Der KI-Kontext kennt die Punkte nicht. Die Freigabe wird ausdruecklich
    #    gesetzt, sonst waere die Liste leer und der Vergleich bedeutungslos.
    assert client.put("/api/v1/grades/sharing", json={"enabled": True}).status_code == 200
    db.expire_all()
    kontext = grade_context_lines(db, user.id)
    assert kontext, "der Notenkontext muss nicht leer sein"
    punkte_text = str(_punkte(client))
    assert all(punkte_text not in zeile for zeile in kontext)
    assert all("punkt" not in zeile.lower() for zeile in kontext)


def test_das_reward_modul_kennt_weder_noten_noch_ki_noch_prompt() -> None:
    """Strukturelle Zusage, nicht nur eine Beobachtung: die Importliste.

    Der Test oben vergleicht Werte. Dieser vergleicht die **Abhaengigkeiten** und
    faengt damit genau den Refactoring-Fall, den ein Werte-Test nicht faengt: ein
    neues ``from app.services.grades import ...`` in ``app/services/rewards.py``
    waere mit einem Werte-Test unauffindbar, bis jemand eine Note durchreicht.

    Umgekehrt darf kein Modul, das Noten oder einen KI-Kontext baut, das
    Reward-Modul kennen - sonst entstuende ein Weg, Punkte in einen Prompt zu tragen,
    ohne dass jemand es bemerkt.
    """
    quelle = Path(rewards_service.__file__)
    baum = ast.parse(quelle.read_text(encoding="utf-8"))

    importierte: set[str] = set()
    beruehrt: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ImportFrom) and knoten.module:
            importierte.add(knoten.module)
        elif isinstance(knoten, ast.Import):
            for alias in knoten.names:
                importierte.add(alias.name)
        elif isinstance(knoten, ast.Name):
            beruehrt.add(knoten.id)
        elif isinstance(knoten, ast.Attribute):
            beruehrt.add(knoten.attr)

    for verboten in ("app.services.grades", "app.services.study", "app.services.ai_provider", "app.services.documents"):
        assert verboten not in importierte, f"rewards.py darf {verboten} nicht importieren"

    # Kein Name aus der Noten- oder KI-Welt darf im Modul auftauchen - auch nicht
    # als Attribut. ``confidence`` ist damit ausgeschlossen: die Konfidenz ist
    # Selbstauskunft ueber ein Thema, kein gemessener Lernstand, und sie gehoert
    # nicht in eine Belohnung.
    for verboten in (
        "Grade",
        "grade_value",
        "grade_priority",
        "project_average",
        "subject_average",
        "exam_priority",
        "confidence",
        "AiProvider",
        "generate",
        "grade_context_lines",
    ):
        assert verboten not in beruehrt, f"rewards.py fasst {verboten} an"

    wurzel = quelle.parents[2]
    for modul in ("app/services/grades.py", "app/services/study.py", "app/services/documents.py", "app/services/greeting.py"):
        assert "rewards" not in (wurzel / modul).read_text(encoding="utf-8"), f"{modul} darf die Belohnung nicht kennen"
