"""Benachrichtigungen unter ``/api/v1/notifications``.

Die Liste ist an drei Stellen knapp gehalten, aus demselben Grund wie bei den
Noten:

* ``NotificationChannel`` ist ein **geschlossenes Literal**, und dieselben
  zwei Werte stehen in ``CHANNELS`` im Service und als Pruefklausel in der
  Migration. Ein dritter Kanal waere eine Aenderung an der Sache, nicht an
  einem Feld.
* ``reminder_time`` und die beiden Ruhezeiten sind **lokale** Uhrzeiten im
  Format ``HH:MM``. Kein ``datetime``: eine Erinnerung hat eine Uhrzeit am Tag,
  kein Datum, und ein Zeitstempel wuerde eine Zeitzone in die Einstellung
  hineinziehen, die dem Nutzer gehoert und nicht dem Server.
* ``start == end`` in der Ruhezeit wird abgelehnt (422). Es hiesse "immer" oder
  "nie", je nachdem, wie man es liest - beides ist ein Tippfehler und keiner
  von beiden ein Wunsch.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import UTCResponseModel

#: Die zwei Kanaele, an **einer** Stelle. ``CHANNELS`` im Service traegt dieselben
#: zwei Namen, und die Migration traegt dieselben zwei Werte als Pruefklausel.
NotificationChannel = Literal["study_tasks", "exams"]

NotificationKind = Literal["study_task_due", "exam_due"]
PushStatus = Literal["skipped", "sent", "failed"]

MAX_ENDPOINT_LENGTH = 2048
MAX_USER_AGENT_LENGTH = 320

#: ``HH:MM``. Als Text und nicht als ``time``, weil die Oberflaeche einen
#: ``<input type="time">``-Wert braucht und zurueckgibt - dort ist ``7:00``
#: ein gueltiger Wert, ``07:00`` nicht zwingend.
_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _check_clock(value: str) -> str:
    text = str(value or "").strip()
    if not _CLOCK.match(text):
        raise ValueError("Die Uhrzeit muss als HH:MM zwischen 00:00 und 23:59 stehen")
    return text


class QuietHoursWrite(BaseModel):
    """Die Ruhezeit als lokale Uhrzeiten.

    ``start > end`` ist erlaubt und ist der Normalfall einer Nacht: dann laeuft
    die Ruhezeit ueber Mitternacht. Die Rechnung steht in ``quiet_covers``.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    start: str = Field(default="22:00")
    end: str = Field(default="07:00")

    @field_validator("start", "end")
    @classmethod
    def normalize_clock(cls, value: str) -> str:
        return _check_clock(value)

    @model_validator(mode="after")
    def reject_empty_window(self) -> Self:
        if self.enabled and self.start == self.end:
            raise ValueError("Beginn und Ende der Ruhezeit dürfen nicht gleich sein")
        return self


class QuietHoursRead(BaseModel):
    enabled: bool
    start: str
    end: str
    #: Die Oberflaeche braucht das fuer den einen Satz, in dem sie die Ruhezeit
    #: erklaert. Zwei Stellen, die dasselbe rechnen, laufen auseinander.
    spans_midnight: bool


class ReminderPreferencesWrite(BaseModel):
    """Ein PATCH auf die Erinnerungseinstellungen.

    Jedes Feld ist optional und **fehlend heisst unveraendert**. Der
    Hauptschalter ``enabled`` schreibt in die Spalte
    ``settings.notifications_enabled``; alles andere wandert in
    ``preferences["notifications"]``.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    channels: dict[NotificationChannel, bool] | None = None
    reminder_time: str | None = None
    quiet_hours: QuietHoursWrite | None = None

    @field_validator("reminder_time")
    @classmethod
    def normalize_reminder_time(cls, value: str | None) -> str | None:
        return None if value is None else _check_clock(value)

    @model_validator(mode="after")
    def reject_null_switches(self) -> Self:
        if self.enabled is None and "enabled" in self.model_fields_set:
            raise ValueError("enabled must not be null")
        return self


class ReminderPreferencesRead(BaseModel):
    enabled: bool
    channels: dict[str, bool]
    channel_labels: dict[str, str]
    channels_available: list[str]
    reminder_time: str
    quiet_hours: QuietHoursRead
    #: Wieviele Tage vor einer Klausur erinnert wird. Steht in der Antwort,
    #: damit die Oberflaeche nicht eine eigene Zahl erfindet.
    exam_lead_days: int
    timezone: str
    #: Der Schluessel in ``settings.preferences``, in dem das alles liegt. Der
    #: Text steht in der Oberflaeche, aber sie soll ihn nicht raten.
    preference_key: str
    summary: str


class PushSubscriptionWrite(BaseModel):
    """Ein Abonnement, wie es der Browser liefert.

    ``endpoint``, ``keys.p256dh`` und ``keys.auth`` kommen aus dem
    ``PushSubscription``-Objekt des Browsers. Der Schluessel ``keys`` wird
    hierher uebersetzt, damit die Zeile drei klare Spalten hat.
    """

    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=8, max_length=MAX_ENDPOINT_LENGTH)
    p256dh: str = Field(min_length=40, max_length=200)
    auth: str = Field(min_length=16, max_length=100)
    user_agent: str | None = Field(default=None, max_length=MAX_USER_AGENT_LENGTH)


class PushSubscriptionRead(UTCResponseModel):
    id: int
    endpoint_hint: str
    user_agent: str | None
    failures: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None
    created_at: datetime


class VapidKeyRead(BaseModel):
    """Was die Seite fuer ``pushManager.subscribe`` braucht.

    ``public_key`` ist bewusst optional: ein Server ohne VAPID-Schluesselpaar
    ist kein Fehler, sondern eine Auspraegung, in der der lokale Weg arbeitet.
    Deshalb ist die Antwort 200 mit ``configured: false`` und nicht 503.
    """

    configured: bool
    public_key: str | None
    subject: str | None


class NotificationRead(UTCResponseModel):
    id: int
    kind: NotificationKind
    title: str
    body: str
    url: str
    target_id: int
    push_status: PushStatus
    created_at: datetime
    read_at: datetime | None


class MarkReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[int] = Field(min_length=1, max_length=50)


class MarkReadResult(BaseModel):
    read: int


class ReminderRunRead(BaseModel):
    """Das Ergebnis eines Laufs - auch der, den ein Mensch ausgeloest hat."""

    considered: int
    created: int
    delivered: int
    push_failed: int
    #: Zeigt, ob der planmaessige Thread laeuft. Die Oberflaeche sagt das dem
    #: Nutzer, statt es zu vermuten.
    scheduler_running: bool
    interval_seconds: float
    outcomes: list["ReminderOutcomeRead"]
    message: str


class ReminderOutcomeRead(BaseModel):
    user_id: int
    created: int
    delivered: int
    push_failed: int
    push_attempted: bool
    message: str


ReminderRunRead.model_rebuild()
