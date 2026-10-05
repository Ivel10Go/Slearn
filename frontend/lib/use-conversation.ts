"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchSpeechAudio, transcribeAudio } from "./api";
import { bereiteVorlesetext } from "./speech";
import { concatFrames, createVoiceDetector, encodeWav16k, rms, type VoiceDetector } from "./voice";

/*
 * Der Gespraechsmodus: einmal einschalten, dann reden wie mit einem Menschen.
 *
 * Ablauf je Satz: zuhoeren -> Sprechbeginn am Pegel erkennen -> aufnehmen ->
 * gut eine Sekunde Stille beendet den Satz -> als 16-kHz-WAV an die
 * Spracherkennung (kein ffmpeg noetig) -> Text an den Assistenten -> Antwort
 * vorlesen -> wieder zuhoeren.
 *
 * Waehrend erkannt, nachgedacht und vorgelesen wird, ist das Mikrofon taub
 * geschaltet: sonst hoerte der Assistent seine eigene Stimme aus dem
 * Lautsprecher und antwortete sich selbst. "Unterbrechen" stoppt das Vorlesen
 * und hoert sofort wieder zu.
 *
 * `ScriptProcessorNode` statt `AudioWorklet`: ein Worklet braucht ein eigenes
 * Skript-Modul, und die CSP (`script-src 'self'`) erlaubt keine Blob-Module.
 * Der Knoten ist veraltet, laeuft aber in Chromium, Electron, Firefox und Safari.
 */

export type GespraechsPhase = "aus" | "startet" | "hoert" | "nimmt_auf" | "erkennt" | "denkt" | "antwortet";

export const PHASEN_TEXT: Record<GespraechsPhase, string> = {
  aus: "Gespräch aus",
  startet: "Mikrofon wird eingeschaltet …",
  hoert: "Ich höre zu – sprich einfach.",
  nimmt_auf: "Du sprichst …",
  erkennt: "Verstehe …",
  denkt: "Denke nach …",
  antwortet: "Antworte …",
};

const BLOCK_SIZE = 2048;
/** So viele Bloecke vor dem erkannten Sprechbeginn werden mitgenommen (~0,3 s), sonst fehlt die erste Silbe. */
const PREROLL_BLOCKS = 8;

export interface ConversationOptions {
  /** Bekommt den erkannten Text und liefert die Antwort, die vorgelesen wird - oder `null`. */
  onUtterance: (text: string) => Promise<string | null>;
}

export interface ConversationState {
  phase: GespraechsPhase;
  aktiv: boolean;
  /** Mikrofonpegel 0 bis 1, fuer die Anzeige. */
  pegel: number;
  /** Was zuletzt verstanden wurde. */
  erkannt: string;
  hinweis: string | null;
  starten: () => void;
  beenden: () => void;
  unterbrechen: () => void;
}

function meldung(ursache: unknown, ersatz: string) {
  if (ursache instanceof ApiError && ursache.message) {
    return ursache.message;
  }
  if (ursache instanceof DOMException && (ursache.name === "NotAllowedError" || ursache.name === "SecurityError")) {
    return "Der Mikrofonzugriff wurde nicht erlaubt.";
  }
  if (ursache instanceof DOMException && ursache.name === "NotFoundError") {
    return "Es wurde kein Mikrofon gefunden.";
  }
  return ersatz;
}

