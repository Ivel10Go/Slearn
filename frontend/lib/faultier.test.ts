import { describe, expect, it } from "vitest";
import { faultierZustand, type FaultierSignale } from "./faultier";

const ruhig: FaultierSignale = { phase: "aus", sendet: false, spielt: false, nimmtAuf: false, freut: false };

describe("faultierZustand", () => {
  it("ruht, wenn nichts passiert", () => {
    expect(faultierZustand(ruhig)).toBe("ruhe");
  });

  it("schläft ohne verbundenes Modell", () => {
    expect(faultierZustand({ ...ruhig, ohneModell: true })).toBe("schlaeft");
  });

  it("übersetzt die Phasen des Gesprächsmodus", () => {
    expect(faultierZustand({ ...ruhig, phase: "hoert" })).toBe("zuhoeren");
    expect(faultierZustand({ ...ruhig, phase: "nimmt_auf" })).toBe("zuhoeren");
    expect(faultierZustand({ ...ruhig, phase: "erkennt" })).toBe("denken");
    expect(faultierZustand({ ...ruhig, phase: "denkt" })).toBe("denken");
    expect(faultierZustand({ ...ruhig, phase: "antwortet" })).toBe("sprechen");
  });

  it("gibt dem Gesprächsmodus Vorrang vor der laufenden Anfrage", () => {
    expect(faultierZustand({ ...ruhig, phase: "antwortet", sendet: true })).toBe("sprechen");
  });

  it("hört beim Halten zum Sprechen zu, denkt beim Senden und spricht beim Vorlesen", () => {
    expect(faultierZustand({ ...ruhig, nimmtAuf: true, sendet: true })).toBe("zuhoeren");
    expect(faultierZustand({ ...ruhig, sendet: true, spielt: true })).toBe("denken");
    expect(faultierZustand({ ...ruhig, spielt: true, freut: true })).toBe("sprechen");
  });

  it("freut sich nach einem Eintrag, auch ohne Modell", () => {
    expect(faultierZustand({ ...ruhig, freut: true, ohneModell: true })).toBe("freut");
  });
});
