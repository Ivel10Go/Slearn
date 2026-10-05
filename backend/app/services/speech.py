"""Der Sprach-Assistent: Sprache zu Text, Text zu Sprache - und der Textmodus.

Vier Bausteine, und die Reihenfolge ist hier die Aussage:

1. **Textmodus zuerst.** :func:`SpeechService.capabilities` antwortet immer mit
   200 und nennt fuer beide Richtungen *warum* etwas geht oder nicht. „Kein
   Modell geladen" ist kein Fehler, sondern eine Zeile Text. Das ist der
   Zustand, in dem die meisten Lernenden sein werden - lokale Modelle sind
   gross, und die wenigsten werden sie herunterladen.
2. **Zwei unabhaengige Bedingungen je Richtung.** Ein Modell kann geladen und
   die Laufzeit weg sein; die Laufzeit kann laufen und das Modell fehlen. Beides
   wird getrennt geprueft und getrennt gemeldet, und aus „Beides“ wird nie
   ein Satz ueber das andere. **Halbe Ausstattung funktioniert halb.**
3. **Ein Upload, der begrenzt ist.** :func:`upload_endung` haelt Groesse und
   Container fest, :func:`prepare_wav` die Dauer. Eine unbegrenzte Datei auf einem
   ``/transcribe``-Endpunkt ist kein Feature, sondern ein Speicherproblem.
4. **Zwei Laufzeiten, beide ausserhalb des Prozesses.** Whisper laeuft als
   HTTP-Dienst (``whisper-server`` aus whisper.cpp), Piper als Kommandozeile.
   Beides ist ueber einen schmalen Rand austauschbar, damit dieser Pfad ohne
   native Runtime testbar ist - siehe ``run_command`` und den
   ``client_factory``-Einstieg beim Transkribierer.

Das Formatproblem der Aufnahme steht in :data:`BROWSER_RECORDING_TYPES` und
wird nicht versteckt: Chrome liefert WebM/Opus, Safari MP4/AAC, und whisper.cpp
liest von beidem nichts. Deshalb der Weg ueber ffmpeg - und deshalb wird in
:func:`prepare_wav` ein bereits brauchbares 16-kHz-Mono-WAV **nicht**
transcodiert, sondern unveraendert durchgereicht. Nur so ist dieser Pfad ohne
ffmpeg ueberhaupt pruefbar.

Bekannte Grenzen, die hier benannt statt kaschiert werden:

* Piper braucht das Programm ``piper`` auf dem Rechner. Fehlt es, meldet
  :meth:`SpeechService.capabilities` das im Klartext und die Sprachausgabe ist
  aus - der Rest laeuft.
* whisper.cpp braucht ``ffmpeg`` fuer alles ausser WAV.
* Die Stimmenauswahl ist eine eigene Entscheidung; siehe
  ``app.services.piper_voices``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

import httpx

from app.services.local_runtimes import RUNTIME_PIPER, RUNTIME_WHISPER, LocalRuntimeError, LocalRuntimeManager
from app.services.model_catalog import ModelSpec, catalog_models
from app.services.model_downloads import ModelDownloadManager
from app.services.piper_voices import (
    PiperVoice,
    VoiceConfigError,
    installed_voice_specs,
    read_voice_config,
    sorted_voices,
    voice_by_id,
)

logger = logging.getLogger("school_assistant.speech")

#: Die Abtastrate, die whisper.cpp verlangt. 16 kHz mono, 16 Bit - nicht
#: verhandelbar, das ist die Zahl aus ``whisper.h``.
WHISPER_SAMPLE_RATE: Final[int] = 16000

#: Standardobergrenze einer Aufnahme. 12 MiB sind rund 7 Minuten in 16-kHz-Mono
#: und damit weit mehr, als jemand in einem Atemzug diktiert.
DEFAULT_MAX_UPLOAD_BYTES: Final[int] = 12 * 1024 * 1024

#: Obergrenze der Aufnahmedauer. Zwei Minuten reichen fuer eine Frage; darueber
#: ist es fast immer ein Aufnahmefehler ( Mikrofon offen geblieben, Rauschen).
DEFAULT_MAX_SECONDS: Final[int] = 120

#: Obergrenze des Textes fuer die Sprachausgabe. Piper braucht eine Datei oder
#: eine Zeile auf stdin; ein ungebremster ChatGPT-Text waere ein Arbeitsauftrag,
#: kein Vorlesetext.
DEFAULT_MAX_TEXT_CHARACTERS: Final[int] = 4000

#: Was ein Browser an ``MediaRecorder`` liefert. **Zwei Container, zwei Codecs** -
#: das ist der Grund fuer den ffmpeg-Schritt und nicht fuer eine Fummelei.
#: Reihenfolge = Praeferenz des Lernraums: erst das, was jeder Browser kann,
#: dann die Nachlieger.
BROWSER_RECORDING_TYPES: Final[tuple[str, ...]] = (
    "audio/webm",
    "audio/webm;codecs=opus",
    "audio/ogg;codecs=opus",
    "audio/mp4",
    "audio/mp4;codecs=mp4a.40.2",
    "audio/aac",
)

#: Container, die der Endpunkt annimmt - samt der Endung, die ``ffmpeg`` fuer
#: das Erraten des Formats braucht. ``None`` heisst: unbekannt, dann wird der
#: Inhalt per Magic-Bytes geprueft.
ACCEPTED_UPLOAD_TYPES: Final[dict[str, str]] = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/flac": ".flac",
    "application/octet-stream": ".webm",
}

#: Ein WAV-Header ist „RIFF....WAVE" - die Magic Bytes stehen hier, weil ein
#: Client auch ``application/octet-stream`` schicken darf und dann nur die
#: ersten vier Byte verraten, was es ist.
_WAV_MAGIC: Final[bytes] = b"RIFF"
_WAVE_MAGIC: Final[bytes] = b"WAVE"

#: Kurzzeitlimit fuer die Erreichbarkeitspruefung. Die Faehigkeitsabfrage
#: laeuft bei jedem Seitenaufruf; ein hartes Timeout wuerde sie dort aufhengen.
PROBE_TIMEOUT_SECONDS: Final[float] = 2.0

#: Wie lange ein Lauf von Piper und ffmpeg hoechstens dauern darf.
RUNTIME_TIMEOUT_SECONDS: Final[float] = 60.0


#: Sprache der eingebauten Spracherkennung, wenn ``WHISPER_LANGUAGE`` leer ist.
BUILTIN_LANGUAGE: Final = "de"


class SpeechRuntimeError(RuntimeError):
    """Eine Sprachlaufzeit liess sich nicht benutzen. Die Meldung ist deutsch."""


class SpeechUnavailableError(SpeechRuntimeError):
    """Die Richtung ist gerade nicht moeglich - ohne installiertes Modell oder ohne Laufzeit."""


class SpeechInputError(ValueError):
    """Die Eingabe ist unbrauchbar: leer, kein Ton, falscher Container."""


class SpeechAudioTooLargeError(SpeechInputError):
    """Die Aufnahme ist groesser (oder laenger) als erlaubt."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Das Ergebnis eines Aufrufs - so, wie die Shell es melden wuerde."""

    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""

    @property
    def fehlermeldung(self) -> str:
        """Der brauchbare Teil der Fehlerausgabe, ohne die ganze Werkzeugmeldung.

        Die Programme schreiben viel auf stderr, von dem niemand etwas hat. Die
        letzte nichtleere Zeile ist fast immer die eigentliche Ursache - und
        die gehoert dem Nutzer, nicht ins Log.
        """
        text = self.stderr.decode("utf-8", errors="replace").strip()
        if not text:
            text = self.stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return ""
        zeilen = [zeile.strip() for zeile in text.splitlines() if zeile.strip()]
        return zeilen[-1] if zeilen else ""


