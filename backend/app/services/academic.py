"""Gemeinsame Aufbereitung der Fächer-, Lehrer- und Stammdaten für alle Ausgaben."""

from app.db.entities import Lesson, Subject

UNKNOWN_SUBJECT_NAME = "Unbekannt"
UNKNOWN_SUBJECT_COLOR = "#64748B"


def subject_values(subject: Subject | None) -> tuple[str, str]:
    """Name und Farbe eines Faches mit sprechenden Ersatzwerten."""
    if subject is None:
        return UNKNOWN_SUBJECT_NAME, UNKNOWN_SUBJECT_COLOR
    return subject.name, subject.color


def lesson_teacher_name(lesson: Lesson) -> str | None:
    """Der Lehrer der Einheit, ersatzweise der im Fach hinterlegte Lehrer.

    Beim WebUntis-Import tragen manche Einheiten keinen eigenen Lehrer, dann
    bleibt nur der fachbezogene. Ohne beide bleibt es ``None``, damit die
    Oberfläche "Nicht angegeben" anzeigen kann.
    """
    if lesson.teacher is not None:
        return lesson.teacher.name
    if lesson.subject is not None and lesson.subject.teacher is not None:
        return lesson.subject.teacher.name
    return None
