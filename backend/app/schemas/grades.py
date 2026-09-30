"""Noten unter ``/api/v1/grades``.

Die Liste ist an drei Stellen bewusst knapp gehalten:

* ``GradeKind`` ist ein geschlossenes Literal, und dieselben drei Werte stehen
  als Check-Constraint in der Migration. Eine vierte Art ist eine Aenderung an
  der Skala und keine Sache, die man per ``PATCH`` nachschiebt.
* ``grade_value`` ist eine Zahl zwischen 1,0 und 6,0. Die Datenbank klemmt
  genauso, und die Pruefklausel im Schreibschema faengt den Wert ab, bevor er
  eine Zeile wird - ein 422 mit Klartext ist brauchbarer als ein 409 aus einer
  Datenbank.
* ``graded_on`` ist ein **Kalendertag**. Ein Datum in der Zeitzone des Nutzers
  ist kein UTC-Moment, deshalb bleibt es hier ein ``date`` und wird nicht zu
  einem Zeitstempel mit Zeitzone umgedeutet.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import UTCResponseModel

# Die drei Arten, die ein Notenspiegel kennt. Ein geschlossenes Literal an
# **einer** Stelle, benutzt von jedem Schema hier: eine vierte Art waere sonst
# an drei Orten zu ergaenzen, und eine Stelle wuerde sie vergessen.
GradeKind = Literal["written_exam", "test", "oral"]

MAX_WEIGHT = 20
MAX_GRADE_COMMENT = 500
MAX_PROJECTED_WEIGHT = 100


def _reject_null_patch_values(payload: BaseModel, nullable_fields: set[str]) -> None:
    for field in payload.model_fields_set - nullable_fields:
        if getattr(payload, field) is None:
            raise ValueError(f"{field} must not be null")


class GradeCreate(BaseModel):
    """Eine Note von Hand.

    ``subject_id`` ist **Pflicht**, obwohl die Spalte NULL erlauben mag: ohne
    Fach gibt es keinen Fachschnitt, und der Schnitt je Fach ist der Zweck der
    ganzen Rechnung. Die Spalte bleibt nullable, damit eine geloeschte Fachzeile
    nicht die Historie mit nimmt.
    """

    model_config = ConfigDict(extra="forbid")

    subject_id: int
    grade_value: float = Field(gt=0, le=6)
    kind: GradeKind = "written_exam"
    weight: int = Field(default=1, ge=1, le=MAX_WEIGHT)
    graded_on: date
    comment: str | None = Field(default=None, max_length=MAX_GRADE_COMMENT)


class GradeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: int | None = None
    grade_value: float | None = Field(default=None, gt=0, le=6)
    kind: GradeKind | None = None
    weight: int | None = Field(default=None, ge=1, le=MAX_WEIGHT)
    graded_on: date | None = None
    comment: str | None = Field(default=None, max_length=MAX_GRADE_COMMENT)

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        _reject_null_patch_values(self, {"subject_id", "comment"})
        return self


class GradeRead(UTCResponseModel):
    """Eine Note, wie die Oberflaeche sie anzeigt."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_id: int | None = None
    subject_name: str | None = None
    grade_value: float
    kind: GradeKind
    weight: int
    graded_on: date
    comment: str | None = None
    source: str
    document_id: int | None = None


class GradeAverageRead(UTCResponseModel):
    """Ein Schnitt: Zahl, Anzahl der Noten, Gewicht und die Bestehensaussage.

    ``passed`` ist ein Wahrheitswert und kein Satz. Das Wort kommt in der
    Oberflaeche, weil es dort hingeschaerft werden kann ("noch nicht bestanden,
    aber erreichbar") - die Rechnung bleibt beim Wahrheitswert.
    """

    subject_id: int | None = None
    subject_name: str
    average: float = Field(ge=0)
    count: int = Field(ge=0)
    weight: int = Field(ge=0)
    passed: bool


class GradeTrendPointRead(UTCResponseModel):
    """Ein Punkt der Verlaufslinie: der Schnitt **nach** diesem Tag.

    ``count`` und ``weight`` sind kumuliert, nicht die des Tages. Die Linie
    braucht die Zahl, und die Unterschrift darunter soll sagen koennen, auf wie
    viel sie sich stuetzt.
    """

    graded_on: date
    average: float = Field(ge=0)
    count: int = Field(ge=1)
    weight: int = Field(ge=1)


