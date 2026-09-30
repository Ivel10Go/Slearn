"""Punkte und Auszeichnungen.

Zwei Tabellen, weil sie zwei verschiedene Fragen beantworten:

``reward_entries``
    Das **Punktekonto**. Eine Zeile je verguetetem Ereignis, und die Summe der
    Punkte ueber alle Zeilen ist der Stand. Kein Zaehler in einer eigenen Spalte,
    weil ein Zaehler die Frage "wann wurde wie viel gutgeschrieben" nicht
    beantworten kann - und weil er genau die Idempotenz verliert, die hier
    Pflicht ist.

``reward_badges``
    Die **Auszeichnungen**. Eine Auszeichnung ist kein Abzeichen, sondern ein
    Datenobjekt: eine Bedingung, ein Fortschritt darauf und ein Zeitpunkt. Sie
    gehoert ins Modell und nicht in die UI-Badge-Komponente, die nur einen
    fertigen Zustand darstellen kann. Siehe `app/services/rewards.py` fuer den
    Katalog und die Regeln.

**Die Eindeutigkeit sitzt in zwei Constraints, und beide sind noetig.**

``uq_reward_entries_user_kind_event``
    Eine Zeile je ``(Nutzer, Art, Ereignisschluessel)``. Der Schluessel traegt
    das *Ereignis*, nicht den Zeitpunkt: ``task:41`` oder
    ``subject-day:7:2026-09-30``. Damit ist der zweifache Aufruf von
    ``POST /complete`` mit derselben Aufgabe keine Frage des Zufalls, sondern
    eine, die die Datenbank nicht beantworten kann. Ohne diese Zeile waere ein
    reiner Zaehler die einzige Moeglichkeit, und der gliche beim zweiten Klick.

``uq_reward_badges_user_code_scope``
    Eine Zeile je ``(Nutzer, Code, Geltungsbereich)``. Der Bereich ist eine
    eigene Spalte und **kein** ``NULL``-Fremdschluessel, weil SQL zwei ``NULL``
    nicht gleichsetzt: eine Eindeutigkeit auf ``(user_id, code, subject_id)``
    wuerde zwei globale Auszeichnungen mit ``subject_id IS NULL`` zulassen.
    Das ist dieselbe Falle umgekehrt wie beim Paar-Primaerschluessel von
    ``study_task_dependencies``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now

from .base import Base
from .identity import Subject, User

if TYPE_CHECKING:
    from .content import Topic

#: Die zwei Arten einer Buchung. Beide sind an ein **Ereignis** gebunden, nicht an
#: eine Uhrzeit - siehe `uq_reward_entries_user_kind_event`.
REWARD_KIND_TASK: Final[str] = "task_completed"
REWARD_KIND_SUBJECT_DAY: Final[str] = "subject_day"
REWARD_KINDS: Final[tuple[str, ...]] = (REWARD_KIND_TASK, REWARD_KIND_SUBJECT_DAY)

#: Geltungsbereich einer Auszeichnung, wenn sie nicht an einem Fach oder Thema
#: haengt. Als Spaltenwert und nicht als `None` im Fremdschluessel, weil
#: `NULL != NULL`.
SCOPE_GLOBAL: Final[str] = "global"


class RewardEntry(Base):
    """Eine gebuchte Lernhandlung: einmal gezahlt, nachvollziehbar, idempotent.

    ``points`` darf **0** sein. Der Fall ist nicht theoretisch: ist das Tageskonto
    schon gefuellt, wird weiter gebucht, aber nicht mehr ausgezahlt. Eine Zeile
    mit 0 Punkten ist ehrlicher als eine Luecke - sie sagt, dass gelernt wurde
    und dass heute nichts mehr auszuzahlen war.

    ``minutes`` ist bei einer Aufgabe 0 und bei einem Fachtag die Zahl der
    angerechneten Minuten dieses Tages in diesem Fach. Die Spalte ist nicht nur
    eine Anzeige: sie ist der Behaelter, aus dem sich der groesste Tageswert je
    Fach fuer die Auszeichnung "Fuenfzig Minuten an einem Fachtag" berechnet, ohne
    dass man die Historie der Lernsessions noch einmal durchlaufen muss.

    Fach und Thema duerfen verschwinden, ohne die Buchung zu loeschen: eine
    geloeschte Aufgabe nimmt nicht die Tatsache weg, dass an diesem Tag gelernt
    wurde. Ohne Fach fuehrt der Weg von der Session zum Fach ueber
    ``StudyPlanTask.topic_id -> Topic.subject_id``, und genau diese Kette bricht
    weg, wenn das Thema geloescht wird.
    """

    __tablename__ = "reward_entries"
    __table_args__ = (
        CheckConstraint("points >= 0", name="reward_entries_nonnegative_points"),
        CheckConstraint("minutes >= 0", name="reward_entries_nonnegative_minutes"),
        CheckConstraint(
            "kind IN ('task_completed', 'subject_day')",
            name="reward_entries_known_kind",
        ),
        UniqueConstraint("user_id", "kind", "event_key", name="uq_reward_entries_user_kind_event"),
        Index("ix_reward_entries_user_day", "user_id", "earned_on"),
        Index("ix_reward_entries_user_subject", "user_id", "subject_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    #: ``task_completed`` oder ``subject_day``. Nicht frei erfunden werden.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Der Schluessel des Ereignisses: ``task:41`` bzw. ``subject-day:7:2026-09-30``.
    event_key: Mapped[str] = mapped_column(String(120), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    #: Der deutsche Klartext, der neben dem Betrag steht. Ohne ihn waere eine
    #: Buchung eine Zahl ohne Herkunft.
    detail: Mapped[str] = mapped_column(String(240), nullable=False)
    #: Der Kalendertag in der Zeitzone des Nutzers, kein Zeitstempel in UTC.
    earned_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="reward_entries")
    subject: Mapped[Subject | None] = relationship(back_populates="reward_entries")
    topic: Mapped["Topic | None"] = relationship(back_populates="reward_entries")


class RewardBadge(Base):
    """Eine Auszeichnung: Bedingung, Fortschritt und Zeitpunkt.

    **Warum eine eigene Tabelle und nicht die UI-Badge-Komponente.** Die
    Komponente in ``components/ui/badge.tsx`` ist eine reine Darstellung: sie
    bekommt einen ``variant`` und rendert eine Zeile. Eine Auszeichnung ist
    dagegen ein Objekt mit Lebenslauf - sie hat eine Bedingung, einen
    Fortschritt darauf, einen Zeitpunkt und die Frage, ob sie wiederholt
    erreichbar ist. Alles davon ist Zustand in der Datenbank und gehoert
    darum ins Modell; die Komponente darf es anzeigen und sonst nichts.

    **Einmalig oder wiederholt?** Das steht nicht in dieser Zeile, sondern im
    Katalog (``BADGE_DEFINITIONS`` in ``app/services/rewards.py``), denn es ist
    eine Eigenschaft der Bedingung und nicht des Nutzers. Fuer eine wiederholte
    Auszeichnung bekommt jeder Zeitraum seine **eigene Zeile**: der Wochenrhythmus
    traegt den Bereich ``week:2026-W40`` und bekommt in der naechsten Woche eine
    neue. Ein Zaehler in derselben Zeile waere die zweite Wahrheit, und eine
    zurueckgesetzte Zaehlung waere eine Frage, die man der Oberflaeche stellen
    muesste.

    ``progress`` und ``target`` tragen die Bedingung in Zahlen, damit die
    Oberflaeche keinen eigenen Zaehler fuehrt. Bei ``fach_durchgearbeitet`` ist
    ``target`` die Zahl aller nicht stornierten Aufgaben des Fachs - sie waechst
    also, wenn neue dazukommen.
    """

    __tablename__ = "reward_badges"
    __table_args__ = (
        CheckConstraint("progress >= 0", name="reward_badges_nonnegative_progress"),
        CheckConstraint("target >= 1", name="reward_badges_positive_target"),
        UniqueConstraint("user_id", "code", "scope", name="uq_reward_badges_user_code_scope"),
        Index("ix_reward_badges_user_code", "user_id", "code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    #: Der Code aus dem Katalog, unveraendert - eine aus Moeglichkeitkeit
    #: gewordene Auszeichnung traegt denselben Code wie ihr Ziel.
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    #: ``global``, ``subject:7``, ``topic:12`` oder ``week:2026-W40``.
    scope: Mapped[str] = mapped_column(String(64), nullable=False, server_default=SCOPE_GLOBAL)
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Wann sie erreicht wurde. ``None`` heisst: der Fortschritt reicht noch nicht.
    awarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(back_populates="reward_badges")
    subject: Mapped[Subject | None] = relationship(back_populates="reward_badges")
    topic: Mapped["Topic | None"] = relationship(back_populates="reward_badges")


__all__ = [
    "REWARD_KINDS",
    "REWARD_KIND_SUBJECT_DAY",
    "REWARD_KIND_TASK",
    "SCOPE_GLOBAL",
    "RewardBadge",
    "RewardEntry",
]
