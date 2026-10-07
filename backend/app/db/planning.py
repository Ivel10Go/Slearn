from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
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
from .content import Topic
from .identity import User


class StudyPlan(Base):
    __tablename__ = "study_plans"
    __table_args__ = (Index("ix_study_plans_user_status", "user_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    starts_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="study_plans")
    tasks: Mapped[list[StudyPlanTask]] = relationship(
        back_populates="study_plan", cascade="all, delete-orphan", order_by="StudyPlanTask.order_index"
    )
    series: Mapped[list[StudyTaskSeries]] = relationship(
        back_populates="study_plan", cascade="all, delete-orphan", order_by="StudyTaskSeries.id"
    )


class StudyPlanTask(Base):
    __tablename__ = "study_plan_tasks"
    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="study_plan_tasks_positive_duration"),
        CheckConstraint("order_index >= 0", name="study_plan_tasks_nonnegative_order"),
        CheckConstraint("priority >= 0", name="study_plan_tasks_nonnegative_priority"),
        UniqueConstraint("study_plan_id", "order_index", name="uq_study_plan_tasks_plan_order"),
        Index("ix_study_plan_tasks_user_schedule", "user_id", "scheduled_for"),
        Index("ix_study_plan_tasks_user_priority", "user_id", "priority"),
        Index("ix_study_plan_tasks_user_due", "user_id", "due_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    study_plan_id: Mapped[int] = mapped_column(ForeignKey("study_plans.id", ondelete="CASCADE"), nullable=False)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    exam_id: Mapped[int | None] = mapped_column(ForeignKey("exams.id", ondelete="SET NULL"), nullable=True)
    series_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_task_series.id", ondelete="SET NULL"), nullable=True
    )
    # Gesetzt vom Wochenplaner: der Block, in dem diese Hausaufgabe erledigt
    # wird. Haelt den Planer idempotent und erledigt die Hausaufgabe mit.
    homework_id: Mapped[int | None] = mapped_column(ForeignKey("homework.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="study_tasks")
    study_plan: Mapped[StudyPlan] = relationship(back_populates="tasks")
    topic: Mapped[Topic | None] = relationship(back_populates="study_tasks")
    series: Mapped[StudyTaskSeries | None] = relationship(back_populates="tasks")
    sessions: Mapped[list[StudySession]] = relationship(back_populates="task", cascade="all, delete-orphan")
    prerequisites: Mapped[list[StudyPlanTask]] = relationship(
        secondary="study_task_dependencies",
        primaryjoin="StudyPlanTask.id == foreign(StudyTaskDependency.task_id)",
        secondaryjoin="StudyPlanTask.id == foreign(StudyTaskDependency.depends_on_task_id)",
        back_populates="dependents",
        order_by="StudyPlanTask.id",
    )
    dependents: Mapped[list[StudyPlanTask]] = relationship(
        secondary="study_task_dependencies",
        primaryjoin="StudyPlanTask.id == foreign(StudyTaskDependency.depends_on_task_id)",
        secondaryjoin="StudyPlanTask.id == foreign(StudyTaskDependency.task_id)",
        back_populates="prerequisites",
        order_by="StudyPlanTask.id",
    )


class StudyTaskSeries(Base):
    """Die Vorlage einer wiederkehrenden Aufgabe, etwa Vokabeln jeden Montag.

    Die Serie erzeugt immer nur die naechste Occurrence, wenn eine Aufgabe
    abgeschlossen wird. Damit bleibt die Kette im Plan sichtbar und ein
    abgebrochener Durchlauf loescht keine Historie.
    """

    __tablename__ = "study_task_series"
    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="study_task_series_positive_duration"),
        CheckConstraint("priority >= 0", name="study_task_series_nonnegative_priority"),
        CheckConstraint("occurrences >= 0", name="study_task_series_nonnegative_occurrences"),
        CheckConstraint("occurrence_limit IS NULL OR occurrence_limit > 0", name="study_task_series_positive_limit"),
        Index("ix_study_task_series_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    study_plan_id: Mapped[int] = mapped_column(ForeignKey("study_plans.id", ondelete="CASCADE"), nullable=False)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    recurrence: Mapped[str] = mapped_column(String(16), nullable=False, default="weekly", server_default="weekly")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=20, server_default="20")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    starts_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # `next_due_on` ist die Occurrence, die als naechstes erzeugt wird. None
    # heisst: die Serie ist zu Ende oder wurde abgebrochen.
    next_due_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Obergrenze der Occurrences. `until` und `occurrence_limit` duerfen
    # beide gesetzt sein; die Serie endet, sobald eines von beiden
    # erreicht ist. Die Spalte heisst nicht `limit`, weil das ein
    # reserviertes SQL-Schlüsselwort ist.
    occurrence_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="study_task_series")
    study_plan: Mapped[StudyPlan] = relationship(back_populates="series")
    topic: Mapped[Topic | None] = relationship(back_populates="study_task_series")
    tasks: Mapped[list[StudyPlanTask]] = relationship(back_populates="series")


class StudyTaskDependency(Base):
    """Vorbedingung zwischen zwei Aufgaben: ``task_id`` wartet auf ``depends_on_task_id``.

    Der Primaerschluessel ist nur das Paar, denn zwei Aufgaben koennen
    hoechstens eine Abhaengigkeit haben. ``user_id`` ist eine normale Spalte
    und dient der Nutzerisolation: so muss die Abhaengigkeitssuche nie auf
    eine fremde Aufgabe hoffen.
    """

    __tablename__ = "study_task_dependencies"
    __table_args__ = (
        CheckConstraint("task_id <> depends_on_task_id", name="study_task_dependencies_no_self"),
        Index("ix_study_task_dependencies_user_task", "user_id", "task_id"),
        Index("ix_study_task_dependencies_user_depends_on", "user_id", "depends_on_task_id"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[int] = mapped_column(ForeignKey("study_plan_tasks.id", ondelete="CASCADE"), primary_key=True)
    depends_on_task_id: Mapped[int] = mapped_column(
        ForeignKey("study_plan_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )


class StudySession(Base):
    __tablename__ = "study_sessions"
    __table_args__ = (
        CheckConstraint("duration_minutes IS NULL OR duration_minutes >= 0", name="study_sessions_nonnegative_duration"),
        CheckConstraint("completed_at IS NULL OR completed_at >= started_at", name="study_sessions_completion_order"),
        Index("ix_study_sessions_user_started", "user_id", "started_at"),
        Index(
            "uq_study_sessions_user_task_open",
            "user_id",
            "task_id",
            unique=True,
            sqlite_where=text("completed_at IS NULL"),
            postgresql_where=text("completed_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[int] = mapped_column(ForeignKey("study_plan_tasks.id", ondelete="CASCADE"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="study_sessions")
    task: Mapped[StudyPlanTask] = relationship(back_populates="sessions")


__all__ = ["StudyPlan", "StudyPlanTask", "StudySession", "StudyTaskDependency", "StudyTaskSeries"]
