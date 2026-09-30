"""Die Piper-Stimmen: **Sprache** und **Stimme** sind zwei Entscheidungen.

Warum ein eigener Katalog und nicht eine Zeile in ``app.services.model_catalog``
(Stand vor dieser Welle):

1. **Das Format ``ModelSpec -> tuple[ModelFile, ...]`` deckt Mehrdatei-Modelle
   bereits ab.** ``files`` ist ein Tupel, und der Download-Manager lädt,
   prüft und benennt jede Datei einzeln. Whisper ist der Beleg: ein GGML-Binary
   ohne Quantisierungssuffix steht genauso darin wie ein GGUF.
2. **Ein Archiv wird für Piper nicht gebraucht.** ``rhasspy/piper-voices``
   veroeffentlicht jede Stimme als **zwei getrennte Dateien** - das ONNX-Modell
   und seine JSON-Konfiguration (``de_DE-thorsten-medium.onnx`` /
   ``...onnx.json``) - nicht als ``.tar.gz``. Jede davon hat eine eigene
   veroeffentlichte SHA256. Einzeldateien sind hier nicht nur ausreichend,
   sondern besser: fortsetzbar, je Datei pruefbar, ohne Pfad-Traversal und ohne
   Entpack-Schritt. Einen Entpacker zu bauen hiesse, eine Verteilungsform zu
   unterstuetzen, die es bei dieser Quelle nicht gibt.
3. **Was das Format *nicht* traegt, ist genau das, worum es hier geht:**
   ``ModelSpec`` hat kein Feld fuer Sprache, Sprecher oder Abtastrate. Die
   Sprache steht zwar in der JSON-Konfiguration der Stimme - aber **erst nach dem
   Download**, waehrend die Oberflaeche schon vorher wissen muss, dass eine
   Stimme deutsch ist. Eine deutsche Oberflaeche mit nur englischen Stimmen
   ist keine Auswahl, sondern eine tote Liste.
4. **Und die Kennungen passen nicht.** ``MODEL_ID_PATTERN`` erlaubt keine
   Grossbuchstaben und keinen Unterstrich; ``de_DE-thorsten-medium`` faellt
   durch. Die Ids hier sind deshalb klein und mit Bindestrichen - der Test
   ``test_der_katalog_erlaubt_keine_grossbuchstaben_in_der_kennung`` haelt
   genau das fest, damit es niemand "reparieren" muss.

Was der Katalog *nicht* macht: er schreibt nichts in ``MODEL_CATALOG``. Die
Stimmen stehen deshalb nicht in ``GET /api/v1/models``, sondern in
``GET /api/v1/speech/voices`` - mit demselben Manager, demselben Ablageort und
denselben Pruefsummen. **Merge-Bedarf:** wer die Stimmen auch im allgemeinen
Modellkatalog zeigen will, zieht ``PIPER_VOICES`` als zusaetzliche
``ModelSpec``-Eintraege in ``MODEL_CATALOG`` (``as_model_spec()`` liefert sie
fertig). Diese Datei darf es nicht anfassen; die Specs sind hier gebaut und
geprueft, das Zusammenfuehren ist eine Zeile.

Sicherheit: Jede Adresse laeuft durch ``validate_download_url`` aus dem
Katalogmodul. Die Allowlist wird damit **wiederverwendet, nicht neu erfunden** -
ein zweiter Hostfilter waere die schlechteste Stelle, um sie zu veroeffaehlichen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.services.model_catalog import (
    MODEL_FILENAME_PATTERN,
    MODEL_ID_PATTERN,
    ModelCatalogError,
    ModelFile,
    ModelSpec,
    validate_download_url,
)

__all__ = [
    "DEFAULT_VOICE_ID",
    "PIPER_VOICES",
    "VOICE_QUALITIES",
    "PiperVoice",
    "VoiceConfig",
    "VoiceConfigError",
    "as_model_specs",
    "deutsche_voices",
    "installed_voice_specs",
    "read_voice_config",
    "sorted_voices",
    "voice_by_id",
    "voice_ids",
]

#: Die drei Guetegrade, die Piper-Stimmen in dieser Quelle tragen. Ein vierter
#: waere eine frei erfundene Auswahl.
VOICE_QUALITIES: Final[tuple[str, ...]] = ("low", "medium", "high")

#: Die Stimme, die die Oberflaeche vorwaehlt. Deutsch, ein Sprecher, mittlere
#: Qualitaet: der dreifache Durchschnitt. Jede andere Wahl ist eine bewusste
#: Entscheidung der Lernenden - und steht in der Liste, nicht versteckt.
DEFAULT_VOICE_ID: Final[str] = "de-de-thorsten-medium"

_REPOSITORY: Final[str] = "rhasspy/piper-voices"


class VoiceConfigError(ValueError):
    """Die JSON-Konfiguration einer Stimme ist unlesbar oder passt nicht zum Katalog."""


@dataclass(frozen=True, slots=True)
class PiperVoice:
    """Eine Stimme in einer Sprache - mit beiden Entscheidungen als Feldern.

    ``language`` ist der Sprachcode aus der Quellbibliothek (``de_DE``), und
    nicht frei gewaehlt: Piper synthetisiert mit einem phonetischen Modell, das
    genau fuer diese Sprache trainiert wurde. ``speaker`` ist die Person, deren
    Stimme das ist, und ``quality`` die Guetestufe. Drei Felder, weil drei
    Entscheidungen - und zwei davon kann man nicht ineinander rechnen.
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
    dataset_url: str
    source_page: str
    files: tuple[ModelFile, ...]

    @property
    def onnx_name(self) -> str:
        """Der Dateiname des ONNX-Modells - immer die erste der beiden Dateien."""
        return self.files[0].name

    @property
    def config_name(self) -> str:
        """Der Dateiname der JSON-Konfiguration neben dem Modell."""
        return self.files[1].name

    @property
    def size_bytes(self) -> int:
        return sum(item.size_bytes for item in self.files)

    def as_model_spec(self) -> ModelSpec:
        """Dieser Katalogeintrag als :class:`ModelSpec` fuer den Download-Manager.

        Bewusst ein Adapter und keine zweite Wahrheit: ``ModelSpec`` ist genau das
        Format, das ``ModelDownloadManager`` bereits kann (Verzeichnis je Id,
        Einzeldateien mit Pruefsumme, Manifest danach). Wer die Stimmen in den
        allgemeinen Katalog ziehen will, nimmt das Ergebnis dieser Methode.
        """
        return ModelSpec(
            id=self.id,
            label=self.label,
            task="text_to_speech",
            family="ONNX für Piper",
            description=self.description,
            license=self.license,
            source_repository=_REPOSITORY,
            source_page=self.source_page,
            parameter_count=None,
            quantization=None,
            context_length=None,
            files=self.files,
        )


