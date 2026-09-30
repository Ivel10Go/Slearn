"""Web-Push: VAPID-Token nach RFC 8292 und Nutzlast nach RFC 8291.

**Es gibt hier keinen Push-Dienst.** Der Code ist vollstaendig und der Weg ist
gegen die Normen gebaut, aber er ist gegen keinen echten Dienst gelaufen: in
diesem Stack gibt es keinen und keinen Endgeräte-Abruf, an dem man vorbeikommen
koennte. Die Tests belegen deshalb zwei Dinge und nichts weiter:

* die **Nutzlast** enthaelt exakt die von RFC 8291 Abschnitt 3.4 beschriebene
  Bytefolge, gegengeprueft am Testvektor aus Anhang A, und
* der **VAPID-Token** traegt die richtige Kopfzeile und laesst sich mit dem
  oeffentlichen Schluessel des Paares verifizieren.

Beides ist gegen einen dokumentierten Sollwert geprueft, nicht gegen einen
Dienst. Wer das hier zum ersten Mal einsetzt, sollte es an einem echten Konto
testen.

Zwei eigene Dinge, die man sonst leicht falsch hat:

* **Keine neue Abhaengigkeit.** ``cryptography`` ist fuer die
  Zugangsdaten-Verschluesselung ohnehin gepinnt und liefert ECDH, HKDF und
  AES-GCM mit. ``httpx`` ist ebenfalls da und stellt die Anfrage. Es war also
  weder ``pywebpush`` noech ein Push-SDK noetig - und eines davon haette
  zusaetzlich noch ein eigenes Schluessel- und Fehlerformat mitgebracht.
* **Der Schluessel gehoert hier und nicht in den Service Worker.** Der
  oeffentliche Application Server Key wird ueber ``GET /v1/notifications/vapid``
  an die Seite gereicht und von dort per ``postMessage`` an den Worker
  weitergereicht. Im Service Worker steht er nicht, und im versionierten
  Quelltext schon gar nicht.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.core.config import (
    Settings,
    configured_web_push_public_key,
    require_web_push_private_key,
    require_web_push_subject,
)
from app.core.logging import log_event, log_warning
from app.core.time import as_utc, utc_now

logger = logging.getLogger("school_assistant.web_push")

#: Die ``info``-Zeichenkette aus RFC 8291 Abschnitt 3.4. Sie bindet den
#: Schluesseltausch an **beide** oeffentlichen Schluessel - ohne sie waere
#: derselbe abgeleitete Schluessel auch mit einem anderen Geraet benutzbar.
_KEY_INFO_PREFIX: Final[bytes] = b"WebPush: info\x00"

#: Die beiden ``info``-Zeichenketten aus **RFC 8188** Abschnitt 2.3, nicht aus
#: RFC 8291: Schluessel und Nonce sind *zwei* HKDF-Aufrufe ueber denselben PRK
#: und nicht zwei Haelften einer gemeinsamen Ausgabe. Aus einem 32-Byte-Ergebnis
#: die zweite Haelfte zu schneiden liefert einen Wert, der die richtige Laenge
#: hat und kein Nonce ist - der Browser entschluesselt damit nichts.
_CEK_INFO: Final[bytes] = b"Content-Encoding: aes128gcm\x00"
_NONCE_INFO: Final[bytes] = b"Content-Encoding: nonce\x00"

#: Das Trennzeichen zwischen Nutzlast und Fuellung. RFC 8291 Abschnitt 4: der
#: Browser **verwirft** jede Nachricht, in der an dieser Stelle etwas anderes
#: als 0x02 steht. Es gehoert also zwingend in den Klartext.
_PADDING_DELIMITER: Final[bytes] = b"\x02"

#: Der empfohlene Wert der Spezifikation. Kleinere Werte wuerden eine lange
#: Nutzlast in Bloecke schneiden, die der Browser nur mit Muehe zusammenbaut.
_RECORD_SIZE: Final[int] = 4096

#: 65 Byte: der unkomprimierte Punkt auf P-256 mit ``0x04`` vorangestellt.
_PUBLIC_KEY_LENGTH: Final[int] = 65

#: 16 Byte Geheimnis des Geraets (RFC 8291 Abschnitt 3.2).
_AUTH_SECRET_LENGTH: Final[int] = 16

#: 16 Byte GCM-Anhaengung.
_TAG_LENGTH: Final[int] = 16

#: RFC 8292 erlaubt hoechstens 24 Stunden Lebensdauer. 12 Stunden sind der
#: uebliche Wert und reichen fuer einen Lauf, der alle fuenf Minuten laeuft.
_TOKEN_LIFETIME: Final[timedelta] = timedelta(hours=12)

#: Wie lange ein Push-Dienst die Nachricht aufbewahren soll, falls der Browser
#: gerade offline ist. ``Urgency: normal`` heisst: kein Expressversand.
_MESSAGE_TTL_SECONDS: Final[str] = "86400"

#: Der Browser loescht ein Abonnement, das der Dienst nicht mehr kennt. Diese
#: beiden Status kommen genau dann und die Zeile ist danach wertlos.
_GONE_STATUSES: Final[frozenset[int]] = frozenset({404, 410})


class WebPushError(RuntimeError):
    """Der Push-Dienst hat die Nachricht nicht angenommen."""


class WebPushConfigError(WebPushError):
    """Das Schluesselpaar oder die Kontaktangabe ist unbrauchbar."""


@dataclass(frozen=True, slots=True)
class PushTarget:
    """Ein Abonnement: wo hin, mit welchen Schluesseln."""

    endpoint: str
    p256dh: str
    auth: str


@dataclass(frozen=True, slots=True)
class VapidKeyPair:
    """Privater Schluessel zum Zeichnen, oeffentlicher zum Anmelden."""

    private_key: ec.EllipticCurvePrivateKey
    public_key_b64: str

    def public_key_bytes(self) -> bytes:
        return b64url_decode(self.public_key_b64)


@dataclass(frozen=True, slots=True)
class PushResult:
    """Was der Dienst aus der Anfrage gemacht hat.

    ``gone`` heisst: das Abonnement existiert dort nicht mehr und wird vom
    Aufrufer geloescht. Es ist kein Fehler, sondern das Ende eines Abos.
    """

    ok: bool
    status: int | None
    gone: bool
    message: str


def b64url_encode(raw: bytes) -> str:
    """base64url **ohne** Polster, wie es die Push-Spezifikation verlangt."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    """Der Gegenweg, tolerant gegen fehlendes Polster."""
    text = value.strip()
    if not text:
        raise WebPushConfigError("Ein base64url-Wert ist leer")
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise WebPushConfigError("Ein base64url-Wert ist nicht lesbar") from exc


def load_vapid_private_key(value: str) -> ec.EllipticCurvePrivateKey:
    """Liest den privaten Schluessel als PEM oder als base64url-PKCS#8.

    Beides wird akzeptiert, weil beides ueblich ist: ein PEM aus
    ``openssl ecparam -genkey`` und ein einzeiliger Wert aus einer
    Deployment-Oberflaeche. Beides landet im selben Objekt, und der Rest des
    Moduls sieht davon nichts.
    """
    text = value.strip()
    if not text:
        raise WebPushConfigError("Der private VAPID-Schlüssel ist leer")
    loaded: Any
    try:
        if text.startswith("-----BEGIN"):
            loaded = serialization.load_pem_private_key(text.encode("utf-8"), password=None)
        else:
            loaded = serialization.load_der_private_key(b64url_decode(text), password=None)
    except WebPushConfigError:
        raise
    except (ValueError, TypeError) as exc:
        raise WebPushConfigError("Der private VAPID-Schlüssel ist nicht lesbar") from exc
    if not isinstance(loaded, ec.EllipticCurvePrivateKey):
        raise WebPushConfigError("Der private VAPID-Schlüssel ist kein ECDSA-Schlüssel")
    if not isinstance(loaded.curve, ec.SECP256R1):
        # RFC 8292 verlangt P-256. Ein P-384-Schluessel wuerde eine
        # WebPush-Anfrage erzeugen, die kein Dienst annimmt.
        raise WebPushConfigError("Der private VAPID-Schlüssel muss auf P-256 liegen")
    return loaded


def public_key_b64(private_key: ec.EllipticCurvePrivateKey) -> str:
    """Der Application Server Key: der unkomprimierte Punkt, base64url.

    Genau die 65 Byte, die der Browser als ``applicationServerKey`` bekommt.
    """
    return b64url_encode(_point_bytes(private_key.public_key()))


def _point_bytes(public_key: ec.EllipticCurvePublicKey) -> bytes:
    numbers = public_key.public_numbers()
    return b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")


def generate_vapid_keypair() -> tuple[str, str]:
    """Erzeugt ein neues Schluesselpaar fuer die Umgebungsvariable.

    Gibt ``(privates PEM, oeffentlicher base64url)`` zurueck. Der oeffentliche
    Wert wird nur zum Nachschreiben gebraucht - ohne ihn rechnet das Backend
    ihn aus dem privaten aus. Gedacht fuer **einmal** beim Aufsetzen, nicht
    fuer einen Aufruf je Erinnerung.
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return pem, public_key_b64(private_key)


