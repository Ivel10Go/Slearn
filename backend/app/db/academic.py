from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
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


__all__ = ["CalendarEvent", "Exam", "Homework", "Lesson"]
