import { describe, expect, it } from "vitest";
import {
  AUFNAHME_FORMATE,
  KUERZEL_BEZEICHNUNG,
  KUERZEL_BEZEICHNUNG_MAC,
  KUERZEL_TASTE,
  bereiteVorlesetext,
  beschreibeAufnahme,
  beschreibeQualitaet,
  beschreibeStimmstatus,
  brauchtDeutscheStimme,
  endungFuerFormat,
  feuertKuerzel,
  formatSekunden,
  istMac,
  mikrofonHinweis,
  mikrofonZustand,
  naechsterAufnahmeZustand,
  parseSpeechCapabilities,
  parseVoiceList,
  sprachHinweis,
  sprachZustand,
  waehleAufnahmeFormat,
  type AufnahmeZustand,
} from "./speech";

/*
 * Die Tests liegen hier und nicht bei der Komponente, weil `vitest` in diesem
 * Projekt ausschliesslich `lib/**` mit `environment: "node"` laufen laesst. Ein
 * Komponenten- oder DOM-Test wuerde stillschweigend nicht ausgefuehrt - und ein
 * Test, der nicht laeuft, ist eine Behauptung ohne Deckung.
 *
 * Drei Dinge lassen sich unter `node` nicht pruefen und stehen deshalb in
 * `speech.ts` ausdruecklich als nicht ausgefuehrt: `MediaRecorder` selbst,
 * `getUserMedia` selbst und die Wiedergabe eines `Blob`. Was **rundherum**
 * entschieden wird - welches Format gewaehlt wird, ob der Fokus im Weg ist,
 * was der Browser-Fehler bedeutet, wie der Vorlesetext aussieht - ist hier
 * vollstaendig getestet.
 */

describe("Aufnahmeformat", () => {
  it("nimmt das erste Format, das der Browser kann", () => {
    const gewaehlt = waehleAufnahmeFormat((mime) => mime === "audio/ogg;codecs=opus");
    expect(gewaehlt).toBe("audio/ogg;codecs=opus");
  });

  it("bevorzugt WebM mit Opus vor allem anderen", () => {
    // Chrome kann WebM *und* MP4. Ohne feste Reihenfolge waere das Ergebnis
    // plattformabhaengig - und die Datei, die dabei herauskommt, laesst sich
    // nicht uebertragen.
    const gewaehlt = waehleAufnahmeFormat(() => true);
    expect(gewaehlt).toBe(AUFNAHME_FORMATE[0]);
    expect(gewaehlt).toBe("audio/webm;codecs=opus");
  });

  it("faellt auf MP4 zurueck, wenn WebM nicht kann", () => {
    // Das ist der Safari-Fall: MP4 mit AAC statt WebM mit Opus.
    const gewaehlt = waehleAufnahmeFormat((mime) => mime.startsWith("audio/mp4"));
    expect(gewaehlt).toBe("audio/mp4;codecs=mp4a.40.2");
  });

  it("meldet null, wenn der Browser gar nichts aufnehmen kann", () => {
    expect(waehleAufnahmeFormat(() => false)).toBeNull();
  });

  it("fragt nicht, wenn es kein MediaRecorder gibt", () => {
    // `environment: "node"` kennt kein `MediaRecorder`. Die Funktion muss das
    // bemerken und `null` liefern, statt einen ReferenceError zu werfen - genau
    // das waere der Fall, in dem die Oberflaeche im Server-Render abstuerzt.
    expect(waehleAufnahmeFormat()).toBeNull();
  });

  it("laesst sich von einem Browser nicht beirren, der statt false wirft", () => {
    const gewaehlt = waehleAufnahmeFormat((mime) => {
      if (mime.includes("webm")) {
        throw new TypeError("unbekannter Mimetype");
      }
      return mime.startsWith("audio/mp4");
    });
    expect(gewaehlt).toBe("audio/mp4;codecs=mp4a.40.2");
  });

  it("leitet die Endung aus dem Container ab", () => {
    expect(endungFuerFormat("audio/webm;codecs=opus")).toBe(".webm");
    expect(endungFuerFormat("audio/mp4;codecs=mp4a.40.2")).toBe(".m4a");
    expect(endungFuerFormat(null)).toBe("");
    expect(endungFuerFormat("application/pdf")).toBe("");
  });
});

