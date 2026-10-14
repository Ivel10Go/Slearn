import { t } from "./i18n";
/*
 * Die Entscheidungen des Sprach-Assistenten - ohne React, ohne DOM.
 *
 * `vitest` laeuft hier mit `environment: "node"` und nimmt nur Dateien unter
 * `lib` auf. Komponenten- und DOM-Tests wuerden **stillschweigend nicht
 * ausgefuehrt**; deshalb liegt alles, was sich entscheiden laesst, in dieser
 * Datei und wird von dort getestet: die Aufnahme-Geraete, der Satz fuer die
 * Oberflaeche, die Zustandsmaschine des Tastenkuerzels und der aufbereitete
 * Vorlesetext.
 *
 * Fuenf Bausteine, jeder mit einer bewussten Entscheidung:
 *
 * 1. **`waehleAufnahmeFormat`** - Chrome liefert WebM/Opus, Safari MP4/AAC. Die
 *    Reihenfolge in {@link AUFNAHME_FORMATE} ist eine Praeferenz, keine
 *    Vollstaendigkeit: der erste Typ, den der Browser in `isTypeSupported`
 *    bejaht, gewinnt. Ohne diese Funktion wuerde die Oberflaeche raten und auf
 *    einem der beiden Browsern eine Datei produzieren, die der Server nicht
 *    lesen kann - ein Fehler, der erst nach dem Aufnehmen auffaellt.
 * 2. **`istTextfeld`** - der Fokus-Schutz. Ein Tastenkuerzel, das in einem
 *    Eingabefeld feuert, ist der klassische Fehler in einer Oberflaeche, die
 *    selbst viele Eingabefelder hat. Es wird **ein** Satz geprueft, und der
 *    Aufrufer kann daraus nichts abmelden.
 * 3. **`naechsterAufnahmeZustand`** - die Zustandsmaschine von Push-to-Talk. Sie
 *    lebt hier, weil das Aufnehmen und das Senden nicht dasselbe sind: ein
 *    Tastendruck startet, der naechste beendet **und uebergibt**, und genau
 *    dieser Uebergang ist die Stelle, an der eine Aufnahme sonst haengen
 *    bleibt.
 * 4. **`bereiteVorlesetext`** - Markdown und Quellen raus, Zahlen und Abkuerzungen
 *    ausgeschrieben. Eine Sprachausgabe, die ``**fett**`` vorliest, ist ein
 *    Fehler, kein Stil.
 * 5. **`sprachZustand`** - die Antwort auf die Frage, was die Oberflaeche gerade
 *    zeigt. Vier Zustaende statt zwei, weil **halbe Ausstattung** ein
 *    Normalzustand ist: Spracheingabe ohne Ausgabe geht, Ausgabe ohne Eingabe
 *    auch, und beides zusammen ist ein Bonus.
 *
 * Der Textmodus ist dabei kein Fehlerzweig: `sprachZustand` liefert auch dann
 * einen vollstaendigen Zustand, wenn gar nichts geladen ist.
 */

/** Die Aufnahmeformate in der Reihenfolge der Praeferenz von Slearn. */
export const AUFNAHME_FORMATE: readonly string[] = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4;codecs=mp4a.40.2",
  "audio/mp4",
  "audio/aac",
];

/** Endungen, damit der Server den Container erraten kann - er riecht nicht. */
export const ENDUNG_PRO_FORMAT: Readonly<Record<string, string>> = {
  "audio/webm": ".webm",
  "audio/ogg": ".ogg",
  "audio/mp4": ".m4a",
  "audio/aac": ".aac",
};

/**
 * Das erste Format, das der Browser kann.
 *
 * `isTypeSupported` wird **in dieser Reihenfolge** abgefragt und beim ersten
 * Ja beendet. Das ist nicht nur eine Optimierung: `MediaRecorder` nimmt genau
 * den Typ entgegen, den man ihm gibt, und ein Browser, der WebM *und* MP4
 * kann (Safari ab 14.1 kann beides), liefert sonst je nach Plattform ein
 * anderes Ergebnis. Die Reihenfolge macht das Ergebnis vorhersagbar.
 *
 * `null` heisst: **dieser Browser kann gar nicht aufnehmen** - kein
 * `MediaRecorder` oder kein Format. Das ist kein Fehler, sondern der Fall, fuer
 * den der Textmodus da ist.
 */
