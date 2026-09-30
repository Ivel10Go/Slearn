"""Noten unter ``/api/v1/grades``.

Zehn Routen, alle mit Session und Nutzertrennung. Drei Reihen sind bewusst
so sortiert, weil FastAPI in **Deklarationsreihenfolge** matcht:

1. die Sammlung (``""``),
2. die benannten Unterrouten (``/summary``, ``/sharing``, ``/import``),
3. zuletzt die einzelne Note (``/{grade_id}``).

Anderenfalls wuerde ``GET /summary`` gegen ``/{grade_id}`` matchen und an der
Integer-Umwandlung scheitern - ein 422, wo eine Auswertung gemeint war.

Der Import hat bewusst **zwei** Routen. Ein Notenspiegel wird falsch gelesen,
und eine Notenliste, die sich nicht zurücknehmen laesst, waere ein Grund, die
Funktion gar nicht erst anzubieten. Die Vorschau zeigt jede Zeile, die
Ubernahme legt nur die bestaetigten an.
"""

from datetime import date
from typing import cast

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.api.helpers import owned_or_404, subject_or_404
from app.core.time import resolve_timezone_name, utc_now, zone_or_utc
from app.db.entities import Grade
from app.schemas.common import MessageResponse
from app.schemas.grades import (
    GradeCreate,
    GradeImportPreview,
    GradeImportRequest,
    GradeImportResult,
    GradeKind,
    GradeRead,
    GradeSharingRead,
    GradeSharingWrite,
    GradeSummary,
    GradeUpdate,
)
from app.services import grades as grades_service
from app.services.grades import GradeImportError, GradeImportNotFound

router = APIRouter(prefix="/api/v1/grades", tags=["grades"])

# Wo die Freigabe fuer Noten ausgewertet wird. Der Text steht in der
# Oberflaeche, aber das Backend muss wissen, dass es zwei Stellen sind -
# sonst kaeme nur eine davon beim Nachsenden von Noten in Frage.
SHARING_USES = [
    "Willkommenssatz des Dashboards",
    "Fragen an den Assistenten (documents/ask)",
]


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _unprocessable(detail: str) -> HTTPException:
    """Eine Verletzung aus der Anfrage: 422, nicht 409.

    Ein Notenspiegel ohne lesbaren Inhalt oder ohne bestaetigte Zeile ist kein
    Datenbankkonflikt, sondern eine Anfrage, die nicht geht. Die Meldung ist
    deutsch, weil die Oberflaeche sie anzeigt.
    """
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _import_error(exc: GradeImportError) -> HTTPException:
    """Fremdes Dokument ist ein 404, unlesbarer Inhalt ein 422.

    Die Trennung ist die Nutzertrennung: eine fremde Id darf nicht verraten, dass
    es sie gibt. Alles andere - kein Textformat, keine bestaetigte Zeile - ist
    eine Angabe, die man korrigieren kann, und gehoert darum als 422 mit
    deutschem Klartext nach oben.
    """
    if isinstance(exc, GradeImportNotFound):
        return _not_found("Dokument nicht gefunden")
    return _unprocessable(str(exc))


def _read(item: Grade) -> GradeRead:
    fach = item.subject
    return GradeRead(
        id=item.id,
        subject_id=item.subject_id,
        subject_name=fach.name if fach else None,
        grade_value=item.grade_value,
        kind=cast(GradeKind, item.kind),
        weight=item.weight,
        graded_on=item.graded_on,
        comment=item.comment,
        source=item.source,
        document_id=item.document_id,
    )


def _check_kind(kind: str) -> GradeKind:
    """Die Art gegen die geschlossene Liste prüfen, auch wenn FastAPI es schon tat.

    Der Importweg nimmt sie aus dem Request-Body, und der Pflichtwert in der
    Vorschau ist eine Eingabe der Lernenden. Eine unbekannte Art wird deshalb
    **nicht** gespeichert: die Datenbank würde sie mit einem IntegrityError
    zerlegen, und das ist eine 500, wenn der Fehler erst dort auffällt.
    """
    if kind not in grades_service.GRADE_KINDS:
        raise _unprocessable("Unbekannte Art der Note")
    return cast(GradeKind, kind)


# --------------------------------------------------------------------------
# Die Sammlung
# --------------------------------------------------------------------------


