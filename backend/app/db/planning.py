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


class StudyPlanTask(Base):
    __tablename__ = "study_plan_tasks"
    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="study_plan_tasks_positive_duration"),
        CheckConstraint("order_index >= 0", name="study_plan_tasks_nonnegative_order"),
        UniqueConstraint("study_plan_id", "order_index", name="uq_study_plan_tasks_plan_order"),
        Index("ix_study_plan_tasks_user_schedule", "user_id", "scheduled_for"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    study_plan_id: Mapped[int] = mapped_column(ForeignKey("study_plans.id", ondelete="CASCADE"), nullable=False)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    sessions: Mapped[list[StudySession]] = relationship(back_populates="task", cascade="all, delete-orphan")


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


__all__ = ["StudyPlan", "StudyPlanTask", "StudySession"]