export function waehleAufnahmeFormat(unterstuetzt?: (mime: string) => boolean): string | null {
  const frage =
    typeof unterstuetzt === "function"
      ? unterstuetzt
      : typeof MediaRecorder !== "undefined" && typeof MediaRecorder.isTypeSupported === "function"
        ? (mime: string) => MediaRecorder.isTypeSupported(mime)
        : null;
  if (!frage) {
    return null;
  }
  for (const mime of AUFNAHME_FORMATE) {
    try {
      if (frage(mime)) {
        return mime;
      }
    } catch {
      // Manche Browser werfen fuer unbekannte Mimetypes statt `false` zu
      // liefern. Das ist dieselbe Antwort - nur lauter - und darf die Schleife
      // nicht abbrechen.
    }
  }
  return null;
}

/** Die Endung, die zu einem MIME-Typ gehoert. Leer, wenn nichts bekannt ist. */
export function endungFuerFormat(mime: string | null): string {
  if (!mime) {
    return "";
  }
  const basis = mime.split(";")[0].trim().toLowerCase();
  return ENDUNG_PRO_FORMAT[basis] ?? "";
}

/**
 * Was traegt gerade den Fokus?
 *
 * Bewusst **nicht** ueber `tagName` allein: `contentEditable` steht auf fast
 * jedem Element, `isContentEditable` aber nur dort, wo es wirklich editierbar
 * ist. Und `<select>` steht nicht in der Liste der "Textfelder", feuert aber
 * genauso - ein Tastenkuerzel waehrend einer offenen Auswahlliste ist ein
 * Zustand, in dem niemand es erwartet.
 *
 * Bewusst **nicht** angefragt, ob irgendein Dialog offen ist: das ist eine
 * Frage an den Zustand des ganzen Fensters, und sie zu beantworten gehoert an
 * die Stelle, die den Dialog geoeffnet hat.
 */
export function istTextfeld(ziel: unknown): boolean {
  if (typeof ziel !== "object" || ziel === null) {
    return false;
  }
  const knoten = ziel as { tagName?: unknown; isContentEditable?: unknown };
  const name = typeof knoten.tagName === "string" ? knoten.tagName.toUpperCase() : "";
  if (name === "INPUT" || name === "TEXTAREA" || name === "SELECT") {
    return true;
  }
  return knoten.isContentEditable === true;
}

/** Die Tastenkombination von Slearn, in beiden Schreibweisen. */
export const KUERZEL_BEZEICHNUNG = "Strg + Umschalt + L";
export const KUERZEL_BEZEICHNUNG_MAC = "Befehl + Umschalt + L";
export const KUERZEL_TASTE = "l";

export interface KurzelEreignis {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  altKey?: boolean;
  /** Eine laufende Eingabeumschaltung (Composition) - da darf nichts passieren. */
  isComposing?: boolean;
  defaultPrevented?: boolean;
  target?: unknown;
  repeat?: boolean;
}

/**
 * Feuert das Tastenkuerzel, oder liegt es an der Taste eines anderen Programms?
 *
 * Fuenf Bedingungen, alle noetig:
 *
 * * **Die Taste ist ``l``** - in Gross- oder Kleinbuchstaben.
 * * **Genau eine der beiden Strg-Tasten** ist gedrueckt. `ctrlKey` und
 *   `metaKey` gleichzeitig zu druecken waere ein Tastenkuerzel eines
 *   anderen Programms, kein Zufall.
 * * **Umschalt ist gedrueckt.** Ohne Umschalt waere es ``Strg+L`` - das
 *   Adressfeld, in jedem Browser.
 * * **Alt ist nicht gedrueckt.** ``Alt`` aendert in Windows die Menueklammer und
 *   in Firefox die Menues.
 * * **Es wird gerade nichts getippt.** Ein ``contentEditable``-Bereich und
 *   jedes Eingabefeld kommen hier raus, ebenso eine laufende Eingabeumschaltung:
 *   wer gerade japanisch ueber eine Eingabemethode tippt, darf davon nicht
 *   ueberrascht werden.
 *
 * `preventDefault` wird hier **nicht** aufgerufen. Das bleibt beim Aufrufer,
 * der den Zustand kennt: das Kuerzel wird auch dann unterdrueckt, wenn gerade
 * nicht aufgenommen wird - sonst wuerde der Browser die Seite dabeiscrollen.
 */