describe("Fokusschutz", () => {
  it("laesst das Kuerzel in keinem Eingabefeld feuern", () => {
    for (const tagName of ["INPUT", "TEXTAREA", "SELECT", "input", "textarea"]) {
      const ereignis = { key: "L", ctrlKey: true, shiftKey: true, target: { tagName } };
      expect(feuertKuerzel(ereignis)).toBe(false);
    }
  });

  it("laesst das Kuerzel in einem editierbaren Bereich nicht feuern", () => {
    // `contentEditable` steht auf fast jedem Element; `isContentEditable` nur
    // dort, wo es wirklich editierbar ist. Nur das zweite zu pruefen ist der
    // Unterschied zwischen "blockiert" und "haengt".
    expect(feuertKuerzel({ key: "L", ctrlKey: true, shiftKey: true, target: { tagName: "DIV", isContentEditable: true } })).toBe(false);
    expect(feuertKuerzel({ key: "L", ctrlKey: true, shiftKey: true, target: { tagName: "DIV", isContentEditable: false } })).toBe(true);
  });

  it("feuert ausserhalb von Eingabefeldern", () => {
    expect(feuertKuerzel({ key: "L", ctrlKey: true, shiftKey: true, target: { tagName: "BODY" } })).toBe(true);
    expect(feuertKuerzel({ key: "l", ctrlKey: true, shiftKey: true })).toBe(true);
    expect(feuertKuerzel({ key: "L", metaKey: true, shiftKey: true })).toBe(true);
  });

  it("bleibt bei einer laufenden Eingabeumschaltung still", () => {
    // Wer gerade mit einer Eingabemethode tippt, darf nicht davon ueberrascht
    // werden - das Kuerzel wuerde in der Zwischeneingabe landen.
    expect(feuertKuerzel({ key: "L", ctrlKey: true, shiftKey: true, isComposing: true })).toBe(false);
  });
});

describe("Das gewaehlte Tastenkuerzel", () => {
  it("kommt ohne Umschalt nicht aus", () => {
    // Strg+L ist das Adressfeld, in jedem Browser. Genau deshalb ist die
    // Umschalt-Taste Teil der Kombination und nicht Zierde.
    expect(feuertKuerzel({ key: "L", ctrlKey: true })).toBe(false);
    expect(feuertKuerzel({ key: "L", metaKey: true })).toBe(false);
  });

  it("kommt mit beiden Strg-Tasten nicht aus", () => {
    expect(feuertKuerzel({ key: "L", ctrlKey: true, metaKey: true, shiftKey: true })).toBe(false);
    expect(feuertKuerzel({ key: "L", shiftKey: true })).toBe(false);
  });

  it("kommt mit Alt nicht aus", () => {
    // Alt aendert in Windows die Menueklammer und in Firefox die Menues.
    expect(feuertKuerzel({ key: "L", ctrlKey: true, shiftKey: true, altKey: true })).toBe(false);
  });

  it("greift bei einer anderen Taste nicht", () => {
    for (const key of ["k", "Enter", "Escape", " ", "ArrowUp", "F2"]) {
      expect(feuertKuerzel({ key, ctrlKey: true, shiftKey: true })).toBe(false);
    }
  });
});

describe("Zustandsmaschine von Push-to-Talk", () => {
  it("nimmt beim Druck auf und sendet beim Loslassen", () => {
    const zustand: AufnahmeZustand = naechsterAufnahmeZustand("bereit", "start");
    expect(zustand).toBe("nimmt_auf");
    expect(naechsterAufnahmeZustand(zustand, "stopp")).toBe("wird_gesendet");
  });

  it("behandelt einen zu spaeten Tastendruck als normalen Fall", () => {
    // Der Knopf war schon gedrueckt, als die Taste losgelassen wurde: das ist
    // kein Fehler und darf keinen Fehler zeigen.
    expect(naechsterAufnahmeZustand("bereit", "stopp")).toBe("bereit");
  });

  it("laesst sofort die naechste Aufnahme zu", () => {
    // Sonst muesste man warten, bis der Text da ist - und genau in dieser
    // Wartezeit entsteht die haengende Aufnahme.
    expect(naechsterAufnahmeZustand("wird_gesendet", "start")).toBe("nimmt_auf");
  });

  it("kommt aus jedem Zustand wieder heraus", () => {
    // Die Datenschutzfrage: es gibt keinen Zustand, aus dem man nicht wieder
    // herauskommt. Ein `nimmt_auf`, der stehen bleibt, laesst das Mikrofon offen.
    for (const start of ["bereit", "nimmt_auf", "wird_gesendet"] as const) {
      const nachStopp = naechsterAufnahmeZustand(start, "stopp");
      expect(nachStopp).not.toBe("nimmt_auf");
      expect(naechsterAufnahmeZustand(nachStopp, "start")).toBe("nimmt_auf");
      expect(naechsterAufnahmeZustand(nachStopp, "stopp")).toBe(nachStopp);
    }
  });

  it("beschreibt die Aufnahme ohne Farbe und ohne blinkenden Kreis", () => {
    expect(beschreibeAufnahme("nimmt_auf", 65)).toBe("Nimmt auf · 1:05");
    expect(beschreibeAufnahme("wird_gesendet", 65)).toBe("Wird ausgewertet …");
    expect(beschreibeAufnahme("bereit", 0)).toBe("Bereit");
  });
});