def resolve_vapid_keypair(settings: Settings) -> VapidKeyPair:
    """Das Schluesselpaar aus der Konfiguration, mit Gegenprobe.

    Steht ein oeffentlicher Schluessel in der Umgebung, muss er zu dem privaten
    gehoeren. Passen sie nicht, ist das die haeufigste Fehlkonfiguration der
    ganzen Kette: die Seite meldet sich mit einem Schluessel an, mit dem der
    Server nicht zeichnen kann, und der Dienst lehnt jede Nachricht mit 403 ab.
    Deshalb wird hier ausdruecklich ein Fehler geworfen statt still der
    berechnete Wert zu gelten.
    """
    private_key = load_vapid_private_key(require_web_push_private_key(settings))
    calculated = public_key_b64(private_key)
    configured = configured_web_push_public_key(settings)
    if configured is not None and _normalize_key(configured) != _normalize_key(calculated):
        raise WebPushConfigError(
            "WEB_PUSH_VAPID_PUBLIC_KEY passt nicht zu WEB_PUSH_VAPID_PRIVATE_KEY. "
            "Ohne den oeffentlichen Wert wird er aus dem privaten berechnet."
        )
    return VapidKeyPair(private_key=private_key, public_key_b64=calculated)


def _normalize_key(value: str) -> str:
    """base64url ohne Polster, damit beide Schreibweisen vergleichbar sind."""
    return b64url_encode(b64url_decode(value))


