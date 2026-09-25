from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now

from .base import Base
from .content import Topic
from .identity import Subject, User

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class Quiz(Base):
    __tablename__ = "quizzes"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_id", name="uq_quizzes_user_source_external"),
        Index("ix_quizzes_user_subject", "user_id", "subject_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty: Mapped[str] = mapped_column(String(32), nullable=False, default="medium", server_default="medium")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="quizzes")
    subject: Mapped[Subject | None] = relationship(back_populates="quizzes")
    topic: Mapped[Topic | None] = relationship(back_populates="quizzes")
    questions: Mapped[list[QuizQuestion]] = relationship(
        back_populates="quiz", cascade="all, delete-orphan", order_by="QuizQuestion.position"
    )
    attempts: Mapped[list[QuizAttempt]] = relationship(back_populates="quiz", cascade="all, delete-orphan")


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    __table_args__ = (
        CheckConstraint("position >= 0", name="quiz_questions_nonnegative_position"),
        UniqueConstraint("quiz_id", "position", name="uq_quiz_questions_quiz_position"),
        Index("ix_quiz_questions_user_topic", "user_id", "topic_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[Any]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    correct_answer: Mapped[Any | None] = mapped_column(JSON_TYPE, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    quiz: Mapped[Quiz] = relationship(back_populates="questions")
    topic: Mapped[Topic | None] = relationship(back_populates="quiz_questions")
    answers: Mapped[list[QuizAnswer]] = relationship(back_populates="question")


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    __table_args__ = (
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="quiz_attempts_score_range"),
        CheckConstraint("completed_at IS NULL OR completed_at >= started_at", name="quiz_attempts_completion_order"),
        Index("ix_quiz_attempts_user_started", "user_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="quiz_attempts")
    quiz: Mapped[Quiz] = relationship(back_populates="attempts")
    answers: Mapped[list[QuizAnswer]] = relationship(back_populates="attempt", cascade="all, delete-orphan")


class QuizAnswer(Base):
    __tablename__ = "quiz_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_quiz_answers_attempt_question"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("quiz_attempts.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[int] = mapped_column(ForeignKey("quiz_questions.id", ondelete="CASCADE"), nullable=False)
    answer: Mapped[Any | None] = mapped_column(JSON_TYPE, nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="quiz_answers")
    attempt: Mapped[QuizAttempt] = relationship(back_populates="answers")
    question: Mapped[QuizQuestion] = relationship(back_populates="answers")


__all__ = ["Quiz", "QuizAnswer", "QuizAttempt", "QuizQuestion"]