class CommandRunner(Protocol):
    """Nur der Aufruf - damit ist die native Runtime die einzige Stelle, die ersetzt wird."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes,
        timeout_seconds: float,
    ) -> CommandResult: ...


def run_command(argv: Sequence[str], *, stdin: bytes = b"", timeout_seconds: float = RUNTIME_TIMEOUT_SECONDS) -> CommandResult:
    """Der echte Aufruf. Das ist die einzige Stelle im Modul mit ``subprocess``.

    ``check=False`` mit eigener Fehlerbehandlung, weil die Meldung des Programms
    die bessere ist: ``piper`` sagt, welches Modell er nicht lesen kann, und
    das ist eine Antwort, die man dem Nutzer zeigen kann.
    """
    try:
        fertig = subprocess.run(  # noqa: S603 - argv ist eine Liste, kein Shell-Befehl
            list(argv),
            input=stdin,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as error:
        raise SpeechRuntimeError(
            f"Das Programm {argv[0]!r} wurde auf diesem Server nicht gefunden. "
            "Ohne dieses Programm ist die Funktion nicht nutzbar."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise SpeechRuntimeError(
            f"{argv[0]} hat nach {timeout_seconds:.0f} Sekunden nicht geantwortet und wurde beendet."
        ) from error
    except OSError as error:
        raise SpeechRuntimeError(f"{argv[0]} liess sich nicht starten: {error.strerror or type(error).__name__}.") from error
    return CommandResult(returncode=fertig.returncode, stdout=fertig.stdout, stderr=fertig.stderr)


def program_exists(path: str) -> bool:
    """Ist das Programm da und startbar? ``shutil.which`` findet auch absolute Pfade."""
    if not path.strip():
        return False
    return shutil.which(path) is not None


def looks_like_wav(payload: bytes) -> bool:
    """Erste vier und achte Byte - genug, um WAV von allem anderen zu trennen."""
    return len(payload) >= 12 and payload[:4] == _WAV_MAGIC and payload[8:12] == _WAVE_MAGIC


def repair_streamed_wav(payload: bytes) -> bytes:
    """Traegt die echten Laengen in einen WAV-Kopf ein, den ffmpeg in eine Pipe schrieb.

    Schreibt ffmpeg nach ``pipe:1``, kann es nicht zurueckspulen und laesst die
    Groessenfelder von ``RIFF`` und ``data`` auf dem Platzhalter ``0xFFFFFFFF``
    stehen. ``wave`` liest daraus eine Laenge von rund 37 Stunden, und die
    Dauergrenze lehnte jede umgewandelte Aufnahme ab. Hier werden beide Felder
    aus der tatsaechlichen Laenge neu berechnet.
    """
    if not looks_like_wav(payload):
        return payload
    position = payload.find(b"data", 12)
    if position < 0 or position + 8 > len(payload):
        return payload
    data_size = len(payload) - (position + 8)
    repaired = bytearray(payload)
    repaired[4:8] = (len(payload) - 8).to_bytes(4, "little")
    repaired[position + 4 : position + 8] = data_size.to_bytes(4, "little")
    return bytes(repaired)


def _wav_duration_seconds(payload: bytes) -> float:
    """Laenge einer WAV-Datei, ohne sie zu laden.

    Der Header wird mit ``wave`` gelesen, weil er die Rahmenzahl *und* die
    Abtastrate liefert - eine Dateigrösse zu raten waere genau die Art
    Schaetzung, die bei diesem Endpunkt teuer wird.

    ``TemporaryDirectory`` und **nicht** ``NamedTemporaryFile``: Letzteres
    reserviert die Datei auf Windows exklusiv, und ein zweites ``open`` darauf
    schlaegt fehl. Der Test laeuft auf Windows, das Docker-Image auf Linux - und
    ein Fehler, der nur auf einer Seite auftritt, ist auf beiden eine Falle.
    """
    with tempfile.TemporaryDirectory(prefix="lernraum-wav-") as verzeichnis:
        datei = Path(verzeichnis) / "aufnahme.wav"
        datei.write_bytes(payload)
        try:
            with wave.open(str(datei), "rb") as leser:
                rate = leser.getframerate()
                frames = leser.getnframes()
        except (OSError, wave.Error) as error:
            raise SpeechInputError(
                "Die WAV-Datei liess sich nicht lesen. Ist die Aufnahme unvollstaendig geworden?"
            ) from error
    if rate <= 0:
        raise SpeechInputError("Die WAV-Datei nennt keine Abtastrate.")
    return frames / float(rate)


def _is_plain_wav(payload: bytes) -> bool:
    """Genau das Format, das whisper.cpp ohne Umweg liest: mono, 16 kHz, 16 Bit."""
    with tempfile.TemporaryDirectory(prefix="lernraum-wav-") as verzeichnis:
        datei = Path(verzeichnis) / "aufnahme.wav"
        datei.write_bytes(payload)
        try:
            with wave.open(str(datei), "rb") as leser:
                return leser.getnchannels() == 1 and leser.getframerate() == WHISPER_SAMPLE_RATE and leser.getsampwidth() == 2
        except (OSError, wave.Error):
            return False


def upload_endung(content_type: str | None, payload_head: bytes, *, max_bytes: int, max_seconds: int, size_bytes: int) -> str:
    """Die Endung der Aufnahme - und nebenbei die beiden billigen Pruefungen.

    Die Reihenfolge ist **Groesse zuerst**, weil sie die billigste ist und ein
    2-GB-Upload sonst schon gelesen ist, bevor man ihn abweist. Dann der
    Container. Die Dauer wird erst in :func:`prepare_wav` geprueft, weil sie
    erst dort etwas weiss.

    Der Dateikopf ist ein Argument und nicht selbst gelesen: der Endpunkt liest
    die Datei ohnehin blockweise, und die ersten Byte noch einmal anzufordern
    waere der einzige Grund, sie im Speicher zu halten. Genau daran scheitert
    die bequemere Variante.

    Ein unbekannter Container ist ein Fehler **mit** Nennung der erlaubten.
    ``application/octet-stream`` steht deshalb in ``ACCEPTED_UPLOAD_TYPES`` (ein
    Browser ohne Angabe schickt das), gibt aber eine WAV-Datei nur dann heraus,
    wenn der Header wirklich einer ist - ein Ratenversuch waere an dieser Stelle
    teuer und still.
    """
    if size_bytes <= 0:
        raise SpeechInputError("Die Aufnahme ist leer.")
    if size_bytes > max_bytes:
        raise SpeechAudioTooLargeError(
            f"Die Aufnahme ist {size_bytes} Byte gross; erlaubt sind {max_bytes} Byte. "
            "Kuerzere Aufnahmen lassen sich auch mit weniger Geraet aufnehmen."
        )
    content = (content_type or "").split(";")[0].strip().lower()
    if content not in ACCEPTED_UPLOAD_TYPES:
        erlaubt = ", ".join(sorted(set(ACCEPTED_UPLOAD_TYPES) - {"application/octet-stream"}))
        raise SpeechInputError(
            f"Dieser Toncontainer wird nicht gelesen ({content or 'unbekannt'}). "
            f"Angenommen werden: {erlaubt}. Im Zweifel eine WAV-Datei."
        )
    if payload_head[:4] == _WAV_MAGIC and payload_head[8:12] == _WAVE_MAGIC:
        return ".wav"
    return ACCEPTED_UPLOAD_TYPES[content]


def prepare_wav(
    payload: bytes,
    *,
    endung: str,
    ffmpeg_path: str,
    runner: CommandRunner,
    max_seconds: int,
    timeout_seconds: float = RUNTIME_TIMEOUT_SECONDS,
) -> bytes:
    """Bringt eine Aufnahme in die Form, die whisper.cpp liest.

    Der Normalfall ist **kein** ffmpeg: Wer schon 16-kHz-Mono-WAV schickt,
    bekommt seine Bytes unveraendert zurueck. Das ist nicht nur schneller,
    sondern der einzige Weg, diesen Pfad ohne installierte native Programme zu
    pruefen.
    """
    if looks_like_wav(payload):
        dauer = _wav_duration_seconds(payload)
        if dauer > max_seconds:
            raise SpeechAudioTooLargeError(
                f"Die Aufnahme dauert {dauer:.0f} Sekunden; erlaubt sind {max_seconds}. "
                "Die Aufnahme wurde nicht weiter ausgewertet."
            )
        if _is_plain_wav(payload):
            return payload
    if not program_exists(ffmpeg_path):
        raise SpeechUnavailableError(
            "Diese Aufnahme muss erst umgewandelt werden, und auf diesem Server fehlt ffmpeg. "
            "Entweder wird ffmpeg installiert, oder es wird eine WAV-Datei geschickt. "
            "Alles andere im Lernraum funktioniert davon unberuehrt."
        )
    argv = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        # Nur ``pipe:0``: ffmpeg erkennt den Container am Inhalt. Ein Name wie
        # ``pipe:0.webm`` ist kein gueltiger Eingang - ffmpeg 8 bricht damit mit
        # "Invalid argument" ab. Die Endung bleibt fuer die Pruefung oben.
        "pipe:0",
        "-ar",
        str(WHISPER_SAMPLE_RATE),
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        "pipe:1",
    ]
    ergebnis = runner(argv, stdin=payload, timeout_seconds=timeout_seconds)
    if ergebnis.returncode != 0:
        raise SpeechInputError(
            f"Die Aufnahme liess sich nicht in WAV umwandeln: {ergebnis.fehlermeldung or 'ffmpeg hat nichts geliefert'}."
        )
    if not looks_like_wav(ergebnis.stdout):
        raise SpeechInputError("Die Umwandlung hat keine brauchbare WAV-Datei ergeben.")
    umgewandelt = repair_streamed_wav(ergebnis.stdout)
    dauer = _wav_duration_seconds(umgewandelt)
    if dauer > max_seconds:
        raise SpeechAudioTooLargeError(
            f"Die Aufnahme dauert {dauer:.0f} Sekunden; erlaubt sind {max_seconds}. "
            "Sie wurde nicht an das Spracherkennungsmodell geschickt."
        )
    return umgewandelt


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    """Ein Transkript samt dem Weg, auf dem es entstanden ist."""

    text: str
    language: str | None
    duration_seconds: float | None
    model_id: str
    transcoded: bool


class WhisperTranscriber:
    """Spracherkennung ueber den HTTP-Dienst von whisper.cpp.

    Der Dienst ist ein eigener Prozess (``whisper-server`` aus whisper.cpp),
    und das ist hier die richtige Form: er laedt das Modell **einmal** und
    nimmt danach eine Anfrage nach der anderen an. Ein Modell pro Anfrage neu
    zu laden waere der umgekehrte Weg und der langsamste.

    ``transport`` ist der einzige Griff nach draussen - so setzt ein Test einen
    ``httpx.MockTransport`` und prueft den echten Anfrageweg, ohne einen laufenden
    Dienst zu brauchen. Er gilt fuer **beide** Verbindungen, auch fuer die
    Erreichbarkeitspruefung: eine Pruefung, die im Test ins Netz ginge, waere
    keine Pruefung.
    """

    def __init__(
        self,
        base_url: str,
        *,
        language: str | None = None,
        timeout_seconds: float = 120.0,
        probe_timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
        client_factory: Callable[[], httpx.Client] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._language = language.strip() or None if isinstance(language, str) else None
        self._timeout_seconds = timeout_seconds
        self._probe_timeout_seconds = probe_timeout_seconds
        self._transport = transport
        self._client_factory: Callable[[], httpx.Client] = client_factory or (
            lambda: httpx.Client(timeout=timeout_seconds, follow_redirects=False, transport=transport)
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def is_reachable(self) -> tuple[bool, str]:
        """Laeuft da ueberhaupt ein Dienst?

        **Jede** HTTP-Antwort gilt als „ja", auch 404: es geht um die Frage, ob
        sich etwas meldet, nicht um eine bestimmte Route. Eine eigene
        ``/health``-Route gaelte es bei whisper.cpp nicht, und eine zu erfinden
        waere eine Zusage, die der Dienst nicht einloest.
        """
        if not self._base_url:
            return False, "Es ist keine Adresse fuer den Spracherkennungsdienst eingetragen."
        try:
            with httpx.Client(timeout=self._probe_timeout_seconds, follow_redirects=False, transport=self._transport) as client:
                client.get(self._base_url)
        except httpx.HTTPError as error:
            return False, f"Der Spracherkennungsdienst antwortet nicht ({type(error).__name__})."
        return True, "Der Spracherkennungsdienst antwortet."

    def transcribe(self, wav: bytes, *, model_id: str, transcoded: bool) -> TranscriptionResult:
        """Schickt die WAV-Datei an den Dienst und liest den Text.

        Fehler werden **nicht** verschluckt: ein leeres Transkript ist kein
        Erfolg, sondern ein Fall, in dem der Aufrufer dem Nutzer etwas sagen
        muss, statt eine leere Karte zu zeigen.
        """
        if not wav:
            raise SpeechInputError("Es kam kein Ton an.")
        # Die Felder gehen als `data`, die Datei als `files`. Beides zusammen zu
        # `data` zu geben waere der klassische Fehler: dann kaeme die Datei als
        # Feld und nicht als Upload, und der Dienst wuerde sie nicht finden.
        felder: dict[str, str] = {"response_format": "json", "temperature": "0"}
        if self._language:
            felder["language"] = self._language
        try:
            with self._client_factory() as client:
                antwort = client.post(
                    f"{self._base_url}/inference",
                    data=felder,
                    files={"file": ("aufnahme.wav", wav, "audio/wav")},
                )
        except httpx.TimeoutException as error:
            raise SpeechRuntimeError("Der Spracherkennungsdienst hat nicht in der Zeit geantwortet.") from error
        except httpx.HTTPError as error:
            raise SpeechRuntimeError(f"Der Spracherkennungsdienst ist nicht erreichbar: {type(error).__name__}.") from error
        if antwort.status_code >= 400:
            raise SpeechInputError(
                f"Der Spracherkennungsdienst hat die Aufnahme abgelehnt (HTTP {antwort.status_code}). "
                f"{_kurze_ausgabe(antwort)}".strip()
            )
        return TranscriptionResult(
            text=_extract_transcript(antwort),
            language=self._language,
            duration_seconds=_wav_duration_seconds(wav),
            model_id=model_id,
            transcoded=transcoded,
        )


def _kurze_ausgabe(antwort: httpx.Response) -> str:
    """Der Fehlertext eines Dienstes, auf eine Zeile gebracht."""
    text = antwort.text.strip().replace("\n", " ")
    return text[:300] if text else ""


def _extract_transcript(antwort: httpx.Response) -> str:
    """Der Text aus der Antwort - tolerant gegenueber Leer- und Fehlern.

    whisper.cpp liefert ``{"text": "..."}``. Manche Gabeln liefern ein Array aus
    Segmenten mit ``offsets``; das wird mitgenommen, weil es dieselbe
    Information ist und nicht als "unlesbar" abgewiesen werden sollte. Ein
    leeres Ergebnis bleibt aber ein Fehler - ein still leeres Feld sieht auf der
    Oberflaeche aus wie ein Erfolg.
    """
    try:
        nutzlast = antwort.json()
    except ValueError as error:
        raise SpeechInputError("Der Spracherkennungsdienst hat eine unlesbare Antwort geliefert.") from error
    if isinstance(nutzlast, dict):
        text = nutzlast.get("text")
        if isinstance(text, str):
            if text.strip():
                return text.strip()
            # Leerer Text ist **kein** Erfolg. Whisper liefert genau das, wenn
            # er nichts erkannt hat - eine leere Zeile, die als Antwort
            # durchginge, saehe auf der Oberflaeche aus wie ein fertiges
            # Ergebnis.
            raise SpeechInputError("Der Spracherkennungsdienst hat keinen Text geliefert.")
        segmente = nutzlast.get("segments")
        if isinstance(segmente, list):
            teile = [str(teil.get("text", "")) for teil in segmente if isinstance(teil, dict)]
            zusammengesetzt = " ".join(t.strip() for t in teile if t.strip())
            if zusammengesetzt:
                return zusammengesetzt.strip()
    raise SpeechInputError("Der Spracherkennungsdienst hat keinen Text geliefert.")


class PiperSynthesizer:
    """Sprachausgabe ueber das Kommandozeilenprogramm ``piper``.

    Piper liest den Text von stdin und schreibt eine WAV-Datei. Genau so
    dokumentiert es das Projekt fuer den Serverbetrieb, und genau so ist es hier
    nachgebaut - inklusive ``--output_file`` ueber ein temporaeres Verzeichnis,
    weil eine Pipe hier keine WAV-Datei ergeben wuerde.

    ``runner`` ist der einzige Griff zur Aussenwelt. Ein Test setzt eine
    Attrappe ein, die eine echte WAV-Datei zurueckgibt: damit ist der ganze
    Rest - Argumentbau, Fehlermeldung, Headerpruefung, Laengengrenze - ohne
    native Runtime pruefbar.
    """

    def __init__(
        self,
        binary_path: str,
        *,
        runner: CommandRunner = run_command,
        timeout_seconds: float = RUNTIME_TIMEOUT_SECONDS,
        length_scale: float = 1.0,
    ) -> None:
        self._binary_path = binary_path.strip()
        self._runner = runner
        self._timeout_seconds = timeout_seconds
        self._length_scale = length_scale

    @property
    def binary_path(self) -> str:
        return self._binary_path

    def is_available(self) -> tuple[bool, str]:
        """Ist das Programm da? Und wenn nicht, steht das in einem deutschen Satz."""
        if not self._binary_path:
            return False, "Es ist kein Piper-Programm eingetragen."
        if not program_exists(self._binary_path):
            return False, (
                f"Das Programm {self._binary_path!r} wurde auf diesem Server nicht gefunden. "
                "Ohne Piper gibt es keine Sprachausgabe; Spracheingabe und der Textmodus bleiben."
            )
        return True, "Piper ist vorhanden."

    def synthesize(self, text: str, onnx_path: Path, config_path: Path) -> bytes:
        """Spricht einen Text und gibt die WAV-Datei zurueck.

        Der leere Text wird **nicht** als Fehler behandelt: leere Antworten
        kommen vor, und eine Sprachausgabe ohne Inhalt braucht keinen Lauf und
        keine Fehlermeldung. Die Oberflaebe ruft den Knopf ohnehin nur bei
        Text.
        """
        if not text.strip():
            return b""
        if not onnx_path.is_file():
            raise SpeechUnavailableError(
                f"Die Stimme liegt nicht auf dem Datentraeger ({onnx_path.name} fehlt). "
                "Sie kann unter Einstellungen → Stimmen erneut geladen werden."
            )
        if not config_path.is_file():
            raise SpeechUnavailableError(
                f"Zur Stimme fehlt die Konfiguration ({config_path.name}). "
                "Ohne sie kennt Piper weder Abtastrate noch Phoneme; die Stimme wird neu geladen."
            )
        with tempfile.TemporaryDirectory(prefix="lernraum-piper-") as verzeichnis:
            ziel = Path(verzeichnis) / "sprache.wav"
            argv = [
                self._binary_path,
                "--model",
                str(onnx_path),
                "--config",
                str(config_path),
                "--output_file",
                str(ziel),
                "--length_scale",
                f"{self._length_scale:.2f}",
            ]
            ergebnis = self._runner(argv, stdin=text.encode("utf-8"), timeout_seconds=self._timeout_seconds)
            if ergebnis.returncode != 0:
                raise SpeechRuntimeError(
                    f"Piper konnte den Text nicht sprechen: {ergebnis.fehlermeldung or f'Aufruf endete mit Code {ergebnis.returncode}'}."
                )
            if not ziel.is_file():
                raise SpeechRuntimeError("Piper hat keine Audiodatei erzeugt.")
            return ziel.read_bytes()


@dataclass(frozen=True, slots=True)
class SpeechTask:
    """Was in einer Richtung gerade geht - und wenn nicht, warum.

    ``reason`` ist **immer** gefuellt, auch im Erfolgsfall. Ein leeres Feld
    waere eine Frage, die die Oberflaeche sich selbst beantworten muesste,
    und genau daran entstehen die zwei Wahrheiten.
    """

    available: bool
    reason: str
    model_id: str | None = None
    label: str = ""
    language: str | None = None
    language_label: str = ""

    @property
    def partial(self) -> bool:
        """Vorhanden, aber nicht lauffaehig - der Zustand, in dem man weiterarbeiten kann."""
        return not self.available


@dataclass(frozen=True, slots=True)
class AudioInput:
    """Was der Endpunkt annimmt, damit die Oberflaeche es nicht erfinden muss."""

    max_bytes: int
    max_seconds: int
    accepted_types: tuple[str, ...]
    sample_rate: int
    needs_transcoding: bool
    transcoding_note: str


@dataclass(frozen=True, slots=True)
class SpeechRuntimeInfo:
    """Der Zustand der beiden Laufzeiten, getrennt von dem der Modelle.

    ``ffmpeg_present`` ist ein eigenes Feld und nicht Teil von
    ``transcriber_reachable``: der Spracherkennungsdienst kann laufen, während
    die Umwandlung fehlt. Das ist kein Widerspruch, sondern der Normalfall auf
    einem Stack ohne ffmpeg - und eine brauchbare WAV-Datei geht trotzdem.
    """

    transcriber_reachable: bool
    transcriber_note: str
    synthesizer_available: bool
    synthesizer_note: str
    ffmpeg_present: bool = False
    ffmpeg_note: str = ""


@dataclass(frozen=True, slots=True)
class SpeechCapabilities:
    """Die Antwort von ``GET /api/v1/speech/capabilities``.

    ``text_mode_available`` steht **konstant** auf ``true``. Das ist keine
    Bequemlichkeit, sondern die Zusicherung dieses Endpunkts: es gibt keinen
    Zustand, in dem der Lernraum nichts kann.
    """

    text_mode_available: bool
    text_mode_note: str
    speech_to_text: SpeechTask
    text_to_speech: SpeechTask
    input: AudioInput
    runtime: SpeechRuntimeInfo
    default_voice_id: str | None = None
    installed_voice_ids: tuple[str, ...] = field(default_factory=tuple)


class SpeechService:
    """Bindeglied zwischen Ablage, Laufzeit und Endpunkt.

    Der Service **entscheidet nichts selbst**: er schliesst den Manager (welches
    Modell liegt geladen und geprueft da) mit der Laufzeit (ist der Dienst
    erreichbar) zusammen und nennt das Ergebnis in einem Satz. Der Endpunkt
    wandelt nur noch Ausnahmen in Statuscodes.
    """

    def __init__(
        self,
        *,
        downloads: ModelDownloadManager,
        transcriber: WhisperTranscriber | None,
        synthesizer: PiperSynthesizer | None,
        enabled: bool = True,
        stt_model_id: str | None = None,
        voice_id: str | None = None,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        max_seconds: int = DEFAULT_MAX_SECONDS,
        max_text_characters: int = DEFAULT_MAX_TEXT_CHARACTERS,
        ffmpeg_path: str = "",
        runtimes: LocalRuntimeManager | None = None,
        language: str | None = None,
        transcriber_timeout_seconds: float = 120.0,
    ) -> None:
        self._downloads = downloads
        # Die eingebauten Laufzeiten (Desktop-App): sind weder Dienst noch
        # Programm eingetragen, holt und startet ``runtimes`` sie beim ersten
        # Gebrauch selbst. Ein eingetragener Dienst hat immer Vorrang.
        self._runtimes = runtimes
        self._language = language
        self._transcriber_timeout_seconds = transcriber_timeout_seconds
        self._transcriber = transcriber
        self._synthesizer = synthesizer
        self._enabled = enabled
        self._stt_model_id = stt_model_id.strip() or None if isinstance(stt_model_id, str) else None
        self._voice_id = voice_id.strip() or None if isinstance(voice_id, str) else None
        self._max_upload_bytes = max_upload_bytes
        self._max_seconds = max_seconds
        self._max_text_characters = max_text_characters
        self._ffmpeg_path = ffmpeg_path.strip()

    # ------------------------------------------------------------- Faehigkeiten

    def _builtin_stt(self) -> bool:
        return self._transcriber is None and self._runtimes is not None and self._runtimes.supports(RUNTIME_WHISPER)

    def _builtin_tts(self) -> bool:
        return self._synthesizer is None and self._runtimes is not None and self._runtimes.supports(RUNTIME_PIPER)

    def _builtin_transcriber(self, spec: object) -> WhisperTranscriber:
        """Startet whisper-server fuer das gewaehlte Modell (oder nutzt den laufenden)."""
        if self._runtimes is None or not isinstance(spec, ModelSpec) or not spec.files:
            raise SpeechUnavailableError("Fuer dieses Spracherkennungsmodell gibt es keine eingebaute Laufzeit.")
        model_path = self._downloads.model_directory(spec) / spec.files[0].name
        # Deutsch fest statt Spracherkennung: bei kurzen Saetzen ("Was steht
        # morgen an?") raet Whisper sonst gern Englisch und uebersetzt dann.
        sprache = self._language or BUILTIN_LANGUAGE
        try:
            base_url = self._runtimes.ensure_whisper_server(spec.id, model_path, sprache)
        except LocalRuntimeError as error:
            raise SpeechUnavailableError(str(error)) from error
        return WhisperTranscriber(base_url, language=sprache, timeout_seconds=self._transcriber_timeout_seconds)

    def _builtin_synthesizer(self) -> PiperSynthesizer:
        if self._runtimes is None:
            raise SpeechUnavailableError("Es ist kein Piper-Programm eingetragen.")
        try:
            return PiperSynthesizer(str(self._runtimes.ensure_installed(RUNTIME_PIPER)))
        except LocalRuntimeError as error:
            raise SpeechUnavailableError(str(error)) from error

    def _builtin_note(self, runtime_id: str) -> str:
        if self._runtimes is None:
            return ""
        if self._runtimes.executable(runtime_id) is not None:
            return "startet beim ersten Gebrauch"
        size_mib = self._runtimes.status(runtime_id).size_bytes / (1024 * 1024)
        return f"das Programm (rund {size_mib:.0f} MiB) wird beim ersten Gebrauch geladen"

    def installed_stt_specs(self) -> tuple[str, ...]:
        """Die Kennungen der geladenen Spracherkennungs-Modelle, kleinstes zuerst.

        Kleinstes zuerst ist eine Absicht: tiny vor base, weil ein Server mit
        knappem RAM sonst an der groesseren Variante kleben bleibt, obwohl die
        kleinere lauffaehig waere. Wer es anders will, traegt eine Kennung ein.
        """
        specs = [spec for spec in catalog_models() if spec.task == "speech_to_text"]
        specs.sort(key=lambda spec: (spec.size_bytes, spec.id))
        return tuple(spec.id for spec in specs if self._downloads.is_installed(spec))

    def selected_stt_spec(self) -> object | None:
        """Das Spracherkennungsmodell, das tatsaechlich benutzt wuerde."""
        kandidaten = self.installed_stt_specs()
        if not kandidaten:
            return None
        # Auf einem Server das kleinste (RAM), in der Desktop-App mit eingebauten
        # Laufzeiten das groesste geladene: base erkennt deutlich genauer als tiny
        # und kostet nur rund 0,1 GiB mehr.
        gewaehlt = self._stt_model_id or (kandidaten[-1] if self._runtimes is not None else kandidaten[0])
        for spec in catalog_models():
            if spec.id != gewaehlt or spec.task != "speech_to_text":
                continue
            # **Auch hier** wird nach der Datei gefragt, nicht nur nach der
            # Kennung: ein eingetragenes Modell, das nicht geladen ist, darf
            # nicht durchgewunken werden. Sonst wuerde die Oberflaeche ein Modell
            # anzeigen, das nicht das ist, was der Betreiber eingestellt hat -
            # und niemand koennte es mehr nachvollziehen.
            if not self._downloads.is_installed(spec):
                return None
            return spec
        return None

    def installed_voices(self) -> tuple[PiperVoice, ...]:
        return installed_voice_specs(self._downloads)

    def usable_voice(self) -> PiperVoice | None:
        """Die Stimme, die gesprochen wuerde - **deutsch bevorzugt**.

        Die Voreinstellung ist eine deutsche, wenn eine da ist. Ist nur eine
        englische geladen, wird **nicht** stillschweigend englisch gesprochen:
        :func:`capabilities` sagt das im Klartext, und die Oberflaeche
        bestaetigt es. Eine erfundene Zusage, dass eine Stimme Deutsch ist,
        waere die schlimmere Haelfte des Problems.
        """
        geladen = self.installed_voices()
        if not geladen:
            return None
        gewaehlt = self._voice_id
        if gewaehlt:
            for stimme in geladen:
                if stimme.id == gewaehlt:
                    return stimme
        for stimme in sorted_voices():
            if stimme in geladen and stimme.language == "de_DE":
                return stimme
        return sorted(geladen, key=lambda stimme: stimme.size_bytes)[0]

    def capabilities(self) -> SpeechCapabilities:
        """Was gerade geht. Endet nie in einer Ausnahme."""
        stt = self._speech_to_text_task()
        tts = self._text_to_speech_task()
        laufzeit = self._runtime_info()
        stimme = self.usable_voice()
        return SpeechCapabilities(
            text_mode_available=True,
            text_mode_note=(
                "Alles hier laeuft ohne Modell. Spracheingabe und Sprachausgabe kommen "
                "hinzu, sobald die passenden Modelle geladen sind - sie ersetzen den "
                "Textmodus nie."
            ),
            speech_to_text=stt,
            text_to_speech=tts,
            input=AudioInput(
                max_bytes=self._max_upload_bytes,
                max_seconds=self._max_seconds,
                accepted_types=tuple(sorted(set(ACCEPTED_UPLOAD_TYPES) - {"application/octet-stream"})),
                sample_rate=WHISPER_SAMPLE_RATE,
                needs_transcoding=True,
                transcoding_note=(
                    "Chrome nimmt WebM mit Opus auf, Safari MP4 mit AAC. Beides wird "
                    f"zuerst in {WHISPER_SAMPLE_RATE // 1000} kHz Mono-WAV umgewandelt; "
                    "eine bereits brauchbare WAV-Datei laeuft ohne Umweg."
                ),
            ),
            runtime=laufzeit,
            default_voice_id=stimme.id if stimme else None,
            installed_voice_ids=tuple(stimme.id for stimme in self.installed_voices()),
        )

    def _speech_to_text_task(self) -> SpeechTask:
        if not self._enabled:
            return SpeechTask(
                available=False,
                reason="Die Sprachfunktionen sind auf diesem Server abgeschaltet. Der Textmodus laeuft.",
            )
        if self._transcriber is None and not self._builtin_stt():
            return SpeechTask(
                available=False,
                reason=(
                    "Es ist kein Spracherkennungsdienst eingetragen. "
                    "Auf einem Docker-Server gehoert dafuer whisper-server aus whisper.cpp hin."
                ),
            )
        geladen = self.installed_stt_specs()
        if not geladen:
            return SpeechTask(
                available=False,
                reason=(
                    "Es ist kein Spracherkennungsmodell geladen. "
                    "Whisper tiny reicht fuer kurze Aufgaben und ist die kleinste Wahl."
                ),
            )
        gewaehlt = self.selected_stt_spec()
        if gewaehlt is None:
            return SpeechTask(
                available=False,
                reason=(
                    f"Das eingetragene Modell {self._stt_model_id!r} liegt nicht geladen und geprueft da. "
                    "Ohne Eintrag wird das kleinste geladene Modell genommen."
                ),
            )
        if self._transcriber is None:
            return SpeechTask(
                available=True,
                reason=f"Spracherkennung ist bereit: Modell geladen, whisper.cpp {self._builtin_note(RUNTIME_WHISPER)}.",
                model_id=getattr(gewaehlt, "id", None),
                label=getattr(gewaehlt, "label", ""),
            )
        erreichbar, notiz = self._transcriber.is_reachable()
        if not erreichbar:
            # Zwei unabhaengige Bedingungen, zwei Saetze. Der erste gilt fuer
            # "Modell geladen", der zweite fuer "Dienst erreichbar" - sie werden
            # nie vermischt, sonst ist nicht mehr zu sehen, was fehlt.
            return SpeechTask(
                available=False,
                reason=f"Das Modell liegt geladen, aber {notiz} Ohne den Dienst gibt es keine Transkription.",
                model_id=getattr(gewaehlt, "id", None),
                label=getattr(gewaehlt, "label", ""),
            )
        return SpeechTask(
            available=True,
            reason="Spracherkennung ist bereit: Modell geladen und Dienst erreichbar.",
            model_id=getattr(gewaehlt, "id", None),
            label=getattr(gewaehlt, "label", ""),
        )

    def _text_to_speech_task(self) -> SpeechTask:
        if not self._enabled:
            return SpeechTask(
                available=False,
                reason="Die Sprachfunktionen sind auf diesem Server abgeschaltet. Der Textmodus laeuft.",
            )
        if self._synthesizer is None and not self._builtin_tts():
            return SpeechTask(
                available=False,
                reason=(
                    "Es ist kein Piper-Programm eingetragen. "
                    "Auf einem Docker-Server gehoert dafuer das piper-Kommandozeilenprogramm hin."
                ),
            )
        if self._synthesizer is not None:
            vorhanden, notiz = self._synthesizer.is_available()
            if not vorhanden:
                return SpeechTask(available=False, reason=notiz)
        geladen = self.installed_voices()
        if not geladen:
            return SpeechTask(
                available=False,
                reason=(
                    "Piper laeuft, aber es ist keine Stimme geladen. "
                    "Eine deutsche Stimme reicht; die kleinste ist rund 60 MiB."
                ),
            )
        stimme = self.usable_voice()
        if stimme is None:
            return SpeechTask(
                available=False,
                reason="Es ist eine Stimme geladen, aber keine davon passt zum Katalog. Bitte neu laden.",
            )
        if stimme.language != "de_DE":
            return SpeechTask(
                available=False,
                reason=(
                    f"Geladen ist nur eine Stimme in {stimme.language_label} ({stimme.speaker_label}). "
                    "Fuer eine deutsche Oberflaeche wird keine andere Sprache ausgegeben - "
                    "lade eine deutsche Stimme nach, dann wird sie benutzt."
                ),
                model_id=stimme.id,
                label=stimme.label,
                language=stimme.language,
                language_label=stimme.language_label,
            )
        try:
            konfiguration = read_voice_config(self._voice_directory(stimme) / stimme.config_name)
        except VoiceConfigError as error:
            return SpeechTask(
                available=False,
                reason=f"Die Konfiguration der geladenen Stimme liess sich nicht lesen: {error}",
                model_id=stimme.id,
                label=stimme.label,
                language=stimme.language,
                language_label=stimme.language_label,
            )
        if konfiguration.language != stimme.language:
            return SpeechTask(
                available=False,
                reason=(
                    f"Die geladene Stimme meldet {konfiguration.language}, der Katalog fuehrt sie als "
                    f"{stimme.language_label}. Sie wird nicht benutzt, bis die Dateien neu geladen sind."
                ),
                model_id=stimme.id,
                label=stimme.label,
                language=stimme.language,
                language_label=stimme.language_label,
            )
        return SpeechTask(
            available=True,
            reason=(
                f"Sprachausgabe ist bereit: {stimme.speaker_label} ({stimme.language_label}, "
                f"{stimme.quality}, {konfiguration.sample_rate} Hz)."
            ),
            model_id=stimme.id,
            label=stimme.label,
            language=stimme.language,
            language_label=stimme.language_label,
        )

    def _runtime_info(self) -> SpeechRuntimeInfo:
        if self._builtin_stt():
            transkription = (True, f"Eingebautes whisper.cpp: {self._builtin_note(RUNTIME_WHISPER)}.")
        elif self._transcriber is None:
            transkription = (False, "Kein Spracherkennungsdienst eingetragen.")
        else:
            transkription = self._transcriber.is_reachable()
        if self._builtin_tts():
            sprache = (True, f"Eingebautes Piper: {self._builtin_note(RUNTIME_PIPER)}.")
        elif self._synthesizer is None:
            sprache = (False, "Kein Piper-Programm eingetragen.")
        else:
            sprache = self._synthesizer.is_available()
        ffmpeg_da = program_exists(self._ffmpeg_path) if self._ffmpeg_path else False
        return SpeechRuntimeInfo(
            transcriber_reachable=transkription[0],
            transcriber_note=transkription[1],
            synthesizer_available=sprache[0],
            synthesizer_note=sprache[1],
            ffmpeg_present=ffmpeg_da,
            ffmpeg_note=(
                "ffmpeg ist vorhanden; WebM und MP4 koennen umgewandelt werden."
                if ffmpeg_da
                else "Ohne ffmpeg lassen sich nur WAV-Dateien umwandeln - eine brauchbare WAV-Datei geht trotzdem."
            ),
        )

    # ------------------------------------------------------------------- Wege

    def _voice_directory(self, stimme: PiperVoice) -> Path:
        return self._downloads.model_directory(stimme.as_model_spec())

    def transcribe(
        self,
        payload: bytes,
        *,
        content_type: str | None,
        runner: CommandRunner | None = None,
    ) -> TranscriptionResult:
        """Von der rohen Aufnahme zum Text - mit allen Pruefungen dazwischen.

        ``runner`` ist absichtlich ``None`` statt eines an den Parameter
        gebundenen Werts: so greift ein Test ueber ``monkeypatch`` auf
        ``run_command`` zu, statt einen Default zu umgehen, der zur
        Definitionszeit festgehalten waere.
        """
        tool = runner or run_command
        if not self._enabled:
            raise SpeechUnavailableError("Die Sprachfunktionen sind auf diesem Server abgeschaltet.")
        endung = upload_endung(
            content_type,
            payload[:16],
            max_bytes=self._max_upload_bytes,
            max_seconds=self._max_seconds,
            size_bytes=len(payload),
        )
        if self._transcriber is None and not self._builtin_stt():
            raise SpeechUnavailableError(
                "Es ist kein Spracherkennungsdienst eingetragen. Auf einem Docker-Server "
                "gehoert dafuer whisper-server aus whisper.cpp hin."
            )
        gewaehlt = self.selected_stt_spec()
        if gewaehlt is None:
            if not self.installed_stt_specs():
                raise SpeechUnavailableError(
                    "Es ist kein Spracherkennungsmodell geladen. Lade unter Einstellungen eines "
                    "und versuche es noch einmal - bis dahin funktioniert der Textmodus."
                )
            raise SpeechUnavailableError(
                f"Das eingetragene Modell {self._stt_model_id!r} liegt nicht geladen und geprueft da."
            )
        if self._transcriber is not None:
            erreichbar, notiz = self._transcriber.is_reachable()
            if not erreichbar:
                raise SpeechUnavailableError(notiz)
        war_umgewandelt = not (looks_like_wav(payload) and _is_plain_wav(payload))
        wav = prepare_wav(
            payload,
            endung=endung,
            ffmpeg_path=self._ffmpeg_path,
            runner=tool,
            max_seconds=self._max_seconds,
        )
        transcriber = self._transcriber or self._builtin_transcriber(gewaehlt)
        return transcriber.transcribe(
            wav,
            model_id=getattr(gewaehlt, "id", "unbekannt"),
            transcoded=war_umgewandelt,
        )

    def speak(self, text: str, voice_id: str | None = None) -> tuple[bytes, PiperVoice | None]:
        """Aus einem Text eine WAV-Datei - und die Stimme, mit der sie entstand."""
        if not self._enabled:
            raise SpeechUnavailableError("Die Sprachfunktionen sind auf diesem Server abgeschaltet.")
        if not text.strip():
            raise SpeechInputError("Es gibt keinen Text zum Vorlesen.")
        if len(text) > self._max_text_characters:
            raise SpeechInputError(
                f"Der Text hat {len(text)} Zeichen; vorgesehen sind {self._max_text_characters}. "
                "Ein Vorlesetext wird gekuerzt, nicht in mehreren Teilen gesprochen."
            )
        if self._synthesizer is None and not self._builtin_tts():
            raise SpeechUnavailableError("Es ist kein Piper-Programm eingetragen.")
        if self._synthesizer is not None:
            vorhanden, notiz = self._synthesizer.is_available()
            if not vorhanden:
                raise SpeechUnavailableError(notiz)
        stimme: PiperVoice | None = None
        if voice_id:
            stimme = voice_by_id(voice_id)
        else:
            stimme = self.usable_voice()
        if stimme is None:
            raise SpeechUnavailableError(
                "Es ist keine Stimme geladen. Lade unter Einstellungen eine deutsche Stimme nach."
            )
        verzeichnis = self._voice_directory(stimme)
        if not self._downloads.is_installed(stimme.as_model_spec()):
            raise SpeechUnavailableError(
                f"{stimme.label} ist nicht vollstaendig geladen. Lade die Stimme nach, bevor du sie benutzt."
            )
        if stimme.language != "de_DE":
            raise SpeechUnavailableError(
                f"Geladen ist nur eine Stimme in {stimme.language_label}. Fuer eine deutsche "
                "Oberflaeche wird keine andere Sprache ausgegeben; lade eine deutsche Stimme nach."
            )
        # Dieselbe Gegenprobe wie in `_text_to_speech_task`: die mitgeladene
        # Konfiguration muss zur Katalogangabe passen. Sonst waere hier eine
        # Zusage moeglich, die die Faehigkeitsabgabe nicht macht - und die
        # Oberflaeche zeigte „bereit", waehrend der Ton in der falschen Sprache
        # erklaenge.
        try:
            konfiguration = read_voice_config(verzeichnis / stimme.config_name)
        except VoiceConfigError as error:
            raise SpeechUnavailableError(
                f"Die Konfiguration der geladenen Stimme liess sich nicht lesen: {error}"
            ) from error
        if konfiguration.language != stimme.language:
            raise SpeechUnavailableError(
                f"Die geladene Stimme meldet {konfiguration.language}, der Katalog fuehrt sie als "
                f"{stimme.language_label}. Sie wird nicht benutzt, bis die Dateien neu geladen sind."
            )
        synthesizer = self._synthesizer or self._builtin_synthesizer()
        return (
            synthesizer.synthesize(
                text,
                verzeichnis / stimme.onnx_name,
                verzeichnis / stimme.config_name,
            ),
            stimme,
        )

    @property
    def max_text_characters(self) -> int:
        return self._max_text_characters

    @property
    def max_upload_bytes(self) -> int:
        return self._max_upload_bytes