def build_vapid_token(
    *,
    private_key: ec.EllipticCurvePrivateKey,
    public_key_b64: str,
    subject: str,
    audience: str,
    now: datetime | None = None,
) -> str:
    """Das JWT, das die Anfrage als ``Authorization: vapid t=...`` ausweist.

    ES256 ueber P-256, und - das ist der Teil, den man gerne vergisst - die
    Signatur wird von DER auf die **rohe** Form ``r || s`` mit je 32 Byte
    umgerechnet. JWT verlangt die rohe Form; der von ``cryptography``
    gelieferte DER-Block wuerde jeder Dienst ablehnen.
    """
    moment = as_utc(now or utc_now())
    header = {"typ": "JWT", "alg": "ES256"}
    claims = {
        "aud": audience,
        "exp": int((moment + _TOKEN_LIFETIME).timestamp()),
        "sub": subject,
    }
    signing_input = ".".join(
        (
            b64url_encode(_compact_json(header)),
            b64url_encode(_compact_json(claims)),
        )
    ).encode("ascii")
    der = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    token = f"{signing_input.decode('ascii')}.{b64url_encode(raw_signature)}"
    return f"vapid t={token}, k={public_key_b64}"


def verify_vapid_token(header_value: str, public_key: ec.EllipticCurvePublicKey, *, now: datetime) -> bool:
    """Prueft einen ``vapid``-Header gegen den oeffentlichen Schluessel.

    Nur fuer die Tests. Es ist die Umkehrung von ``build_vapid_token`` mit
    denselben Regeln (ES256, rohe Signatur, ``exp``), und ohne sie waere
    "der Token ist richtig" eine Behauptung.
    """
    moment = as_utc(now)
    # Der Header ist `vapid t=<token>, k=<schluessel>`. Der Token enthaelt
    # Punkte, aber keine Kommas - deshalb darf hier nach Komma getrennt werden,
    # und **nicht** nach Leerzeichen: nach dem Token steht erst das Komma, und
    # ein `split(" ")[1]` haette den Schluessel abgeschnitten.
    _scheme, _trennzeichen, rest = header_value.partition(" ")
    token = ""
    key = ""
    for part in rest.split(","):
        name, _, value = part.strip().partition("=")
        if name == "t":
            token = value
        elif name == "k":
            key = value
    if not token or not key:
        return False
    teile = token.split(".")
    if len(teile) != 3:
        return False
    head_b64, claims_b64, signature = teile
    signing_input = f"{head_b64}.{claims_b64}"
    try:
        claims = json.loads(b64url_decode(claims_b64).decode("utf-8"))
        head = json.loads(b64url_decode(head_b64).decode("utf-8"))
    except (WebPushConfigError, ValueError, UnicodeDecodeError):
        return False
    if head.get("alg") != "ES256" or not isinstance(claims, dict):
        return False
    expires = claims.get("exp")
    if not isinstance(expires, int) or expires <= int(moment.timestamp()):
        return False
    if b64url_encode(b64url_decode(key)) != b64url_encode(_point_bytes(public_key)):
        return False
    signature_bytes = b64url_decode(signature)
    if len(signature_bytes) != 64:
        return False
    r = int.from_bytes(signature_bytes[:32], "big")
    s = int.from_bytes(signature_bytes[32:], "big")
    try:
        # `verify` erwartet den **DER**-Block, JWT schickt aber die rohe Form
        # `r || s`. Zurueckumwandeln gehoert zum Nachmachen der Signatur dazu -
        # sonst wuerde dieser Test genau die Sache pruefen, die er beweisen soll.
        public_key.verify(
            encode_dss_signature(r, s),
            signing_input.encode("ascii"),
            ec.ECDSA(hashes.SHA256()),
        )
    except InvalidSignature:
        return False
    return True


