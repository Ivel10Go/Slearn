"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchSpeechAudio, getSpeechCapabilities, transcribeAudio } from "./api";
import {
  bereiteVorlesetext,
  feuertKuerzel,
  mikrofonHinweis,
  mikrofonZustand,
  naechsterAufnahmeZustand,
  sprachZustand,
  waehleAufnahmeFormat,
  type AufnahmeZustand,
  type MikrofonZustand,
  type SpeechCapabilitiesPayload,
  type SprachZustand,
} from "./speech";
import { t } from "./i18n";

/**
 * Mikrofon, Aufnahme, Transkript und Wiedergabe - der ganze Browser-Teil.
 *
 * Was hier bewusst **nicht** getestet ist und warum: `getUserMedia`,
 * `MediaRecorder` und das Abspielen eines `Blob` brauchen alle einen echten
 * Browser. `vitest` laeuft in diesem Projekt mit `environment: "node"`; ein
 * Komponententest wuerde stillschweigend nicht ausgefuehrt. Alles, was sich
 * ohne Browser entscheiden laesst, steht deshalb in `./speech` und ist dort
 * getestet - hier bleiben nur der Aufruf und die Reihenfolge.
 *
 * Vier Entscheidungen, die man hier nicht wegdiskutieren kann:
 *
 * 1. **Berechtigung ist asynchron und darf verweigert werden.** Der Aufruf
 *    liegt in `try/catch`, und ein `NotAllowedError` ist ueber
 *    {@link mikrofonZustand} ein *gueltiger* Zustand mit deutschem Satz - kein
 *    Fehler. Es gibt hier keinen Pfad, der `setFehler` bei einer Ablehnung
 *    aufruft.
 * 2. **Stummschalten muss immer gehen.** `stopp` ist an drei Stellen erreichbar
 *    (Feld verlassen, `Escape`, `blur`), und `keepsAufnahme` wird ueber einen
 *    `useEffect`-Aufraeumer gesichert, der beim Unmount alle Tracks stilllegt.
 *    Eine Aufnahme, die beim Unmount weiterlaeuft, ist kein Bedienfehler,
 *    sondern ein offenes Mikrofon.
 * 3. **Die Wiedergabe startet nie von allein.** Es gibt keinen Autoplay-Pfad.
 *    Jede Wiedergabe kommt von einem Klick, damit die Autoplay-Regeln des
 *    Browsers gar nicht erst ins Spiel kommen; ein Blockieren ist damit kein
 *    Fehlerfall, sondern der Normalfall eines Browsers ohne vorherige
 *    Nutzergeste.
 * 4. **Die Sprachausgabe blockiert nie.** Beim Navigieren, beim Tippen und beim
 *    Unmount wird gestoppt - und die Aufnahme sowieso.
 */

/** Groesse des Chunks, mit dem der Blobby-Aufbau laeuft. */
const CHUNK_MS = 250;
/** Hoechstlaenge einer Aufnahme, ab der der Nutzer loslassen sollte. */
const WARNUNG_SEKUNDEN = 90;

/**
 * Der Zustand, den die Sprachkarte anzeigt.
 *
 * Zwei Zustandsmaschinen, weil es zwei Vorgaenge sind: die **Sprachkarte**
 * (``sprechZustand``) und die **Aufnahme** (``aufnahmeZustand``). Sie werden
 * nicht vermischt - ein Zustand kann vollstaendig sein, waehrend gerade eine
 * Aufnahme laeuft.
 */
export interface SprechState {
  /** Was gerade geht: nur Text, nur Eingabe, nur Ausgabe oder beides. */
  sprechZustand: SprachZustand;
  aufnahmeZustand: AufnahmeZustand;
  mikrofon: MikrofonZustand;
  sekunden: number;
  transkript: string;
  laedt: boolean;
  spielt: boolean;
  fehler: string | null;
  hinweis: string;
}

export interface SprechApi {
  faehigkeiten: SpeechCapabilitiesPayload | null;
  /** Der Grund, warum die Eingabe nicht geht - kommt vom Server, ist ein Satz. */
  grundEingabe: string;
  grundAusgabe: string;
  aufnehmen: () => void;
  beenden: () => void;
  abbrechen: () => void;
  uebernehmen: (text: string) => void;
  sprechen: (text: string) => void;
  schweigen: () => void;
  neuLaden: () => void;
  format: string | null;
  kannEingabe: boolean;
  kannAusgabe: boolean;
}