describe("Sekunden als Zeit", () => {
  it("schreibt Minuten und Sekunden mit fuehrender Null", () => {
    expect(formatSekunden(0)).toBe("0:00");
    expect(formatSekunden(5)).toBe("0:05");
    expect(formatSekunden(65)).toBe("1:05");
    expect(formatSekunden(3599)).toBe("59:59");
  });

  it("behandelt Unsinn als null", () => {
    expect(formatSekunden(-3)).toBe("0:00");
    expect(formatSekunden(Number.NaN)).toBe("0:00");
    expect(formatSekunden(Number.POSITIVE_INFINITY)).toBe("0:00");
  });
});

describe("Vorlesetext", () => {
  it("spricht keine Markdown-Zeichen mit", () => {
    expect(bereiteVorlesetext("**fett** und _kursiv_ und `code`")).toBe("fett und kursiv und code");
  });

  it("spricht keine Links und Bilder mit", () => {
    expect(bereiteVorlesetext("Siehe [der Satz](https://example.org) und ![Bild](x.png)")).toBe("Siehe der Satz und");
  });

  it("wirft die Fundstellen am Antwortende raus", () => {
    // Eine Zeile aus einer Zahl und einem Punkt ist eine Quelle, kein Satz. Ohne
    // das wuerde die Sprachausgabe mitten in einer Liste abbrechen.
    expect(bereiteVorlesetext("Der Satz gilt.\n\n[1]")).toBe("Der Satz gilt.");
  });

  it("sagt Dezimalzahlen wie ein Mensch", () => {
    expect(bereiteVorlesetext("Der Wert 2.3 und die Grenze 0.75")).toBe("Der Wert 2 Komma 3 und die Grenze 0 Komma 75");
  });

  it("macht aus harten Umbruechen Satzzeichen", () => {
    expect(bereiteVorlesetext("Erster Teil.\nZweiter Teil.")).toBe("Erster Teil. Zweiter Teil.");
    expect(bereiteVorlesetext("Erster Teil.\n\nZweiter Teil.")).toBe("Erster Teil. Zweiter Teil.");
  });

  it("setzt kein doppeltes Satzzeichen", () => {
    expect(bereiteVorlesetext("Fertig.\n\n\nEnde.")).toBe("Fertig. Ende.");
  });

  it("kuerzt am Satzende und nie mitten im Wort", () => {
    const lang = `${"Ein sehr langer Satz. ".repeat(20)}Ende.`;
    const gekuerzt = bereiteVorlesetext(lang, 120);
    expect(gekuerzt.endsWith(" …")).toBe(true);
    expect(gekuerzt).not.toMatch(/[A-Za-zäöüß]$/);
    expect(gekuerzt.length).toBeLessThanOrEqual(122);
  });

  it("laesst einen kurzen Text unangetastet", () => {
    expect(bereiteVorlesetext("Kurz und gut.")).toBe("Kurz und gut.");
  });

  it("gibt bei Unsinn einen leeren String zurueck", () => {
    expect(bereiteVorlesetext(undefined as unknown as string)).toBe("");
    expect(bereiteVorlesetext("")).toBe("");
  });
});

