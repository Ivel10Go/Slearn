"""Die Antworten von ``/api/v1/ai-runtime``: bekannte lokale Laufzeiten.

Eine eigene, winzige Route neben ``/api/v1/models``. Sie beantwortet genau eine
Frage, die die Oberflaeche beim Anlegen einer ``ai_provider``-Verbindung stellt:
*welche lokalen Server gibt es, unter welcher Adresse, und was muss ich tun,
damit der Backend sie von hier aus erreicht?*

Der ``host.docker.internal``-Hinweis ist der eigentliche Grund fuer diese Route.
Er steht in der Antwort **als Feld**, nicht nur im Text: die Oberflaeche zeigt
ihn neben dem Formular an, und ein Satz in einer Fehlermeldung erscheint erst,
wenn der Knopf schon rot war. Die verifizierte Falle (aus ``AGENTS.md``) ist,
dass ein auf dem Host laufendes Ollama unter ``localhost:3001`` nicht erreichbar
ist, sondern nur unter ``host.docker.internal:3001``.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import UTCResponseModel


class LocalRuntimeRead(UTCResponseModel):
    """Eine bekannte lokale Laufzeit mit beiden Adressen.

    ``base_url`` ist die Fassung fuer den Docker-Stack (der dokumentierten
    Betriebsart), ``native_base_url`` die fuer ein nativ gestartetes Backend.
    Beide stehen in der Antwort, weil die Oberflaeche nicht raten soll, in
    welcher Bauart der Lernraum gerade laeuft.
    """

    kind: str
    label: str
    port: int
    base_url: str
    native_base_url: str
    default_model: str
    needs_api_key: bool
    note: str


class AiRuntimeRead(UTCResponseModel):
    """Die Laufzeiten und die Regeln, mit denen der Backend sie benutzt.

    ``stages`` sagt in Worten, in welcher Reihenfolge ein Satz entsteht. Das
    steht hier und nicht in einem ``README``, weil die Oberflaeche es anzeigen
    soll: wer zwei lokale Server einrichtet, will wissen, welcher davon zuerst
    gefragt wird.
    """

    runtimes: list[LocalRuntimeRead] = Field(default_factory=list)
    runtime_values: list[str] = Field(default_factory=list)
    runtime_labels: dict[str, str] = Field(default_factory=dict)
    container_host: str
    container_loopback_hosts: list[str] = Field(default_factory=list)
    container_trap_hint: str
    stages: list[str] = Field(default_factory=list)
    local_first: bool