export function feuertKuerzel(ereignis: KurzelEreignis): boolean {
  if (ereignis.key.toLowerCase() !== KUERZEL_TASTE) {
    return false;
  }
  const ctrl = ereignis.ctrlKey === true;
  const meta = ereignis.metaKey === true;
  if (ctrl === meta) {
    return false;
  }
  if (ereignis.shiftKey !== true) {
    return false;
  }
  if (ereignis.altKey === true) {
    return false;
  }
  if (ereignis.isComposing === true) {
    return false;
  }
  return !istTextfeld(ereignis.target);
}

/** Die drei Zustaende, in denen eine Aufnahme sein kann. */
export type AufnahmeZustand = "bereit" | "nimmt_auf" | "wird_gesendet";

export type AufnahmeAktion = "start" | "stopp";

/**
 * Die Zustandsmaschine von Push-to-Talk, als reine Funktion.
 *
 * Vier Faelle, und der vierte ist der Grund fuer die ganze Datei:
 *
 * * ``bereit`` + ``start`` -> ``nimmt_auf`` (die erste Nutzergeste ueberhaupt)
 * * ``nimmt_auf`` + ``stopp`` -> ``wird_gesendet`` (die Taste loslassen)
 * * ``bereit`` + ``stopp`` -> ``bereit`` (Tastendruck kam zu spaet: kein Fehler)
 * * ``wird_gesendet`` + ``start`` -> ``nimmt_auf`` (die naechste Aufnahme darf
 *   sofort beginnen; der Textmodus laeuft nebenbei weiter)
 *
 * **Es gibt keinen Zustand, aus dem man nicht wieder herauskommt.** Genau
 * daran haengt die Datenschutzfrage: ein ``nimmt_auf``, der bei einem Abbruch
 * stehenbleibt, laesst das Mikrofon offen, und das ist kein Bedienfehler,
 * sondern eine offene Aufnahme im Raum der Lernenden.
 */
export function naechsterAufnahmeZustand(zustand: AufnahmeZustand, aktion: AufnahmeAktion): AufnahmeZustand {
  if (aktion === "start") {
    return "nimmt_auf";
  }
  return zustand === "nimmt_auf" ? "wird_gesendet" : zustand;
}

/** Wie eine Aufnahme in diesem Zustand heisst. Deutsch, ohne Farbe, ohne Punkt. */
export function beschreibeAufnahme(zustand: AufnahmeZustand, sekunden: number): string {
  if (zustand === "nimmt_auf") {
    return t("Nimmt auf · {dauer}", { dauer: formatSekunden(sekunden) });
  }
  if (zustand === "wird_gesendet") {
    return t("Wird ausgewertet …");
  }
  return t("Bereit");
}

/** Sekunden als `m:ss`. Negative und nicht endliche Werte ergeben `0:00`. */
export function formatSekunden(sekunden: number): string {
  if (!Number.isFinite(sekunden) || sekunden <= 0) {
    return "0:00";
  }
  const ganz = Math.floor(sekunden);
  return `${Math.floor(ganz / 60)}:${String(ganz % 60).padStart(2, "0")}`;
}

