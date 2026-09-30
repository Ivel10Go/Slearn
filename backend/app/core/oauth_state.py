"""Ein signierter, kurzlebiger ``state`` für den OAuth-Rückweg.

Wozu das gut ist
----------------

Nach dem Autorisierungsschritt schickt Google den Browser zu unserer
Weiterleitungs-Adresse zurück, mit ``code`` und ``state``. Der ``state`` ist der
Schutz davor, dass jemand einen **eigenen** ``code`` einschiebt: der Rückweg
darf nur gelten, wenn er zu einer Autorisierung gehört, die wir selbst
angestoßen haben, und zwar für diese Integration und diesen Nutzer.

Die übliche Umsetzung legt dafür eine Zeile in der Datenbank an. Das ist mehr
Zustand, als ein Schutzziel braucht: eine Tabelle, die aufräumen muss, eine
Lücke zwischen Anlegen und Prüfen, und ein Rest, wenn der Browser den Rückweg
abricht. Ein HMAC über Integration, Nutzer und Zeitpunkt ersetzt das durch eine
reine Rechnung:

* **Nicht fälschbar** ohne ``SECRET_KEY``. Ein geratener ``state`` fällt auf.
* **Ohne Zustand.** Es gibt nichts, das liegen bleibt.
* **Kurzlebig**, weil der Zeitstempel mit signiert wird. Ein abgefangener
  Rückweg ist nach ``ttl_seconds`` tot.

Das Format ist bewusst dasselbe zweigeteilte wie beim Sitzungstoken in
``security.py``: ein base64url-Nutzwert, ein Punkt, eine Signatur. Wer die eine
Prüfung versteht, versteht die andere.

**Was das nicht ist:** kein Sitzungsersatz. Der ``state`` trägt keine Berechtigung
und kein Geheimnis des Nutzers, sondern nur die Bindung an eine bestimmte
Autorisierung. Er darf deshalb im URL stehen - dort muss er sogar stehen, weil
Google ihn von sich aus anhängt.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Final

from pydantic import SecretStr

_PAYLOAD_VERSION: Final = 1
_NONCE_BYTES: Final = 16
#: Großzügig genug für einen Einwilligungsdialog, kurz genug, dass ein
#: abgefangener Link nicht tagelang benutzt werden kann.
DEFAULT_TTL_SECONDS: Final = 900
#: Toleranz für Uhrenabweichung zwischen zwei Instanzen. Sitzungstokens nutzen
#: dieselben 30 Sekunden (``security.decode_session_token``).
_CLOCK_SKEW_SECONDS: Final = 30


class OAuthStateError(ValueError):
    """Der ``state`` ist unbrauchbar - nicht gültig signiert, zu alt oder unlesbar."""


@dataclass(frozen=True, slots=True)
class SignedState:
    """Was ein gültiger ``state`` behauptet."""

    subject: int
    actor: int
    issued_at: int
    nonce: str


def sign_state(
    subject: int,
    actor: int,
    secret: SecretStr | str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Signiert eine Rückkehr-Autorisierung für Integration ``subject`` und Nutzer ``actor``."""
    if subject <= 0 or actor <= 0:
        raise OAuthStateError("Eine Autorisierung braucht eine positive Integrations- und Nutzerkennung.")
    if ttl_seconds <= 0:
        raise OAuthStateError("Die Gültigkeit eines state muss positiv sein.")
    secret_value = _secret_value(secret)
    issued_at = int(now if now is not None else time.time())
    payload = {
        "s": subject,
        "a": actor,
        "i": issued_at,
        "n": secrets.token_urlsafe(_NONCE_BYTES),
        "v": _PAYLOAD_VERSION,
    }
    segment = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{segment}.{_signature(segment, secret_value)}"


def verify_state(
    token: str,
    secret: SecretStr | str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> SignedState:
    """Prüft Signatur, Alter und Aufbau - oder wirft ``OAuthStateError``.

    Es wird **geworfen und nicht ``None`` zurückgegeben**: die Ursache steht im
    Text, und der Aufrufer soll daraus eine Meldung für den Nutzer machen
    können. Drei verschiedene Gründe - falsch signiert, zu alt, unlesbar - sind
    drei verschiedene Ursachen, und sie gehören nicht in denselben Sack.
    """
    try:
        secret_value = _secret_value(secret)
    except ValueError as exc:
        raise OAuthStateError("Für die Autorisierung fehlt der geheime Schlüssel der Anwendung.") from exc
    if not token.strip() or token.count(".") != 1:
        raise OAuthStateError("Die Autorisierung trägt keinen gültigen state mit.")
    segment, signature = token.split(".", maxsplit=1)
    if not hmac.compare_digest(signature, _signature(segment, secret_value)):
        raise OAuthStateError("Der state dieser Autorisierung wurde unterwegs verändert.")

    payload = _payload(segment)
    if payload.get("v") != _PAYLOAD_VERSION:
        raise OAuthStateError("Der state dieser Autorisierung hat ein unbekanntes Format.")
    try:
        subject = _positive_int(payload["s"])
        actor = _positive_int(payload["a"])
        issued_at = _positive_int(payload["i"])
        nonce = payload["n"]
    except (KeyError, TypeError, ValueError) as exc:
        raise OAuthStateError("Der state dieser Autorisierung ist unvollständig.") from exc
    if not isinstance(nonce, str) or not nonce.strip():
        raise OAuthStateError("Der state dieser Autorisierung ist unvollständig.")

    current = int(now if now is not None else time.time())
    if issued_at > current + _CLOCK_SKEW_SECONDS:
        raise OAuthStateError("Der state dieser Autorisierung stammt aus der Zukunft.")
    if current - issued_at > ttl_seconds:
        raise OAuthStateError("Die Autorisierung ist abgelaufen. Bitte verbinde Google Kalender erneut.")
    return SignedState(subject=subject, actor=actor, issued_at=issued_at, nonce=nonce)


def _secret_value(secret: SecretStr | str) -> str:
    value = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
    if not value:
        raise ValueError("Secret must not be empty")
    return value


def _signature(segment: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), segment.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.b64decode((segment + padding).encode("ascii"), altchars=b"-_", validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise OAuthStateError("Der state dieser Autorisierung ist nicht lesbar.") from exc


def _payload(segment: str) -> dict[str, Any]:
    try:
        decoded = json.loads(_decode(segment).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OAuthStateError) as exc:
        raise OAuthStateError("Der state dieser Autorisierung ist nicht lesbar.") from exc
    if not isinstance(decoded, dict):
        raise OAuthStateError("Der state dieser Autorisierung hat einen unerwarteten Aufbau.")
    return decoded


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Zahlenwert erwartet")
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("Zahlenwert muss positiv sein")
    return parsed


__all__ = ["DEFAULT_TTL_SECONDS", "OAuthStateError", "SignedState", "sign_state", "verify_state"]
