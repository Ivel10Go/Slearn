"""Die ausdrückliche Einwilligung, Termine in einen Kalender zu **schreiben**.

Das Muster ist bewusst das der Notenfreigabe (``app/services/grades.py``), und
nicht nur, weil es das einzige vorhandene ist. Zwei Dinge sind hier dasselbe:

* **Der Standard ist aus.** Ein Schlüssel, der fehlt, ein ``false``, ein
  falscher Typ, kaputtes JSON - alles bedeutet *aus*. Ein Schalter, der durch
  einen kaputten Eintrag von selbst aufginge, wäre kein Schalter.
* **Sie steht in ``settings.preferences``, nicht in einer eigenen Spalte.** Die
  Spalte gibt es schon, und ein zweiter Ort für Nutzereinstellungen wäre eine
  dritte Wahrheit über dieselbe Sache.
* **Es wird nichts still übertragen.** Lesen ist erlaubt, sobald eine Quelle
  verbunden ist - der Nutzer hat sie ja selbst angegeben. Schreiben nicht: dafür
  muss er sagen, dass Termine aus der App in seinem Google- oder Apple-Kalender
  erscheinen sollen, und zwar mit einem ausdrücklichen Schalter.

Warum die Einwilligung neben dem Token steht und nicht darin
------------------------------------------------------------

Das OAuth-Token sagt, *was* technisch möglich ist: Google erlaubt Lesen und
Schreiben. Die Einwilligung sagt, *ob* es passiert. Wäre die Einwilligung im
Token enthalten, hinge sie an einem Wert, den die Gegenseite vergeben hat und
den man nur durch einen Widerruf in einem fremden Menü wieder loswird - und ein
Widerruf, der nur dort funktioniert, ist kein Widerruf.

Deshalb wird die Einwilligung **zusätzlich** geprüft, an zwei Stellen:
``calendar_writing_allowed()`` für den Abgleich (der ohne sie nichts schreibt)
und vor dem Autorisierungsaufruf (der die Schreib-Scope gar nicht erst
anfragt, solange die Einwilligung fehlt). Ein späterer Widerruf nimmt Google
das Recht nicht sofort weg, aber es nimmt ihm die Wirkung.
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.entities import UserSettings

#: Der Schlüssel in ``settings.preferences``. Fehlt er, ist die Einwilligung
#: aus - siehe Modul-Docstring.
WRITING_PREFERENCE_KEY: Final[str] = "share_calendar_for_writing"

#: Wozu die Freigabe benutzt wird. Steht in der Antwort, damit die Oberfläche
#: auflisten kann, **was** freigegeben wird, statt nur einen Schalter zu zeigen.
WRITING_USES: Final[tuple[str, ...]] = (
    "Termine aus Slearn in deinen verbundenen Google-Kalender schreiben",
    "Termine aus Slearn in deinen verbundenen Apple-Kalender schreiben",
    "In der App gelöschte Termine auch dort löschen",
)


def calendar_writing_allowed(db: Session, user_id: int) -> bool:
    """Darf aus der App in eine Gegenseite geschrieben werden?

    Jeder unlesbare Zustand bedeutet **aus**: kein Schlüssel, ``false``, ein
    fremder Typ, kein ``dict``, kaputtes JSON.
    """
    row = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if row is None or not isinstance(row.preferences, dict):
        return False
    return _truthy(row.preferences.get(WRITING_PREFERENCE_KEY))


def set_calendar_writing(db: Session, user_id: int, enabled: bool) -> bool:
    """Die Einwilligung setzen und die gespeicherte Entscheidung zurückgeben.

    Liest und schreibt ``preferences`` als Ganzes, weil dort fremde Schlüssel
    stehen bleiben: das ist ein fremdes Verzeichnis. Fehlt die
    ``settings``-Zeile, wird sie angelegt - ein Testkonto hat sie nicht.
    """
    row = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if row is None:
        row = UserSettings(user_id=user_id)
        db.add(row)
    preferences: dict[str, Any] = dict(row.preferences) if isinstance(row.preferences, dict) else {}
    preferences[WRITING_PREFERENCE_KEY] = enabled
    row.preferences = preferences
    db.flush()
    return enabled


def _truthy(value: Any) -> bool:
    return value is True