@router.get("", response_model=list[GradeRead])
def list_grades(
    current_user: CurrentUser,
    db: DatabaseSession,
    subject_id: int | None = Query(default=None, alias="subject_id"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[GradeRead]:
    """Die Noten des Nutzers, neueste zuerst.

    ``subject_id`` schraenkt auf ein Fach ein. Ohne Filter kommen alle; die
    Oberflaeche zeigt davon die letzten, der Verlauf kommt aus ``/summary``.
    """
    subject_or_404(db, subject_id, current_user.id)
    return [_read(item) for item in grades_service.list_grades(db, current_user.id, subject_id, limit)]


@router.post("", response_model=GradeRead, status_code=status.HTTP_201_CREATED)
def create_grade(payload: GradeCreate, current_user: CurrentUser, db: DatabaseSession) -> GradeRead:
    """Eine Note von Hand anlegen.

    Die Pruefklauseln des Schreibschemas (1,0 bis 6,0, Gewicht ab 1, die drei
    bekannten Arten) greifen **vor** der Datenbank. Das ist Absicht: ein 422 mit
    Klartext sagt der Lernenden, was falsch ist, waehrend ein 409 aus einer
    Check-Constraint nur "could not be created" sagen koennte.
    """
    subject_or_404(db, payload.subject_id, current_user.id)
    note = Grade(user_id=current_user.id, source="manual", **payload.model_dump())
    db.add(note)
    try:
        db.commit()
        db.refresh(note)
    except IntegrityError as exc:
        db.rollback()
        raise _unprocessable("Die Note konnte nicht gespeichert werden") from exc
    return _read(note)


# --------------------------------------------------------------------------
# Die benannten Unterrouten - vor ``/{grade_id}`` deklariert
# --------------------------------------------------------------------------


@router.get("/summary", response_model=GradeSummary)
def read_summary(
    current_user: CurrentUser,
    db: DatabaseSession,
    subject_id: int | None = Query(default=None, alias="subject_id"),
    projected_weight: int = Query(default=0, ge=0, le=100),
) -> GradeSummary:
    """Schnitt, Verlauf und Hochrechnung.

    Ohne ``subject_id`` geht es um **alle** Faecher; mit darum um dieses eine -
    und dann auch nur um dessen Verlauf. Eine Linie, die zwischen zwei Faechern
    springt, beantwortet keine Frage.

    ``projected_weight`` ist die Gewichtung, die noch aussteht, etwa zwei weitere
    Tests. Sie ist eine Angabe der Lernenden und keine gespeicherte Groesse:
    es gibt in diesem Stack keine geplanten Noten, und eine erfundene waere eine
    Zusage, die niemand belegen kann. Ohne sie bleibt die Hochrechnung leer und
    die Karte zeigt den Rueckblick.
    """
    subject_or_404(db, subject_id, current_user.id)
    return grades_service.grade_summary(db, current_user.id, subject_id, projected_weight)


@router.get("/sharing", response_model=GradeSharingRead)
def read_sharing(current_user: CurrentUser, db: DatabaseSession) -> GradeSharingRead:
    """Darf der KI-Dienst Noten sehen? Standard ist nein."""
    return GradeSharingRead(
        share_grades_with_ai=grades_service.grades_sharing_enabled(db, current_user.id),
        preference_key=grades_service.SHARING_PREFERENCE_KEY,
        uses=list(SHARING_USES),
    )


@router.put("/sharing", response_model=GradeSharingRead)
def update_sharing(
    payload: GradeSharingWrite,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> GradeSharingRead:
    """Die Freigabe setzen oder zuruecknehmen.

    **Das Zuruecknehmen loescht den heutigen Willkommenssatz.** Der Satz ist
    zwischengespeichert und kann eine Note genannt haben; nach dem Widerruf darf
    er nicht weiter angezeigt werden. Neu erzeugt wird er beim naechsten Abruf -
    es geht also nichts verloren, was nicht aus einer Note besteht.

    Der Umweg ueber ``PUT``: die Route kennt genau eine Aenderung, es gibt kein
    ``PATCH`` mit einem Feld, dessen Name man raten muesste.
    """
    aktiv = grades_service.set_grades_sharing(db, current_user.id, payload.enabled)
    if not aktiv:
        zone = zone_or_utc(resolve_timezone_name(current_user.timezone, settings.timezone))
        grades_service.invalidate_today_greeting(db, current_user.id, utc_now().astimezone(zone).date())
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _unprocessable("Die Freigabe konnte nicht gespeichert werden") from exc
    return GradeSharingRead(
        share_grades_with_ai=aktiv,
        preference_key=grades_service.SHARING_PREFERENCE_KEY,
        uses=list(SHARING_USES),
    )


@router.get("/import", response_model=GradeImportPreview)
def preview_import(
    current_user: CurrentUser,
    db: DatabaseSession,
    document_id: int = Query(..., alias="document_id"),
    subject_id: int | None = Query(default=None, alias="subject_id"),
    default_kind: GradeKind = Query(default="written_exam", alias="default_kind"),
    default_weight: int = Query(default=1, ge=1, le=20),
    default_date: date | None = Query(default=None, alias="default_date"),
) -> GradeImportPreview:
    """Welche Noten stehen in diesem Dokument? Es wird noch nichts angelegt.

    ``subject_id`` ist das **Vorgabefach** fuer Zeilen, in denen kein Fachname
    steht. Solche Zeilen kommen mit ``subject_recognized: false`` zurueck, damit
    die Oberflaeche das zeigen kann, statt ein geratenes Fach als gelesenes
    auszugeben.
    """
    subject_or_404(db, subject_id, current_user.id)
    try:
        return grades_service.preview_grade_import(
            db,
            current_user.id,
            document_id,
            subject_id,
            default_kind=_check_kind(default_kind),
            default_weight=default_weight,
            default_date=default_date,
        )
    except GradeImportError as exc:
        raise _import_error(exc) from exc


@router.post("/import", response_model=GradeImportResult, status_code=status.HTTP_201_CREATED)
def import_grades(
    payload: GradeImportRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> GradeImportResult:
    """Die bestaetigten Zeilen als Noten anlegen.

    Der Server liest Note, Art, Datum und Fach selbst aus dem Dokument; der
    Request schickt nur die Schluessel der bestaetigten Zeilen. Damit kann die
    Oberflaeche keine Note erfinden, die in keiner Zeile steht.

    Ein Schluessel, der schon importiert wurde, zaehlt als uebersprungen und
    legt keine zweite Zeile an - derselbe Import zweimal ist ein No-Op.
    """
    subject_or_404(db, payload.subject_id, current_user.id)
    try:
        ergebnis = grades_service.import_grades(
            db,
            current_user.id,
            payload.document_id,
            payload.keys,
            payload.subject_id,
            default_kind=_check_kind(payload.default_kind),
            default_weight=payload.default_weight,
            default_date=payload.default_date,
        )
    except GradeImportError as exc:
        raise _import_error(exc) from exc
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _unprocessable("Die Noten konnten nicht übernommen werden") from exc
    return ergebnis


# --------------------------------------------------------------------------
# Die einzelne Note - steht bewusst zuletzt
# --------------------------------------------------------------------------


@router.get("/{grade_id}", response_model=GradeRead)
def get_grade(grade_id: int, current_user: CurrentUser, db: DatabaseSession) -> GradeRead:
    """Ohne ``_kind``-Pruefung: eine gespeicherte Note hat immer eine bekannte Art."""
    return _read(owned_or_404(db, Grade, grade_id, current_user.id))


@router.patch("/{grade_id}", response_model=GradeRead)
def update_grade(
    grade_id: int,
    payload: GradeUpdate,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> GradeRead:
    """Nur die gesetzten Felder aendern.

    Ein fehlendes Feld heisst "unveraendert", ein explizites ``null`` loescht -
    genau wie bei den anderen Sammlungen. Deshalb lehnt das Schema ``null`` bei
    ``grade_value``, ``kind``, ``weight`` und ``graded_on`` ab: eine Note ohne
    Wert waere keine Note mehr.
    """
    item = owned_or_404(db, Grade, grade_id, current_user.id)
    werte = payload.model_dump(exclude_unset=True)
    if "subject_id" in werte:
        subject_or_404(db, werte["subject_id"], current_user.id)
    for key, value in werte.items():
        setattr(item, key, value)
    try:
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise _unprocessable("Die Note konnte nicht gespeichert werden") from exc
    return _read(item)


@router.delete("/{grade_id}", response_model=MessageResponse)
def delete_grade(grade_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    """Loescht die Note.

    Nicht rueckgaengig zu machen - und genau deshalb steht am Ende der Liste der
    Importweg, bei dem der Nutzer vorher jede Zeile sieht.
    """
    item = owned_or_404(db, Grade, grade_id, current_user.id)
    db.delete(item)
    db.commit()
    return MessageResponse(message="Note gelöscht")
