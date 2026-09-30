from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now

from .base import Base
from .identity import Room, Subject, SyncSource, Teacher, User

if TYPE_CHECKING:
    from .content import Document


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (
        CheckConstraint("end_time > start_time", name="lessons_end_after_start"),
        UniqueConstraint("user_id", "source", "external_id", name="uq_lessons_user_source_external"),
        Index("ix_lessons_user_start", "user_id", "start_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"), nullable=True)
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True)
    sync_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_sources.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(240), nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="lessons")
    subject: Mapped[Subject | None] = relationship(back_populates="lessons")
    teacher: Mapped[Teacher | None] = relationship(back_populates="lessons")
    room: Mapped[Room | None] = relationship(back_populates="lessons")
    sync_source: Mapped[SyncSource | None] = relationship(back_populates="lessons")


class Homework(Base):
    __tablename__ = "homework"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_id", name="uq_homework_user_source_external"),
        Index("ix_homework_user_due", "user_id", "due_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    sync_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_sources.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="homeworks")
    subject: Mapped[Subject | None] = relationship(back_populates="homeworks")
    sync_source: Mapped[SyncSource | None] = relationship(back_populates="homeworks")


class Exam(Base):
    __tablename__ = "exams"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_id", name="uq_exams_user_source_external"),
        Index("ix_exams_user_date", "user_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    sync_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_sources.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="exams")
    subject: Mapped[Subject | None] = relationship(back_populates="exams")
    sync_source: Mapped[SyncSource | None] = relationship(back_populates="exams")


class Grade(Base):
    """Eine einzelne Note: Fach, Wert, Art, Gewichtung, Datum und Kommentar.

    **Ein eigenes Modell, keine Erweiterung von ``Exam``.** Eine Klausur ist ein
    *Termin*, eine Note ist ein *Ergebnis* - eine abgegebene Arbeit hat kein
    Datum, an dem sie stattfindet, und eine Klausur ohne Ergebnis ist keine Note.
    Beide in eine Tabelle zu pressen hieße, jede zweite Spalte müsste NULL sein.

    Die Spalte heisst ``grade_value`` und nicht ``value``: ``value`` ist in
    mehreren SQL-Dialekten ein Schlüsselwort, und der Attributname ``value`` in
    einem Modul mit importierten SQLAlchemy-Typen ist genau die Art von Namen,
    die sich später irgendwo im Klassenrumpf auswertet statt im Attribut. Der
    Zusatz kostet ein Wort und nimmt das Risiko weg.

    ``grade_value`` ist ``Float`` und nicht ``Numeric``: die Skala hat höchstens
    eine Nachkommastelle, der Schnitt wird ohnehin auf zwei Stellen gerundet, und
    ``confidence`` und ``share_percent`` im Stack sind genauso ``Float``.

    ``graded_on`` ist ein **Kalendertag** und kein Zeitstempel. Eine Note hat
    keinen Zeitpunkt, an dem sie "gegeben" wurde, und ein erzwungener UTC-Mitternacht
    wäre eine Zeitangabe, die es nicht gibt - dieselbe Entscheidung wie bei
    ``next_review_on`` im Lernfortschritt.

    ``source``/``external_id`` machen den Import aus einem Dokument idempotent:
    derselbe Text ergibt denselben Schluessel, ein zweiter Import legt also
    nichts doppelt an. Von Hand erfasste Noten haben ``external_id = NULL``,
    und NULL-Werte sind in SQL nicht gleich - mehrere davon sind also erlaubt.
    """

    __tablename__ = "grades"
    __table_args__ = (
        CheckConstraint("weight > 0", name="grades_positive_weight"),
        CheckConstraint("grade_value > 0 AND grade_value <= 6", name="grades_value_in_scale"),
        CheckConstraint(
            "kind IN ('written_exam', 'test', 'oral')",
            name="grades_known_kind",
        ),
        UniqueConstraint("user_id", "source", "external_id", name="uq_grades_user_source_external"),
        Index("ix_grades_user_subject", "user_id", "subject_id"),
        Index("ix_grades_user_date", "user_id", "graded_on"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Fach und Dokument duerfen verschwinden, ohne die Note zu loeschen: eine
    # geloeschte Note waere eine aus der Historie gerissene Zahl.
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    grade_value: Mapped[float] = mapped_column(Float, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="written_exam", server_default="written_exam")
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    graded_on: Mapped[date] = mapped_column(Date, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="grades")
    subject: Mapped[Subject | None] = relationship(back_populates="grades")
    document: Mapped["Document | None"] = relationship(back_populates="grades")


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        CheckConstraint("end_time >= start_time", name="calendar_events_end_after_start"),
        UniqueConstraint("user_id", "source", "external_id", name="uq_calendar_events_user_source_external"),
        Index("ix_calendar_events_user_start", "user_id", "start_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    sync_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_sources.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="calendar_events")
    sync_source: Mapped[SyncSource | None] = relationship(back_populates="calendar_events")


__all__ = ["CalendarEvent", "Exam", "Grade", "Homework", "Lesson"]
