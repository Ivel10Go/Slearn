import { describe, expect, it } from "vitest";
import { concatFrames, createVoiceDetector, downsample, encodeWav16k, rms } from "./voice";

describe("encodeWav16k", () => {
  it("schreibt ein 16-kHz-Mono-WAV mit 16 Bit", () => {
    const eineSekunde48k = new Float32Array(48000).fill(0.5);
    const view = new DataView(encodeWav16k(eineSekunde48k, 48000));
    const text = (offset: number) => String.fromCharCode(...[0, 1, 2, 3].map((i) => view.getUint8(offset + i)));
    expect(text(0)).toBe("RIFF");
    expect(text(8)).toBe("WAVE");
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getUint16(34, true)).toBe(16);
    expect(view.getUint32(40, true)).toBe(16000 * 2);
    expect(view.byteLength).toBe(44 + 32000);
    expect(view.getInt16(44, true)).toBe(Math.trunc(0.5 * 0x7fff));
  });

  it("klemmt Werte ausserhalb von -1 bis 1", () => {
    const view = new DataView(encodeWav16k(new Float32Array([2, -2]), 16000));
    expect(view.getInt16(44, true)).toBe(0x7fff);
    expect(view.getInt16(46, true)).toBe(-0x8000);
  });
});

describe("Signalhelfer", () => {
  it("mittelt beim Heruntertakten", () => {
    const ergebnis = Array.from(downsample(new Float32Array([0, 1, 0, 1, 0, 1]), 48000));
    expect(ergebnis).toHaveLength(2);
    expect(ergebnis[0]).toBeCloseTo(1 / 3);
    expect(ergebnis[1]).toBeCloseTo(2 / 3);
  });

  it("berechnet Pegel und fuegt Bloecke zusammen", () => {
    expect(rms(new Float32Array([0.5, -0.5]))).toBeCloseTo(0.5);
    expect(rms(new Float32Array())).toBe(0);
    expect(Array.from(concatFrames([new Float32Array([1]), new Float32Array([2, 3])]))).toEqual([1, 2, 3]);
  });
});

describe("createVoiceDetector", () => {
  function lauf(pegel: (zeit: number) => number, bisMs: number) {
    const erkennung = createVoiceDetector();
    const ereignisse: { zeit: number; ereignis: string }[] = [];
    for (let zeit = 0; zeit <= bisMs; zeit += 50) {
      const ereignis = erkennung.push(pegel(zeit), zeit);
      if (ereignis === "start" || ereignis === "ende" || ereignis === "zu_kurz") {
        ereignisse.push({ zeit, ereignis });
      }
    }
    return ereignisse;
  }

  it("erkennt Beginn und Ende eines Satzes nach gut einer Sekunde Stille", () => {
    const ereignisse = lauf((zeit) => (zeit >= 1000 && zeit < 2500 ? 0.2 : 0.002), 5000);
    expect(ereignisse.map((e) => e.ereignis)).toEqual(["start", "ende"]);
    expect(ereignisse[0].zeit).toBeGreaterThanOrEqual(1100);
    expect(ereignisse[1].zeit).toBeGreaterThanOrEqual(2500 + 1000);
  });

  it("verwirft ein kurzes Klicken", () => {
    const ereignisse = lauf((zeit) => (zeit >= 1000 && zeit < 1200 ? 0.3 : 0.002), 4000);
    expect(ereignisse.map((e) => e.ereignis)).toEqual(["start", "zu_kurz"]);
  });

  it("bleibt in einem gleichmaessig lauten Raum still", () => {
    expect(lauf(() => 0.005, 4000)).toEqual([]);
  });

  it("kurze Pausen beim Sprechen beenden den Satz nicht", () => {
    const sprechen = (zeit: number) => (zeit >= 500 && zeit < 1500) || (zeit >= 2000 && zeit < 3000);
    const ereignisse = lauf((zeit) => (sprechen(zeit) ? 0.2 : 0.002), 6000);
    expect(ereignisse.map((e) => e.ereignis)).toEqual(["start", "ende"]);
  });
});
