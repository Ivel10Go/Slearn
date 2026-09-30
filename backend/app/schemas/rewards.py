"""Die Antwortformen von ``/api/v1/rewards``.

Zwei Dinge sind hier Vertrag und nicht Anzeige:

``RewardRules`` und ``RewardStep``
    Die Punktregel selbst: Punkte je erledigter Aufgabe, die Stufen eines
    Fachtags, die Obergrenzen. Sie gehen mit jeder Antwort mit, damit die Karte
    die Regel **aus den Zahlen erzeugt**, nach denen das Backend rechnet, und
    nicht aus einem zweiten Satz im Frontend. Das ist dieselbe Entscheidung wie
    bei ``confidence_steps`` im Lernfortschritt.

``RewardBadgeDefinition``
    Der Katalog der Auszeichnungen mit Bezeichnung, Erklaerung, Ziel, Einheit und
    der Angabe, ob sie wiederholt erreichbar ist. Auch diese Liste reist als
    Vertrag mit - sonst koennte das Frontend die Bedingung nicht als Text
    ausgeben und muesste sie raten.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import UTCResponseModel

#: Die zwei Arten einer Buchung. Geschlossen, weil die Datenbank denselben
#: Check-Constraint traegt und die Spalte sonst einen Wert annehmen koennte,
#: fuer den es keine Regel gibt.
RewardKind = Literal["task_completed", "subject_day"]

#: Der Geltungsbereich einer Auszeichnung, wie ihn die Spalte ``scope`` traegt.
#: ``global`` steht fuer "haengt an keinem Fach und keinem Thema"; die beiden
#: anderen Formen nennen die Id, damit zwei Bereiche desselben Codes getrennt
#: bleiben. Die Karte stellt das aus den Feldern dar und nicht aus diesem Text.
RewardScopeKind = Literal["global", "subject", "topic", "week"]


class RewardEntryRead(UTCResponseModel):
    """Eine gebuchte Lernhandlung.

    ``points`` kann 0 sein: an diesem Tag war das Tageskonto bereits gefuellt.
    Die Buchung steht trotzdem da, weil sie der Nachweis ist, dass gelernt wurde
    - eine Luecke waere keine Auskunft.

    ``earned_on`` ist ein **Kalendertag in der Zeitzone des Nutzers** und kein
    Zeitstempel, dieselbe Entscheidung wie bei ``next_review_on`` und
    ``graded_on``. ``earned_at`` ist der Zeitpunkt der Buchung.
    """

    id: int
    kind: RewardKind
    points: int = Field(ge=0)
    minutes: int = Field(ge=0)
    subject_id: int | None = None
    subject_name: str | None = None
    topic_id: int | None = None
    topic_name: str | None = None
    detail: str
    earned_on: date
    earned_at: datetime


class RewardBadgeRead(UTCResponseModel):
    """Eine Auszeichnung samt Bedingung, Fortschritt und Zeitpunkt.

    ``progress`` und ``target`` tragen die Bedingung in Zahlen; die Karte zeigt
    daraus einen Balken und einen Text, ohne selbst zu zaehlen. ``unit`` sagt,
    was gezaehlt wird, damit die Oberflaeche nicht je nach Code eine eigene
    Formulierung braucht.

    Eine **einmal** verdiente Auszeichnung friert bei ihrem Wert ein: faengt
    jemand spaeter eine neue Aufgabe in dasselbe Fach an, verschwindet die
    Auszeichnung nicht wieder. Genau das ist der Unterschied zu einer laufenden
    Anforderung, und der ist beabsichtigt - eine Belohnung, die man wieder
    verlieren kann, erzeugt Druck.
    """

    id: int
    code: str
    title: str
    description: str
    scope: str
    scope_kind: RewardScopeKind
    subject_id: int | None = None
    subject_name: str | None = None
    topic_id: int | None = None
    topic_name: str | None = None
    progress: int = Field(ge=0)
    target: int = Field(ge=1)
    awarded: bool
    awarded_at: datetime | None = None
    repeatable: bool
    unit: str
    #: Die Mehrzahl in der Form, die der Satz braucht: "Tagen" neben "Tag". Die
    #: deutsche Grammatik gehoert in den Katalog und nicht in den Browser.
    unit_plural: str


class RewardBadgeDefinition(UTCResponseModel):
    """Eine Auszeichnung, wie sie im Katalog steht - noch ohne Zustand.

    Der Katalog geht als Vertrag mit, damit die Oberflaeche die Erklaerung einer
    Bedingung anzeigen kann, auch fuer eine Auszeichnung, die der Nutzer noch
    nicht erreicht hat und fuer die deshalb noch keine Zeile existiert.
    """

    code: str
    title: str
    description: str
    target: int = Field(ge=1)
    unit: str
    unit_plural: str
    scope_kind: RewardScopeKind
    repeatable: bool


class RewardStep(UTCResponseModel):
    """Eine Stufe der Fachtag-Staffel: ab so vielen Minuten gibt es so viele Punkte."""

    minutes: int = Field(ge=1)
    points: int = Field(ge=1)


class RewardRules(UTCResponseModel):
    """Die Punktregel in Zahlen, damit die Karte sie nicht nachbauen muss.

    ``day_cap`` ist die Obergrenze fuer **einen** Kalendertag. Sie ist bewusst
    so groess und bewusst so vorhanden: die Menge der vergebenen Punkte muss
    gedeckelt sein, sonst wird aus dem Zaehlen der Sinn. Wird sie erreicht, wird
    weiter gebucht, aber nicht mehr ausgezahlt - der Eintrag steht dann mit 0
    Punkten da und sagt, warum.

    ``min_subject_day_minutes`` ist die Schwelle, ab der ein Fachtag ueberhaupt
    zaehlt. Darunter gibt es keine Buchung und keinen Balken.
    """

    task_points: int = Field(ge=1)
    day_cap: int = Field(ge=1)
    subject_day_cap: int = Field(ge=1)
    min_subject_day_minutes: int = Field(ge=1)
    subject_day_steps: list[RewardStep]


class RewardSummary(UTCResponseModel):
    """Die Zahlen des Kontos.

    ``today_points`` gegen ``today_cap`` ist die einzige Stelle, an der die
    Obergrenze sichtbar wird. Ohne sie waere eine Zahl, die nicht weiterwaechst,
    eine Enttaeuschung statt einer Regel.
    """

    generated_at: datetime
    timezone: str
    today_points: int = Field(ge=0)
    today_cap: int = Field(ge=1)
    week_points: int = Field(ge=0)
    total_points: int = Field(ge=0)
    today: date
    week_starts_on: date
    week_ends_on: date
    awarded_badges: int = Field(ge=0)
    pending_badges: int = Field(ge=0)


class RewardOverview(UTCResponseModel):
    """Alles fuer die Belohnungskarte in **einer** Antwort.

    Kennzahl, Auszeichnungen und Buchungen gehoeren zu derselben Frage und werden
    immer zusammen gelesen. Drei Endpunkte wuerden drei Anfragen und drei
    Moeglichkeiten schaffen, in denen die Karte einen halben Stand zeigt - eine
    Punktzahl ohne die Buchung, aus der sie stammt.
    """

    summary: RewardSummary
    rules: RewardRules
    catalog: list[RewardBadgeDefinition]
    badges: list[RewardBadgeRead]
    entries: list[RewardEntryRead]
