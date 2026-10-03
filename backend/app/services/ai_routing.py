"""Die Reihenfolge der KI-Wege und die Herkunft der Antwort.

**Hier steht die Fallback-Kette, und nur hier.** Begruessungssatz
(``app/services/greeting.py``) und Dokumentenfrage
(``app/services/documents.py``) rufen beide ``generate_with_routes`` auf, statt
ihre eigene Reihenfolge zu bauen. Zwei Ketten wuerden zwei Antworten auf die
Frage geben, welche Stufe zuerst kommt - und die Frage ist eine.

Die Reihenfolge lautet:

1. **Lokaler Server** (``llama-server``, Ollama, LM Studio) - kostenlos, offline
   moeglich, und die Daten verlassen den Rechner nicht.
2. **Online-Provider** - wenn einer eingetragen und erreichbar ist.
3. **Der Aufrufer baut die Antwort selbst** aus seinen Daten. Das ist kein
   Notnagel, sondern der Normalfall: das Dashboard darf an einer fehlenden oder
   verweigerenden KI nie haengen.

**Warum lokal zuerst?** Weil es das Einzige ist, was nichts kostet und nichts
versendet. Ein Online-Provider ist schneller und meistens besser - das ist ein
echter Gegenspruch, und er wird nicht weggewogen, sondern **begrenzt**: der
lokale Weg bekommt ein kurzes Zeitbudget (siehe ``LOCAL_PROBE_TIMEOUT_SECONDS``),
scheitert er daran, geht es weiter zum Provider, und der Weg ist beziffert. Ein
lokales Modell, das in 20 Sekunden keine Antwort liefert, ist fuer ein
Dashboard schlechter als ein schneller Online-Antwort.

**Was mit den Noten passiert - und was ausdruecklich nicht.** Die Freigabe
``share_grades_with_ai`` bleibt die einzige Quelle der Notenzeilen und gilt fuer
*jeden* Weg, auch den lokalen. Ein lokales Modell macht die Freigabe nicht
ueberfluessig: sie ist eine Entscheidung der Lernenden ueber ihre Daten, und ein
lokaler Server kann ein geteiltes Notebook im Klassenraum sein. Was die Kette
stattdessen erreicht, ist etwas anderes und reicht: mit einem lokalen Modell
verlaesst die Anfrage im Normalfall den Rechner nicht, weil Stufe 1 seldom
genuegt. Wer die Freigabe nicht gesetzt hat, hat sie nicht gesetzt - auch lokal.

**Herkunft (``source``).** Das Muster ist im Repo bereits etabliert: die
Begruessung liefert ``ai``/``fallback``, die Dokumentenfrage
``ai``/``fallback``/``none``, und die Oberflaeche zeigt die Herkunft als Badge,
damit niemand einen Zufallssatz fuer eine KI-Aussage haelt. Diese Kette
verlaengert das um genau zwei Werte:

* ``local`` - ein Server auf diesem Rechner hat geantwortet
* ``provider`` - ein Online-Provider hat geantwortet
* ``fallback`` - kein Modell, der Aufrufer hat aus den Daten gebaut

``ai`` bleibt als **Altwert** gueltig und wird ueber :func:`normalize_source` zu
``provider``. Das ist noetig, weil ``greeting_cache.source`` heute schon Zeilen
mit ``ai`` enthaelt und ein alter Cacheeintrag sonst als ``fallback`` erschiene -
also als Datenluege.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, require_integration_encryption_key
from app.core.crypto import CredentialDecryptionError, decrypt_credentials
from app.db.entities import SyncSource
from app.services.ai_provider import AiProviderError, generate_text
from app.services.local_inference import (
    LOCAL_FAST_GENERATION_TIMEOUT_SECONDS,
    LOCAL_GENERATION_TIMEOUT_SECONDS,
    LOCAL_PROBE_TIMEOUT_SECONDS,
    RUNTIME_AUTO,
    RUNTIME_VALUES,
    generate_locally,
    probe_local_runtime,
    route_kind,
)
from app.services.local_runtimes import LocalRuntimeError, active_manager

logger = logging.getLogger("school_assistant")

__all__ = [
    "AI_KIND",
    "SOURCE_FALLBACK",
    "SOURCE_LOCAL",
    "SOURCE_PROVIDER",
    "AiRoute",
    "AiOutcome",
    "normalize_runtime",
    "normalize_source",
    "resolve_ai_routes",
    "generate_with_routes",
    "runtime_values",
]

#: Die Integrationsart, in der **jede** KI-Verbindung steht - lokal wie online.
#: Es gibt bewusst keine zweite Art: eine Nutzerin, die heute Ollama als
#: ``ai_provider`` eingetragen hat, braucht nach dieser Aenderung keinen zweiten
#: Eintrag (siehe ``app/services/integrations.py``).
AI_KIND: Final = "ai_provider"

SOURCE_LOCAL: Final = "local"
SOURCE_PROVIDER: Final = "provider"
SOURCE_FALLBACK: Final = "fallback"

#: ``runtime`` des eingebauten Weges: ein im Lernraum geladenes GGUF, das
#: ``app.services.local_runtimes`` per ``llama-server`` selbst startet. Seine
#: ``integration_id`` ist 0 - es gibt keine Zeile in ``sync_sources`` dafuer, und
#: 0 sortiert ihn innerhalb der lokalen Wege nach vorn.
RUNTIME_BUILTIN: Final = "builtin"
BUILTIN_INTEGRATION_ID: Final = 0

#: Werte, die ``normalize_source`` kennt. ``ai`` gehoert dazu, weil es der
#: gespeicherte Altwert des Tagescaches ist.
_KNOWN_SOURCES: Final[frozenset[str]] = frozenset({SOURCE_LOCAL, SOURCE_PROVIDER, SOURCE_FALLBACK, "ai"})

#: Zeitbudget eines Online-Antwort. Deutlich ueber ``INTEGRATION_TIMEOUT_SECONDS``
#: (10 s, das ist der *Verbindungs*-test) und unter dem lokalen Budget.
PROVIDER_GENERATION_TIMEOUT_SECONDS: Final = 30.0


@dataclass(frozen=True, slots=True)
class AiRoute:
    """Ein möglicher Weg zu einem Modell - noch ungetestet.

    ``runtime`` steht mit drin, obwohl der Weg schon klassifiziert ist: die
    Fehlermeldung eines Servers, der gar nicht laeuft, soll den Namen der
    Laufzeit nennen koennen ("Geprüft wurde http://host.docker.internal:11434").
    """

    source: str
    runtime: str
    integration_id: int
    name: str
    base_url: str
    model: str
    api_key: str

    @property
    def is_local(self) -> bool:
        return self.source == SOURCE_LOCAL

    @property
    def is_builtin(self) -> bool:
        return self.runtime == RUNTIME_BUILTIN


def builtin_route(model_id: str | None = None) -> AiRoute | None:
    """Der eingebaute Weg fuer ein geladenes Textmodell - oder ``None``.

    Ohne ``model_id`` gilt das zuletzt gewaehlte (sonst das groesste) Modell.
    ``base_url`` bleibt leer: der Server laeuft erst, wenn der Weg versucht wird.
    """
    manager = active_manager()
    if manager is None:
        return None
    spec = manager.text_model(model_id) if model_id else manager.preferred_text_model()
    if spec is None:
        return None
    return AiRoute(
        source=SOURCE_LOCAL,
        runtime=RUNTIME_BUILTIN,
        integration_id=BUILTIN_INTEGRATION_ID,
        name=f"Eingebaut: {spec.label}",
        base_url="",
        model=spec.id,
        api_key="",
    )


@dataclass(frozen=True, slots=True)
class AiAttempt:
    """Ein Weg, der versucht wurde und warum er nicht getragen hat.

    Die Liste wandert in ``logger.warning`` und nicht in die Antwort: die Nutzerin
    will einen Satz, keine Diagnose. Sie ist trotzdem nicht weg - sie ist der
    einzige Ort, an dem steht, *warum* die lokale Stufe ausfiel.
    """

    source: str
    runtime: str
    base_url: str
    message: str


@dataclass(frozen=True, slots=True)
class AiOutcome:
    """Das Ergebnis eines erfolgreichen Weges.

    ``None`` als ``text`` gibt es nicht: wer keinen Weg hatte, ruft diese
    Funktion gar nicht auf, sondern baut seinen Satz aus den Daten. Deshalb
    heisst ``text is None`` immer "nichts erzeugt worden" und der Aufrufer weiss
    ohne Ausnahme, dass er selbst zustaendig war.
    """

    text: str
    source: str
    model: str
    attempts: tuple[AiAttempt, ...] = ()


def normalize_runtime(value: str | None) -> str:
    """Ein unbekanntes ``runtime`` wird zu ``auto``.

    Kein Fehler: ein Handeintrag in der Datenbank mit ``runtime = "llamacpp"``
    darf die KI nicht lahmlegen. Was nicht erkannt wird, wird geraten - und
    geraten wird ueber die Adresse.
    """
    cleaned = (value or "").strip().lower().replace("-", "_")
    return cleaned if cleaned in RUNTIME_VALUES else RUNTIME_AUTO


def runtime_values() -> tuple[str, ...]:
    """Die erlaubten ``runtime``-Werte in Anzeigereihenfolge."""
    return RUNTIME_VALUES


def normalize_source(value: str | None) -> str:
    """Bringt einen gespeicherten ``source`` auf die heutige Bedeutung.

    ``ai`` wird zu ``provider``: das ist die Herkunft, die ein Modell ohne
    lokalen Server immer hatte. Alles Unbekannte wird ``fallback`` - im
    Zweifel wird *nicht* behauptet, ein Modell habe geantwortet. Das ist die
    Richtung, in der ein Fehler harmlos ist.
    """
    cleaned = (value or "").strip().lower()
    if cleaned == "ai":
        return SOURCE_PROVIDER
    if cleaned in _KNOWN_SOURCES:
        return cleaned
    return SOURCE_FALLBACK


def _profile_of(source: SyncSource) -> tuple[str, str]:
    """``(base_url, model)`` aus der Konfiguration, mit sinnvollen Defaults."""
    configuration = source.configuration or {}
    return str(configuration.get("base_url", "")).strip(), str(configuration.get("model") or RUNTIME_AUTO).strip()


def resolve_ai_routes(db: Session, user_id: int, settings: Settings) -> tuple[AiRoute, ...]:
    """Alle Wege dieses Nutzers, in der Reihenfolge, in der sie versucht werden.

    Sortiert wird nach ``lokal vor Online`` - oder umgekehrt, wenn
    ``settings.ai_local_first`` auf ``false`` steht. Die Id entscheidet danach nur
    noch **innerhalb** einer Gruppe: die Reihenfolge der Wege ist eine
    Produktentscheidung und keine Nebenwirkung der Datenbank.

    Ein fehlender ``INTEGRATION_ENCRYPTION_KEY`` und ein nicht entschluesselbarer
    Eintrag liefern schlicht **weniger** Wege und keine Ausnahme. Ein Eintrag ganz
    **ohne** Zugangsdaten ist dagegen ein vollwertiger Weg - ein lokaler Server
    braucht keinen Schluessel.
    """
    rows = db.scalars(
        select(SyncSource)
        .where(
            SyncSource.user_id == user_id,
            SyncSource.source_type == AI_KIND,
            SyncSource.enabled.is_(True),
        )
        .order_by(SyncSource.id)
    ).all()

    # **Kein** Zugangsschluessel noetig. Ein lokaler Server (Ollama, LM Studio,
    # llama-server) verlangt keinen, und eine Zeile ohne hinterlegte
    # Zugangsdaten ist darum kein unbrauchbarer Eintrag, sondern der
    # haeufigste. Wer hier `has_credentials` verlangt, wuerde genau die
    # Verbindungen aussortieren, um die es in dieser Welle geht - und niemand
    # faelle auf, weil `missing_fields` leer bleibt.
    usable = [row for row in rows if _profile_of(row)[0]]
    eingebaut = builtin_route()
    if not usable:
        return (eingebaut,) if eingebaut else ()

    try:
        key = require_integration_encryption_key(settings)
    except RuntimeError:
        logger.warning("Keine KI-Wege: INTEGRATION_ENCRYPTION_KEY fehlt.")
        return (eingebaut,) if eingebaut else ()

    routes: list[AiRoute] = [eingebaut] if eingebaut else []
    for row in usable:
        base_url, model = _profile_of(row)
        try:
            credentials = decrypt_credentials(row.credentials, key) if row.credentials else {}
        except CredentialDecryptionError as exc:
            logger.warning("KI-Eintrag %s ist nicht entschluesselbar: %s", row.id, exc)
            continue
        runtime = normalize_runtime(str((row.configuration or {}).get("runtime", "")))
        routes.append(
            AiRoute(
                source=route_kind(base_url, runtime),
                runtime=runtime,
                integration_id=row.id,
                name=row.name,
                base_url=base_url,
                model=model or RUNTIME_AUTO,
                api_key=credentials.get("api_key", ""),
            )
        )

    # Die Reihenfolge ist eine **Einstellung**, keine Konstante. Wer im
    # Schulnetz einen lokalen Server hat, der langsamer ist als der
    # Online-Dienst, stellt `AI_LOCAL_FIRST=false` - die Kette bleibt dieselbe,
    # nur die Reihenfolge dreht sich. Die Id entscheidet danach nur noch
    # innerhalb einer Gruppe.
    local_zuerst = settings.ai_local_first
    return tuple(sorted(routes, key=lambda route: (0 if route.is_local == local_zuerst else 1, route.integration_id)))

def _try_route(
    route: AiRoute,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    *,
    timeout: float,
    probe_before_generate: bool,
) -> AiAttempt | AiOutcome:
    """Ein Weg, einmal versucht. Gibt entweder ein Ergebnis oder eine Notiz zurueck.

    Die zwei Stufen sind Absicht und nicht Aufteilung: der lokale Weg fragt erst
    **billig** nach, ob der Server ueberhaupt da ist (``GET /models``, drei
    Sekunden), und erzeugt danach mit dem vollen Budget. Ohne diese Trennung
    wuerde ein toter Server das ganze Zeitbudget des Modelllaufs auffressen, bevor
    ueberhaupt klar ist, dass da nichts ist - und auf einer kaputten
    ``localhost``-Einstellung ist genau das der Normalfall.
    """
    base_url = route.base_url
    if route.is_builtin:
        # Der eingebaute Server wird hier gestartet (oder der laufende benutzt).
        # Keine Vorab-Probe: ``ensure_text_model`` wartet selbst, bis er antwortet.
        manager = active_manager()
        spec = manager.text_model(route.model) if manager else None
        if manager is None or spec is None:
            return AiAttempt(source=route.source, runtime=route.runtime, base_url="eingebaut",
                             message="Das eingebaute Modell ist nicht mehr geladen.")
        try:
            base_url = manager.ensure_text_model(spec)
        except LocalRuntimeError as exc:
            return AiAttempt(source=route.source, runtime=route.runtime, base_url="eingebaut", message=str(exc))
    elif route.is_local and probe_before_generate:
        status = probe_local_runtime(
            base_url=route.base_url,
            model=route.model,
            runtime=route.runtime,
            api_key=route.api_key,
            timeout_seconds=min(LOCAL_PROBE_TIMEOUT_SECONDS, timeout),
        )
        if not status.reachable:
            return AiAttempt(
                source=route.source,
                runtime=route.runtime,
                base_url=route.base_url,
                message=status.message,
            )

    try:
        if route.is_local:
            text = generate_locally(
                system_prompt,
                user_prompt,
                model=route.model,
                base_url=base_url,
                api_key=route.api_key,
                timeout_seconds=timeout,
                max_tokens=max_tokens,
            )
        else:
            text = generate_text(
                base_url=route.base_url,
                model=route.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                api_key=route.api_key,
                timeout_seconds=timeout,
                max_tokens=max_tokens,
            )
    except AiProviderError as exc:
        return AiAttempt(
            source=route.source,
            runtime=route.runtime,
            base_url=route.base_url,
            message=str(exc),
        )

    return AiOutcome(text=text, source=route.source, model=route.model)


def local_timeout_for(has_online_fallback: bool, *, alone: float, with_fallback: float) -> float:
    """Das Zeitbudget eines lokalen Weges, je nachdem ob etwas danach wartet.

    Das ist die ganze Stufung in einer Zeile: mit einem Online-Provider im
    Rueckgrat bekommen lokale Wege das **kurze** Budget, ohne Provider das volle.
    Ein zweiter Versuch mit dem langen Budget waere die naheliegende Alternative
    und ist hier bewusst **nicht** gebaut: er wuerde die Wartezeit verdoppeln, um
    eine Antwort zu bekommen, die derselbe Server vermutlich auch beim zweiten Mal
    nicht liefert - ein langsames Modell ist langsam, nicht abhaengig davon, wie oft
    man fragt.
    """
    return with_fallback if has_online_fallback else alone


def generate_with_routes(
    routes: tuple[AiRoute, ...],
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 200,
    local_timeout: float = LOCAL_GENERATION_TIMEOUT_SECONDS,
    local_fast_timeout: float = LOCAL_FAST_GENERATION_TIMEOUT_SECONDS,
    provider_timeout: float = PROVIDER_GENERATION_TIMEOUT_SECONDS,
    probe_before_generate: bool = True,
) -> AiOutcome | None:
    """Geht die Wege der Reihe nach durch und liefert die erste Antwort.

    ``None`` heisst: **jeder** Weg ist gescheitert oder es gab keinen. Der
    Aufrufer baut dann seinen Satz aus den Daten - so wie er es ohne KI auch
    täte. Es gibt hier bewusst keinen Ersatztext: eine erfundene KI-Antwort wäre
    nicht als eine erkennbar, und das ist der ganze Zweck von ``source``.

    Ein Fehler wird **gefangen**, nicht gereicht. Eine kaputte KI-Konfiguration
    oder ein abgestuerzter ``llama-server`` darf das Dashboard nicht lahmlegen -
    dieselbe Regel wie bei der Begruessung. Was trotzdem passiert, landet in
    ``logger.warning`` mit dem Grund.
    """
    online_vorhanden = any(not route.is_local for route in routes)
    lokales_budget = local_timeout_for(
        online_vorhanden,
        alone=local_timeout,
        with_fallback=local_fast_timeout,
    )

    attempts: list[AiAttempt] = []
    for route in routes:
        try:
            ergebnis = _try_route(
                route,
                system_prompt,
                user_prompt,
                max_tokens,
                timeout=lokales_budget if route.is_local else provider_timeout,
                probe_before_generate=probe_before_generate,
            )
        except Exception as exc:  # noqa: BLE001 - ein Fremdfehler darf den Satz nicht kippen
            logger.warning("KI-Weg %s hat unerwartet versagt: %s", route.base_url, exc)
            attempts.append(
                AiAttempt(source=route.source, runtime=route.runtime, base_url=route.base_url, message=str(exc))
            )
            continue
        if isinstance(ergebnis, AiOutcome):
            if not ergebnis.text.strip():
                # Ein leerer Text ist schlechter als der ehrliche Daten-Satz.
                attempts.append(
                    AiAttempt(
                        source=route.source,
                        runtime=route.runtime,
                        base_url=route.base_url,
                        message="Die Antwort war leer.",
                    )
                )
                continue
            if attempts:
                logger.warning(
                    "KI: %d Weg(e) vor '%s' gescheitert: %s",
                    len(attempts),
                    ergebnis.source,
                    "; ".join(f"{versuch.base_url}: {versuch.message}" for versuch in attempts),
                )
            return AiOutcome(
                text=ergebnis.text,
                source=ergebnis.source,
                model=ergebnis.model,
                attempts=tuple(attempts),
            )
        attempts.append(ergebnis)

    if attempts:
        logger.warning(
            "Kein KI-Weg hat geantwortet: %s",
            "; ".join(f"{versuch.base_url}: {versuch.message}" for versuch in attempts),
        )
    return None

