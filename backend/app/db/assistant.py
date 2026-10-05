"""Das Gedaechtnis des Assistenten.

Eine Zeile je Satz, den der Assistent sich ueber die Lernende merkt ("lernt
Vokabeln lieber morgens", "Schwaeche: Bruchrechnen"). Das ist das ganze
"lernt an dir": **kein** Feintuning und keine Gewichte, sondern Saetze, die bei
jeder Anfrage im Prompt stehen. Der Grund ist Nachvollziehbarkeit - jeder Satz
ist in der Oberflaeche sichtbar und loeschbar, und ein lokales Modell laesst
sich auf einem Schulrechner ohnehin nicht nachtrainieren.

``source`` unterscheidet, wer den Satz geschrieben hat: ``assistant`` (das
Modell hat ``MERKE:`` geantwortet) oder ``manual`` (von Hand eingetragen).

``TrainingExample`` ist die Vorstufe fuer echtes Feintuning (LoRA je Nutzer):
mit Freigabe wird jede Modellantwort samt Prompt abgelegt, und was die Lernende
danach tut - Vorschlag eintragen, verwerfen, Notiz loeschen, Daumen - wird als
**Tatsache** daneben gespeichert, nicht als fertiges Urteil. Das Urteil
(``label``) rechnet ``app/services/training.py`` daraus; so ueberschreibt ein
spaeteres Ereignis kein frueheres.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
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
    #: Die Antwort, aus der der Satz stammt - nur mit Trainingsfreigabe gesetzt.
    #: Wird der Satz geloescht, ist das ein Hinweis, dass die Antwort daneben lag.
    training_example_id: Mapped[int | None] = mapped_column(
        ForeignKey("training_examples.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )


RATING_GOOD: Final[str] = "good"
RATING_BAD: Final[str] = "bad"


class TrainingExample(Base):
    __tablename__ = "training_examples"
    __table_args__ = (
        CheckConstraint("user_rating IS NULL OR user_rating IN ('good', 'bad')", name="training_examples_known_rating"),
        CheckConstraint("rejected_actions >= 0", name="training_examples_rejected_non_negative"),
        Index("ix_training_examples_user", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    #: Genau das, was das Modell bekommen hat - Systemprompt und Nutzerprompt.
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    #: Der Rohtext des Modells, **vor** dem Bereinigen (mit AKTION/MERKE-Zeilen).
    completion: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: Das Backend hat das Datum eines Vorschlags aus dem Satz korrigiert: die
    #: Rohantwort lag also daneben, auch wenn der Vorschlag danach stimmte.
    server_adjusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    #: Eingetragene Vorschlaege, so wie sie eingetragen wurden (Zielwerte).
    applied_actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    rejected_actions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    memory_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    user_rating: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
