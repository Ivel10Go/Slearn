import { t } from "./i18n";
/*
 * Bausteine fuer den Gespraechsmodus des Assistenten - ohne DOM, damit vitest
 * sie pruefen kann.
 *
 * - `encodeWav16k` macht aus dem Mikrofonsignal ein 16-kHz-Mono-WAV mit 16 Bit.
 *   Genau dieses Format reicht das Backend ohne ffmpeg an whisper.cpp weiter
 *   (`_is_plain_wav`). Der Browser nimmt sonst WebM/MP4 auf, und dafuer braeuchte
 *   der Rechner ein installiertes ffmpeg.
 * - `createVoiceDetector` entscheidet anhand des Pegels, wann jemand zu sprechen
 *   beginnt und wann der Satz vorbei ist. Kein Modell, sondern ein Schwellwert
 *   ueber dem gemessenen Grundrauschen - das reicht fuer ein Gespraech am
 *   Schreibtisch und laeuft ohne Zusatzdownload.
 */

export const TARGET_SAMPLE_RATE = 16000;

/** Mittlerer Pegel (RMS) eines Blocks, 0 bis 1. */
export function rms(frame: Float32Array): number {
  if (frame.length === 0) {
    return 0;
  }
  let sum = 0;
  for (let index = 0; index < frame.length; index += 1) {
    sum += frame[index] * frame[index];
  }
  return Math.sqrt(sum / frame.length);
}

/** Fuegt Bloecke zu einem Signal zusammen. */
export function concatFrames(frames: Float32Array[]): Float32Array {
  const total = frames.reduce((summe, frame) => summe + frame.length, 0);
  const result = new Float32Array(total);
  let offset = 0;
  for (const frame of frames) {
    result.set(frame, offset);
    offset += frame.length;
  }
  return result;
}

/** Rechnet auf 16 kHz herunter, indem je Zielwert die Quellwerte gemittelt werden (kein Aliasing-Rauschen). */
export function downsample(samples: Float32Array, inputRate: number, targetRate = TARGET_SAMPLE_RATE): Float32Array {
  if (inputRate === targetRate) {
    return samples;
  }
  if (inputRate < targetRate) {
    throw new Error(t("Die Abtastrate des Mikrofons ist kleiner als 16 kHz."));
  }
  const ratio = inputRate / targetRate;
  const length = Math.floor(samples.length / ratio);
  const result = new Float32Array(length);
  for (let index = 0; index < length; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.min(samples.length, Math.floor((index + 1) * ratio));
    let sum = 0;
    for (let source = start; source < end; source += 1) {
      sum += samples[source];
    }
    result[index] = end > start ? sum / (end - start) : 0;
  }
  return result;
}

/** 16-kHz-Mono-WAV mit 16 Bit, wie es whisper.cpp ohne Umwandlung liest. */
export function encodeWav16k(samples: Float32Array, inputRate: number): ArrayBuffer {
  const pcm = downsample(samples, inputRate);
  const buffer = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, text: string) => {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(offset + index, text.charCodeAt(index));
    }
  };
  write(0, "RIFF");
  view.setUint32(4, 36 + pcm.length * 2, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, TARGET_SAMPLE_RATE, true);
  view.setUint32(28, TARGET_SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, pcm.length * 2, true);
  for (let index = 0; index < pcm.length; index += 1) {
    const value = Math.max(-1, Math.min(1, pcm[index]));
    view.setInt16(44 + index * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true);
  }
  return buffer;
}

export type VoiceEvent = "still" | "start" | "spricht" | "ende" | "zu_kurz";

export interface VoiceDetectorOptions {
  /** Unterhalb dieses Pegels ist es nie Sprache, egal wie leise der Raum ist. */
  minLevel?: number;
  /** Faktor ueber dem Grundrauschen, ab dem Sprache beginnt. */
  factor?: number;
  /** So lange muss der Pegel oben bleiben, bevor es als Sprechen zaehlt. */
  startMs?: number;
  /** So lange Stille beendet den Satz. */
  silenceMs?: number;
  /** Kuerzere Aeusserungen (Husten, Klicken) werden verworfen. */
  minSpeechMs?: number;
  /** Danach wird der Satz in jedem Fall beendet. */
  maxSpeechMs?: number;
}

export interface VoiceDetector {
  /** Ein Pegelwert zum Zeitpunkt `nowMs`. Liefert, was gerade passiert. */
  push(level: number, nowMs: number): VoiceEvent;
  reset(): void;
  readonly noiseFloor: number;
}

export function createVoiceDetector(options: VoiceDetectorOptions = {}): VoiceDetector {
  const minLevel = options.minLevel ?? 0.012;
  const factor = options.factor ?? 3;
  const startMs = options.startMs ?? 120;
  const silenceMs = options.silenceMs ?? 1100;
  const minSpeechMs = options.minSpeechMs ?? 350;
  const maxSpeechMs = options.maxSpeechMs ?? 30000;

  let noiseFloor = minLevel / factor;
  let speaking = false;
  let aboveSince: number | null = null;
  let speechStart = 0;
  let lastLoud = 0;

  return {
    get noiseFloor() {
      return noiseFloor;
    },
    reset() {
      speaking = false;
      aboveSince = null;
    },
    push(level: number, nowMs: number): VoiceEvent {
      const threshold = Math.max(minLevel, noiseFloor * factor);
      const loud = level > threshold;
      if (!speaking) {
        // Das Grundrauschen folgt nur in der Stille - sonst hebt die eigene
        // Stimme die Schwelle an.
        if (!loud) {
          noiseFloor = noiseFloor * 0.95 + level * 0.05;
          aboveSince = null;
          return "still";
        }
        aboveSince ??= nowMs;
        if (nowMs - aboveSince >= startMs) {
          speaking = true;
          speechStart = aboveSince;
          lastLoud = nowMs;
          return "start";
        }
        return "still";
      }
      if (loud) {
        lastLoud = nowMs;
      }
      const zuLang = nowMs - speechStart >= maxSpeechMs;
      if (nowMs - lastLoud >= silenceMs || zuLang) {
        speaking = false;
        aboveSince = null;
        return lastLoud - speechStart >= minSpeechMs ? "ende" : "zu_kurz";
      }
      return "spricht";
    },
  };
}