def encode_aes128gcm(
    payload: bytes,
    *,
    target: PushTarget,
    server_public_key: bytes,
    salt: bytes | None = None,
    ephemeral_private_key: ec.EllipticCurvePrivateKey | None = None,
    record_size: int = _RECORD_SIZE,
) -> bytes:
    """Die Nutzlast so verpacken, wie ein Browser sie erwartet (RFC 8291).

    Der Aufbau ist fest vorgegeben und wird hier nicht "verbessert":

    ``salt(16) | rs(4) | idlen(1) | oeffentlicher Schluessel(65) | Chiffrat``

    ``rs`` ist die Blockgroesse als ``uint32`` gross. Die Schluessel entstehen
    aus **drei** Schritten: ECDH zwischen einem fluechtigen Schluessel und dem
    Geraet, dann HKDF ueber das Geheimnis des Geraets, dann je ein HKDF ueber
    denselben Zwischenwert fuer Schluessel und Nonce. Wer den ersten Schritt
    weisst und die beiden HKDFs vertauscht, erhaelt ein Chiffrat, das die
    richtige Laenge hat und trotzdem nicht lesbar ist.

    Geprueft wird das an **allen Zwischenwerten** aus Anhang A der RFC 8291
    sowie am fertigen Koerper aus Abschnitt 5 - im Test, nicht hier.
    """
    if len(server_public_key) != _PUBLIC_KEY_LENGTH:
        raise WebPushConfigError("Der öffentliche VAPID-Schlüssel muss 65 Byte lang sein")
    # RFC 8291 Abschnitt 4: `rs` muss groesser sein als Nutzlast + Trennzeichen
    # + Fuellung + Anhaengung. Sonst waere die Nachricht zwar gueltig gebaut,
    # aber nicht lesbar - und das faellt erst beim Nutzer auf.
    if len(payload) + 1 + _TAG_LENGTH >= record_size:
        raise WebPushError(
            f"Die Nutzlast ist zu lang fuer eine Push-Nachricht ({len(payload)} Byte)."
        )
    receiver_public = _load_point(target.p256dh)
    auth_secret = b64url_decode(target.auth)
    ephemeral = ephemeral_private_key or ec.generate_private_key(ec.SECP256R1())
    shared = ephemeral.exchange(ec.ECDH(), receiver_public)
    key_info = _KEY_INFO_PREFIX + b64url_decode(target.p256dh) + server_public_key
    key_prk = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=key_info).derive(shared)
    chosen_salt = salt or os.urandom(16)
    if len(chosen_salt) != 16:
        raise WebPushConfigError("Der Salz der Nutzlast muss 16 Byte lang sein")
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=chosen_salt, info=_CEK_INFO).derive(key_prk)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=chosen_salt, info=_NONCE_INFO).derive(key_prk)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + _PADDING_DELIMITER, None)
    return (
        chosen_salt
        + record_size.to_bytes(4, "big")
        + bytes([_PUBLIC_KEY_LENGTH])
        + server_public_key
        + ciphertext
    )


