from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now

from .base import Base
from .identity import Subject, SyncSource, User

if TYPE_CHECKING:
    from .planning import StudyPlanTask
    from .quiz import Quiz, QuizQuestion


JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
VECTOR_TYPE = Numeric(precision=1536).with_variant(Vector(dim=1536), "postgresql")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_id", name="uq_documents_user_source_external"),
        Index("ix_documents_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    sync_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_sources.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    extra_data: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="documents")
    subject: Mapped[Subject | None] = relationship(back_populates="documents")
    sync_source: Mapped[SyncSource | None] = relationship(back_populates="documents")
    topics: Mapped[list[Topic]] = relationship(secondary="topic_documents", back_populates="documents")
    chunks: Mapped[list[DocumentChunk]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_index"),
        Index("ix_document_chunks_user_document", "user_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    document: Mapped[Document] = relationship(back_populates="chunks")


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint("user_id", "subject_id", "name", name="uq_topics_user_subject_name"),
        Index("ix_topics_user_subject", "user_id", "subject_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="topics")
    subject: Mapped[Subject | None] = relationship(back_populates="topics")
    documents: Mapped[list[Document]] = relationship(secondary="topic_documents", back_populates="topics")
    study_tasks: Mapped[list[StudyPlanTask]] = relationship(back_populates="topic")
    quizzes: Mapped[list[Quiz]] = relationship(back_populates="topic")
    quiz_questions: Mapped[list[QuizQuestion]] = relationship(back_populates="topic")
    progress: Mapped[LearningProgress | None] = relationship(
        back_populates="topic", uselist=False, cascade="all, delete-orphan"
    )


class TopicDocument(Base):
    __tablename__ = "topic_documents"
    __table_args__ = (
        CheckConstraint("relevance IS NULL OR (relevance >= 0 AND relevance <= 1)", name="topic_documents_relevance_range"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(viewonly=True)
    topic: Mapped[Topic] = relationship(viewonly=True)
    document: Mapped[Document] = relationship(viewonly=True)


class LearningProgress(Base):
    __tablename__ = "learning_progress"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="learning_progress_confidence_range"),
        Index("ix_learning_progress_user_topic", "user_id", "topic_id"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="learning_progress")
    topic: Mapped[Topic] = relationship(back_populates="progress")


__all__ = ["Document", "DocumentChunk", "LearningProgress", "Topic", "TopicDocument"]
