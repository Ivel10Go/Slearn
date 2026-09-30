"""``/api/v1/speech``: der Sprach-Assistent und sein **Textmodus**.

Sieben Routen:

* ``GET    /api/v1/speech/capabilities``            was gerade geht, und wenn nicht, warum
* ``GET    /api/v1/speech/voices``                  die Piper-Stimmen mit ihrem Stand
* ``POST   /api/v1/speech/voices/{id}/download``    Stimme laden oder fortsetzen
* ``POST   /api/v1/speech/voices/{id}/cancel``      Laden abbrechen
* ``DELETE /api/v1/speech/voices/{id}``             die Ablage loeschen
* ``POST   /api/v1/speech/transcribe``              Aufnahme zu Text
* ``POST   /api/v1/speech/speak``                   Text zu Ton (Rohdaten)

Die eine Entscheidung, die den ganzen Router bestimmt: **„Nichts geladen" ist
kein Fehlerfall, sondern eine Antwort.** ``GET /capabilities`` antwortet
deshalb immer mit 200 und nennt fuer beide Richtungen einen deutschen Grund.
Dasselbe gilt fuer ``/transcribe`` und ``/speak`` - dort wird es zu 503 mit
eben diesem Grund, und die Oberflaeche behandelt eine 503 aus diesem Bereich als
den erwarteten Zustand und nicht als Fehlermeldung. Die Arbeitsteilung ist
bewusst: **der Endpunkt ist ehrlich, die Oberflaeche ist freundlich.**

Nutzung: ``/transcribe`` und ``/speak`` tragen keine fremde Id, weil es keine
gibt - Modelle sind gemeinsamer Installationsbestand, wie beim Modellkatalog.
Wer je Nutzer eigene Modelle will, braucht eine Tabelle; siehe
``app.services.piper_voices``.

Beim Aufnehmen endet die Endung bei ``;codecs=opus``. Der Browser schickt
Content-Type mit Parametern, und ``upload_endung`` trennt sie ab - ohne das
wuerde ein Safari-MP4 als unbekannter Container abgewiesen.
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status

from app.api.deps import CurrentUser, ModelDownloads, Speech
from app.schemas.common import MessageResponse
from app.schemas.speech import (
    SpeakRequest,
    SpeechAudioInputRead,
    SpeechCapabilitiesRead,
    SpeechRunStatus,
    SpeechRuntimeRead,
    SpeechTaskRead,
    TranscriptionRead,
    VoiceFileRead,
    VoiceListRead,
    VoiceRead,
    VoiceStatusRead,
)
from app.services.model_catalog import ModelCatalogError
from app.services.model_downloads import (
    ModelDownloadBusy,
    ModelDownloadError,
    ModelDownloadManager,
    ModelInstallState,
    ModelSpaceError,
    ModelStorageError,
)
from app.services.piper_voices import DEFAULT_VOICE_ID, PiperVoice, sorted_voices, voice_by_id
from app.services.speech import (
    SpeechAudioTooLargeError,
    SpeechInputError,
    SpeechRuntimeError,
    SpeechUnavailableError,
)

router = APIRouter(prefix="/api/v1/speech", tags=["speech"])

#: Wie viele Byte am Stueck aus ``UploadFile`` gelesen werden. Gross genug fuer
#: einen brauchbaren Durchsatz, klein genug, dass ein Endpunkt ohne
#: Durchsatzmessung auskommt.
READ_CHUNK_BYTES = 64 * 1024


def _task_read(task: object) -> SpeechTaskRead:
    return SpeechTaskRead(
        available=bool(getattr(task, "available")),
        reason=str(getattr(task, "reason")),
        model_id=getattr(task, "model_id", None),
        label=str(getattr(task, "label", "")),
        language=getattr(task, "language", None),
        language_label=str(getattr(task, "language_label", "")),
    )


def _voice_or_404(voice_id: str) -> PiperVoice:
    """Unbekannte Stimme ist 404 mit deutschem Klartext.

    Es gibt hier nichts zu verbergen: der Stimmenkatalog ist ohnehin in der
    Oberflaeche sichtbar.
    """
    try:
        return voice_by_id(voice_id)
    except ModelCatalogError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _status_read(state: ModelInstallState) -> VoiceStatusRead:
    return VoiceStatusRead(
        status=cast(SpeechRunStatus, state.status),
        bytes_downloaded=state.bytes_downloaded,
        bytes_total=state.bytes_total,
        progress_percent=state.progress_percent,
        current_file=state.current_file if state.status == "downloading" else None,
        verified=state.verified,
        message=state.message,
        finished_at=state.finished_at,
    )


def _voice_read(voice: PiperVoice, manager: ModelDownloadManager) -> VoiceRead:
    state = manager.status(voice.as_model_spec())
    return VoiceRead(
        id=voice.id,
        label=voice.label,
        language=voice.language,
        language_label=voice.language_label,
        speaker=voice.speaker,
        speaker_label=voice.speaker_label,
        quality=voice.quality,
        sample_rate=voice.sample_rate,
        description=voice.description,
        license=voice.license,
        source_page=voice.source_page,
        dataset_url=voice.dataset_url,
        size_bytes=voice.size_bytes,
        files=[
            VoiceFileRead(
                name=item.name,
                size_bytes=item.size_bytes,
                is_model=item.name == voice.onnx_name,
                is_config=item.name == voice.config_name,
            )
            for item in voice.files
        ],
        status=_status_read(state),
    )


def _raise_for_download_error(error: ModelDownloadError) -> None:
    """Download-Fehler in HTTP - dieselbe Reihenfolge wie in ``app.api.local_models``.

    Geteilt wird nach Ausnahmeklassen und nicht nach Formulierungen im Text; die
    Vererbung entscheidet, deshalb muss ``ModelSpaceError`` zuerst drankommen.
    """
    if isinstance(error, (ModelDownloadBusy, ModelSpaceError)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if isinstance(error, ModelStorageError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


def _raise_for_speech_error(error: Exception) -> None:
    """Sprachfehler in HTTP, in einer Reihenfolge, die man nachlesen kann.

    * **Nicht vorhanden** (kein Modell, kein Dienst, kein Piper) -> 503. Das ist
      der Normalzustand auf einem frischen Server und deshalb ein Status, den
      die Oberflaeche als erwarteten Fall behandelt.
    * **Zu gross** -> 413. Die Datei wurde gelesen und wieder verworfen, aber
      nicht weiter ausgewertet.
    * **Unbrauchbar** (leer, kein Ton, falscher Container) -> 422.
    * **Sonst** (Piper ist abgestuerzt) -> 502. Der Aufruf ging raus, die Antwort
      kam nicht - und genau das ist ein 502.
    """
    if isinstance(error, SpeechAudioTooLargeError):
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(error)) from error
    if isinstance(error, SpeechUnavailableError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    if isinstance(error, SpeechInputError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    if isinstance(error, SpeechRuntimeError):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Die Sprachfunktion liess sich nicht ausfuehren.",
    ) from error


# --- Faehigkeiten -----------------------------------------------------------


@router.get("/capabilities", response_model=SpeechCapabilitiesRead)
def read_capabilities(_user: CurrentUser, speech: Speech) -> SpeechCapabilitiesRead:
    """Was gerade geht - und wenn nicht, warum.

    **Immer 200.** Auch ohne ein einziges Modell, ohne Laufzeit und mit
    abgeschalteten Sprachfunktionen: es gibt keinen Zustand, in dem dieser
    Endpunkt keinen Antworten kann. Wer ihn im Fehlerfall wegfallen laesst,
    verliert die Information, *warum* - und die Oberflaeche koennte ihre Frage
    dann nur noch raten.
    """
    capabilities = speech.capabilities()
    return SpeechCapabilitiesRead(
        text_mode_available=capabilities.text_mode_available,
        text_mode_note=capabilities.text_mode_note,
        speech_to_text=_task_read(capabilities.speech_to_text),
        text_to_speech=_task_read(capabilities.text_to_speech),
        input=SpeechAudioInputRead(
            max_bytes=capabilities.input.max_bytes,
            max_seconds=capabilities.input.max_seconds,
            accepted_types=list(capabilities.input.accepted_types),
            sample_rate=capabilities.input.sample_rate,
            needs_transcoding=capabilities.input.needs_transcoding,
            transcoding_note=capabilities.input.transcoding_note,
        ),
        runtime=SpeechRuntimeRead(
            transcriber_reachable=capabilities.runtime.transcriber_reachable,
            transcriber_note=capabilities.runtime.transcriber_note,
            synthesizer_available=capabilities.runtime.synthesizer_available,
            synthesizer_note=capabilities.runtime.synthesizer_note,
            ffmpeg_present=capabilities.runtime.ffmpeg_present,
            ffmpeg_note=capabilities.runtime.ffmpeg_note,
        ),
        default_voice_id=capabilities.default_voice_id,
        recommended_voice_id=DEFAULT_VOICE_ID,
        installed_voice_ids=list(capabilities.installed_voice_ids),
    )


# --- Stimmen ----------------------------------------------------------------


@router.get("/voices", response_model=VoiceListRead)
def list_voices(_user: CurrentUser, downloads: ModelDownloads, speech: Speech) -> VoiceListRead:
    """Alle Stimmen mit ihrem Stand - Deutsch zuerst, danach nach Groesse."""
    stimme = speech.usable_voice()
    return VoiceListRead(
        voices=[_voice_read(voice, downloads) for voice in sorted_voices()],
        default_voice_id=stimme.id if stimme else None,
    )


@router.post("/voices/{voice_id}/download", response_model=VoiceRead)
def start_voice_download(
    voice_id: str, _user: CurrentUser, downloads: ModelDownloads
) -> VoiceRead:
    """Laedt eine Stimme im Hintergrund oder setzt einen Teilstand fort.

    Der **gleiche** Manager wie beim Modellkatalog und derselbe Ablageort. Es
    gibt damit nur eine Sperre je Modell und nur eine Wahrheit darueber, was
    geladen ist - zwei Manager wuerden zwei Antworten auf dieselbe Frage
    erzeugen.
    """
    voice = _voice_or_404(voice_id)
    try:
        downloads.start(voice.as_model_spec())
    except ModelDownloadError as exc:
        _raise_for_download_error(exc)
    return _voice_read(voice, downloads)


@router.post("/voices/{voice_id}/cancel", response_model=VoiceRead)
def cancel_voice_download(voice_id: str, _user: CurrentUser, downloads: ModelDownloads) -> VoiceRead:
    """Bricht einen laufenden Download ab. Der Teilstand bleibt fuer den naechsten Start."""
    voice = _voice_or_404(voice_id)
    downloads.cancel(voice_id)
    return _voice_read(voice, downloads)


@router.delete("/voices/{voice_id}", response_model=MessageResponse)
def delete_voice(voice_id: str, _user: CurrentUser, downloads: ModelDownloads) -> MessageResponse:
    """Entfernt die Ablage einer Stimme samt Manifest und Teilstand.

    Idempotent: Gab es nichts zu loeschen, ist das 200 mit einem deutschen Satz
    und kein 404 - der Katalogeintrag bleibt bestehen, nur die Dateien sind weg.
    """
    voice = _voice_or_404(voice_id)
    try:
        removed = downloads.remove(voice.as_model_spec())
    except ModelDownloadError as exc:
        _raise_for_download_error(exc)
    return MessageResponse(
        message=(
            f"{voice.label} wurde aus der Ablage entfernt."
            if removed
            else f"Fuer {voice.label} lag nichts in der Ablage."
        )
    )


# --- Aufnehmen und sprechen -------------------------------------------------


@router.post("/transcribe", response_model=TranscriptionRead)
def transcribe_audio(
    _user: CurrentUser,
    speech: Speech,
    file: UploadFile = File(...),
) -> TranscriptionRead:
    """Macht aus einer Aufnahme einen Text.

    Drei Grenzen, alle vor dem Modelllauf:

    1. **Groesse.** 413 mit der Zahl, die erlaubt waere. Ohne diese Grenze ist
       der Endpunkt eine Einladung, den Arbeitsspeicher des Containers zu
       fuellen.
    2. **Container.** 422 mit der Liste der erlaubten. Chrome liefert WebM,
       Safari MP4, und beides wird von whisper.cpp nicht gelesen - der Weg
       darueber steht in :mod:`app.services.speech`.
    3. **Dauer.** 413, wenn die Aufnahme laenger ist als erlaubt. Zwei Minuten
       sind fuer eine Frage reichlich; darueber ist es fast immer ein
       Aufnahmefehler.

    Ohne Modell oder ohne Dienst antwortet dieser Endpunkt mit **503** und
    deutschem Klartext. Das ist Absicht: die Oberflaeche behandelt eine 503 aus
    diesem Bereich als den erwarteten Zustand und zeigt den Text als Hinweis.
    Der Endpunkt luegt nicht, die Oberflaeche meckert nicht.
    """
    grenze = speech.max_upload_bytes
    gelesen = 0
    teile: list[bytes] = []
    try:
        while True:
            block = file.file.read(READ_CHUNK_BYTES)
            if not block:
                break
            gelesen += len(block)
            if gelesen > grenze:
                # Hier wird **nicht** weiter gelesen: eine unbegrenzt grosse
                # Datei braucht genau diesen Abbruch, sonst steht am Ende die
                # ganze Datei im Speicher.
                raise SpeechAudioTooLargeError(
                    f"Die Aufnahme ist groesser als {grenze} Byte. "
                    "Kuerzere Aufnahmen lassen sich auch mit weniger Geraet aufnehmen."
                )
            teile.append(block)
    except (SpeechRuntimeError, SpeechInputError) as exc:
        # Der Abbruch im Strom gehoert in dieselbe Fehlerbehandlung wie alles
        # andere: ein 500 waere hier eine Falschmeldung, weil nichts kaputt ist.
        file.file.close()
        _raise_for_speech_error(exc)
    except OSError as exc:
        file.file.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Die Aufnahme liess sich nicht lesen: {exc.strerror or type(exc).__name__}.",
        ) from exc
    finally:
        file.file.close()
    payload = b"".join(teile)
    try:
        ergebnis = speech.transcribe(
            payload,
            content_type=file.content_type,
        )
    except (SpeechRuntimeError, SpeechInputError) as exc:
        _raise_for_speech_error(exc)
        raise  # pragma: no cover - _raise_for_speech_error wirft immer
    return TranscriptionRead(
        text=ergebnis.text,
        model_id=ergebnis.model_id,
        language=ergebnis.language,
        duration_seconds=round(ergebnis.duration_seconds, 2) if ergebnis.duration_seconds else None,
        transcoded=ergebnis.transcoded,
        message=(
            "Aus der Aufnahme wurde Text."
            if not ergebnis.transcoded
            else "Aus der Aufnahme wurde Text; der Ton wurde dafuer zuerst in WAV umgewandelt."
        ),
    )


@router.post("/speak")
def speak_text(_user: CurrentUser, speech: Speech, payload: SpeakRequest) -> Response:
    """Liest einen Text vor und liefert eine WAV-Datei als Rohdaten.

    **Kein JSON mit base64.** Eine WAV-Datei waere als base64 um ein Drittel
    groesser und muesste im Browser wieder dekodiert werden, ohne dass etwas
    gewuenne. Der Browser holt die Bytes, legt ein ``Blob`` an und spielt es ab.

    Der Pfad laeuft ueber den Next-Proxy wie alle anderen - ``connect-src
    'self'`` gilt in Produktion, und es gibt keine externe Adresse.

    Eine fremde ``voice_id`` ist **404**, kein 503: es geht um eine falsche
    Angabe in der Anfrage, nicht um einen Zustand des Servers. Dieselbe
    Uebersetzung wie bei den Stimmrouten, nur mit der Id aus dem Request-Body.
    """
    try:
        ton, stimme = speech.speak(payload.text, payload.voice_id)
    except ModelCatalogError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (SpeechRuntimeError, SpeechInputError) as exc:
        _raise_for_speech_error(exc)
        raise  # pragma: no cover - _raise_for_speech_error wirft immer
    if not ton:
        return Response(content=b"", media_type="audio/wav", headers={"Cache-Control": "no-store"})
    return Response(
        content=ton,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'inline; filename="{stimme.id}.wav"' if stimme else 'inline; filename="sprache.wav"',
            # Kein `nosniff` aus Notwendigkeit, sondern als Härtung: wer die
            # Adresse direkt in ein `<audio src>` setzt, bekommt dann den
            # deklarierten Typ und keine geratene. Gegen den Next-Proxy hilft es
            # allerdings nichts - `getForwardResponseHeaders` in `lib/proxy.ts`
            # wirft den Header weg. Die Wiedergabe im Lernraum laeuft ohnehin ueber
            # ein `Blob` aus `fetch`, und da ist der Header gegenstandslos.
            "X-Content-Type-Options": "nosniff",
        },
    )