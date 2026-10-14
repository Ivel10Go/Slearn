"""Die Sprache einer Anfrage.

Die Oberflaeche schickt ihre Sprache als ``Accept-Language`` (``lib/api.ts``),
nicht die des Browsers: wer Slearn auf Englisch stellt, bekommt englische
Antworten, auch in einem deutschen Browser. Gespeichert wird die Sprache nicht -
sie gilt fuer die Anfrage, in der sie steht.

Die Meldungen des Backends bleiben deutsch; uebersetzt werden die Texte, die ein
Modell schreibt oder die aus Daten entstehen (Assistent, Willkommenssatz), sowie
Spracherkennung und Stimme.
"""

from __future__ import annotations

from typing import Final, Literal

Language = Literal["de", "en"]

SUPPORTED_LANGUAGES: Final[tuple[Language, ...]] = ("de", "en")
DEFAULT_LANGUAGE: Final[Language] = "de"

#: Die Anweisung an ein Modell. Deutsch braucht keine: die Prompts sind deutsch.
_REPLY_INSTRUCTIONS: Final[dict[Language, str]] = {
    "de": "",
    "en": (
        "\n\nWICHTIG: Antworte ausschliesslich auf Englisch (English), auch wenn die Daten, Dokumente und "
        "diese Anweisungen deutsch sind. Die Protokollwoerter AKTION und MERKE bleiben unveraendert."
    ),
}


def parse_accept_language(header: str | None) -> Language:
    """Die erste unterstuetzte Sprache aus ``Accept-Language``, sonst Deutsch.

    Gewichte (``;q=``) werden nicht ausgewertet: die Oberflaeche schickt genau
    eine Sprache, und ein Browser-Kopf ohne Oberflaeche davor faellt fuer einen
    deutsches Slearn ohnehin auf Deutsch zurueck.
    """
    if not header:
        return DEFAULT_LANGUAGE
    for part in header.split(","):
        tag = part.split(";", 1)[0].strip().lower()
        base = tag.split("-", 1)[0]
        for language in SUPPORTED_LANGUAGES:
            if base == language:
                return language
    return DEFAULT_LANGUAGE


def reply_instruction(language: Language) -> str:
    """Der Zusatz zum Systemprompt, damit ein Modell in ``language`` antwortet."""
    return _REPLY_INSTRUCTIONS[language]


def voice_matches(voice_language: str, language: Language) -> bool:
    """Ob eine Piper-Stimme (``de_DE``, ``en_US``) in ``language`` spricht."""
    return voice_language.lower().split("_", 1)[0] == language
