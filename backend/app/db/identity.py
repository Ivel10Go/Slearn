from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now

from .base import Base

if TYPE_CHECKING:
    from .academic import CalendarEvent, Exam, Homework, Lesson
    from .content import Document, LearningProgress, Topic
    from .planning import StudyPlan, StudyPlanTask, StudySession
    from .quiz import Quiz, QuizAnswer, QuizAttempt


JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    settings: Mapped[UserSettings | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    subjects: Mapped[list[Subject]] = relationship(back_populates="user", cascade="all, delete-orphan")
    teachers: Mapped[list[Teacher]] = relationship(back_populates="user", cascade="all, delete-orphan")
    rooms: Mapped[list[Room]] = relationship(back_populates="user", cascade="all, delete-orphan")
    lessons: Mapped[list[Lesson]] = relationship(back_populates="user", cascade="all, delete-orphan")
    homeworks: Mapped[list[Homework]] = relationship(back_populates="user", cascade="all, delete-orphan")
    exams: Mapped[list[Exam]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sync_sources: Mapped[list[SyncSource]] = relationship(back_populates="user", cascade="all, delete-orphan")
    calendar_events: Mapped[list[CalendarEvent]] = relationship(back_populates="user", cascade="all, delete-orphan")
    documents: Mapped[list[Document]] = relationship(back_populates="user", cascade="all, delete-orphan")
    topics: Mapped[list[Topic]] = relationship(back_populates="user", cascade="all, delete-orphan")
    study_plans: Mapped[list[StudyPlan]] = relationship(back_populates="user", cascade="all, delete-orphan")
    study_tasks: Mapped[list[StudyPlanTask]] = relationship(back_populates="user", cascade="all, delete-orphan")
    study_sessions: Mapped[list[StudySession]] = relationship(back_populates="user", cascade="all, delete-orphan")
    learning_progress: Mapped[list[LearningProgress]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    quizzes: Mapped[list[Quiz]] = relationship(back_populates="user", cascade="all, delete-orphan")
    quiz_attempts: Mapped[list[QuizAttempt]] = relationship(back_populates="user", cascade="all, delete-orphan")
    quiz_answers: Mapped[list[QuizAnswer]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserSettings(Base):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("user_id", name="uq_settings_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")
    theme: Mapped[str] = mapped_column(String(32), nullable=False, default="system", server_default="system")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    preferences: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="settings")


class Teacher(Base):
    __tablename__ = "teachers"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_teachers_user_name"),
        UniqueConstraint("user_id", "source", "external_id", name="uq_teachers_user_source_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="teachers")
    subjects: Mapped[list[Subject]] = relationship(back_populates="teacher")
    lessons: Mapped[list[Lesson]] = relationship(back_populates="teacher")


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_rooms_user_name"),
        UniqueConstraint("user_id", "source", "external_id", name="uq_rooms_user_source_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    building: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="rooms")
    lessons: Mapped[list[Lesson]] = relationship(back_populates="room")


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_subjects_user_name"),
        UniqueConstraint("user_id", "source", "external_id", name="uq_subjects_user_source_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="#3B82F6", server_default="#3B82F6")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="subjects")
    teacher: Mapped[Teacher | None] = relationship(back_populates="subjects")
    lessons: Mapped[list[Lesson]] = relationship(back_populates="subject")
    homeworks: Mapped[list[Homework]] = relationship(back_populates="subject")
    exams: Mapped[list[Exam]] = relationship(back_populates="subject")
    documents: Mapped[list[Document]] = relationship(back_populates="subject")
    topics: Mapped[list[Topic]] = relationship(back_populates="subject")
    quizzes: Mapped[list[Quiz]] = relationship(back_populates="subject")


class SyncSource(Base):
    __tablename__ = "sync_sources"
    __table_args__ = (UniqueConstraint("user_id", "source_type", "name", name="uq_sync_sources_user_type_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    configuration: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE, nullable=True)
    credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="configured", server_default="configured")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="sync_sources")
    lessons: Mapped[list[Lesson]] = relationship(back_populates="sync_source")
    homeworks: Mapped[list[Homework]] = relationship(back_populates="sync_source")
    exams: Mapped[list[Exam]] = relationship(back_populates="sync_source")
    calendar_events: Mapped[list[CalendarEvent]] = relationship(back_populates="sync_source")
    documents: Mapped[list[Document]] = relationship(back_populates="sync_source")
