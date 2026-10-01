"""Ersteinrichtung ohne Anmeldung.

Die Desktop-App laeuft mit ``AUTH_ENABLED=false``: es gibt genau eine Person
an diesem Rechner, und ``get_current_user`` nimmt den ersten aktiven Nutzer.
Auf einer frischen Datenbank gibt es den aber noch nicht - und
``ensure_bootstrap_user`` setzt Zugangsdaten aus der Umgebung voraus, die eine
Verbraucher-Installation nicht hat. Diese Einrichtung schliesst die Luecke: die
Oberflaeche fragt nach einem Namen und legt das Konto an.

Das Passwort ist ein Zufallswert, den niemand kennt. Mit abgeschalteter
Anmeldung wird es nie geprueft; es steht nur da, weil die Spalte Pflicht ist.
Wer die Datenbank spaeter auf einen Server mit Anmeldung umzieht, setzt es dort
per ``ADMIN_PASSWORD`` neu.
"""

import secrets
from threading import Lock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.entities import User, UserSettings


class SetupAlreadyDoneError(Exception):
    """Es gibt schon ein Konto - eine zweite Einrichtung wuerde ein zweites anlegen."""


# Zwei gleichzeitige Anfragen (Doppelklick, zwei Fenster) duerfen nicht zwei
# Konten erzeugen. Die Eindeutigkeit des Namens hilft dabei nicht, wenn sich
# die Namen unterscheiden - deshalb eine Sperre um Pruefen und Anlegen.
_setup_lock = Lock()


def needs_setup(db: Session) -> bool:
    count = db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True)))
    return not count


def create_first_user(db: Session, name: str, timezone: str) -> User:
    with _setup_lock:
        if not needs_setup(db):
            raise SetupAlreadyDoneError
        user = User(
            username=name,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            is_admin=True,
            is_active=True,
            timezone=timezone,
        )
        db.add(user)
        db.flush()
        db.add(UserSettings(user_id=user.id, timezone=timezone))
        db.commit()
        db.refresh(user)
        return user
