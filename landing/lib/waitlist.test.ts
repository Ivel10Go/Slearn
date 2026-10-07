import { describe, expect, it } from "vitest";
import { parseWaitlistInput, RateLimiter } from "./waitlist";

const gueltig = { email: "  Mia@Example.DE ", consent: true };

describe("parseWaitlistInput", () => {
  it("normalisiert die E-Mail-Adresse und laesst leere Felder weg", () => {
    const result = parseWaitlistInput(gueltig);
    expect(result).toEqual({
      ok: true,
      entry: { email: "mia@example.de", name: null, role: null, platform: null },
      spam: false,
    });
  });

  it("uebernimmt Name, Rolle und Plattform", () => {
    const result = parseWaitlistInput({ ...gueltig, name: "  Mia   K. ", role: "schueler", platform: "macos" });
    expect(result.ok && result.entry).toEqual({
      email: "mia@example.de",
      name: "Mia K.",
      role: "schueler",
      platform: "macos",
    });
  });

  it.each([
    ["fehlt", ""],
    ["ohne @", "mia.example.de"],
    ["ohne Domain-Punkt", "mia@example"],
    ["mit Leerzeichen", "mia k@example.de"],
  ])("lehnt eine E-Mail-Adresse %s ab", (_, email) => {
    const result = parseWaitlistInput({ ...gueltig, email });
    expect(result.ok).toBe(false);
    expect(!result.ok && result.field).toBe("email");
  });

  it("lehnt eine unbekannte Rolle und Plattform ab", () => {
    expect(parseWaitlistInput({ ...gueltig, role: "admin" })).toMatchObject({ ok: false, field: "role" });
    expect(parseWaitlistInput({ ...gueltig, platform: "linux" })).toMatchObject({ ok: false, field: "platform" });
  });

  it("verlangt die Einwilligung", () => {
    expect(parseWaitlistInput({ email: "mia@example.de" })).toMatchObject({ ok: false, field: "consent" });
    expect(parseWaitlistInput({ ...gueltig, consent: "on" }).ok).toBe(true);
  });

  it("lehnt einen zu langen Namen ab", () => {
    expect(parseWaitlistInput({ ...gueltig, name: "x".repeat(81) })).toMatchObject({ ok: false, field: "name" });
  });

  it("erkennt das ausgefuellte Honigtopf-Feld als Spam", () => {
    const result = parseWaitlistInput({ ...gueltig, website: "https://spam.example" });
    expect(result).toMatchObject({ ok: true, spam: true });
  });

  it("lehnt eine leere oder unlesbare Anfrage ab", () => {
    expect(parseWaitlistInput(null)).toMatchObject({ ok: false, field: "form" });
    expect(parseWaitlistInput([1, 2])).toMatchObject({ ok: false, field: "form" });
  });
});

describe("RateLimiter", () => {
  it("erlaubt hoechstens die Obergrenze je Fenster", () => {
    const limiter = new RateLimiter(2, 1000);
    expect(limiter.allow("a", 0)).toBe(true);
    expect(limiter.allow("a", 10)).toBe(true);
    expect(limiter.allow("a", 20)).toBe(false);
    expect(limiter.allow("b", 20)).toBe(true);
  });

  it("gibt nach Ablauf des Fensters wieder frei", () => {
    const limiter = new RateLimiter(1, 1000);
    expect(limiter.allow("a", 0)).toBe(true);
    expect(limiter.allow("a", 999)).toBe(false);
    expect(limiter.allow("a", 1001)).toBe(true);
  });
});