def _load_point(value: str) -> ec.EllipticCurvePublicKey:
    """Der oeffentliche Schluessel des Geraets aus seinem base64url-Wert."""
    raw = b64url_decode(value)
    if len(raw) != _PUBLIC_KEY_LENGTH or not raw.startswith(b"\x04"):
        raise WebPushConfigError("Der Schlüssel des Geräts (p256dh) ist kein Punkt auf P-256")
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)


def _compact_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _audience_of(endpoint: str) -> str:
    """Der ``aud`` des Tokens ist der Ursprung des Endpunkts, ohne Pfad.

    RFC 8292 Abschnitt 2: ``aud`` ist der Ursprung des Push-Endpunkts. Nimmt man
    den ganzen Endpunkt, lehnt der Dienst den Token ab.
    """
    parts = urlsplit(endpoint)
    if not parts.scheme or not parts.netloc:
        raise WebPushConfigError("Der Endpunkt des Abonnements ist keine URL")
    return f"{parts.scheme}://{parts.netloc}"


def check_subscription_keys(p256dh: str, auth: str) -> None:
    """Prueft die Schluessel des Geraets **beim Anlegen**, nicht beim Senden.

    Zwei Fehler, die man sonst erst nach Tagen sieht: ein ``p256dh``, der kein
    Punkt auf P-256 ist, und ein ``auth``, das keine 16 Byte hat. Beide fallen
    beim Senden in ``encode_aes128gcm`` auf - aber dann ist das Abonnement
    schon gespeichert und der Nutzer glaubt, es laeufe. Hier wird deshalb mit
    einem 422 geantwortet, solange es noch niemanden verwirrt hat.

    RFC 8291 Abschnitt 3.2 verlangt fuer ``auth`` "a hard-to-guess sequence of
    16 octets"; ``p256dh`` ist der unkomprimierte Punkt, also 65 Byte.
    """
    if len(_load_point(p256dh).public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)) != _PUBLIC_KEY_LENGTH:
        raise WebPushConfigError("Der Schlüssel des Geräts (p256dh) hat nicht die erwartete Länge")
    if len(b64url_decode(auth)) != _AUTH_SECRET_LENGTH:
        raise WebPushConfigError("Das Geheimnis des Geräts (auth) muss 16 Byte lang sein")


