"""Benachrichtigungen und Web-Push-Abonements.

Zwei Tabellen mit zwei sehr verschiedenen Aufgaben:

* ``notifications`` ist die **Erinnerung als Zeile**. Der planmaessige Lauf
  schreibt hier hinein, und die geoeffnete Seite liest von hier. Sie ist damit
  der Weg, der ohne Push-Dienst funktioniert - der lokale Fallback ist der
  Hauptpfad und nicht der Notbehelf.
* ``push_subscriptions`` ist ein **Abo pro Endgeraet**. Ein Nutzer kann am
  Laptop und am Handy angemeldet sein, also ist der Schluessel
  ``(user_id, endpoint)``: derselbe Browser liefert denselben Endpunkt, ein
  zweites Geraet einen anderen.

Kein Fremdschluessel auf Aufgaben oder Klausuren. ``target_id`` plus ``kind``
sagen, worauf sich die Zeile bezieht, aber die Zeile **überlebt** ihre Quelle:
eine geloeschte Klausur darf keine Erinnerung ins Leere zeigen, und eine
Foreign Key mit ``ON DELETE CASCADE`` wuerde genau das erreichen - die Zeile
waeche still verschwinden, nachdem der Nutzer sie gelesen hatte.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from .identity import User


class Notification(Base):
    """Eine erzeugte Erinnerung, noch ungezeigt.

    ``dedupe_key`` ist das Rueckgrat der Idempotenz und traegt den **lokalen
    Tag**: ``study_task_due:42:2026-09-30``. Damit ist der Schluessel aus dem
    Lauf, in dem er entstand, und ein zweiter Lauf am selben Tag erzeugt nichts
    Neues - auch dann nicht, wenn die Erinnerung erst nach einer Ruhezeit
    rausgeht und der Lauf sie an einem anderen Tag anlegt.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('study_task_due', 'exam_due')",
            name="notifications_known_kind",
        ),
        CheckConstraint(
            "push_status IN ('skipped', 'sent', 'failed')",
            name="notifications_known_push_status",
        ),
        # Das Rueckgrat der Idempotenz: ein Lauf, ein Tag, eine Zeile.
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe_key"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(String(400), nullable=False)
    # Ein Anker auf der Einzelseite, etwa `/#study-tasks`. Kein Pfad auf eine
    # eigene Route: die Navigation ist eine Seite mit Ankern plus `/settings`.
    url: Mapped[str] = mapped_column(String(240), nullable=False)
    # Die Aufgaben- oder Klausur-Id, auf die sich die Zeile bezieht. Kein
    # Fremdschluessel, siehe Modul-Docstring.
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)
    push_status: Mapped[str] = mapped_column(String(16), nullable=False, default="skipped", server_default="skipped")
    # Nur gesetzt, wenn ein Push-Dienst die Nachricht **angenommen** hat. Solange
    # es `None` ist, holt die offene Seite die Zeile ueber den lokalen Weg ab.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped["User"] = relationship(back_populates="notifications")


class PushSubscription(Base):
    """Ein Web-Push-Abonnement eines Endgeraets.

    ``p256dh`` und ``auth`` sind die Schluessel des Geraets, mit denen der
    Nutzer die Nutzlast verschluesselt - ohne sie kann der Server nichts
    zustellen, das der Browser auch nur lesen koennte. Sie sind deshalb
    Pflichtspalten und nicht "Details".
    """

    __tablename__ = "push_subscriptions"
    __table_args__ = (
        # Der Endpunkt **ist** die Identitaet des Geraets. Ein zweiler
        # Anmeldeversuch ersetzt die Zeile, statt eine zweite anzulegen.
        UniqueConstraint("user_id", "endpoint", name="uq_push_subscriptions_user_endpoint"),
        Index("ix_push_subscriptions_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(320), nullable=True)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped["User"] = relationship(back_populates="push_subscriptions")


__all__ = ["Notification", "PushSubscription"]
