"""Client für OpenAI-kompatible Endpunkte (Ollama, LM Studio, LiteLLM, OpenAI).

Der Verbindungstest läuft bewusst über ``GET /models`` und nicht über eine
Generierung: Das ist der billigste Read-only-Check, der in einer einzigen
Anfrage Erreichbarkeit, API-Schlüssel und Verfügbarkeit des konfigurierten
Modells klärt, ohne Token zu verbrauchen oder unerwartete Kosten zu erzeugen.

Ein Aufruf sieht so aus::

    GET {base_url}/models
    Authorization: Bearer <api_key>      # nur wenn ein Schlüssel hinterlegt ist

Die Antwort ist die OpenAI-Standardliste::

    {"object": "list", "data": [{"id": "llama3.2:latest"}, ...]}

Lokale Server wie Ollama brauchen keinen Schlüssel, gehostete Anbieter schon.
Fehlt der Schlüssel, antworten sie mit 401 - deshalb wird ein gespeicherter
Schlüssel immer mitgeschickt, auch wenn er optional ist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlparse

import httpx

_DEFAULT_PORT_PATH: Final = "/v1"
_MODELS_SEGMENT: Final = "/models"
_CHAT_SEGMENT: Final = "/chat/completions"
_MAX_SUGGESTIONS: Final = 4
_AUTO_VALUES: Final = frozenset({"auto", "automatisch", "*", "default"})


class AiProviderError(RuntimeError):
    """Basis für alle Fehler aus der Kommunikation mit dem KI-Provider."""


class AiProviderConfigError(AiProviderError):
    """Die hinterlegte Konfiguration ist unbrauchbar (falsche Adresse, unbekanntes Modell)."""


class AiProviderAuthError(AiProviderError):
    """Der API-Schlüssel fehlt oder wurde vom Server abgelehnt."""


@dataclass(frozen=True, slots=True)
class AiProviderModels:
    """Ergebnis eines erfolgreichen Verbindungstests."""

    model_id: str
    available: tuple[str, ...]
    auto_selected: bool

    def describe(self) -> str:
        total = len(self.available)
        plural = "Modell" if total == 1 else "Modelle"
        if self.auto_selected:
            return (
                f"{total} {plural} verfügbar, "
                f"automatisch gewählt wurde '{self.model_id}'."
            )
        return f"Modell '{self.model_id}' ist verfügbar ({total} {plural} insgesamt)."


def build_models_url(base_url: str) -> str:
    """Leitet die ``/models``-URL aus der Basis-URL ab.

    Akzeptiert ``https://api.openai.com/v1`` ebenso wie das sehr häufige
    ``http://localhost:11434`` ohne den ``/v1``-Zusatz. Eine fehlende ``/v1``
    Stufe ist der häufigste Konfigurationsfehler, deshalb wird sie
    automatisch ergänzt. Eine URL, die bereits auf ``/models`` endet, wird
    unverändert übernommen.
    """
    root = _api_root(base_url)
    if root.endswith(_MODELS_SEGMENT):
        return root
    return f"{root}{_MODELS_SEGMENT}"


def build_chat_url(base_url: str) -> str:
    """Leitet die ``/chat/completions``-URL aus der Basis-URL ab.

    Ein Endpunkt, der direkt auf ``/models`` endet, wird um genau dieses
    Segment gekuerzt, weil die Basis-URL sonst als Basis des Gespraechs
    interpretiert wuerde und der Server 404 antwortet.
    """
    root = _api_root(base_url)
    if root.endswith(_MODELS_SEGMENT):
        root = root[: -len(_MODELS_SEGMENT)]
    return f"{root}{_CHAT_SEGMENT}"


def _api_root(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AiProviderConfigError("Die KI-Adresse muss eine vollständige http(s)-URL sein.")
    path = parsed.path.rstrip("/")
    if not path:
        path = _DEFAULT_PORT_PATH
    if path.endswith(_MODELS_SEGMENT):
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


class AiProviderClient:
    """Fragt die Modellliste eines OpenAI-kompatiblen Endpunkts ab."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._url = build_models_url(base_url)
        self._chat_url = build_chat_url(base_url)
        self._base_url = base_url
        self._model = model
        self._api_key = api_key
        self._client = httpx.Client(timeout=timeout_seconds, follow_redirects=True)

    def __enter__(self) -> AiProviderClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def check_connection(self) -> AiProviderModels:
        """Prüft Erreichbarkeit, Schlüssel und ob das konfigurierte Modell existiert."""
        available = self._fetch_models()
        wanted = self._model.strip()
        if not available:
            raise AiProviderConfigError("Der KI-Provider meldet keine Modelle.")
        if wanted.lower() in _AUTO_VALUES:
            return AiProviderModels(model_id=available[0], available=available, auto_selected=True)
        matched = _match_model(available, wanted)
        if matched is None:
            raise AiProviderConfigError(_unknown_model_message(wanted, available))
        return AiProviderModels(model_id=matched, available=available, auto_selected=False)

    def generate(self, system_prompt: str, user_prompt: str, *, max_tokens: int = 200) -> str:
        """Erzeugt Text ueber ``POST /chat/completions``.

        Bewusst ueber die Chat-Vervollstaendigung und nicht ueber ``/models``:
        Hier wird tatsaechlich ein Modelllauf bezahlt, deshalb gehoert der Aufruf
        nicht in einen Verbindungstest. ``model`` darf ``Auto`` sein, dann wird
        wie beim Test das erste Modell der Liste verwendet.
        """
        model = self._resolve_model()

        try:
            response = self._client.post(
                self._chat_url,
                headers=self._headers(),
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.4,
                },
            )
        except httpx.TimeoutException as exc:
            raise AiProviderError("Zeitüberschreitung bei der Generierung.") from exc
        except httpx.HTTPError as exc:
            raise AiProviderError(f"Der KI-Provider ist nicht erreichbar: {exc}") from exc

        if response.status_code in {401, 403}:
            raise AiProviderAuthError("Der KI-Provider hat den API-Schlüssel abgelehnt oder erwartet keinen.")
        if response.status_code == 404:
            raise AiProviderConfigError(
                f"Der KI-Provider kennt '{self._chat_url}' nicht. "
                "Häufige Ursache: die Basis-URL muss mit /v1 enden."
            )
        if response.status_code >= 400:
            raise AiProviderError(f"Der KI-Provider hat die Anfrage abgelehnt (HTTP {response.status_code}).")

        try:
            body = response.json()
        except ValueError as exc:
            raise AiProviderError("Der KI-Provider hat eine nicht lesbare Antwort geliefert.") from exc
        return _extract_text(body)

    def _resolve_model(self) -> str:
        """Bestimmt das Modell fuer einen Lauf, ``Auto`` aufloesend."""
        wanted = self._model.strip()
        if wanted.lower() in _AUTO_VALUES:
            available = self._fetch_models()
            if not available:
                raise AiProviderConfigError("Der KI-Provider meldet keine Modelle.")
            return available[0]
        return wanted

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _fetch_models(self) -> tuple[str, ...]:
        try:
            response = self._client.get(self._url, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise AiProviderError("Zeitüberschreitung bei der Verbindung zum KI-Provider.") from exc
        except httpx.HTTPError as exc:
            raise AiProviderError(f"Der KI-Provider ist nicht erreichbar: {exc}") from exc

        if response.status_code in {401, 403}:
            raise AiProviderAuthError("Der KI-Provider hat den API-Schlüssel abgelehnt oder erwartet keinen.")
        if response.status_code == 404:
            raise AiProviderConfigError(
                f"Der KI-Provider kennt '{self._url}' nicht. "
                "Häufige Ursache: die Basis-URL muss mit /v1 enden."
            )
        if response.status_code >= 500:
            raise AiProviderError(f"Der KI-Provider meldet einen Fehler (HTTP {response.status_code}).")
        if response.status_code >= 400:
            raise AiProviderError(f"Der KI-Provider hat die Anfrage abgelehnt (HTTP {response.status_code}).")

        try:
            body = response.json()
        except ValueError as exc:
            raise AiProviderError("Der KI-Provider hat eine nicht lesbare Antwort geliefert.") from exc
        if not isinstance(body, dict):
            raise AiProviderError("Der KI-Provider hat eine unerwartete Antwort geliefert.")
        return _parse_models(body)


def _extract_text(body: object) -> str:
    """Zieht den Text aus einer OpenAI-kompatiblen Antwort.

    Bewusst tolerant: manche Server liefern ``content`` als Liste von Teilen mit
    ``{"type": "text", "text": ...}`` statt als einfachen String. Ein leeres
    Ergebnis ist ein Fehler, weil dann kein brauchbarer Satz entstuende und der
    Aufrufer stillschweigend seinen Fallback zeigen wuerde.
    """
    if not isinstance(body, dict):
        raise AiProviderError("Der KI-Provider hat eine unerwartete Antwort geliefert.")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AiProviderError("Der KI-Provider hat keinen Text geliefert.")
    first = choices[0]
    if not isinstance(first, dict):
        raise AiProviderError("Der KI-Provider hat eine unerwartete Antwort geliefert.")
    message = first.get("message")
    if not isinstance(message, dict):
        raise AiProviderError("Der KI-Provider hat keinen Text geliefert.")
    content = message.get("content")
    if isinstance(content, list):
        teile = [
            teil["text"]
            for teil in content
            if isinstance(teil, dict) and isinstance(teil.get("text"), str)
        ]
        content = "".join(teile)
    if not isinstance(content, str) or not content.strip():
        raise AiProviderError("Der KI-Provider hat keinen Text geliefert.")
    return content.strip()


def _parse_models(body: dict[str, Any]) -> tuple[str, ...]:
    data = body.get("data")
    if not isinstance(data, list):
        raise AiProviderError("Der KI-Provider hat keine Modellliste geliefert.")
    names: list[str] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        for key in ("id", "name", "model"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
                break
    return tuple(dict.fromkeys(names))


def _base_name(name: str) -> str:
    return name.split(":", 1)[0].strip().lower()


def _match_model(available: tuple[str, ...], wanted: str) -> str | None:
    target = wanted.strip().lower()
    for name in available:
        if name.lower() == target:
            return name
    base = _base_name(target)
    for name in available:
        if _base_name(name) == base:
            return name
    return None


def _unknown_model_message(wanted: str, available: tuple[str, ...]) -> str:
    suggestions = ", ".join(f"'{name}'" for name in available[:_MAX_SUGGESTIONS])
    if len(available) > _MAX_SUGGESTIONS:
        suggestions += f" und {len(available) - _MAX_SUGGESTIONS} weitere"
    return (
        f"Der KI-Provider kennt das Modell '{wanted}' nicht. "
        f"Verfügbar sind: {suggestions}. "
        "Mit 'Auto' wird automatisch das erste Modell gewählt."
    )


def probe_ai_provider(
    base_url: str,
    model: str,
    api_key: str = "",
    timeout_seconds: float = 10.0,
) -> AiProviderModels:
    """Bequemlichkeit für Aufrufer, die nur die Verbindung prüfen wollen."""
    with AiProviderClient(base_url, model, api_key, timeout_seconds) as client:
        return client.check_connection()


def generate_text(
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    api_key: str = "",
    timeout_seconds: float = 30.0,
    max_tokens: int = 200,
) -> str:
    """Bequemlichkeit für Aufrufer, die nur einen Text erzeugen wollen.

    Das Standard-Timeout liegt bewusst ueber dem des Verbindungstests: ein
    Modelllauf auf einer lokalen CPU braucht spuerbar laenger als ein
    ``GET /models``.
    """
    with AiProviderClient(base_url, model, api_key, timeout_seconds) as client:
        return client.generate(system_prompt, user_prompt, max_tokens=max_tokens)
