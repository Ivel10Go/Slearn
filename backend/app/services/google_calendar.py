"""Google Kalender über OAuth 2.0: Einwilligung, Token, Lesen und Schreiben.

Ablauf einer Einrichtung
------------------------

1. Die Oberfläche legt eine Integration der Art ``google_calendar`` an und
   ruft ``POST /api/v1/integrations/{id}/authorize``. Der Server baut die
   Autorisierungs-URL und gibt sie zurück - **ohne** den Browser umzuleiten, das
   bleibt Sache der Oberfläche.
2. Der Browser folgt der URL. Google zeigt die Einwilligungsseite mit dem
   Text der Scopes.
3. Google schickt den Browser zu ``redirect_uri`` zurück, mit ``code`` und
   ``state``. Diese Adresse zeigt auf das **Backend**, nicht auf die Oberfläche:
   ein Autorisierungscode im Frontend wäre ein Geheimnis, das durch den
   Anwendungszustand läuft.
4. Das Backend prüft ``state``, tauscht den Code gegen ein Token und legt es
   verschlüsselt ab.

**Warum ``state`` eine signierte Zeichenkette ist und keine Datenbankzeile:**
Der Zweck von ``state`` ist, dass ein Angreifer keinen eigenen ``code``
einschieben kann. Dafür braucht es eine Prüfung, die der Browser nicht umgehen
kann. Eine HMAC über Integration, Nutzer und Zeitpunkt mit ``SECRET_KEY``
erfüllt das ohne Tabelle, ohne Aufräumen und ohne Zustand. Der Zeitstempel ist
Teil der signierten Zeichenkette und begrenzt die Lebensdauer auf
``STATE_TTL_SECONDS`` - sonst wäre ein abgefangener Link tagelang gültig.

Warum das Refresh-Token das eigentliche Geheimnis ist
----------------------------------------------------

Das Zugriffstoken ist ein kurzlebiges Ticket, das Google jederzeit widerrufen
kann. Das Refresh-Token ist es nicht, und es wird als einziges langlebig
ausgestellt. Deshalb liegt es - zusammen mit dem Client-Schlüssel - in der
verschlüsselten Credential-Blase (``app/core/crypto.py``) und **nicht** in
``sync_sources.configuration``, das im Klartext in einer JSON-Spalte steht.
Genau das ist der Grund, weshalb ``client_secret`` hier als Credential und nicht
als Konfigurationsfeld geführt wird.

Ein Access-Token wird **nicht** gespeichert. Es wird bei Bedarf aus dem
Refresh-Token geholt und danach weggeworfen: eine Kopie, die ein Jahr gültig
bleibt, wäre ein zusätzlicher Weg nach Google, den es sonst nicht gäbe.

Scopes
------

Angefordert wird ``calendar.events`` in beiden Richtungen, also Lesen **und**
Schreiben. Es gibt keinen sinnvollen Halbweg: ein Google-Token, das nur lesen
darf, kann keinen Termin anlegen, und ein Lese-Token, das man beim ersten
Aufruf verschwendet, muss beim zweiten neu eingefragt werden. Das Recht zu
schreiben ist deshalb an die ausdrückliche Einwilligung gebunden
(``app/services/calendar_consent.py``), nicht an das Token allein - das Token
sagt, *was* möglich ist, die Einwilligung sagt, *ob* es passiert.

Nicht verifiziert
-----------------

Dieser Adapter ist gegen **gemockte** HTTP-Antworten getestet. Es gab weder ein
Google-Konto noch einen Netzzugang zu ``accounts.google.com``. Die
Endpunktpfade, die Feldnamen und die Fehlercodes sind aus der dokumentierten
API abgeleitet, nicht live geprüft. Wer das produktiv einsetzt, sollte den
Verbindungstest einmal gegen ein echtes Konto laufen lassen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlencode

import httpx

from app.core.time import utc_now
from app.services.calendar_sync import (
    CalendarAuthError,
    CalendarConfigError,
    CalendarConflictError,
    CalendarSyncError,
    RemoteBatch,
    RemoteEvent,
)

AUTHORIZATION_ENDPOINT: Final = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT: Final = "https://oauth2.googleapis.com/token"
API_BASE: Final = "https://www.googleapis.com/calendar/v3"

#: Lesen **und** Schreiben an derselben Ressource. Bewusst eine Scope: ein
#: Lese-Token, das beim ersten Abruf verbraucht wird, muss beim zweiten neu
#: eingefragt werden, und der Nutzer sieht dann zweimal dieselbe Frage.
CALENDAR_SCOPE: Final = "https://www.googleapis.com/auth/calendar.events"
SCOPES: Final = (CALENDAR_SCOPE,)

#: Ein Google-Access-Token gilt eine Stunde. Der Sicherheitsabstand sorgt dafür,
#: dass ein Token nicht mitten im Abruf abläuft.
TOKEN_MARGIN_SECONDS: Final = 15 * 60
DEFAULT_TOKEN_LIFETIME_SECONDS: Final = 3600

#: Wie lange ein Autorisierungslink gültig bleibt.
STATE_TTL_SECONDS: Final = 900


class GoogleOAuthError(CalendarSyncError):
    """Der Autorisierungslauf ist gescheitert (kein Code, falscher State, Fehler von Google)."""


@dataclass(frozen=True, slots=True)
class OAuthStart:
    """Was der Aufrufer für den Browser braucht: die URL und der signierte ``state``."""

    authorization_url: str
    state: str
    scopes: tuple[str, ...] = SCOPES


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    """Ein Tokenpaket. Nur ``refresh_token`` ist langlebig."""

    refresh_token: str
    access_token: str = ""
    expires_at: datetime | None = None
    scope: str = ""


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    login_hint: str = "",
) -> str:
    """Baut die Autorisierungs-URL, auf die der Browser gehen soll."""
    if not client_id.strip():
        raise GoogleOAuthError("Es fehlt die Client-ID von Google.")
    if not redirect_uri.strip():
        raise GoogleOAuthError("Es fehlt die Weiterleitungs-Adresse, die bei Google hinterlegt sein muss.")
    params = {
        "client_id": client_id.strip(),
        "redirect_uri": redirect_uri.strip(),
        "response_type": "code",
        # ``offline`` ist der Unterschied zwischen einem nutzbaren und einem
        # nutzlosen Setup: ohne diesen Wert gibt Google kein Refresh-Token, und
        # nach einer Stunde wäre die Integration tot.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "scope": " ".join(SCOPES),
        "state": state,
    }
    if login_hint.strip():
        params["login_hint"] = login_hint.strip()
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


class GoogleCalendarClient:
    """Liest und schreibt Termine eines Google-Kalenders."""

    kind = "google_calendar"
    supports_writing = True

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        calendar_id: str,
        tokens: OAuthTokens,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._calendar_id = calendar_id.strip()
        self._tokens = tokens
        self._timeout = timeout_seconds
        if not self._calendar_id:
            raise CalendarConfigError("Es fehlt die Kalender-Id von Google.")
        if not self._tokens.refresh_token.strip():
            raise CalendarAuthError("Der Zugriff von Google ist noch nicht eingerichtet.")
        self._client = httpx.Client(timeout=timeout_seconds, follow_redirects=True)

    def __enter__(self) -> GoogleCalendarClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- Einwilligung ----------------------------------------------------

    def check_connection(self) -> str:
        """Echter Verbindungstest: ein Token holen und den Kalender selbst benennen.

        Bewusst ``calendars.get`` und nicht ein Terminabruf. Es ist der
        billigste Aufruf, der zugleich Token, Schlüssel, Kalender-Id und
        Lese-Recht in einer Runde prüft - und er erzeugt nichts, das man später
        wieder aufräumen müsste.
        """
        access_token = self._access_token()
        body = self._get(f"{API_BASE}/calendars/{_path_segment(self._calendar_id)}", access_token)
        summary = _string(body.get("summary")) or self._calendar_id
        access = _string(body.get("accessRole"))
        text = f"Google Kalender ist erreichbar, Kalender '{summary}'."
        if access:
            text += f" Deine Berechtigung dort ist '{access}'."
        if _bool(body.get("canWrite")):
            text += " Termine lassen sich dort anlegen und ändern."
        else:
            text += " Termine lassen sich dort nicht anlegen oder ändern."
        return text

    # -- Lesen -----------------------------------------------------------

    def fetch_events(self, start: datetime, end: datetime) -> RemoteBatch:
        access_token = self._access_token()
        params: dict[str, str] = {
            "timeMin": _rfc3339(start),
            "timeMax": _rfc3339(end),
            # Ohne ``singleEvents=true`` liefert Google die *Regel* einer
            # Wiederholung statt der Termine. Die App löst Wiederholungen
            # bewusst nicht auf (siehe `ical.py`), deshalb ist ausdrücklich
            # ``false`` gesetzt: eine einzelne Master-Instanz ist ehrlicher als
            # eine Liste, in der jede Woche dieselbe Überschrift steht.
            "singleEvents": "false",
            "showDeleted": "false",
            "maxResults": "250",
            "orderBy": "startTime",
        }
        url = f"{API_BASE}/calendars/{_path_segment(self._calendar_id)}/events"
        events: list[RemoteEvent] = []
        page: str | None = None
        skipped = 0
        while True:
            if page:
                params["pageToken"] = page
            body = self._get(url, access_token, params=params)
            for entry in _items(body):
                parsed = _from_api_event(entry)
                if parsed is None:
                    skipped += 1
                else:
                    events.append(parsed)
            page = _string(body.get("nextPageToken"))
            if not page:
                break
        return RemoteBatch(events=tuple(events), skipped=skipped)

    # -- Schreiben -------------------------------------------------------

    def push_event(self, event: RemoteEvent, etag: str | None) -> RemoteEvent:
        access_token = self._access_token()
        url = f"{API_BASE}/calendars/{_path_segment(self._calendar_id)}/events"
        if etag is None:
            response = self._request("POST", url, access_token, json=_to_api_event(event))
        else:
            response = self._request(
                "PATCH",
                f"{url}/{_path_segment(event.uid)}",
                access_token,
                json=_to_api_event(event),
                headers={"If-Match": etag},
            )
        return _to_remote(response, event)

    def delete_event(self, uid: str, etag: str | None) -> None:
        access_token = self._access_token()
        url = f"{API_BASE}/calendars/{_path_segment(self._calendar_id)}/events/{_path_segment(uid)}"
        headers = {"If-Match": etag} if etag else {}
        # Ein 404 gilt als erledigt: der Termin ist dort nicht mehr, und genau
        # das wollte der Löschauftrag. 412 dagegen ist ein Konflikt und wird von
        # `_request` als solcher weitergegeben.
        self._request("DELETE", url, access_token, headers=headers, ignore_missing=True)

    # -- HTTP ------------------------------------------------------------

    def _access_token(self) -> str:
        """Ein frisches Access-Token aus dem Refresh-Token."""
        cached = self._tokens.access_token
        if cached and not _expires_soon(self._tokens.expires_at):
            return cached
        body = _post_form(
            self._client,
            TOKEN_ENDPOINT,
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._tokens.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=self._timeout,
        )
        token = _string(body.get("access_token"))
        if not token:
            raise CalendarAuthError("Google hat kein Zugriffstoken geliefert.")
        lifetime = _int(body.get("expires_in")) or DEFAULT_TOKEN_LIFETIME_SECONDS
        self._tokens = OAuthTokens(
            refresh_token=self._tokens.refresh_token,
            access_token=token,
            expires_at=utc_now() + timedelta(seconds=max(0, lifetime - TOKEN_MARGIN_SECONDS)),
            scope=_string(body.get("scope")),
        )
        return token

    def _get(self, url: str, access_token: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        response = self._request("GET", url, access_token, params=params)
        if not isinstance(response, dict):
            raise CalendarSyncError("Google hat eine unerwartete Antwort geliefert.")
        return response

    def _request(
        self,
        method: str,
        url: str,
        access_token: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        ignore_missing: bool = False,
    ) -> Any:
        merged = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        merged.update(headers or {})
        try:
            response = self._client.request(method, url, headers=merged, json=json, params=params)
        except httpx.TimeoutException as exc:
            raise CalendarSyncError("Zeitüberschreitung bei der Verbindung zu Google.") from exc
        except httpx.HTTPError as exc:
            raise CalendarSyncError(f"Google Kalender ist nicht erreichbar: {exc}") from exc

        # Der Body zuerst, der Status danach. Google liefert zu 401/403/404/412
        # eine Fehlerbeschreibung im JSON, und die ist aussagekräftiger als die
        # nackte Nummer.
        body: Any = None
        if response.content:
            try:
                body = response.json()
            except ValueError:
                body = None

        if ignore_missing and response.status_code == 404:
            return None
        if response.status_code == 401:
            raise CalendarAuthError(_error_text(body, "Google hat das Zugriffstoken abgelehnt."))
        if response.status_code == 403:
            raise CalendarAuthError(
                _error_text(body, "Google verweigert den Zugriff. Wahrscheinlich fehlt die Schreib-Erlaubnis.")
            )
        if response.status_code == 404:
            raise CalendarConfigError(_error_text(body, "Diesen Google-Kalender kennt es nicht."))
        if response.status_code == 412:
            raise CalendarConflictError(
                "Der Termin wurde bei Google zwischenzeitlich geändert; es wurde nichts überschrieben."
            )
        if response.status_code >= 400:
            raise CalendarSyncError(
                f"Google hat die Anfrage abgelehnt (HTTP {response.status_code}): "
                f"{_error_text(body, 'ohne nähere Angabe')}"
            )
        return body


def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    timeout_seconds: float = 10.0,
) -> OAuthTokens:
    """Tauscht den Autorisierungscode gegen ein Tokenpaket.

    Ohne ``code`` gibt es kein Token, und ohne Refresh-Token nur ein Ticket für
    eine Stunde. Beides zusammen ist ein Fehler, kein Erfolg mit Einschränkung:
    eine halb eingerichtete Integration, die beim nächsten Lauf überrascht
    aufgibt, ist schlechter als eine klare Meldung.
    """
    if not code.strip():
        raise GoogleOAuthError("Die Rückkehr von Google enthielt keinen Autorisierungscode.")
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        body = _post_form(
            client,
            TOKEN_ENDPOINT,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=timeout_seconds,
        )
    refresh_token = _string(body.get("refresh_token"))
    access_token = _string(body.get("access_token"))
    if not refresh_token:
        raise GoogleOAuthError(
            "Google hat kein Refresh-Token geliefert. Die Verbindung muss neu aufgebaut werden."
        )
    if not access_token:
        raise CalendarAuthError("Google hat kein Zugriffstoken geliefert.")
    lifetime = _int(body.get("expires_in")) or DEFAULT_TOKEN_LIFETIME_SECONDS
    return OAuthTokens(
        refresh_token=refresh_token,
        access_token=access_token,
        expires_at=utc_now() + timedelta(seconds=max(0, lifetime - TOKEN_MARGIN_SECONDS)),
        scope=_string(body.get("scope")),
    )


def tokens_to_credentials(tokens: OAuthTokens, existing: dict[str, str]) -> dict[str, str]:
    """Baut die Credential-Blase neu.

    **Das Access-Token wird nicht abgelegt.** Ein Refresh-Token holt es jederzeit
    neu; eine Kopie im Klartext wäre ein zweiter Weg zu Google mit unbekannter
    Lebensdauer. Nur der Refresh-Token, der Schlüssel und die brauchbaren Scopes
    kommen in die Blase.
    """
    merged = dict(existing)
    merged["refresh_token"] = tokens.refresh_token
    if tokens.scope:
        merged["token_scope"] = tokens.scope
    merged.pop("access_token", None)
    merged.pop("access_expires_at", None)
    return {key: value for key, value in merged.items() if value}


def describe_token_credentials(credentials: dict[str, str]) -> str:
    """Sagt dem Nutzer, was hinterlegt ist - ohne etwas preiszugeben."""
    if not credentials.get("refresh_token"):
        return "Der Zugriff auf Google Kalender ist noch nicht eingerichtet."
    scope = credentials.get("token_scope") or ""
    text = "Der Zugriff auf Google Kalender ist eingerichtet."
    if "calendar.events" in scope:
        text += " Google hat das Lesen und Schreiben von Terminen erlaubt."
    elif scope:
        text += f" Google hat nur eingeschränkte Rechte erteilt ({scope})."
    return text


def _to_api_event(event: RemoteEvent) -> dict[str, Any]:
    """Baut den Rumpf, den die Google-API erwartet."""
    return {
        "summary": event.title,
        "description": event.description or "",
        "start": _to_api_time(event.start, event.all_day),
        "end": _to_api_time(event.end, event.all_day),
    }


def _to_api_time(value: datetime, all_day: bool) -> dict[str, str]:
    if all_day:
        return {"date": value.astimezone(UTC).date().strftime("%Y-%m-%d")}
    return {"dateTime": _rfc3339(value), "timeZone": "UTC"}


def _from_api_event(entry: dict[str, Any]) -> RemoteEvent | None:
    """Ein Termin aus der API-Antwort, oder ``None``, wenn er unbrauchbar ist."""
    # Ein abgebrochener Termin wird nicht importiert: er steht in Google als
    # ausgefallen und würde hier als normaler Termin weiterleben.
    if _string(entry.get("status")) == "cancelled":
        return None
    uid = _string(entry.get("id"))
    start_body = entry.get("start")
    end_body = entry.get("end")
    if not uid or not isinstance(start_body, dict):
        return None
    all_day = "date" in start_body
    start = _read_time(start_body, all_day)
    if start is None:
        return None
    end = _read_time(end_body, all_day) if isinstance(end_body, dict) else None
    if end is None or end <= start:
        end = start + (timedelta(days=1) if all_day else timedelta(hours=1))
    return RemoteEvent(
        uid=uid,
        title=_string(entry.get("summary")) or "(ohne Titel)",
        start=start,
        end=end,
        description=_string(entry.get("description")) or None,
        all_day=all_day,
        etag=_string(entry.get("etag")),
        modified_at=_read_instant(_string(entry.get("updated"))),
    )


def _to_remote(body: Any, fallback: RemoteEvent) -> RemoteEvent:
    """Die Antwort auf ein Schreiben: Termindaten plus die neue Fassung."""
    if not isinstance(body, dict):
        raise CalendarSyncError("Google hat beim Speichern keine lesbare Antwort geliefert.")
    parsed = _from_api_event(body)
    if parsed is not None:
        return parsed
    # Google antwortet beim PUT/PATCH mit dem gespeicherten Termin. Sollte der
    # unlesbar sein, zählt der, den wir schicken wollten - aber mit dem ``etag``
    # aus der Antwort, sonst wäre der Zustand der Zeile verloren.
    return RemoteEvent(
        uid=_string(body.get("id")) or fallback.uid,
        title=fallback.title,
        start=fallback.start,
        end=fallback.end,
        description=fallback.description,
        all_day=fallback.all_day,
        etag=_string(body.get("etag")),
        modified_at=_read_instant(_string(body.get("updated"))),
    )


def _read_time(body: Any, all_day: bool) -> datetime | None:
    if not isinstance(body, dict):
        return None
    if all_day:
        day = _string(body.get("date"))
        if not day:
            return None
        try:
            return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    return _read_instant(_string(body.get("dateTime")))


def _read_instant(stamp: str) -> datetime | None:
    """Ein Zeitstempel aus der API. Ohne Zone wird er als UTC gelesen.

    Google liefert durchgehend ``Z``, es sei denn, der Kalender selbst trägt
    eine Zone; dann steht sie in ``timeZone`` und nicht am Wert. Ein Wert ohne
    Zone ist deshalb ein Sonderfall, und ``UTC`` ist die einzig vertretbare
    Annahme - alles andere hieße raten.
    """
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path_segment(value: str) -> str:
    """Eine Kalender-Id darf ``primary`` oder eine E-Mail sein; beide brauchen Maskierung."""
    return value.strip().replace("@", "%40").replace("/", "%2F").replace(" ", "%20")


def _expires_soon(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        return True
    return expires_at - timedelta(seconds=TOKEN_MARGIN_SECONDS) <= utc_now()


def _post_form(client: httpx.Client, url: str, data: dict[str, str], *, timeout: float) -> dict[str, Any]:
    try:
        response = client.post(url, data=data, headers={"Accept": "application/json"}, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise GoogleOAuthError("Zeitüberschreitung bei der Verbindung zu Google.") from exc
    except httpx.HTTPError as exc:
        raise GoogleOAuthError(f"Google ist nicht erreichbar: {exc}") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise GoogleOAuthError("Google hat eine nicht lesbare Antwort geliefert.") from exc
    if not isinstance(body, dict):
        raise GoogleOAuthError("Google hat eine unerwartete Antwort geliefert.")
    if response.status_code >= 400:
        raise GoogleOAuthError(_error_text(body, f"Google hat die Anfrage abgelehnt (HTTP {response.status_code})."))
    return body


def _error_text(body: Any, fallback: str) -> str:
    """Die Meldung aus einem Google-Fehlerobjekt, sonst die Vorgabe.

    Google liefert ``{"error": {"message": "..."}}``. Die Klartextmeldung ist
    meist englisch, aber sie nennt den konkreten Grund - und das ist mehr wert
    als eine deutsche Umschreibung, die denselben Fehler für sechs
    verschiedene Ursachen behauptet.
    """
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = _string(error.get("message"))
            if message:
                return message
        if isinstance(error, str) and error.strip():
            return error.strip()
    return fallback


def _items(body: dict[str, Any]) -> list[dict[str, Any]]:
    items = body.get("items")
    if not isinstance(items, list):
        return []
    return [entry for entry in items if isinstance(entry, dict)]


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _bool(value: Any) -> bool:
    return value is True or value == "true"