def send_push(
    *,
    target: PushTarget,
    payload: Mapping[str, object],
    settings: Settings,
    keypair: VapidKeyPair | None = None,
    now: datetime | None = None,
    client: httpx.Client | None = None,
) -> PushResult:
    """Eine Nachricht an genau ein Abonnement schicken.

    Der Client ist ein Argument, damit die Tests einen ``httpx.MockTransport``
    einsetzen koennen. **Das ist eine Attrappe**: es gibt hier keinen Push-
    Dienst, gegen den dieser Pfad laufen koennte, und ein Test mit einem
    gemockten Transport beweist die Anfrage, nicht die Zustellung.
    """
    pair = keypair or resolve_vapid_keypair(settings)
    subject = require_web_push_subject(settings)
    audience = _audience_of(target.endpoint)
    body = encode_aes128gcm(
        _compact_json(payload),
        target=target,
        server_public_key=pair.public_key_bytes(),
    )
    token = build_vapid_token(
        private_key=pair.private_key,
        public_key_b64=pair.public_key_b64,
        subject=subject,
        audience=audience,
        now=now,
    )
    headers = {
        "Authorization": token,
        # Ohne diesen Kopf lehnt jeder Dienst die Nachricht ab: es ist die
        # Angabe, dass der Koerper nach RFC 8291 verschluesselt ist - nicht
        # nach RFC 8188 selbst (das wuerde eine komprimierte K Codierung
        # versprechen, und genau die verbietet RFC 8291 fuer Push).
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": _MESSAGE_TTL_SECONDS,
        "Urgency": "normal",
    }
    owns_client = client is None
    http_client = client or httpx.Client(timeout=settings.web_push_timeout_seconds, follow_redirects=True)
    try:
        response = http_client.post(target.endpoint, content=body, headers=headers)
    except httpx.TimeoutException as exc:
        raise WebPushError("Der Push-Dienst hat nicht rechtzeitig geantwortet.") from exc
    except httpx.HTTPError as exc:
        raise WebPushError(f"Der Push-Dienst ist nicht erreichbar: {exc}") from exc
    finally:
        if owns_client:
            http_client.close()

    status = response.status_code
    if 200 <= status < 300:
        return PushResult(ok=True, status=status, gone=False, message="Vom Push-Dienst angenommen.")
    if status in _GONE_STATUSES:
        return PushResult(
            ok=False,
            status=status,
            gone=True,
            message="Das Abonnement ist beim Push-Dienst nicht mehr bekannt (HTTP "
            f"{status}).",
        )
    if status in {401, 403}:
        return PushResult(
            ok=False,
            status=status,
            gone=False,
            message="Der Push-Dienst hat die VAPID-Signatur abgelehnt. Prüfe das Schlüsselpaar.",
        )
    return PushResult(ok=False, status=status, gone=False, message=f"Der Push-Dienst lehnte die Nachricht ab (HTTP {status}).")


def push_is_configured(settings: Settings) -> bool:
    """Sind **alle drei** Angaben da? Sonst laeuft der lokale Weg allein.

    Absichtlich conservative: fehlt nur der ``subject``, waere ein echter Versand
    moeglich, aber nicht zuverlaessig, und die Oberflaeche soll das sagen, bevor
    jemand auf einen Knopf drueckt. Gefangen wird ``RuntimeError`` und nicht
    nur ``WebPushError``: ein **fehlender** Schluessel kommt aus
    ``app.core.config`` als ``RuntimeError``, und beide bedeuten dasselbe.
    """
    try:
        resolve_vapid_keypair(settings)
        require_web_push_subject(settings)
    except RuntimeError:
        return False
    return True


def describe_push_failure(error: WebPushError) -> str:
    """Eine Ausnahme als deutsche Zeile fuer ``last_error``."""
    return str(error) or "Der Push-Versand ist fehlgeschlagen."


def log_push_result(
    *,
    user_id: int,
    notification_id: int | None,
    result: PushResult,
    endpoint: str,
) -> None:
    """Ein Versand im Log - **ohne** den Endpunkt.

    Der Endpunkt eines Push-Abonnements enthaelt ein geheimes Pfad-Stueck des
    Anbieters. Deshalb wird er nicht geloggt, sondern ueber ``hash_endpoint``
    gekuerzt.
    """
    fields: dict[str, Any] = {
        "user_id": user_id,
        "endpoint_hash": hash_endpoint(endpoint),
        "status": result.status,
        "ok": result.ok,
    }
    if notification_id is not None:
        fields["notification_id"] = notification_id
    if result.ok:
        log_event(logger, "web_push_sent", **fields)
    else:
        log_warning(logger, "web_push_failed", **fields)


def hash_endpoint(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:12]