export function useConversation({ onUtterance }: ConversationOptions): ConversationState {
  const [phase, setPhaseState] = useState<GespraechsPhase>("aus");
  const [pegel, setPegel] = useState(0);
  const [erkannt, setErkannt] = useState("");
  const [hinweis, setHinweis] = useState<string | null>(null);

  const phaseRef = useRef<GespraechsPhase>("aus");
  const lauf = useRef(0);
  const kontext = useRef<AudioContext | null>(null);
  const strom = useRef<MediaStream | null>(null);
  const knoten = useRef<{ quelle: MediaStreamAudioSourceNode; prozessor: ScriptProcessorNode; stumm: GainNode } | null>(null);
  const erkennung = useRef<VoiceDetector | null>(null);
  const vorlauf = useRef<Float32Array[]>([]);
  const aufnahme = useRef<Float32Array[]>([]);
  const audio = useRef<HTMLAudioElement | null>(null);
  const audioAdresse = useRef<string | null>(null);
  const letzterPegel = useRef(0);
  const onUtteranceRef = useRef(onUtterance);

  useEffect(() => {
    onUtteranceRef.current = onUtterance;
  }, [onUtterance]);

  const setPhase = useCallback((neu: GespraechsPhase) => {
    phaseRef.current = neu;
    setPhaseState(neu);
  }, []);

  const wiedergabeStoppen = useCallback(() => {
    if (audio.current) {
      audio.current.onended = null;
      audio.current.onerror = null;
      audio.current.pause();
      audio.current = null;
    }
    if (audioAdresse.current) {
      URL.revokeObjectURL(audioAdresse.current);
      audioAdresse.current = null;
    }
  }, []);

  const zuhoeren = useCallback(() => {
    erkennung.current?.reset();
    vorlauf.current = [];
    aufnahme.current = [];
    setPhase("hoert");
  }, [setPhase]);

  const vorlesen = useCallback(
    async (text: string, meinLauf: number) => {
      const vorlesetext = bereiteVorlesetext(text);
      if (!vorlesetext) {
        zuhoeren();
        return;
      }
      setPhase("antwortet");
      try {
        const ton = await fetchSpeechAudio(vorlesetext);
        if (lauf.current !== meinLauf || phaseRef.current !== "antwortet") {
          return;
        }
        const adresse = URL.createObjectURL(ton);
        audioAdresse.current = adresse;
        const element = new Audio(adresse);
        audio.current = element;
        const fertig = () => {
          wiedergabeStoppen();
          if (lauf.current === meinLauf && phaseRef.current === "antwortet") {
            zuhoeren();
          }
        };
        element.onended = fertig;
        element.onerror = fertig;
        await element.play();
      } catch (ursache) {
        if (lauf.current === meinLauf) {
          setHinweis(meldung(ursache, "Die Antwort konnte nicht vorgelesen werden."));
          zuhoeren();
        }
      }
    },
    [setPhase, wiedergabeStoppen, zuhoeren],
  );

  const verarbeiten = useCallback(
    async (bloecke: Float32Array[], sampleRate: number) => {
      const meinLauf = lauf.current;
      setPhase("erkennt");
      try {
        const wav = new Blob([encodeWav16k(concatFrames(bloecke), sampleRate)], { type: "audio/wav" });
        const ergebnis = await transcribeAudio(wav, "audio/wav");
        if (lauf.current !== meinLauf) {
          return;
        }
        const text = ergebnis.text.trim();
        // Whisper liefert bei Stille gern Platzhalter wie "[Musik]" oder "(Applaus)".
        if (!text || /^[[(].*[\])]$/.test(text) || text.replace(/[^\p{L}]/gu, "").length < 2) {
          setHinweis("Das habe ich nicht verstanden – sag es bitte noch einmal.");
          zuhoeren();
          return;
        }
        setHinweis(null);
        setErkannt(text);
        setPhase("denkt");
        const antwort = await onUtteranceRef.current(text);
        if (lauf.current !== meinLauf) {
          return;
        }
        if (antwort) {
          await vorlesen(antwort, meinLauf);
        } else {
          zuhoeren();
        }
      } catch (ursache) {
        if (lauf.current === meinLauf) {
          setHinweis(meldung(ursache, "Die Aufnahme konnte nicht verarbeitet werden."));
          zuhoeren();
        }
      }
    },
    [setPhase, vorlesen, zuhoeren],
  );

  const aufraeumen = useCallback(() => {
    lauf.current += 1;
    wiedergabeStoppen();
    if (knoten.current) {
      knoten.current.prozessor.onaudioprocess = null;
      knoten.current.quelle.disconnect();
      knoten.current.prozessor.disconnect();
      knoten.current.stumm.disconnect();
      knoten.current = null;
    }
    strom.current?.getTracks().forEach((spur) => spur.stop());
    strom.current = null;
    void kontext.current?.close().catch(() => undefined);
    kontext.current = null;
    vorlauf.current = [];
    aufnahme.current = [];
  }, [wiedergabeStoppen]);

  const beenden = useCallback(() => {
    aufraeumen();
    setPegel(0);
    setPhase("aus");
  }, [aufraeumen, setPhase]);

  const starten = useCallback(async () => {
    if (phaseRef.current !== "aus") {
      return;
    }
    aufraeumen();
    const meinLauf = lauf.current;
    setHinweis(null);
    setErkannt("");
    setPhase("startet");
    try {
      const mikrofon = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (lauf.current !== meinLauf) {
        mikrofon.getTracks().forEach((spur) => spur.stop());
        return;
      }
      const ctx = new AudioContext();
      await ctx.resume();
      const quelle = ctx.createMediaStreamSource(mikrofon);
      const prozessor = ctx.createScriptProcessor(BLOCK_SIZE, 1, 1);
      // Der Prozessor laeuft nur, wenn er am Ausgang haengt - stumm, sonst
      // hoerte man sich selbst.
      const stumm = ctx.createGain();
      stumm.gain.value = 0;
      quelle.connect(prozessor);
      prozessor.connect(stumm);
      stumm.connect(ctx.destination);
      strom.current = mikrofon;
      kontext.current = ctx;
      knoten.current = { quelle, prozessor, stumm };
      erkennung.current = createVoiceDetector();

      prozessor.onaudioprocess = (ereignis) => {
        const block = new Float32Array(ereignis.inputBuffer.getChannelData(0));
        const level = rms(block);
        const jetzt = performance.now();
        if (jetzt - letzterPegel.current > 100) {
          letzterPegel.current = jetzt;
          setPegel(Math.min(1, level * 8));
        }
        const aktuell = phaseRef.current;
        if (aktuell !== "hoert" && aktuell !== "nimmt_auf") {
          return;
        }
        const ergebnis = erkennung.current?.push(level, jetzt);
        if (aktuell === "hoert") {
          vorlauf.current.push(block);
          if (vorlauf.current.length > PREROLL_BLOCKS) {
            vorlauf.current.shift();
          }
          if (ergebnis === "start") {
            aufnahme.current = [...vorlauf.current];
            vorlauf.current = [];
            setPhase("nimmt_auf");
          }
          return;
        }
        aufnahme.current.push(block);
        if (ergebnis === "ende") {
          const bloecke = aufnahme.current;
          aufnahme.current = [];
          void verarbeiten(bloecke, ctx.sampleRate);
        } else if (ergebnis === "zu_kurz") {
          zuhoeren();
        }
      };
      zuhoeren();
    } catch (ursache) {
      aufraeumen();
      setPhase("aus");
      setHinweis(meldung(ursache, "Das Mikrofon konnte nicht eingeschaltet werden."));
    }
  }, [aufraeumen, setPhase, verarbeiten, zuhoeren]);

  const unterbrechen = useCallback(() => {
    if (phaseRef.current === "antwortet") {
      wiedergabeStoppen();
      zuhoeren();
    }
  }, [wiedergabeStoppen, zuhoeren]);

  useEffect(() => () => aufraeumen(), [aufraeumen]);

  return {
    phase,
    aktiv: phase !== "aus",
    pegel,
    erkannt,
    hinweis,
    starten: () => void starten(),
    beenden,
    unterbrechen,
  };
}