function fehlermeldung(ursache: unknown, ersatz: string) {
  if (ursache instanceof ApiError && ursache.message) {
    return ursache.message;
  }
  if (ursache instanceof Error && ursache.message) {
    return ursache.message;
  }
  return ersatz;
}

/**
 * Nimmt einen `MediaStream` auseinander und legt alle Spuren wieder zu.
 *
 * Das ist die Funktion, an der die Datenschutzfrage haengt: `stop()` am
 * Aufnahmegerät schliesst den Datenstrom, aber die **Tracks** bleiben offen,
 * solange nichts sie stoppt. Die Mikrofon-Anzeige im Betriebssystem bleibt dann
 * an, und das ist der Unterschied zwischen „die Aufnahme ist vorbei" und
 * „die Aufnahme ist vorbei, aber der Raum wird weiter abgehoert".
 */
function tracksStoppen(strom: MediaStream | null) {
  strom?.getTracks().forEach((spur) => {
    try {
      spur.stop();
    } catch {
      // Eine Spur, die schon steht, ist kein Fehler - sie zu stoppen ist dann
      // nur nicht mehr noetig.
    }
  });
}

export function useSpeech(): SprechApi & SprechState {
  const anfrageId = useRef(0);
  const aufnahmeGeraet = useRef<MediaRecorder | null>(null);
  const aufnahmeStrom = useRef<MediaStream | null>(null);
  const aufnahmeStuecke = useRef<Blob[]>([]);
  const aufnahmeFormat = useRef<string | null>(null);
  /*
   * Das Format steht auch im **State**, nicht nur im Ref. Die Anzeige laesst es
   * sich waehrend des Renderns zeigen, und ein Ref darf dort nicht angefasst
   * werden - `react-hooks/refs` ist eine Fehlerregel in eslint-config-next.
   */
  const [format, setFormat] = useState<string | null>(null);
  const stoppGeplant = useRef(false);
  const audioElement = useRef<HTMLAudioElement | null>(null);
  const blobAdresse = useRef<string | null>(null);
  const zaehler = useRef<number | null>(null);
  const aufnahmeStart = useRef(0);

  const [faehigkeiten, setFaehigkeiten] = useState<SpeechCapabilitiesPayload | null>(null);
  const [zustand, setZustand] = useState<AufnahmeZustand>("bereit");
  const [mikrofon, setMikrofon] = useState<MikrofonZustand>("unbekannt");
  const [sekunden, setSekunden] = useState(0);
  const [transkript, setTranskript] = useState("");
  const [laedt, setLaedt] = useState(false);
  const [spielt, setSpielt] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);

  const laden = useCallback(async () => {
    const aktuelle = ++anfrageId.current;
    try {
      const antwort = await getSpeechCapabilities();
      if (aktuelle !== anfrageId.current) {
        return;
      }
      setFaehigkeiten(antwort);
      setFehler(null);
    } catch {
      // Kein Fehlerdialog: der Textmodus ist der Zustand, in dem es weitergeht.
      // `faehigkeiten` bleibt `null`, und `sprachZustand(null)` ist
      // `nur_text` - die Sprachkarte zeigt dann genau das an.
      if (aktuelle === anfrageId.current) {
        setFaehigkeiten(null);
      }
    }
  }, []);

  useEffect(() => {
    // `react-hooks/set-state-in-effect` (Fehlerregel in eslint-config-next)
    // erlaubt keinen synchronen Zustandswechsel im Effektkoerper. Der erste
    // Abruf laeuft deshalb einen Tick spaeter - dasselbe Muster wie in
    // `use-now.ts`.
    const erster = window.setTimeout(() => void laden(), 0);
    return () => {
      window.clearTimeout(erster);
      anfrageId.current += 1;
    };
  }, [laden]);

  /** Beendet die Aufnahme und schickt sie zum Transkribierer. */
  const senden = useCallback(
    async (stuecke: Blob[], format: string | null) => {
      const blob = new Blob(stuecke, { type: format ?? "audio/webm" });
      if (blob.size === 0) {
        setZustand("bereit");
        return;
      }
      setLaedt(true);
      setFehler(null);
      try {
        const ergebnis = await transcribeAudio(blob, format ?? "audio/webm");
        setTranskript(ergebnis.text);
        if (!ergebnis.text.trim()) {
          // Kein Satz ist kein Fehler, aber es ist auch kein Erfolg. Der Satz
          // sagt beides und ist deshalb in der Sprachkarte sichtbar.
          setFehler(t("Aus der Aufnahme wurde kein Text. Leiser sprechen oder das Mikrofon naeher an den Mund halten."));
        }
      } catch (ursache) {
        setFehler(fehlermeldung(ursache, t("Die Aufnahme liess sich nicht auswerten.")));
      } finally {
        setLaedt(false);
        setZustand("bereit");
      }
    },
    [],
  );

  /** Haelt die Aufnahme an und raeumt auf - ohne zu senden. */
  const verwerfen = useCallback(() => {
    const geraet = aufnahmeGeraet.current;
    aufnahmeGeraet.current = null;
    aufnahmeStuecke.current = [];
    tracksStoppen(aufnahmeStrom.current);
    aufnahmeStrom.current = null;
    if (geraet && geraet.state !== "inactive") {
      try {
        // `stoppGeplant` bleibt bewusst **false**: `onstop` erkennt daran, dass
        // nicht gewollt beendet wurde, und raeumt nur auf.
        geraet.stop();
      } catch {
        // Ein Geraet, das schon steht, laesst sich nicht noch einmal stoppen.
      }
    }
    if (zaehler.current !== null) {
      window.clearInterval(zaehler.current);
      zaehler.current = null;
    }
    aufnahmeStart.current = 0;
    setSekunden(0);
    setFormat(null);
    setZustand("bereit");
  }, []);

  const beenden = useCallback(() => {
    if (zustand !== "nimmt_auf") {
      return;
    }
    /*
     * Nur der Aufruf, **nicht** das Abschliessen: `MediaRecorder.stop()` stellt
     * erst den letzten `dataavailable` in die_queue und feuert danach `stop`.
     * Beides ist asynchron. Wer hier schon die Teilstuecke wegraeumt oder selbst
     * sendet, verliert genau das letzte Stueck - und schickt die davor
     * gesammelten zweimal. Das Abschliessen gehoert deshalb **ausschliesslich**
     * an `onstop`; diese Funktion markiert nur, dass die Aufnahme gewollt
     * beendet wurde, und raeumt die Hardware auf.
     *
     * `stoppGeplant` wird hier **nicht** zurueckgesetzt: solange die Aufnahme als
     * gewollt beendet markiert ist, nimmt `onstop` das auch so und sendet. Wuerde
     * derselbe Klick als „verwerfen" durchgehen, waere die ganze Aufnahme weg.
     */
    stoppGeplant.current = true;
    tracksStoppen(aufnahmeStrom.current);
    aufnahmeStrom.current = null;
    if (zaehler.current !== null) {
      window.clearInterval(zaehler.current);
      zaehler.current = null;
    }
    const geraet = aufnahmeGeraet.current;
    if (geraet && geraet.state !== "inactive") {
      try {
        geraet.stop();
      } catch {
        // Ein Geraet, das schon steht, laesst sich nicht noch einmal stoppen -
        // dann feuert `onstop` aber auch nicht mehr, und es bleibt bei
        // „wird ausgewertet". Genau deshalb wird der Zustand **vorher** gesetzt.
      }
    }
    setSekunden(0);
    setZustand("wird_gesendet");
  }, [zustand]);

  const aufnehmen = useCallback(async () => {
    if (zustand !== "bereit" || laedt) {
      return;
    }
    const format = waehleAufnahmeFormat();
    if (!format || typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      /*
       * Kein `getUserMedia` im Browser, kein `MediaRecorder`, oder kein Format
       * bekannt: das ist kein Fehler, sondern der Grund fuer den Textmodus.
       *
       * Der Aufnahmezustand bleibt dabei **unveraendert** - es laeuft ja nichts.
       * Ein `nimmt_auf` ohne Aufnahme wuerde „Nimmt auf" anzeigen, ohne dass
       * irgendetwas passiert, und der Zustand kaeme nie wieder heraus.
       */
      setMikrofon("fehlt");
      return;
    }
    setFehler(null);
    let strom: MediaStream;
    try {
      strom = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (ursache) {
      // Der Kern dieses Hooks: eine Ablehnung ist eine gueltige Antwort und
      // kein Fehler. `verweigert` bekommt einen Hinweis und **keinen** Fehler,
      // und der Textmodus laeuft weiter.
      setMikrofon(mikrofonZustand(ursache));
      return;
    }
    setMikrofon("erlaubt");
    let geraet: MediaRecorder;
    try {
      geraet = new MediaRecorder(strom, { mimeType: format });
    } catch {
      tracksStoppen(strom);
      setMikrofon("blockiert");
      return;
    }
    aufnahmeStrom.current = strom;
    aufnahmeGeraet.current = geraet;
    aufnahmeFormat.current = format;
    setFormat(format);
    aufnahmeStuecke.current = [];
    stoppGeplant.current = false;
    aufnahmeStart.current = Date.now();
    geraet.ondataavailable = (ereignis) => {
      if (ereignis.data && ereignis.data.size > 0) {
        aufnahmeStuecke.current.push(ereignis.data);
      }
    };
    /*
      * `onstop` ist der einzige Ort, an dem aus Teilstuecken ein Ganzes wird -
      * und der einzige, an dem der Aufnahmezustand zu Ende gehen darf. Hier
      * steht das **letzte** Stueck bereits im Ref, weil `dataavailable` vorher
      * feuert; wer in `beenden` sendet, schickt es nicht mit.
      */
    geraet.onstop = () => {
      if (!stoppGeplant.current) {
        verwerfen();
        return;
      }
      stoppGeplant.current = false;
      const stuecke = aufnahmeStuecke.current;
      const format = aufnahmeFormat.current;
      tracksStoppen(aufnahmeStrom.current);
      aufnahmeStrom.current = null;
      aufnahmeGeraet.current = null;
      aufnahmeStuecke.current = [];
      if (zaehler.current !== null) {
        window.clearInterval(zaehler.current);
        zaehler.current = null;
      }
      setFormat(null);
      setZustand("wird_gesendet");
      void senden(stuecke, format);
    };
    try {
      geraet.start(CHUNK_MS);
    } catch {
      tracksStoppen(strom);
      aufnahmeGeraet.current = null;
      setMikrofon("blockiert");
      return;
    }
    setSekunden(0);
    setZustand(naechsterAufnahmeZustand(zustand, "start"));
    zaehler.current = window.setInterval(() => {
      setSekunden(Math.max(0, Math.round((Date.now() - aufnahmeStart.current) / 1000)));
    }, 1000);
  }, [laedt, senden, verwerfen, zustand]);

  /** Haelt jede laufende Wiedergabe an. */
  const schweigen = useCallback(() => {
    const element = audioElement.current;
    if (element) {
      element.pause();
      element.currentTime = 0;
    }
    if (blobAdresse.current) {
      URL.revokeObjectURL(blobAdresse.current);
      blobAdresse.current = null;
    }
    audioElement.current = null;
    setSpielt(false);
  }, []);

  const sprechen = useCallback(
    async (text: string) => {
      const vorlesetext = bereiteVorlesetext(text);
      if (!vorlesetext) {
        return;
      }
      if (typeof window === "undefined" || typeof Audio === "undefined") {
        return;
      }
      setFehler(null);
      let blob: Blob;
      try {
        blob = await fetchSpeechAudio(vorlesetext);
      } catch (ursache) {
        setFehler(fehlermeldung(ursache, t("Der Text liess sich nicht vorlesen.")));
        return;
      }
      if (blob.size === 0) {
        return;
      }
      schweigen();
      const adresse = URL.createObjectURL(blob);
      blobAdresse.current = adresse;
      const element = new Audio(adresse);
      audioElement.current = element;
      element.onended = () => schweigen();
      element.onerror = () => schweigen();
      try {
        await element.play();
        setSpielt(true);
      } catch {
        // **Normalfall, kein Fehler.** Der Browser verweigert die Wiedergabe
        // ohne vorherige Nutzergeste - oder ein Autoplay-Riegel steht dazwischen.
        // Es gibt keinen Autoplay-Pfad im Lernraum (jede Wiedergabe kommt von
        // einem Klick), deshalb ist das nur noch die Variante, in der ein
        // Browser nachtraeglich eine Regel durchsetzt. Der Knopf bleibt da und
        // ein zweiter Klick funktioniert.
        schweigen();
        setFehler(t("Der Browser hat die Wiedergabe nicht zugelassen. Einmal antippen und noch einmal vorlesen."));
      }
    },
    [schweigen],
  );

  const uebernehmen = useCallback((text: string) => {
    setTranskript(text.trim());
  }, []);

  // --- Tastenkuerzel -------------------------------------------------------

  useEffect(() => {
    const aufTaste = (ereignis: KeyboardEvent) => {
      if (!feuertKuerzel(ereignis)) {
        return;
      }
      // `preventDefault` auch dann, wenn gerade nicht aufgenommen wird: ohne
      // das wuerde die Seite beim Kuerzel scrollen, und der Nutzer haelt die
      // Taste fuer einen Knopf, nicht fuer einen Sprung.
      ereignis.preventDefault();
      if (ereignis.repeat) {
        return;
      }
      if (zustand === "nimmt_auf") {
        beenden();
        return;
      }
      if (ereignis.type === "keydown") {
        void aufnehmen();
      }
    };
    const aufLoslassen = (ereignis: KeyboardEvent) => {
      if (!feuertKuerzel(ereignis)) {
        return;
      }
      if (zustand === "nimmt_auf") {
        ereignis.preventDefault();
        beenden();
      }
    };
    window.addEventListener("keydown", aufTaste);
    window.addEventListener("keyup", aufLoslassen);
    return () => {
      window.removeEventListener("keydown", aufTaste);
      window.removeEventListener("keyup", aufLoslassen);
    };
  }, [aufnehmen, beenden, zustand]);

  // **Stummschalten ist Pflicht, nicht Komfort.** Drei Ausloeser, alle drei
  // noetig: das Verlassen des Fensters, ein sichtbarer Dialog (Alt-Tab) und das
  // Unmount der Seite. Ohne das bliebe das Mikrofon offen, wenn der Tab
  // gewechselt wird - und das ist die Art Fehler, die man erst bemerkt, wenn
  // die Aufnahme schon fertig ist.
  useEffect(() => {
    const aufVerlassen = () => {
      if (zustand === "nimmt_auf") {
        beenden();
      }
    };
    const aufUnsichtbar = () => {
      if (document.hidden && zustand === "nimmt_auf") {
        verwerfen();
      }
    };
    window.addEventListener("blur", aufVerlassen);
    window.addEventListener("pagehide", aufVerlassen);
    document.addEventListener("visibilitychange", aufUnsichtbar);
    return () => {
      window.removeEventListener("blur", aufVerlassen);
      window.removeEventListener("pagehide", aufVerlassen);
      document.removeEventListener("visibilitychange", aufUnsichtbar);
    };
  }, [beenden, verwerfen, zustand]);

  // Die Wiedergabe darf die Oberflaeche nie blockieren: beim Navigieren und
  // beim Unmount geht sie aus.
  useEffect(() => {
    const aufNavigation = () => schweigen();
    window.addEventListener("hashchange", aufNavigation);
    window.addEventListener("pagehide", aufNavigation);
    return () => {
      window.removeEventListener("hashchange", aufNavigation);
      window.removeEventListener("pagehide", aufNavigation);
    };
  }, [schweigen]);

  // Der Aufraeumer beim Unmount. Ohne ihn bliebe eine laufende Aufnahme ohne
  // Besitzer zurueck, und die Tracks liefe weiter.
  useEffect(
    () => () => {
      tracksStoppen(aufnahmeStrom.current);
      aufnahmeStrom.current = null;
      const geraet = aufnahmeGeraet.current;
      aufnahmeGeraet.current = null;
      if (geraet && geraet.state !== "inactive") {
        try {
          geraet.stop();
        } catch {
          // Wie oben.
        }
      }
      if (zaehler.current !== null) {
        window.clearInterval(zaehler.current);
        zaehler.current = null;
      }
      if (blobAdresse.current) {
        URL.revokeObjectURL(blobAdresse.current);
        blobAdresse.current = null;
      }
      audioElement.current?.pause();
      audioElement.current = null;
    },
    [],
  );

  const zustandAnzeige = sprachZustand(faehigkeiten);
  const mikrofonVerfuegbar = typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia) && typeof MediaRecorder !== "undefined";

  return {
    sprechZustand: zustandAnzeige,
    aufnahmeZustand: zustand,
    mikrofon,
    sekunden,
    transkript,
    laedt,
    spielt,
    fehler,
    hinweis: mikrofonVerfuegbar ? mikrofonHinweis(mikrofon) : mikrofonHinweis("fehlt"),
    faehigkeiten,
    grundEingabe: faehigkeiten?.speech_to_text.reason ?? "",
    grundAusgabe: faehigkeiten?.text_to_speech.reason ?? "",
    aufnehmen: () => void aufnehmen(),
    beenden,
    abbrechen: verwerfen,
    uebernehmen,
    sprechen: (text: string) => void sprechen(text),
    schweigen,
    neuLaden: () => void laden(),
    format,
    kannEingabe: Boolean(faehigkeiten?.speech_to_text.available) && Boolean(waehleAufnahmeFormat()),
    kannAusgabe: Boolean(faehigkeiten?.text_to_speech.available),
  };
}

export { WARNUNG_SEKUNDEN };