def _voice_file(path: str, sha256: str, size_bytes: int) -> ModelFile:
    return ModelFile(
        name=path.rsplit("/", 1)[-1],
        url=f"https://huggingface.co/{_REPOSITORY}/resolve/main/{path}",
        sha256=sha256,
        size_bytes=size_bytes,
    )


def _stem_files(directory: str, stem: str, onnx_sha: str, onnx_size: int, json_sha: str, json_size: int) -> tuple[ModelFile, ...]:
    """Die zwei Dateien einer Stimme, in der Reihenfolge ``.onnx``, ``.onnx.json``.

    Piper laedt das JSON ueber ``--config``; ohne sie kennt das Programm weder
    die Abtastrate noch die Phonemzuordnung. Deshalb sind es **zwei** Katalogeintraege
    und nicht einer - und deshalb schlaegt ``_is_installed`` auch dann fehl, wenn
    das Modell da liegt und die Konfiguration fehlt.
    """
    return (
        _voice_file(f"{directory}/{stem}.onnx", onnx_sha, onnx_size),
        _voice_file(f"{directory}/{stem}.onnx.json", json_sha, json_size),
    )


#: Vier Stimmen, und zwar nicht zufaellig: **zwei deutsche Sprecher** (Thorsten,
#: Kerstin), **drei Guetestufen** bei einem davon und **eine englische** Stimme,
#: damit "deutsche Stimme" und "diese Stimme" nachweislich zwei Entscheidungen
#: sind und nicht dieselbe.
#:
#: Alle Groessen und Pruefsummen sind von Hugging Face geholt, nicht geschaetzt:
#: der ``oid`` eines LFS-Objekts *ist* die SHA256, und die JSON-Dateien sind so
#: klein, dass ihre SHA256 direkt berechnet wurde. Eine erfundene Pruefsumme
#: waere hier kein Nebenschaden - der Download wuerde jedes Mal beim Pruefen
#: scheitern und niemand wuesste warum.
PIPER_VOICES: Final[dict[str, PiperVoice]] = {
    "de-de-thorsten-medium": PiperVoice(
        id="de-de-thorsten-medium",
        label="Thorsten (deutsch, mittel)",
        language="de_DE",
        language_label="Deutsch",
        speaker="thorsten",
        speaker_label="Thorsten",
        quality="medium",
        sample_rate=22050,
        description=(
            "Männliche deutsche Stimme in mittlerer Qualität, 22.050 Hz. "
            "Die Voreinstellung des Lernraums: verständlich und nicht zu langsam."
        ),
        license="CC0 (Stimmdaten)",
        dataset_url="https://github.com/thorstenMueller/Thorsten-Voice",
        source_page=(
            "https://huggingface.co/rhasspy/piper-voices/tree/main/de/de_DE/thorsten/medium"
        ),
        files=_stem_files(
            "de/de_DE/thorsten/medium",
            "de_DE-thorsten-medium",
            "7e64762d8e5118bb578f2eea6207e1a35a8e0c30595010b666f983fc87bb7819",
            63201294,
            "974adee790533adb273a1ac88f49027d2a1b8f0f2cf4905954a4791e79264e85",
            4819,
        ),
    ),
    "de-de-thorsten-high": PiperVoice(
        id="de-de-thorsten-high",
        label="Thorsten (deutsch, hoch)",
        language="de_DE",
        language_label="Deutsch",
        speaker="thorsten",
        speaker_label="Thorsten",
        quality="high",
        sample_rate=22050,
        description=(
            "Dieselbe Stimme in hoher Qualität. Erkennbar sauberer, aber fast doppelt "
            "so groß und auf einer CPU merklich langsamer - vor allem beim Vorlesen langer Antworten."
        ),
        license="CC0 (Stimmdaten)",
        dataset_url="https://github.com/thorstenMueller/Thorsten-Voice",
        source_page="https://huggingface.co/rhasspy/piper-voices/tree/main/de/de_DE/thorsten/high",
        files=_stem_files(
            "de/de_DE/thorsten/high",
            "de_DE-thorsten-high",
            "9df1c43c61149ef9b39e618e2b861fbe41e1fcea9390b2dac62e8761573ea4f1",
            113895201,
            "6de734444e4c3f9e33b7ebe2746dbc19b71e85f613e79c65acf623200b99a76a",
            4875,
        ),
    ),
    "de-de-kerstin-low": PiperVoice(
        id="de-de-kerstin-low",
        label="Kerstin (deutsch, niedrig)",
        language="de_DE",
        language_label="Deutsch",
        speaker="kerstin",
        speaker_label="Kerstin",
        quality="low",
        sample_rate=16000,
        description=(
            "Weibliche deutsche Stimme mit 16.000 Hz. Die zweite Sprecherin im Katalog, "
            "damit die Auswahl eine echte ist: eine andere Person, eine andere Stimmfarbe."
        ),
        license="CC0 (Stimmdaten)",
        dataset_url="https://github.com/rhasspy/dataset-voice-kerstin",
        source_page="https://huggingface.co/rhasspy/piper-voices/tree/main/de/de_DE/kerstin/low",
        files=_stem_files(
            "de/de_DE/kerstin/low",
            "de_DE-kerstin-low",
            "d352a7641892cebf2903859af94e9ba81a141110215fe3943bcda7f7da401b7a",
            63104526,
            "56e708556b7b9b7a53c4f8957e021421e69f11a600962bba554cffbe72cf2d47",
            4158,
        ),
    ),
    "en-us-amy-medium": PiperVoice(
        id="en-us-amy-medium",
        label="Amy (englisch, mittel)",
        language="en_US",
        language_label="Englisch",
        speaker="amy",
        speaker_label="Amy",
        quality="medium",
        sample_rate=22050,
        description=(
            "Englische Stimme. Bewusst im Katalog, obwohl die Oberfläche deutsch ist: "
            "so ist sichtbar, dass „deutsche Stimme“ und „diese Stimme“ zwei "
            "Entscheidungen sind - und eine geladene englische Stimme wird nicht "
            "stillschweigend zum Voreinstellen."
        ),
        license="Lizenz siehe Quellseite (Mimic3-Stimmen)",
        dataset_url="https://github.com/MycroftAI/mimic3-voices",
        source_page="https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/amy/medium",
        files=_stem_files(
            "en/en_US/amy/medium",
            "en_US-amy-medium",
            "b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18",
            63201294,
            "95a23eb4d42909d38df73bb9ac7f45f597dbfcde2d1bf9526fdeaf5466977d77",
            4882,
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    """Was die mitgeladene JSON-Konfiguration ueber eine Stimme sagt.

    Das ist die **Gegenprobe** zum Katalogeintrag: der Katalog behauptet eine
    Sprache, die Datei weiss sie erst nach dem Download. Stimmen die
    auseinanderlaufen werden nicht angeboten - eine Oberflaeche, die eine
    englische Stimme als deutsch ausweist, waere schlimmer als gar keine.
    """

    language: str
    quality: str
    sample_rate: int
    num_speakers: int


def read_voice_config(path: Path) -> VoiceConfig:
    """Liest die ``.onnx.json`` einer installierten Stimme.

    Bewusst tolerant gegenueber dem, was **nicht** da ist: Piper fuegt Felder
    hinzu und entfernt welche. Was aber fehlen darf, ist nicht ``audio`` - ohne
    Abtastrate ist die Antwort unbrauchbar, und das ist ein deutscher Fehler
    statt eines ``None``.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise VoiceConfigError(
            f"Die Konfiguration der Stimme liess sich nicht lesen: "
            f"{error.strerror or type(error).__name__}."
        ) from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise VoiceConfigError("Die Konfiguration der Stimme ist keine lesbare JSON-Datei.") from error
    if not isinstance(payload, dict):
        raise VoiceConfigError("Die Konfiguration der Stimme ist kein JSON-Objekt.")
    audio = payload.get("audio")
    if not isinstance(audio, dict):
        raise VoiceConfigError("Die Konfiguration der Stimme nennt keine Audio-Einstellungen.")
    sample_rate = audio.get("sample_rate")
    if not isinstance(sample_rate, int) or sample_rate <= 0:
        raise VoiceConfigError("Die Konfiguration der Stimme nennt keine plausible Abtastrate.")
    language = payload.get("language")
    code = language.get("code") if isinstance(language, dict) else None
    if not isinstance(code, str) or not code.strip():
        raise VoiceConfigError("Die Konfiguration der Stimme nennt keinen Sprachcode.")
    quality = audio.get("quality")
    num_speakers = payload.get("num_speakers")
    return VoiceConfig(
        language=code.strip(),
        quality=quality.strip().lower() if isinstance(quality, str) and quality.strip() else "unbekannt",
        sample_rate=sample_rate,
        num_speakers=num_speakers if isinstance(num_speakers, int) and num_speakers > 0 else 1,
    )


def sorted_voices() -> tuple[PiperVoice, ...]:
    """Die Stimmen in der Anzeigereihenfolge der Oberflaeche."""
    return tuple(sorted(PIPER_VOICES.values(), key=lambda voice: (voice.language != "de_DE", voice.size_bytes, voice.id)))


def voice_ids() -> tuple[str, ...]:
    """Alle Stimmen in stabiler Reihenfolge: erst Deutsch, dann nach Groesse.

    Das Deutsche zuerst, weil die Oberflaeche deutsch ist; danach die
    Grosse, damit die kleinen Stimmen oben stehen. Die englische steht damit
    ganz hinten - sichtbar, aber nicht in der vordersten Reihe.
    """
    return tuple(voice.id for voice in sorted_voices())


def voice_by_id(voice_id: str) -> PiperVoice:
    """Die Stimme mit dieser Kennung, sonst ein deutscher Fehler.

    Der Aufrufer entscheidet, ob daraus 404 oder 422 wird. Der Text ist deutsch,
    weil er so in der Oberflaeche landet.
    """
    voice = PIPER_VOICES.get(voice_id)
    if voice is None:
        raise ModelCatalogError(
            f"Unbekannte Stimme: {voice_id!r}. Der Katalog kennt {len(PIPER_VOICES)} Stimmen."
        )
    return voice


def deutsche_voices() -> tuple[PiperVoice, ...]:
    """Nur die deutschen Stimmen - was die Oberflaeche vorwaehlen darf."""
    return tuple(voice for voice in PIPER_VOICES.values() if voice.language == "de_DE")


def as_model_specs() -> tuple[ModelSpec, ...]:
    """Alle Stimmen als ``ModelSpec``, in Anzeigereihenfolge."""
    return tuple(voice.as_model_spec() for voice in sorted_voices())


def installed_voice_specs(manager: object) -> tuple[PiperVoice, ...]:
    """Die Stimmen, deren Dateien **vollstaendig und geprueft** liegen.

    Der Aufruf nimmt den Manager absichtlich als Parameter und nicht als
    Import: so bleibt dieser Katalog frei von der Ablage, und ein Test kann
    einen Attrappen-Manager einsetzen. ``is_installed`` ist die Bedingung des
    Managers - Manifest vorhanden **und** jede Datei vollstaendig.
    """
    is_installed = getattr(manager, "is_installed", None)
    if not callable(is_installed):
        return ()
    return tuple(voice for voice in PIPER_VOICES.values() if bool(is_installed(voice.as_model_spec())))


def _assert_voice_is_usable(voice: PiperVoice) -> None:
    """Bricht beim Import ab, wenn ein Stimmeneintrag kaputt ist.

    Dasselbe Prinzip wie in ``model_catalog``: ein schlechter Eintrag ist ein
    Programmierfehler, und ``import app.main`` ist die frueheste Stelle, an der
    er auffallen kann.
    """
    if not MODEL_ID_PATTERN.match(voice.id):
        raise ModelCatalogError(
            f"Die Stimmenkennung {voice.id!r} ist kein zulaessiger Verzeichnisname. "
            "Kleinbuchstaben und Bindestriche verwenden - MODEL_ID_PATTERN erlaubt "
            "weder Grossbuchstaben noch Unterstriche."
        )
    if voice.language not in {"de_DE", "en_US"}:
        raise ModelCatalogError(f"Die Stimme {voice.id!r} traegt die unbekannte Sprache {voice.language!r}.")
    if voice.quality not in VOICE_QUALITIES:
        raise ModelCatalogError(f"Die Stimme {voice.id!r} traegt die unbekannte Qualitaet {voice.quality!r}.")
    if voice.sample_rate <= 0:
        raise ModelCatalogError(f"Die Stimme {voice.id!r} nennt keine plausible Abtastrate.")
    if len(voice.files) != 2:
        raise ModelCatalogError(
            f"Die Stimme {voice.id!r} hat {len(voice.files)} Dateien; erwartet werden genau zwei "
            "(das ONNX-Modell und seine JSON-Konfiguration)."
        )
    for item in voice.files:
        if not MODEL_FILENAME_PATTERN.match(item.name):
            raise ModelCatalogError(f"Der Dateiname {item.name!r} von {voice.id!r} ist nicht zulaessig.")
        if item.size_bytes <= 0:
            raise ModelCatalogError(f"Die Dateigroesse von {voice.id!r}/{item.name} ist nicht plausibel.")
        validate_download_url(item.url)


for _voice in PIPER_VOICES.values():
    _assert_voice_is_usable(_voice)