describe("Der Zustand der Oberflaeche", () => {
  it("kennt vier Zustaende, weil halbe Ausstattung normal ist", () => {
    expect(sprachZustand({ speech_to_text: { available: true }, text_to_speech: { available: true } })).toBe("vollstaendig");
    expect(sprachZustand({ speech_to_text: { available: true }, text_to_speech: { available: false } })).toBe("nur_eingabe");
    expect(sprachZustand({ speech_to_text: { available: false }, text_to_speech: { available: true } })).toBe("nur_ausgabe");
  });

  it("faellt ohne Faehigkeiten auf den reinen Textmodus zurueck", () => {
    // Das ist der Zustand, in dem sich die meisten Lernenden befinden werden -
    // und er ist ein vollstaendiger, benannter Zustand und kein Fehler.
    expect(sprachZustand(null)).toBe("nur_text");
    expect(sprachZustand(undefined)).toBe("nur_text");
    expect(sprachZustand({})).toBe("nur_text");
    expect(sprachZustand({ speech_to_text: { available: "ja" }, text_to_speech: {} })).toBe("nur_text");
  });

  it("hat zu jedem Zustand einen deutschen Satz", () => {
    for (const zustand of ["nur_text", "nur_eingabe", "nur_ausgabe", "vollstaendig"] as const) {
      const hinweis = sprachHinweis(zustand);
      expect(hinweis.length).toBeGreaterThan(20);
      expect(hinweis).toMatch(/[.!?]$/);
    }
  });

  it("erklaert den Textmodus nie als Fehler", () => {
    // Kein Wort aus der Fehlersprache, wenn nichts geladen ist.
    const hinweis = sprachHinweis("nur_text");
    expect(hinweis).not.toMatch(/Fehler|fehlgeschlagen|nicht verfügbar|nicht verfuegbar/);
  });
});

