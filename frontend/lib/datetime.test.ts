import { describe, expect, it } from "vitest";
import { toDateInput, toDateTimeLocal, toUtcIso, toUtcIsoAtTime, toUtcIsoFromParts } from "./datetime";

/**
 * Bezugspunkte fuer die lokalen Zeiten.
 *
 * `datetime-local` kennt keine Zeitzone: `2026-03-04T07:45` bedeutet "07:45
 * Uhr **hier**". Deshalb sind alle Erwartungen ueber `new Date(y, m, d, ...)`
 * gebaut - der Konstruktor ist die einzige Form, die genau das ausdrueckt und
 * in jeder Zeitzone dasselbe meint. Fest verdrahtete UTC-Erwartungen wuerden
 * nur auf einem Rechner mit Zone UTC gelten und auf jedem anderen falsch
 * sein - ein Test, der die Zone versteckt, testet die falsche Sache.
 */
const lokal = (stunde: number, minute = 0, sekunde = 0) => new Date(2026, 2, 4, stunde, minute, sekunde, 0).toISOString();

describe("datetime-local-Brücke", () => {
  it("liefert fuer einen UTC-Zeitpunkt wieder denselben Zeitpunkt zurueck", () => {
    const moment = new Date(2026, 2, 4, 7, 45, 0, 0).toISOString();
    expect(toUtcIso(toDateTimeLocal(moment))).toBe(moment);
  });

  it("schneidet auf Minuten genau ab, sonst landen Sekunden im Textfeld", () => {
    const moment = new Date(2026, 2, 4, 7, 45, 37, 0).toISOString();
    const value = toDateTimeLocal(moment);
    expect(value).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    expect(toUtcIso(value)).toBe(moment.replace(":37.000Z", ":00.000Z"));
  });

  it("behandelt einen schon lokalen Wert unveraendert", () => {
    // `2026-03-04T07:45` ohne Zone: genau das, was ein `datetime-local` liefert.
    expect(toUtcIso(toDateTimeLocal("2026-03-04T07:45"))).toBe(lokal(7, 45));
  });

  it("liefert leere Strings statt kaputter Zeitpunkte", () => {
    expect(toDateTimeLocal(null)).toBe("");
    expect(toDateTimeLocal(undefined)).toBe("");
    expect(toDateTimeLocal("")).toBe("");
    expect(toDateTimeLocal("kein Datum")).toBe("");
    expect(toDateInput("kein Datum")).toBe("");
  });

  it("verkleinert auf den Tag, ohne die Zeitzone zu verschieben", () => {
    // Nur der Tag darf wandern, und zwar derselbe, den der Browser zeigt.
    const moment = new Date(2026, 2, 4, 23, 30, 0, 0).toISOString();
    expect(toDateInput(moment)).toBe(toDateTimeLocal(moment).slice(0, 10));
  });
});

describe("Formularwerte", () => {
  it("lehnt leere und unbrauchbare Eingaben ab, statt zu werfen", () => {
    expect(toUtcIso("")).toBeNull();
    expect(toUtcIso("   ")).toBeNull();
    expect(toUtcIso(null)).toBeNull();
    expect(toUtcIso("morgen")).toBeNull();
  });

  it("haengt die fehlende Minute an eine Zeitangabe", () => {
    // Ohne das wuerde `07:45` als `07:45:00Z` gelesen - mit einem Sekundenfeld,
    // das manche Eingabemasken zurueckweisen.
    expect(toUtcIsoFromParts("2026-03-04", "07:45")).toBe(lokal(7, 45));
  });

  it("nimmt eine vollstaendige Zeitangabe unveraendert", () => {
    expect(toUtcIsoFromParts("2026-03-04", "07:45:30")).toBe(lokal(7, 45, 30));
  });

  it("legt ein Datum ohne Uhrzeit auf Mitternacht", () => {
    expect(toUtcIsoFromParts("2026-03-04")).toBe(lokal(0));
    expect(toUtcIsoFromParts("2026-03-04", "")).toBe(lokal(0));
  });

  it("verlangt ein Datum, eine Uhrzeit allein reicht nicht", () => {
    expect(toUtcIsoFromParts("", "07:45")).toBeNull();
    expect(toUtcIsoFromParts(null)).toBeNull();
  });

  it("setzt eine feste Uhrzeit auf einen vorhandenen Tag", () => {
    // Der Tag kommt aus einem UTC-Zeitpunkt und wird ueber die Bruecke in die
    // Anzeige des Browsers geholt - sonst wueere es der 3. statt der 4.
    const moment = new Date(2026, 2, 4, 13, 20, 0, 0).toISOString();
    expect(toUtcIsoAtTime(moment, "18:00")).toBe(lokal(18));
    expect(toUtcIsoAtTime(null, "18:00")).toBeNull();
  });
});