class GradeProjectionRead(UTCResponseModel):
    """Die Hochrechnung: was ab jetzt noch kommen muss.

    ``needed_average`` ist auf 1,0 bis 6,0 geklemmt - ausserhalb der Skala gibt
    es keine Note. ``still_possible`` beantwortet die Frage, auf die es
    ankommt, und ist auch dann ``False``, wenn gar keine Noten da sind: es gibt
    dann nichts, worauf sich etwas rechnen laesst.
    """

    current_average: float | None = None
    passing_grade: float
    projected_weight: int = Field(ge=0)
    needed_average: float | None = None
    best_possible_average: float | None = None
    still_possible: bool


class GradeSummary(UTCResponseModel):
    """Schnitt, Verlauf und Hochrechnung in einer Antwort.

    Die Karte braucht alle drei fuer denselben Gegenstand: der Schnitt ist das
    Ergebnis, der Verlauf die Bewegung, die Hochrechnung die naechste Frage. Drei
    Aufrufe waeren drei Ladezustaende fuer eine Karte.
    """

    passing_grade: float
    generated_at: datetime
    overall: GradeAverageRead
    by_subject: list[GradeAverageRead] = Field(default_factory=list)
    trend: list[GradeTrendPointRead] = Field(default_factory=list)
    total_count: int = Field(ge=0)
    projection: GradeProjectionRead


class GradeSharingWrite(BaseModel):
    """Die ausdrueckliche Zustimmung, ob Noten den KI-Dienst erreichen duerfen.

    ``enabled`` ist Pflicht und hat keinen Default: ein fehlendes Feld darf keine
    bestehende Entscheidung versehentlich loeschen, und ein Default von ``True``
    waere genau die stille Uebertragung, die hier nicht stattfinden soll.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class GradeSharingRead(UTCResponseModel):
    """Der Zustand der Freigabe, mit dem Ort, an dem sie steht."""

    share_grades_with_ai: bool
    preference_key: str
    uses: list[str] = Field(default_factory=list)


class GradeImportCandidateRead(UTCResponseModel):
    """Eine Zeile aus dem Dokument, so wie der Server sie liest."""

    key: str
    line_number: int = Field(ge=1)
    line: str
    value: float = Field(gt=0, le=6)
    kind: GradeKind
    graded_on: date
    weight: int = Field(ge=1)
    comment: str | None = None
    subject_id: int | None = None
    subject_name: str | None = None
    """``False`` heisst: das Fach stand nicht in der Zeile, es kam aus der Vorgabe."""
    subject_recognized: bool
    already_imported: bool


class GradeImportPreview(UTCResponseModel):
    """Die Vorschau einer Uebernahme, bevor irgendetwas angelegt wird."""

    document_id: int
    filename: str
    subject_id: int | None = None
    default_kind: GradeKind = "written_exam"
    default_weight: int = Field(ge=1, le=MAX_WEIGHT)
    default_date: date | None = None
    entries: list[GradeImportCandidateRead] = Field(default_factory=list)


class GradeImportRequest(BaseModel):
    """Die bestaetigten Zeilen einer Uebernahme.

    Nur die **Schluessel**, keine Werte: der Server sucht die Zeilen selbst und
    liest Note, Art, Datum und Fach aus dem Dokument. Eine Oberflaeche, die hier
    Zahlen schicken koennte, koennte Noten erfinden, die in keiner Zeile stehen.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: int
    keys: list[str] = Field(min_length=1, max_length=500)
    subject_id: int | None = None
    default_kind: GradeKind = "written_exam"
    default_weight: int = Field(default=1, ge=1, le=MAX_WEIGHT)
    default_date: date | None = None


class GradeImportResult(UTCResponseModel):
    """Was die Uebernahme angelegt hat.

    ``skipped`` sind Zeilen, die schon importiert waren. Sie sind kein Fehler -
    ein zweiter Klick auf "uebernehmen" soll ein No-Op sein und kein Duplikat.
    """

    document_id: int
    created: int = Field(ge=0)
    skipped: int = Field(ge=0)
    created_ids: list[int] = Field(default_factory=list)
