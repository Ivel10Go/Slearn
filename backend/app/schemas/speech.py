"""Die Antworten von ``/api/v1/speech``: Faehigkeiten, Stimmen, Transkript, Ton.

Die Form ist hier die Kernaussage des ganzen Abschnitts: **kein Feld heisst
„geht nicht"**. Jede Faehigkeit traegt ein ``available`` **und** ein ``reason``,
und der Grund ist auch im Erfolgsfall gefuellt. Die Oberflaeche muss dadurch
nichts erraten und nichts erfinden - insbesondere nicht die Frage, ob sie
gerade Texteingabe, Sprachausgabe oder beides anbietet.

Zwei weitere Entscheidungen, die man hier nicht wegdiskutieren kann:

* **Modelle sind gemeinsamer Bestand**, genau wie beim Modellkatalog. Es gibt
  kein ``user_id`` an ``VoiceRead``, und ein fremder Zugriff ist deshalb kein
  moegliches Problem. Wer je Nutzer eigene Stimmen will, braucht eine Tabelle -
  siehe ``app.services.piper_voices``.
* **Die Sprachausgabe kommt als Rohdaten**, nicht als Base64 im JSON. Eine
  WAV-Datei als base64 waere um ein Drittel groesser und muesste im Browser
  wieder dekodiert werden, ohne einen Vorteil zu haben.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import UTCResponseModel

SpeechDirection = Literal["speech_to_text", "text_to_speech"]
SpeechRunStatus = Literal["not_downloaded", "downloading", "installed", "interrupted", "failed"]


class SpeechTaskRead(UTCResponseModel):
    """Eine Richtung: geht es, und wenn nicht, warum.

    ``reason`` hat **keinen** Default und ist Pflicht: ein leeres Feld waere die
    Stelle, an der die Oberflaeche anfängt zu raten.
    """

    available: bool
    reason: str
    model_id: str | None = None
    label: str = ""
    language: str | None = None
    language_label: str = ""


class SpeechAudioInputRead(UTCResponseModel):
    """Was der Aufnahme-Endpunkt annimmt - damit die Oberflaeche es nicht erfindet."""

    max_bytes: int
    max_seconds: int
    accepted_types: list[str] = Field(default_factory=list)
    sample_rate: int
    needs_transcoding: bool
    transcoding_note: str


class SpeechRuntimeRead(UTCResponseModel):
    """Der Zustand der Laufzeiten, getrennt vom Zustand der Modelle.

    Drei Felder, weil es drei Fragen sind. „Modell geladen, Dienst weg" und
    „Dienst laeuft, Modell fehlt" sehen in einer Zahl gleich aus und sind es
    nicht - und ``ffmpeg_present`` ist wieder etwas anderes: der
    Spracherkennungsdienst kann laufen, waehrend die Umwandlung fehlt. Eine
    brauchbare WAV-Datei geht dann trotzdem.
    """

    transcriber_reachable: bool
    transcriber_note: str
    synthesizer_available: bool
    synthesizer_note: str
    ffmpeg_present: bool = False
    ffmpeg_note: str = ""


class SpeechCapabilitiesRead(UTCResponseModel):
    """``GET /api/v1/speech/capabilities``.

    ``text_mode_available`` steht konstant auf ``true``. Das ist eine Zusage des
    Endpunkts, keine Beobachtung: es gibt keinen Zustand, in dem Slearn
    nichts kann.

    ``default_voice_id`` ist die Stimme, die **jetzt** gesprochen wuerde - also
    ``None``, solange keine geladen ist. ``recommended_voice_id`` ist etwas
    anderes: die Empfehlung von Slearn, unabhaengig vom Zustand. Ohne beide
    wuerde die Auswahl entweder verschwinden, sobald nichts geladen ist, oder
    eine Empfehlung als Tatsache ausgeben.
    """

    text_mode_available: bool = True
    text_mode_note: str
    speech_to_text: SpeechTaskRead
    text_to_speech: SpeechTaskRead
    input: SpeechAudioInputRead
    runtime: SpeechRuntimeRead
    default_voice_id: str | None = None
    recommended_voice_id: str | None = None
    installed_voice_ids: list[str] = Field(default_factory=list)
    shared_installation: bool = True


class VoiceStatusRead(UTCResponseModel):
    """Der Stand einer Stimme in der Ablage - wie beim Modellkatalog."""

    status: SpeechRunStatus
    bytes_downloaded: int
    bytes_total: int
    progress_percent: float
    current_file: str | None = None
    verified: bool = False
    message: str
    finished_at: datetime | None = None


class VoiceFileRead(UTCResponseModel):
    """Eine Datei der Stimme: Name und Groesse. Ohne Adresse, ohne Pruefsumme im Text."""

    name: str
    size_bytes: int
    is_model: bool = False
    is_config: bool = False


class VoiceRead(UTCResponseModel):
    """Eine Stimme mit **beiden** Entscheidungen: Sprache und Sprecher.

    ``language`` und ``speaker`` sind eigene Felder, weil es eigene
    Entscheidungen sind. Wer beides in ein Feld presst, kann nicht ausdruecken,
    dass eine geladene englische Stimme keine deutsche Ersatzstimme ist.
    """

    id: str
    label: str
    language: str
    language_label: str
    speaker: str
    speaker_label: str
    quality: str
    sample_rate: int
    description: str
    license: str
    source_page: str
    dataset_url: str
    size_bytes: int
    files: list[VoiceFileRead] = Field(default_factory=list)
    status: VoiceStatusRead


class VoiceListRead(UTCResponseModel):
    """``GET /api/v1/speech/voices``: alle Stimmen mit ihrem Stand."""

    voices: list[VoiceRead] = Field(default_factory=list)
    default_voice_id: str | None = None
    shared_installation: bool = True


class TranscriptionRead(UTCResponseModel):
    """Das Ergebnis einer Aufnahme.

    ``transcoded`` sagt, ob die Datei unterwegs umgewandelt wurde. Das ist keine
    Nebensache: es erklaert, warum ein WebM funktioniert hat und ein MP4 nicht,
    ohne dass man beides von Hand nachgeschlagen haette.
    """

    text: str
    model_id: str
    language: str | None = None
    duration_seconds: float | None = None
    transcoded: bool
    message: str


class SpeakRequest(UTCResponseModel):
    """Was vorgelesen werden soll.

    ``voice_id`` ist optional und heisst: die eingetragene bzw. die passende
    deutsche Stimme. Eine fremde Kennung ist 404 - es gibt hier nichts zu
    verbergen, der Stimmenkatalog ist ohnehin sichtbar.
    """

    text: str = Field(min_length=1, max_length=40000)
    voice_id: str | None = None