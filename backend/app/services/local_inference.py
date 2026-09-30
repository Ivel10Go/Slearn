"""Lokale Inference: ein OpenAI-kompatibler Server auf dem eigenen Rechner.

**Die Startentscheidung, und sie ist eine bewusste.**

Es gibt zwei Wege, ein GGUF-Modell aus dem Katalog (``app/services/model_catalog.py``)
zum Laufen zu bringen:

1. Der Backend-Container **startet** ``llama-server`` als Kindprozess. Das braucht
   das C++-Binary im Image, macht das Image um hunderte Megabyte größer und
   braucht CPU- und RAM-Reserven, die dann auch das Backend und PostgreSQL
   beanspruchen. Ein abgestürzter Kindprozess wäre außerdem ein Zustand, den
   jemand aufräumen müsste.
2. Die Lernende **bringt ihren eigenen Server mit** und das Backend spricht nur
   dagegen.

Diese Welle baut Weg 2. Der Grund ist nicht Bequemlichkeit: Weg 1 liefert im
Docker-Image etwas, das auf einem Raspberry Pi - dem ausdrücklich unterstützten
Ziel - gar nicht lauffähig wäre, und der Pfad ließe sich hier nicht einmal
verifizieren, weil in dieser Umgebung kein Compiler, kein Rust und keine native
Toolchain existieren.

Was daraus inhaltlich folgt, muss ausgesprochen werden: **``llama.cpp`` ist über
Weg 2 abgedeckt.** ``llama-server`` ist ein OpenAI-kompatibler Server wie Ollama
und LM Studio, spricht also genau das Protokoll, das ``AiProviderClient`` in
``app/services/ai_provider.py`` bereits beherrscht. Es gibt deshalb hier
*keinen* zweiten Weg für llama.cpp, sondern eine Liste bekannter Laufzeiten, unter
denen ``llama_cpp`` steht.

**Was diese Datei nicht ist:** kein Modell-laden, kein Token-erzeugen, kein
Tokenizer. Sie erkennt, ob ein Server erreichbar ist, und stellt die Anfrage.
Jeder Test hier läuft gegen einen **gemockten** OpenAI-kompatiblen Server. Gegen
einen echten ``llama-server``, echtes Ollama oder echtes LM Studio ist hier
**nichts verifiziert**.

Der Container-Host ist der eigentliche Fallstrick (siehe ``AGENTS.md``): Das
Backend läuft in Docker, und ``localhost`` ist damit *der Container*, nicht der
Rechner der Lernenden. Ein auf dem Host laufendes Ollama ist unter
``localhost:11434`` nicht erreichbar, sondern nur unter
``host.docker.internal:11434``. Deshalb stehen hier die Presets mit beiden
Adressen und ``describe_container_trap`` als deutscher Satz bereit - die
Oberfläche zeigt ihn, statt ihn in einen Kommentar zu schreiben.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit, urlunsplit

from app.services.ai_provider import AiProviderError, AiProviderModels, generate_text, probe_ai_provider

__all__ = [
    "CONTAINER_HOST",
    "LOCAL_FAST_GENERATION_TIMEOUT_SECONDS",
    "LOCAL_GENERATION_TIMEOUT_SECONDS",
    "LOCAL_PROBE_TIMEOUT_SECONDS",
    "LOCAL_RUNTIME_KINDS",
    "LocalRuntimePreset",
    "LocalRuntimeStatus",
    "RUNTIME_ONLINE",
    "RUNTIME_PRESETS",
    "RUNTIME_VALUES",
    "container_loopback_hosts",
    "describe_container_trap",
    "docker_host_for",
    "generate_locally",
    "is_local_base_url",
    "is_online_runtime",
    "is_private_host",
    "probe_local_runtime",
    "route_kind",
    "runtime_preset",
]


#: Der Name, unter dem der Docker-Host aus dem Container erreichbar ist. Der
#: Compose-Stack setzt ihn nicht selbst - Docker löst ihn auf - deshalb ist hier
#: **kein** Konfigurationswert nötig und keiner sinnvoll.
CONTAINER_HOST: Final = "host.docker.internal"

#: Namen, die **immer** auf den Container selbst zeigen. In einem
#: Docker-Netzwerk ist jeder davon eine geschlossene Tuer zum Host.
CONTAINER_LOOPBACK: Final[frozenset[str]] = frozenset(
    {
        "localhost",
        "0.0.0.0",
        "::1",
        "::",
    }
)

#: Namen, die aus einem Container auf den Host zeigen. Sie sind die *richtige*
#: Adresse für einen Server, der auf dem Rechner läuft.
HOST_BRIDGES: Final[frozenset[str]] = frozenset(
    {
        CONTAINER_HOST,
        "host.containers.internal",
        "gateway.docker.internal",
    }
)

#: Auf dem Rechner laufende Dienste bekommen unter Linux per mDNS einen Namen
#: wie ``LM-Studio.local``. Das ist derselbe Rechner, nur anders geschrieben.
MDNS_SUFFIX: Final = ".local"

#: ``runtime`` in der Konfiguration einer ``ai_provider``-Integration.
RUNTIME_AUTO: Final = "auto"
RUNTIME_OLLAMA: Final = "ollama"
RUNTIME_LM_STUDIO: Final = "lm_studio"
RUNTIME_LLAMA_CPP: Final = "llama_cpp"
RUNTIME_ONLINE: Final = "online"

#: Die erlaubten Werte, in dieser Reihenfolge. Die Reihenfolge ist die
#: Anzeigereihenfolge im Formular.
RUNTIME_VALUES: Final[tuple[str, ...]] = (
    RUNTIME_AUTO,
    RUNTIME_OLLAMA,
    RUNTIME_LM_STUDIO,
    RUNTIME_LLAMA_CPP,
    RUNTIME_ONLINE,
)

#: Die Laufzeiten, die **lokal** sind. ``auto`` steht nicht dabei: es ist keine
#: Laufzeit, sondern die Entscheidung, aus der Adresse zu raten.
LOCAL_RUNTIME_KINDS: Final[tuple[str, ...]] = (RUNTIME_OLLAMA, RUNTIME_LM_STUDIO, RUNTIME_LLAMA_CPP)

#: Zeitbudget der billigen Erreichbarkeitsfrage vor einem lokalen Lauf. Kurz,
#: weil ein nicht laufender Server sofort ``ECONNREFUSED`` liefert und ein
#: langsames Modell erst beim Erzeugen auffällt.
LOCAL_PROBE_TIMEOUT_SECONDS: Final = 3.0

#: Zeitbudget eines lokalen Modelllaufs, **wenn er der einzige Weg ist**. Ein
#: 1.5B-Modell auf CPU braucht fuer einen Absatz mehrere Sekunden, ein 8B-Modell
#: deutlich ueber eine halbe Minute.
LOCAL_GENERATION_TIMEOUT_SECONDS: Final = 60.0

#: Dasselbe, aber kurz - fuer den Fall, dass hinter dem lokalen Weg noch ein
#: Online-Provider wartet. Siehe ``app.services.ai_routing.local_timeout_for``.
LOCAL_FAST_GENERATION_TIMEOUT_SECONDS: Final = 15.0


@dataclass(frozen=True, slots=True)
class LocalRuntimePreset:
    """Eine bekannte lokale Laufzeit mit ihren Angaben.

    ``base_url`` und ``native_base_url`` sind bewusst **beide** vorhanden. Der
    Compose-Stack ist die dokumentierte Betriebsart, also ist
    ``host.docker.internal`` der Vorschlag; wer das Backend nativ startet, braucht
    genau die andere. Die Oberfläche zeigt beide an, statt eine zu wählen und die
    andere zu verschweigen.
    """

    kind: str
    label: str
    port: int
    default_model: str
    needs_api_key: bool
    note: str

    @property
    def base_url(self) -> str:
        return f"http://{CONTAINER_HOST}:{self.port}"

    @property
    def native_base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


#: Die Presets. Drei Laufzeiten, weil das ToDo zwei nennt (``llama.cpp`` als
#: dritte) und jede davon eine eigene Standardadresse und ein eigenes Startverhalten hat.
RUNTIME_PRESETS: Final[tuple[LocalRuntimePreset, ...]] = (
    LocalRuntimePreset(
        kind=RUNTIME_OLLAMA,
        label="Ollama",
        port=11434,
        default_model="llama3.2",
        needs_api_key=False,
        note="Ollama laeuft als Dienst auf deinem Rechner und ist unter Port 11434 erreichbar.",
    ),
    LocalRuntimePreset(
        kind=RUNTIME_LM_STUDIO,
        label="LM Studio",
        port=1234,
        default_model="Auto",
        needs_api_key=False,
        note=(
            "LM Studio muss im Reiter 'Developer' mit einem geladenen Modell laufen; "
            "der Server hört auf Port 1234."
        ),
    ),
    LocalRuntimePreset(
        kind=RUNTIME_LLAMA_CPP,
        label="llama.cpp (llama-server)",
        port=8080,
        default_model="Auto",
        needs_api_key=False,
        note=(
            "llama-server aus llama.cpp, von Hand mit einem GGUF aus dem Modellkatalog gestartet, "
            "zum Beispiel: llama-server -m Modell.gguf --port 8080"
        ),
    ),
)


def runtime_preset(kind: str) -> LocalRuntimePreset | None:
    """Das Preset zu dieser Laufzeit, sonst ``None`` (``auto`` hat keins)."""
    for preset in RUNTIME_PRESETS:
        if preset.kind == kind:
            return preset
    return None


def _host_of(base_url: str) -> str:
    """Der Host aus einer URL, kleingeschrieben und ohne IPv6-Klammern.

    Wirft **nicht**: das Auswerten einer fremden Adresse darf keinen
    ``ValueError`` in einen Aufrufer schicken, der daraus nur einen Satz bauen
    wollte.
    """
    try:
        parts = urlsplit(base_url.strip())
    except ValueError:
        return ""
    host = (parts.hostname or "").strip().lower()
    return host.strip("[]")


def _port_of(base_url: str) -> int | None:
    try:
        return urlsplit(base_url.strip()).port
    except ValueError:
        return None


def _is_loopback_name(host: str) -> bool:
    if not host:
        return False
    if host in CONTAINER_LOOPBACK:
        return True
    # 127.0.0.0/8 ist der ganze Loopback-Bereich, nicht nur 127.0.0.1.
    return host.startswith("127.")


def is_private_host(host: str) -> bool:
    """Liegt der Host im eigenen Netz (RFC 1918, Link-Local, Unique Local)?

    Bewusst über :mod:`ipaddress` und nicht über eine Liste von Zeichenketten:
    ``192.168.1.7``, ``10.0.0.1`` und ``fd12:3456::1`` sind dieselbe Antwort,
    und eine Liste müsste gepflegt werden.
    """
    candidate = host.strip().lower().strip("[]")
    if not candidate:
        return False
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return bool(address.is_private or address.is_link_local)


def container_loopback_hosts() -> tuple[str, ...]:
    """Die Namen, die im Container auf den Container zeigen, sortiert."""
    return tuple(sorted(CONTAINER_LOOPBACK))


def is_container_loopback(base_url: str) -> bool:
    """Zeigt diese Adresse auf den Container selbst statt auf den Rechner?"""
    return _is_loopback_name(_host_of(base_url))


def is_local_base_url(base_url: str) -> bool:
    """Liegt der Server auf diesem Rechner oder in dessen Netz?

    Das ist die **Ableitung**, die ein Eintrag ohne ``runtime`` benutzt. Sie ist
    bewusst großzügig: ein Ollama im LAN des Klassenzimmers ist genauso lokal wie
    eines auf dem Schreibtisch, und für beide gilt derselbe Datenschutz.
    """
    host = _host_of(base_url)
    if not host:
        return False
    if _is_loopback_name(host):
        return True
    if host in HOST_BRIDGES:
        return True
    if host.endswith(MDNS_SUFFIX):
        return True
    return is_private_host(host)


def is_online_runtime(runtime: str, base_url: str) -> bool:
    """Geht der Aufruf an einen Server außerhalb des eigenen Netzes?

    ``runtime`` entscheidet, wenn es etwas anderes als ``auto`` sagt - so kann
    jemand einen OpenAI-Endpunkt hinter einer privaten Adresse als Online-Anbieter
    markieren. Sonst entscheidet die Adresse.
    """
    if runtime == RUNTIME_ONLINE:
        return True
    if runtime in LOCAL_RUNTIME_KINDS:
        return False
    return not is_local_base_url(base_url)


def route_kind(base_url: str, runtime: str) -> str:
    """``"local"`` oder ``"provider"`` für diese Verbindung.

    Das ist die **eine** Stelle, an der aus einer Integration ein Weg wird. Der
    Aufrufer fragt nicht selbst nach ``localhost``, und die Oberfläche nicht
    selbst either - sonst gäbe es drei Antworten auf dieselbe Frage.
    """
    return "provider" if is_online_runtime(runtime, base_url) else "local"


def docker_host_for(base_url: str) -> str | None:
    """Die Container-taugliche Fassung einer lokalen Adresse, sonst ``None``.

    ``http://localhost:11434`` wird zu ``http://host.docker.internal:11434``.
    Eine Adresse, die schon über eine Host-Brücke oder einen privaten Host geht,
    bleibt unverändert - sie ist richtig. Hat die URL keine Portangabe, wird auch
    keine erfunden: der Aufrufer soll nicht raten müssen.
    """
    host = _host_of(base_url)
    port = _port_of(base_url)
    if not host or port is None:
        return None
    if not (_is_loopback_name(host) or host == "localhost"):
        return None
    scheme = "http"
    try:
        parsed = urlsplit(base_url.strip())
        if parsed.scheme in {"http", "https"}:
            scheme = parsed.scheme
    except ValueError:
        pass
    return urlunsplit((scheme, f"{CONTAINER_HOST}:{port}", "", "", ""))


def describe_container_trap(base_url: str) -> str | None:
    """Der deutsche Satz, der vor ``localhost`` in einer Docker-URL warnt.

    ``None``, wenn die Adresse nicht betroffen ist. Bewusst **nicht** an
    ``is_local_base_url`` gekoppelt: Das gilt für jede lokale Adresse, gewarnt
    werden muss aber nur bei den Loopback-Namen, weil nur die im Container
    garantiert falsch sind.
    """
    replacement = docker_host_for(base_url)
    if replacement is None:
        return None
    return (
        "Diese Adresse zeigt aus dem Docker-Container auf den Container selbst, nicht auf deinen Rechner. "
        f"Ein lokaler Server ist unter {replacement} erreichbar."
    )


@dataclass(frozen=True, slots=True)
class LocalRuntimeStatus:
    """Was die billige Erreichbarkeitsfrage über einen lokalen Server ergab.

    Der Unterschied zwischen ``reachable`` und ``ok`` ist Absicht: ein Server
    kann erreichbar sein und das konfigurierte Modell trotzdem nicht kennen. Das
    ist ein Konfigurationsfehler und kein Netzproblem, und die Oberfläche soll
    die beiden nicht gleich behandeln.
    """

    reachable: bool
    ok: bool
    base_url: str
    runtime: str
    model_id: str | None
    available: tuple[str, ...]
    message: str


def _describe_failure(base_url: str, runtime: str, error: AiProviderError) -> str:
    """Macht aus einem Provider-Fehler einen deutschen Satz mit dem Fallstrick-Hinweis."""
    message = str(error)
    trap = describe_container_trap(base_url)
    if trap is not None:
        return f"{message} {trap}"
    preset = runtime_preset(runtime)
    if preset is not None:
        return f"{message} Geprüft wurde {preset.base_url}."
    return message


def probe_local_runtime(
    base_url: str,
    model: str,
    runtime: str = RUNTIME_AUTO,
    api_key: str = "",
    timeout_seconds: float = LOCAL_PROBE_TIMEOUT_SECONDS,
) -> LocalRuntimeStatus:
    """Fragt einen lokalen Server, ob er da ist und das Modell kennt.

    Bewusst über ``GET /models`` und nicht über eine Generierung: der Test darf
    nichts kosten, keine Token verbrauchen und nicht minutenlang hängen. Genau
    derselbe Aufruf wie beim Verbindungstest einer ``ai_provider``-Integration -
    ein **neuer** Testweg wäre eine zweite Antwort auf dieselbe Frage.
    """
    try:
        models: AiProviderModels = probe_ai_provider(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
    except AiProviderError as exc:
        return LocalRuntimeStatus(
            reachable=False,
            ok=False,
            base_url=base_url,
            runtime=runtime,
            model_id=None,
            available=(),
            message=_describe_failure(base_url, runtime, exc),
        )
    return LocalRuntimeStatus(
        reachable=True,
        ok=True,
        base_url=base_url,
        runtime=runtime,
        model_id=models.model_id,
        available=models.available,
        message=f"Der lokale Server antwortet, {models.describe()}",
    )


def generate_locally(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    base_url: str,
    api_key: str = "",
    timeout_seconds: float = LOCAL_GENERATION_TIMEOUT_SECONDS,
    max_tokens: int = 200,
) -> str:
    """Erzeugt Text über einen lokalen OpenAI-kompatiblen Server.

    Wirft ``AiProviderError`` bei jedem Fehler - **das ist hier die richtige
    Sorte Fehler**. Ein abgestuerzter ``llama-server`` ist eine kaputte
    Fremdverbindung und kein Serverfehler dieses Prozesses; wer das faengt,
    entscheidet, was passiert. ``app/services/ai_routing.py`` faengt es und geht
    zum naechsten Weg.

    Das Standard-Timeout liegt ueber dem des Verbindungstests, weil ein
    Modelllauf auf CPU-Minuten dauern und kein Serverfehler sein kann.
    """
    return generate_text(
        base_url=base_url,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
    )