/**
 * Der Text, der wirklich gesprochen wird.
 *
 * Fuenf Schritte, in dieser Reihenfolge - jede davon faellt in der
 * Oberflaeche sofort auf:
 *
 * 1. **Quellenzeilen raus.** Eine Zeile, die nur aus einer Zahl und einem Punkt
 *    besteht, ist eine Fundstelle, kein Satz. Ein Vorlesetext mit "3." darin
 *    klingt wie ein Absturz.
 * 2. **Markdown weg.** Fuette, Kursiv, Code, Ueberschriften, Listenzeichen,
 *    Links und Bilder. Ohne das wuerde die Sprachausgabe ``**fett**`` und
 *    ``[Text](Ziel)`` buchstabieren.
 * 3. **Zahlen ausschreiben.** `2.3` wird zu "zwei Komma drei". Ein Vorleser
 *    sagt auch "zwei Komma drei", aber nicht "zweipunkt drei".
 * 4. **Zeilenumbruch zu Punkt.** Zwei harte Umbrueche hintereinander sind ein
 *    Absatz, kein Satzende - und ohne Punkt stolpert die Stimme darueber.
 * 5. **Kuerzen, wenn es zu lang wird.** Am **Satzende**, nie mitten im Satz.
 *    Eine mitten im Wort abgeschnittene Antwort ist schlimmer als eine
 *    vollstaendig vorgelesene, kuerzere Antwort.
 */
