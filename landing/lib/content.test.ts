import { describe, expect, it } from "vitest";
import { CONTENT, homePath, LOCALES, otherLocale, type LandingContent } from "./content";
import { preferredLocale } from "./locale";
import { parseWaitlistInput, PLATFORMS, ROLES } from "./waitlist";
import { errorMessage } from "./waitlist-messages";

// Gleiche Gestalt in beiden Sprachen: jede Liste gleich lang, jede Zeichenkette gefuellt.
function shape(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(shape);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, inner]) => [key, shape(inner)]));
  }
  if (typeof value === "string") {
    return value.trim().length > 0 ? "text" : "LEER";
  }
  return typeof value;
}

describe("Texte der Landingpage", () => {
  it("haben in beiden Sprachen dieselbe Gestalt", () => {
    expect(shape({ ...CONTENT.en, locale: "" })).toEqual(shape({ ...CONTENT.de, locale: "" }));
  });

  it("verlinken nur Abschnitte, die es gibt, mit eindeutigen Sprungmarken", () => {
    for (const locale of LOCALES) {
      const content: LandingContent = CONTENT[locale];
      const ids = Object.values(content.ids);
      expect(new Set(ids).size).toBe(ids.length);
      for (const link of [...content.header.nav, ...content.footer.links]) {
        expect(content.ids[link.section]).toBeTruthy();
      }
    }
  });

  it("nennt jede Rolle und Plattform der Warteliste", () => {
    for (const locale of LOCALES) {
      expect(Object.keys(CONTENT[locale].form.roles).sort()).toEqual(Object.keys(ROLES).sort());
      expect(Object.keys(CONTENT[locale].form.platforms).sort()).toEqual(Object.keys(PLATFORMS).sort());
    }
  });

  it("deutsche Fehlermeldungen stimmen mit denen des Servers ueberein", () => {
    const errors = CONTENT.de.form.errors;
    const cases: [unknown, string][] = [
      [{ email: "" }, errors.emailMissing],
      [{ email: "kaputt" }, errors.emailInvalid],
      [{ email: "a@b.de", name: "x".repeat(81) }, errors.name],
      [{ email: "a@b.de", role: "chef" }, errors.role],
      [{ email: "a@b.de", platform: "linux" }, errors.platform],
      [{ email: "a@b.de" }, errors.consent],
    ];
    for (const [input, expected] of cases) {
      const result = parseWaitlistInput(input);
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.message).toBe(expected);
      }
    }
  });

  it("Startpfade und Gegensprache", () => {
    expect(homePath("de")).toBe("/");
    expect(homePath("en")).toBe("/en/");
    expect(otherLocale("de")).toBe("en");
    expect(otherLocale("en")).toBe("de");
  });
});

describe("Fehlermeldung des Formulars", () => {
  const errors = CONTENT.en.form.errors;

  it("waehlt je Feld die passende Meldung, leer oder ungueltig getrennt", () => {
    expect(errorMessage(errors, 422, "email", true)).toBe(errors.emailMissing);
    expect(errorMessage(errors, 422, "email", false)).toBe(errors.emailInvalid);
    expect(errorMessage(errors, 422, "consent", false)).toBe(errors.consent);
  });

  it("uebersetzt die Statuscodes ohne Feld", () => {
    expect(errorMessage(errors, 429, "form", false)).toBe(errors.rateLimited);
    expect(errorMessage(errors, 503, "form", false)).toBe(errors.unavailable);
    expect(errorMessage(errors, 403, undefined, false)).toBe(errors.forbidden);
    expect(errorMessage(errors, 500, undefined, false)).toBe(errors.generic);
  });
});

describe("Bevorzugte Sprache", () => {
  it("nimmt die erste unterstuetzte Sprache aus der Browserliste", () => {
    expect(preferredLocale(["fr-FR", "en-US", "de"])).toBe("en");
    expect(preferredLocale(["de-AT", "en"])).toBe("de");
    expect(preferredLocale(["fr", "es"])).toBeNull();
    expect(preferredLocale([])).toBeNull();
  });
});
