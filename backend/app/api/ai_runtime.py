"""``GET /api/v1/ai-runtime``: die bekannten lokalen Laufzeiten.

Eine einzige Route, read-only, ohne Geheimnis und **ohne Netzverbindung** - sie
fragt keinen Server ab. Sie liefert das, was die Oberflaeche fuer das Formular
einer ``ai_provider``-Verbindung braucht: die Standardadresse je Laufzeit, den
``host.docker.internal``-Hinweis und die Reihenfolge der KI-Wege.

Dass sie nichts abfragt, ist Absicht und traegt eine Entscheidung: diese Welle
baut **keinen** Prozessstart und **keine** Inference im Backend (siehe
``app/services/local_inference.py`` fuer die Begruendung). Ein Endpunkt, der
"Server starten" koennte, wuerde eine Faehigkeit vortaeuschen, die es nicht gibt
- und in dieser Umgebung waere sie auch nicht verifizierbar.
"""

from fastapi import APIRouter

from app.api.deps import AppSettings, CurrentUser
from app.schemas.ai_runtime import AiRuntimeRead, LocalRuntimeRead
from app.services import ai_routing
from app.services.local_inference import (
    CONTAINER_HOST,
    RUNTIME_AUTO,
    RUNTIME_LLAMA_CPP,
    RUNTIME_LM_STUDIO,
    RUNTIME_OLLAMA,
    RUNTIME_ONLINE,
    RUNTIME_PRESETS,
    container_loopback_hosts,
)

router = APIRouter(prefix="/api/v1/ai-runtime", tags=["ai-runtime"])

#: Die Reihenfolge in Worten, fuer die Oberflaeche. Bewusst als Text und nicht
#: als Enum: die Stufen sind eine Erklaerung, keine Zustandsmaschine, die man
#: verwechseln koennte.
_STAGES: tuple[str, ...] = (
    "1. Ein lokaler Server auf diesem Rechner (Ollama, LM Studio, llama.cpp).",
    "2. Sonst der eingetragene Online-Provider.",
    "3. Sonst der Satz aus deinen Daten - ohne KI, aber nie leer.",
)

_RUNTIME_LABELS: dict[str, str] = {
    RUNTIME_AUTO: "Automatisch aus der Adresse",
    RUNTIME_OLLAMA: "Ollama",
    RUNTIME_LM_STUDIO: "LM Studio",
    RUNTIME_LLAMA_CPP: "llama.cpp (llama-server)",
    RUNTIME_ONLINE: "Online-Anbieter",
}


@router.get("", response_model=AiRuntimeRead)
def read_ai_runtime(_user: CurrentUser, settings: AppSettings) -> AiRuntimeRead:
    """Die Presets, der Container-Hinweis und die Reihenfolge der Wege."""
    return AiRuntimeRead(
        runtimes=[
            LocalRuntimeRead(
                kind=preset.kind,
                label=preset.label,
                port=preset.port,
                base_url=preset.base_url,
                native_base_url=preset.native_base_url,
                default_model=preset.default_model,
                needs_api_key=preset.needs_api_key,
                note=preset.note,
            )
            for preset in RUNTIME_PRESETS
        ],
        runtime_values=list(ai_routing.runtime_values()),
        runtime_labels=_RUNTIME_LABELS,
        container_host=CONTAINER_HOST,
        container_loopback_hosts=list(container_loopback_hosts()),
        container_trap_hint=(
            "Läuft Slearn in Docker, zeigen localhost und 127.0.0.1 auf den Container, "
            f"nicht auf deinen Rechner. Einen lokalen Server erreichst du dann über {CONTAINER_HOST}."
        ),
        stages=list(_STAGES),
        local_first=settings.ai_local_first,
    )
