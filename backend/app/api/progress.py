"""Lernfortschritt und Lernprozess unter ``/api/v1/progress``.

Vier Routen, alle mit Session und Nutzertrennung:

* ``GET   /insights``          - die Auswertung des Lernprotokolls
* ``GET   /topics``            - der Lernstand je Thema, mit Wiederholungstermin
* ``PUT   /topics/{topic_id}`` - Konfidenz und ``last_reviewed_at`` schreiben
* ``PATCH /goal``              - das Wochenziel setzen
"""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.core.time import as_utc, resolve_timezone_name, utc_now, zone_or_utc
from app.db.entities import LearningProgress, Topic
from app.schemas.progress import (
    LearningInsights,
    TopicProgressRead,
    TopicProgressWrite,
    WeeklyGoalRead,
    WeeklyGoalWrite,
)
from app.services import progress as progress_service

router = APIRouter(prefix="/api/v1/progress", tags=["progress"])


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _owned_topic(db: Session, topic_id: int, user_id: int) -> Topic:
    """Das Thema des Nutzers - sonst 404, auch wenn es das Thema eines anderen ist.

    Kein 403: das wuerde verraten, dass die Id ueberhaupt existiert. Die
    Nutzertrennung ist hier Pflicht, weil ohne sie jeder den Fortschritt jeder
    fremden Id schreiben koennte.
    """
    topic = db.get(Topic, topic_id)
    if topic is None or topic.user_id != user_id:
        raise _not_found("Topic not found")
    return topic


@router.get("/insights", response_model=LearningInsights)
def read_insights(current_user: CurrentUser, db: DatabaseSession, settings: AppSettings) -> LearningInsights:
    """Das Lernprotokoll als Auswertung, nicht als Liste.

    Enthaelt Serie, Wochenziel, Lernzeit je Fach und die Verteilung auf die
    Wochentage. Alles rechnet in der Zeitzone des Nutzers, nicht in UTC: eine
    Session von Sonntag 23:30 bis Montag 00:10 ist hier ein Sonntag.

    Offene Sessions stehen getrennt in ``open_sessions`` und ``active_minutes``
    und gehen **nicht** in die Summen ein - eine offene Session hat noch keine
    Dauer und wuerde nach dem Beenden ein zweites Mal gezaehlt.
    """
    timezone_name = resolve_timezone_name(current_user.timezone, settings.timezone)
    return progress_service.learning_insights(db, current_user.id, zone_or_utc(timezone_name), timezone_name)


@router.get("/topics", response_model=list[TopicProgressRead])
def list_topic_progress(
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    subject_id: int | None = Query(default=None, alias="subject_id"),
) -> list[TopicProgressRead]:
    """Der Lernstand aller Themen, mit Konfidenz, Lernzeit und naechster Wiederholung.

    Ein Thema ohne Lernstand ist **kein** Fehler: es kommt mit Konfidenz 0 und
    der Bezeichnung "unsicher" zurueck, denn ein fehlender Lernstand ist keine
    gute Nachricht und soll auch nicht wie eine aussehen.

    ``subject_id`` schraenkt auf ein Fach ein. Sortiert wird serverseitig:
    faellige Themen zuerst, dann der Termin, dann die niedrigste Konfidenz.
    """
    timezone_name = resolve_timezone_name(current_user.timezone, settings.timezone)
    return progress_service.topic_progress(db, current_user.id, zone_or_utc(timezone_name), subject_id=subject_id)


@router.put("/topics/{topic_id}", response_model=TopicProgressRead)
def write_topic_progress(
    topic_id: int,
    payload: TopicProgressWrite,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> TopicProgressRead:
    """Konfidenz und Wiederholungsdatum eines Themas schreiben.

    Das ist der erste Ort, an dem ``LearningProgress`` ueberhaupt geschrieben
    wird: vorher gab es die Zeile nur aus dem Demo-Seed. Der Aufruf legt sie bei
    Bedarf an und aendert sie sonst - deshalb ein ``PUT`` und kein ``PATCH``.

    ``last_reviewed_at`` hat drei Zustaende: **nicht gesetzt** heisst *jetzt*
    (eine Einschaetzung ohne Datum ist eine Wiederholung in diesem Moment, und
    ohne Anker gaebe es kein Intervall), **explizit ``null``** loescht den
    Anker, und ein **Zeitpunkt** wird uebernommen. ``notes`` bleibt
    unberuehrt, wenn das Feld fehlt.

    Der Klausur-Generator liest dieselbe Konfidenz: sie entscheidet ueber
    Reihenfolge und Prioritaet der erzeugten Aufgaben. Ein hier gesetzter Wert
    verschiebt beim naechsten Lauf den fruehesten Termin auf das unsicherste
    Thema.
    """
    _owned_topic(db, topic_id, current_user.id)
    progress = db.get(LearningProgress, (current_user.id, topic_id))
    if progress is None:
        progress = LearningProgress(user_id=current_user.id, topic_id=topic_id, confidence=payload.confidence)
        db.add(progress)
    progress.confidence = payload.confidence
    if "last_reviewed_at" in payload.model_fields_set:
        # `as_utc` haertet einen naiven Zeitpunkt auf UTC, statt ihn als
        # Ortszeit zu deuten - dieselbe Rechnung wie in `app.api.study`.
        progress.last_reviewed_at = as_utc(payload.last_reviewed_at) if payload.last_reviewed_at else None
    else:
        # Ohne Datum ist die Einschaetzung selbst die Wiederholung.
        progress.last_reviewed_at = utc_now()
    if "notes" in payload.model_fields_set:
        progress.notes = payload.notes
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Learning progress could not be saved") from exc
    timezone_name = resolve_timezone_name(current_user.timezone, settings.timezone)
    read = progress_service.one_topic_progress(db, current_user.id, topic_id, zone_or_utc(timezone_name))
    if read is None:
        raise _not_found("Topic not found")
    return read


@router.patch("/goal", response_model=WeeklyGoalRead)
def update_weekly_goal(
    payload: WeeklyGoalWrite,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> WeeklyGoalRead:
    """Das Wochenziel in gelernten Minuten setzen.

    Die Einheit ist **Minuten**, nicht Sessions: ein Block von fuenf Minuten und
    einer von neunzig sind nicht dasselbe, und eine Serie von Leerlaeufen waere
    kein Wochenziel. Das Ziel gilt fuer **Montag bis Sonntag** in der Zeitzone
    des Nutzers.

    Das Ziel wird nicht anteilig auf die vergangenen Tage gekuerzt: ein
    Rueckstand am Mittwoch ist eine Information und kein Rechenfehler.
    """
    progress_service.set_weekly_goal_minutes(db, current_user.id, payload.goal_minutes)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Weekly goal could not be saved") from exc
    timezone_name = resolve_timezone_name(current_user.timezone, settings.timezone)
    return progress_service.learning_insights(db, current_user.id, zone_or_utc(timezone_name), timezone_name).week