describe("Was ein Mikrofon-Fehler bedeutet", () => {
  it("liest ein verweigertes Mikrofon als gueltige Antwort", () => {
    // Der Nutzer hat abgelehnt. Das ist keine Fehlermeldung und darf keine sein.
    expect(mikrofonZustand({ name: "NotAllowedError" })).toBe("verweigert");
    expect(mikrofonZustand({ name: "SecurityError" })).toBe("verweigert");
    expect(mikrofonHinweis("verweigert")).toMatch(/tippen/i);
    expect(mikrofonHinweis("verweigert")).not.toMatch(/Fehler/);
  });

  it("unterscheidet ein fehlendes Geraet von einem blockierten", () => {
    expect(mikrofonZustand({ name: "NotFoundError" })).toBe("fehlt");
    expect(mikrofonZustand({ name: "NotReadableError" })).toBe("blockiert");
    expect(mikrofonHinweis("fehlt")).toMatch(/kein Mikrofon/);
    expect(mikrofonHinweis("blockiert")).toMatch(/anderen Programm/);
  });

  it("bleibt bei einem unbekannten Fehler unaufgeregt", () => {
    expect(mikrofonZustand({ name: "IrgendwasNeues" })).toBe("unbekannt");
    expect(mikrofonZustand(new Error("nope"))).toBe("unbekannt");
    expect(mikrofonZustand(null)).toBe("unbekannt");
    expect(mikrofonHinweis("unbekannt")).toMatch(/tippen/i);
  });
});
describe("Die Antwort des Servers", () => {
  const vollstaendig = {
    text_mode_available: true,
    text_mode_note: "Alles laeuft ohne Modell.",
    speech_to_text: { available: true, reason: "bereit", model_id: "whisper-tiny-ggml", label: "Whisper tiny", language: null, language_label: "" },
    text_to_speech: { available: false, reason: "keine Stimme", model_id: null, label: "", language: "en_US", language_label: "Englisch" },
    input: { max_bytes: 12, max_seconds: 120, accepted_types: ["audio/webm", "audio/mp4"], sample_rate: 16000, needs_transcoding: true, transcoding_note: "Hinweis" },
    runtime: { transcriber_reachable: true, transcriber_note: "a", synthesizer_available: false, synthesizer_note: "b" },
    default_voice_id: null,
    installed_voice_ids: [],
  };

  it("liest eine vollstaendige Antwort", () => {
    const geparst = parseSpeechCapabilities(vollstaendig);
    expect(geparst?.speech_to_text.available).toBe(true);
    expect(geparst?.speech_to_text.model_id).toBe("whisper-tiny-ggml");
    expect(geparst?.text_to_speech.available).toBe(false);
    expect(geparst?.text_to_speech.language_label).toBe("Englisch");
    expect(geparst?.input.max_bytes).toBe(12);
    expect(geparst?.runtime.transcriber_reachable).toBe(true);
  });

  it("macht aus einer kaputten Antwort den reinen Textmodus", () => {
    // Kein Fehler und kein Dialog: eine fehlende oder unlesbare Antwort heisst
    // "es weiss nichts", und das ist genau der Zustand, fuer den der Textmodus
    // da ist.
    for (const kaputt of [null, 42, "text", []]) {
      expect(parseSpeechCapabilities(kaputt)).toBeNull();
      expect(sprachZustand(parseSpeechCapabilities(kaputt))).toBe("nur_text");
    }
    // Ein Objekt mit brauchbaren Feldern wird angenommen - auch dann, wenn die
    // Richtungen fehlen. Der Textmodus ist keine Sonderbehandlung, sondern der
    // Zustand, in den alles ohne Modell zurueckfaellt.
    for (const kaputt of [{}, { speech_to_text: "nein" }, { input: 5 }]) {
      const geparst = parseSpeechCapabilities(kaputt);
      expect(geparst?.text_mode_available).toBe(true);
      expect(sprachZustand(geparst)).toBe("nur_text");
    }
  });

  it("verliert keine Richtungsangabe", () => {
    const nur_eingabe = parseSpeechCapabilities({ ...vollstaendig, text_to_speech: undefined });
    expect(sprachZustand(nur_eingabe)).toBe("nur_eingabe");
    expect(nur_eingabe?.text_to_speech.reason).toBe("");
  });

  it("liest die Stimmenliste und verwirft Eintraege ohne Kennung", () => {
    const geparst = parseVoiceList({
      default_voice_id: "de-de-thorsten-medium",
      voices: [
        {
          id: "de-de-thorsten-medium",
          label: "Thorsten",
          language: "de_DE",
          language_label: "Deutsch",
          speaker: "thorsten",
          speaker_label: "Thorsten",
          quality: "medium",
          sample_rate: 22050,
          description: "Beschreibung",
          license: "CC0",
          source_page: "https://example.org",
          dataset_url: "https://example.org/daten",
          size_bytes: 100,
          files: [{ name: "a.onnx", size_bytes: 90, is_model: true, is_config: false }],
          status: { status: "installed", bytes_downloaded: 100, bytes_total: 100, progress_percent: 100, current_file: null, verified: true, message: "Geladen." },
        },
        { label: "ohne Kennung" },
      ],
    });
    expect(geparst?.voices).toHaveLength(1);
    expect(geparst?.voices[0].status.verified).toBe(true);
    expect(geparst?.voices[0].files[0].is_model).toBe(true);
    expect(geparst?.default_voice_id).toBe("de-de-thorsten-medium");
  });

  it("nennt eine geladene englische Stimme als Ersatz, den es nicht gibt", () => {
    const stimmen = parseVoiceList({
      voices: [
        { id: "en-us-amy-medium", language: "en_US", status: { status: "installed" } },
        { id: "de-de-thorsten-medium", language: "de_DE", status: { status: "not_downloaded" } },
      ],
    })?.voices ?? [];
    // Nur installierte Stimmen zaehlen. Eine deutsche Stimme im Katalog, die
    // nicht geladen ist, hilft niemandem - sie wuerde eine deutsche Ausgabe
    // versprechen, die es nicht gibt.
    expect(brauchtDeutscheStimme(stimmen)).toBe(false);
    stimmen[1].status.status = "installed";
    expect(brauchtDeutscheStimme(stimmen)).toBe(true);
  });

  it("spricht ueber Stand und Qualitaet in deutschen Worten", () => {
    expect(beschreibeStimmstatus("installed")).toBe("Geladen");
    expect(beschreibeStimmstatus("not_downloaded")).toBe("Nicht geladen");
    expect(beschreibeQualitaet("medium")).toBe("mittel");
    expect(beschreibeQualitaet("irgendwas")).toBe("irgendwas");
  });
});

describe("Die Beschriftung des Tastenkuerzels", () => {
  it("schreibt auf einem Mac &bdquo;Befehl&ldquo; statt &bdquo;Strg&ldquo;", () => {
    // Nur die Beschriftung. `feuertKuerzel` nimmt `ctrlKey` **und** `metaKey`,
    // damit die Funktion nicht davon abhaengt, was der Nutzer gerade benutzt -
    // und die Anzeige trotzdem stimmt.
    expect(istMac("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")).toBe(true);
    expect(istMac("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")).toBe(true);
    expect(istMac("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe(false);
    expect(istMac("Mozilla/5.0 (X11; Linux x86_64)")).toBe(false);
    expect(KUERZEL_BEZEICHNUNG).toContain("Umschalt");
    expect(KUERZEL_BEZEICHNUNG_MAC).toContain("Befehl");
    expect(KUERZEL_TASTE).toBe("l");
  });
});