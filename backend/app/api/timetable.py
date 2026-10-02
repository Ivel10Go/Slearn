from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.core.time import zone_or_utc
from app.db.entities import SyncSource
from app.schemas.timetable import (
    LessonChoiceGroup,
    LessonChoiceOption,
    LessonChoiceRequest,
    LessonChoiceResult,
    TimetableApplyRequest,
    TimetableApplyResult,
    TimetableExtractResult,
    TimetableSubjectDraft,
)
from app.services import lesson_choices
from app.services import timetable_upload as service

router = APIRouter(prefix="/api/v1/timetable", tags=["timetable"])


def _groups(db: DatabaseSession, user_id: int, timezone_name: str) -> list[LessonChoiceGroup]:
    return [
        LessonChoiceGroup(
            key=group.key,
            weekday=group.weekday,
            start=group.start,
            end=group.end,
            decided=group.decided,
            options=[
                LessonChoiceOption(
                    subject_id=option.subject_id, name=option.name, short_name=option.short_name, selected=option.selected
                )
                for option in group.options
            ],
        )
        for group in lesson_choices.find_groups(db, user_id, zone_or_utc(timezone_name))
    ]


@router.get("/conflicts", response_model=list[LessonChoiceGroup])
def list_conflicts(current_user: CurrentUser, db: DatabaseSession, settings: AppSettings) -> list[LessonChoiceGroup]:
    """Faecher, die im Plan zur selben Zeit liegen - mit der bisherigen Auswahl."""
    return _groups(db, current_user.id, current_user.timezone or settings.timezone)


@router.put("/choices", response_model=LessonChoiceResult)
def save_choices(
    payload: LessonChoiceRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> LessonChoiceResult:
    """Speichert, welche Parallelstunden besucht werden; die anderen fallen aus dem Plan.

    Wurde eine fruehere Abwahl zurueckgenommen, ist die Stunde geloescht und
    kommt nur ueber einen neuen Abruf zurueck - den macht dieser Endpunkt selbst.
    """
    timezone_name = current_user.timezone or settings.timezone
    try:
        needs_resync = lesson_choices.save_choices(
            db,
            current_user.id,
            zone_or_utc(timezone_name),
            {selection.key: selection.attended for selection in payload.selections},
        )
    except lesson_choices.LessonChoiceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    resynced = False
    if needs_resync:
        from app.api import integrations as integrations_api

        for source in db.scalars(
            select(SyncSource).where(
                SyncSource.user_id == current_user.id,
                SyncSource.source_type == "webuntis",
                SyncSource.enabled.is_(True),
            )
        ).all():
            integrations_api._sync_after_connect(db, source, settings, timezone_name)
            resynced = True
    return LessonChoiceResult(groups=_groups(db, current_user.id, timezone_name), resynced=resynced)


@router.post("/extract", response_model=TimetableExtractResult)
def extract_timetable(
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    file: UploadFile = File(...),
) -> TimetableExtractResult:
    """Liest einen hochgeladenen Plan aus und schlaegt Faecher und Stunden vor.

    Schreibt nichts: erst ``/apply`` uebernimmt den (ggf. korrigierten) Vorschlag.
    """
    data = file.file.read(service.MAX_UPLOAD_BYTES + 1)
    try:
        extraction = service.extract_timetable(db, current_user.id, settings, file.filename or "", data)
    except service.TimetableUploadError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    known = service.existing_subject_names(db, current_user.id)
    seen: dict[str, TimetableSubjectDraft] = {}
    for entry in extraction.entries:
        key = entry.subject.casefold()
        if key not in seen:
            seen[key] = TimetableSubjectDraft(name=entry.subject, short_name=entry.short_name, exists=key in known)
    return TimetableExtractResult(entries=extraction.entries, subjects=list(seen.values()), via=extraction.via)


@router.post("/apply", response_model=TimetableApplyResult, status_code=status.HTTP_201_CREATED)
def apply_timetable(
    payload: TimetableApplyRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> TimetableApplyResult:
    created, imported, start, end = service.apply_timetable(
        db,
        current_user.id,
        payload.entries,
        current_user.timezone or settings.timezone,
        payload.weeks,
    )
    return TimetableApplyResult(
        created_subjects=created, imported_lessons=imported, window_start=start, window_end=end
    )