export function bereiteVorlesetext(text: string, maxZeichen = 1200): string {
  if (typeof text !== "string") {
    return "";
  }
  let text2 = text
    .replace(/^\s*\[\d+\]\s*/gm, "")
    .replace(/^\s*Quelle:\s.*$/gim, "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s.,;:!?)]|$)/g, "$1$2")
    .replace(/(^|[\s(])_([^_\n]+)_(?=[\s.,;:!?)]|$)/g, "$1$2")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
  text2 = text2.replace(/(\d),(\d)/g, "$1 Komma $2");
  text2 = text2.replace(/(\d)\.(\d)/g, "$1 Komma $2");
  text2 = text2.replace(/\n{2,}/g, ". ");
  text2 = text2.replace(/\n/g, ". ");
  text2 = text2.replace(/\.{2,}/g, ".").replace(/\.\s*\./g, ".").trim();
  if (maxZeichen > 0 && text2.length > maxZeichen) {
    const gekuerzt = text2.slice(0, maxZeichen);
    const letzterPunkt = Math.max(gekuerzt.lastIndexOf(". "), gekuerzt.lastIndexOf("! "), gekuerzt.lastIndexOf("? "));
    text2 = `${gekuerzt.slice(0, letzterPunkt + 1).trim()} …`;
  }
  return text2;
}

/** Was die Oberflaeche anbietet. Vier Zustaende, weil halbe Ausstattung normal ist. */
export type SprachZustand = "nur_text" | "nur_eingabe" | "nur_ausgabe" | "vollstaendig";

export interface FaehigkeitenEingabe {
  speech_to_text?: { available?: unknown };
  text_to_speech?: { available?: unknown };
}

/**
 * Welche der beiden Richtungen gerade geht - **ohne** Fehler und **ohne** dass
 * irgendwo ein Dialog aufgeht.
 *
 * Das ist der Kern von Punkt 5 des Plans: der Textmodus ist kein
 * Notfallpfad, sondern der Zustand, in dem die meisten Lernenden sein werden.
 * Deshalb gibt es vier Zustaende statt zwei und jeder davon ist benannt -
 * „es geht nichts" ist eine vollstaendige, benannte Antwort.
 */
export function sprachZustand(faehigkeiten: FaehigkeitenEingabe | null | undefined): SprachZustand {
  const ein = faehigkeiten?.speech_to_text?.available === true;
  const aus = faehigkeiten?.text_to_speech?.available === true;
  if (ein && aus) {
    return "vollstaendig";
  }
  if (ein) {
    return "nur_eingabe";
  }
  if (aus) {
    return "nur_ausgabe";
  }
  return "nur_text";
}

/** Der Satz, der zu diesem Zustand gehoert. Ein Satz, kein Dialog, kein Fehler. */
export function sprachHinweis(zustand: SprachZustand): string {
  switch (zustand) {
    case "vollstaendig":
      return t("Spracheingabe und Sprachausgabe sind bereit.");
    case "nur_eingabe":
      return t("Du kannst hier sprechen. Gelesen wird der Text weiterhin von dir - eine Stimme dafür wäre ein eigenes Modell.");
    case "nur_ausgabe":
      return t("Die Antwort wird vorgelesen. Tippen kannst du weiterhin - Spracheingabe wäre ein eigenes Modell.");
    default:
      return t("Tippen geht immer. Spracheingabe und Sprachausgabe kommen hinzu, sobald die passenden Modelle geladen sind.");
  }
}

/** Der Mikrofon-Zustand, inklusive der Faelle, die **kein** Fehler sind. */
export type MikrofonZustand = "unbekannt" | "erlaubt" | "verweigert" | "fehlt" | "blockiert";

/**
 * Was ein ``getUserMedia``-Fehler bedeutet - **ohne** ihn als Fehler zu melden.
 *
 * Der wichtigste Fall ist ``verweigert``: der Nutzer hat abgelehnt. Das ist eine
 * gueltige Antwort auf eine Frage und kein Defekt - es darf keine rote Meldung
 * und keinen Dialog geben. ``NotReadableError`` heisst dagegen "das Geraet ist
 * da, laesst sich aber nicht oeffnen" (es lueuft gerade etwas anderes), und
 * das ist eine echte Meldung wert.
 */
export function mikrofonZustand(ursache: unknown): MikrofonZustand {
  const name =
    typeof ursache === "object" && ursache !== null && "name" in ursache
      ? String((ursache as { name?: unknown }).name ?? "")
      : "";
  if (!name) {
    return "unbekannt";
  }
  if (name === "NotAllowedError" || name === "PermissionDeniedError" || name === "SecurityError") {
    return "verweigert";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") {
    return "fehlt";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "blockiert";
  }
  return "unbekannt";
}

/** Der deutsche Satz zu einem Mikrofonzustand. Auch hier: kein Fehlerton. */
export function mikrofonHinweis(zustand: MikrofonZustand): string {
  switch (zustand) {
    case "erlaubt":
      return t("Das Mikrofon ist bereit.");
    case "verweigert":
      return t("Ohne Mikrofon kannst du hier tippen. Falls du es doch erlauben willst, steht es oben in der Adresszeile des Browsers.");
    case "fehlt":
      return t("An diesem Geraet wurde kein Mikrofon gefunden. Tippen geht genauso.");
    case "blockiert":
      return t("Das Mikrofon liess sich nicht oeffnen - es wird gerade von einem anderen Programm benutzt.");
    default:
      return t("Das Mikrofon wurde noch nicht angesprochen. Tippen geht jederzeit.");
  }
}

/* ------------------------------------------------------------------ Antworten */

/** Die Form einer Richtung in der Antwort des Servers. */
export interface SpeechTaskPayload {
  available: boolean;
  reason: string;
  model_id: string | null;
  label: string;
  language: string | null;
  language_label: string;
}

/** Die Form der Aufnahmegrenzen in der Antwort des Servers. */
export interface SpeechInputPayload {
  max_bytes: number;
  max_seconds: number;
  accepted_types: string[];
  sample_rate: number;
  needs_transcoding: boolean;
  transcoding_note: string;
}

/** Die Form von ``GET /api/v1/speech/capabilities``. */
export interface SpeechCapabilitiesPayload {
  text_mode_available: boolean;
  text_mode_note: string;
  speech_to_text: SpeechTaskPayload;
  text_to_speech: SpeechTaskPayload;
  input: SpeechInputPayload;
  runtime: {
    transcriber_reachable: boolean;
    transcriber_note: string;
    synthesizer_available: boolean;
    synthesizer_note: string;
    ffmpeg_present: boolean;
    ffmpeg_note: string;
  };
  /**
   * Die Stimme, die **jetzt** gesprochen wuerde - `null`, solange keine geladen ist.
   *
   * Getrennt von {@link recommended_voice_id}: die Empfehlung gilt immer, der
   * tatsaechliche Gebrauch nur mit Ablage. Aus einer Empfehlung eine Tatsache zu
   * machen waere genau die Art Zusage, die der Sprachauswahl zugrunde liegt.
   */
  default_voice_id: string | null;
  /** Die Empfehlung von Slearn, unabhaengig davon, was geladen ist. */
  recommended_voice_id: string | null;
  installed_voice_ids: string[];
}

/** Der Downloadstand einer Stimme - dieselben Zustaende wie beim Modellkatalog. */
export type VoiceStatus = "not_downloaded" | "downloading" | "installed" | "interrupted" | "failed";

export interface VoicePayload {
  id: string;
  label: string;
  language: string;
  language_label: string;
  speaker: string;
  speaker_label: string;
  quality: string;
  sample_rate: number;
  description: string;
  license: string;
  source_page: string;
  dataset_url: string;
  size_bytes: number;
  files: { name: string; size_bytes: number; is_model: boolean; is_config: boolean }[];
  status: {
    status: VoiceStatus;
    bytes_downloaded: number;
    bytes_total: number;
    progress_percent: number;
    current_file: string | null;
    verified: boolean;
    message: string;
  };
}

export interface VoiceListPayload {
  voices: VoicePayload[];
  default_voice_id: string | null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function asText(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asTask(value: unknown): SpeechTaskPayload {
  const row = asRecord(value);
  return {
    available: row?.available === true,
    reason: asText(row?.reason),
    model_id: asText(row?.model_id) || null,
    label: asText(row?.label),
    language: asText(row?.language) || null,
    language_label: asText(row?.language_label),
  };
}

/**
 * Engt ``GET /api/v1/speech/capabilities`` auf das Angezeigte.
 *
 * Fehlt eine Richtung ganz, wird sie als **nicht verfuegbar mit leerem Grund**
 * uebernommen, nicht geraten. Das ist wichtig, weil {@link sprachZustand} daraus
 * den Textmodus macht: eine fehlende Antwort soll den Textmodus ergeben und
 * keinen Fehler.
 */
export function parseSpeechCapabilities(payload: unknown): SpeechCapabilitiesPayload | null {
  const root = asRecord(payload);
  if (!root) {
    return null;
  }
  const input = asRecord(root.input);
  const runtime = asRecord(root.runtime);
  return {
    text_mode_available: root.text_mode_available !== false,
    text_mode_note: asText(root.text_mode_note),
    speech_to_text: asTask(root.speech_to_text),
    text_to_speech: asTask(root.text_to_speech),
    input: {
      max_bytes: asNumber(input?.max_bytes),
      max_seconds: asNumber(input?.max_seconds),
      accepted_types: Array.isArray(input?.accepted_types) ? (input?.accepted_types as unknown[]).filter((x): x is string => typeof x === "string") : [],
      sample_rate: asNumber(input?.sample_rate, 16000),
      needs_transcoding: input?.needs_transcoding === true,
      transcoding_note: asText(input?.transcoding_note),
    },
    runtime: {
      transcriber_reachable: runtime?.transcriber_reachable === true,
      transcriber_note: asText(runtime?.transcriber_note),
      synthesizer_available: runtime?.synthesizer_available === true,
      synthesizer_note: asText(runtime?.synthesizer_note),
      ffmpeg_present: runtime?.ffmpeg_present === true,
      ffmpeg_note: asText(runtime?.ffmpeg_note),
    },
    default_voice_id: asText(root.default_voice_id) || null,
    recommended_voice_id: asText(root.recommended_voice_id) || null,
    installed_voice_ids: Array.isArray(root.installed_voice_ids)
      ? (root.installed_voice_ids as unknown[]).filter((x): x is string => typeof x === "string")
      : [],
  };
}

/** Engt ``GET /api/v1/speech/voices`` - ohne Eintrag ohne ``id``, weil ohne sie kein Knopf gehoert. */
export function parseVoiceList(payload: unknown): VoiceListPayload | null {
  const root = asRecord(payload);
  if (!root) {
    return null;
  }
  const roh = Array.isArray(root.voices) ? root.voices : [];
  const stimmen = roh.flatMap((eintrag): VoicePayload[] => {
    const row = asRecord(eintrag);
    const id = asText(row?.id);
    if (!id) {
      return [];
    }
    const status = asRecord(row?.status);
    const dateien = Array.isArray(row?.files) ? row.files : [];
    return [
      {
        id,
        label: asText(row?.label, id),
        language: asText(row?.language, "unbekannt"),
        language_label: asText(row?.language_label, "Unbekannt"),
        speaker: asText(row?.speaker, "unbekannt"),
        speaker_label: asText(row?.speaker_label, "Unbekannt"),
        quality: asText(row?.quality, "unbekannt"),
        sample_rate: asNumber(row?.sample_rate),
        description: asText(row?.description),
        license: asText(row?.license, "unbekannt"),
        source_page: asText(row?.source_page),
        dataset_url: asText(row?.dataset_url),
        size_bytes: asNumber(row?.size_bytes),
        files: dateien.flatMap((datei) => {
          const eintrag2 = asRecord(datei);
          const name = asText(eintrag2?.name);
          return name
            ? [
                {
                  name,
                  size_bytes: asNumber(eintrag2?.size_bytes),
                  is_model: eintrag2?.is_model === true,
                  is_config: eintrag2?.is_config === true,
                },
              ]
            : [];
        }),
        status: {
          status: asText(status?.status, "not_downloaded") as VoiceStatus,
          bytes_downloaded: asNumber(status?.bytes_downloaded),
          bytes_total: asNumber(status?.bytes_total),
          progress_percent: asNumber(status?.progress_percent),
          current_file: asText(status?.current_file) || null,
          verified: status?.verified === true,
          message: asText(status?.message),
        },
      },
    ];
  });
  return { voices: stimmen, default_voice_id: asText(root.default_voice_id) || null };
}

/** Der Stand einer Stimme als deutsches Wort. Wie beim Modellkatalog. */
export function beschreibeStimmstatus(status: string): string {
  switch (status) {
    case "installed":
      return t("Geladen");
    case "downloading":
      return t("Wird geladen");
    case "interrupted":
      return t("Unterbrochen");
    case "failed":
      return t("Fehlgeschlagen");
    default:
      return t("Nicht geladen");
  }
}

/** Die Guetestufe als deutsches Wort - ein Auswahlfeld, das Sprache spricht. */
export function beschreibeQualitaet(quality: string): string {
  switch (quality) {
    case "low":
      return t("niedrig");
    case "medium":
      return t("mittel");
    case "high":
      return t("hoch");
    default:
      return quality;
  }
}

/**
 * Ist eine Stimme fuer eine **deutsche** Oberflaeche brauchbar?
 *
 * Die zweite Haelfte der Sprachauswahl: nicht nur „welche Sprache", sondern
 * „welche Sprache braucht dieser Nutzer". Eine geladene englische Stimme ist
 * in einer deutschen Oberflaeche keine Alternative - sie ist die falsche
 * Sprache, und das wird hier benannt statt stillschweigend akzeptiert.
 */
export function brauchtDeutscheStimme(voices: VoicePayload[]): boolean {
  return hatStimmeInSprache(voices, "de");
}

/** Spricht eine Stimme (`de_DE`, `en_US`) die Sprache `locale`? */
export function stimmeSpricht(stimme: Pick<VoicePayload, "language">, locale: string): boolean {
  return stimme.language.toLowerCase().split("_")[0] === locale;
}

/** Ist eine Stimme in der Sprache der Oberflaeche geladen? Dieselbe Regel wie im Backend (`voice_matches`). */
export function hatStimmeInSprache(voices: VoicePayload[], locale: string): boolean {
  return voices.some((stimme) => stimme.status.status === "installed" && stimmeSpricht(stimme, locale));
}
/**
 * Ist das ein Mac? Fuer die **Beschriftung** des Tastenkuerzels, nicht fuer die
 * Funktion - {@link feuertKuerzel} nimmt ``ctrlKey`` und ``metaKey`` beide.
 *
 * Der User-Agent ist eine '''niedrige''' Sicherheitsgrenze: jemand, der die
 * Anzeige mit einem falschen Browsertext versehen will, schafft das. Das ist in
 * Ordnung, weil hier nichts geschuetzt wird - nur die Bezeichnung eines
 * Tastenkuerzels, und die steht sonst auf der falschen Taste.
 */
export function istMac(userAgent: string): boolean {
  return /Mac|iPhone|iPad|iPod/i.test(userAgent);
}