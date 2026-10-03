"""Das Gedaechtnis des Assistenten.

Eine Zeile je Satz, den der Assistent sich ueber die Lernende merkt ("lernt
Vokabeln lieber morgens", "Schwaeche: Bruchrechnen"). Das ist das ganze
"lernt an dir": **kein** Feintuning und keine Gewichte, sondern Saetze, die bei
jeder Anfrage im Prompt stehen. Der Grund ist Nachvollziehbarkeit - jeder Satz
ist in der Oberflaeche sichtbar und loeschbar, und ein lokales Modell laesst
sich auf einem Schulrechner ohnehin nicht nachtrainieren.

``source`` unterscheidet, wer den Satz geschrieben hat: ``assistant`` (das
Modell hat ``MERKE:`` geantwortet) oder ``manual`` (von Hand eingetragen).
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now

from .base import Base

MEMORY_SOURCE_ASSISTANT: Final[str] = "assistant"
MEMORY_SOURCE_MANUAL: Final[str] = "manual"
MEMORY_MAX_CHARS: Final[int] = 400


class AssistantMemory(Base):
    __tablename__ = "assistant_memories"
    __table_args__ = (
        CheckConstraint("source IN ('assistant', 'manual')", name="assistant_memories_known_source"),
        Index("ix_assistant_memories_user", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(String(MEMORY_MAX_CHARS), nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MEMORY_SOURCE_ASSISTANT, server_default=MEMORY_SOURCE_ASSISTANT
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
