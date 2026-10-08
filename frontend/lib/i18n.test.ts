import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { en } from "./i18n/en";
import { formatDuration } from "./format";
import { pickLocale, setLocaleForTests, t, tn, tSplit } from "./i18n";

const ROOT = join(__dirname, "..");
const SOURCE_DIRS = ["app", "components", "lib"];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      return name === "i18n" ? [] : sourceFiles(path);
    }
    return /\.(ts|tsx)$/.test(name) && !name.endsWith(".test.ts") ? [path] : [];
  });
}

/** Jeder Text, der als erstes Argument an `t(` oder als zweites/drittes an `tn(` geht. */
function usedKeys() {
  const keys = new Map<string, string>();
  const literal = String.raw`"((?:[^"\\]|\\.)*)"`;
  const patterns = [
    new RegExp(String.raw`\bt(?:Split)?\(\s*${literal}`, "g"),
    new RegExp(String.raw`\btn\(\s*[^,]+,\s*${literal}\s*,\s*${literal}`, "g"),
  ];
  for (const dir of SOURCE_DIRS) {
    for (const file of sourceFiles(join(ROOT, dir))) {
      const text = readFileSync(file, "utf8");
      for (const pattern of patterns) {
        for (const match of text.matchAll(pattern)) {
          for (const raw of match.slice(1)) {
            if (raw !== undefined) {
              keys.set(JSON.parse(`"${raw}"`) as string, relative(ROOT, file));
            }
          }
        }
      }
    }
  }
  return keys;
}

function placeholders(text: string) {
  return [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
}

describe("Übersetzungen", () => {
  afterEach(() => setLocaleForTests("de"));

  it("hat für jeden Text im Quelltext einen englischen Eintrag", () => {
    const missing = [...usedKeys()].filter(([key]) => !(key in en)).map(([key, file]) => `${file}: ${key}`);
    expect(missing).toEqual([]);
  });

  it("behält in jeder Übersetzung dieselben Platzhalter", () => {
    const broken = Object.entries(en)
      .filter(([source, target]) => placeholders(source).join() !== placeholders(target).join())
      .map(([source]) => source);
    expect(broken).toEqual([]);
  });

  it("liefert auf Deutsch den Schlüssel selbst und ersetzt Platzhalter", () => {
    expect(t("Noch {n} Tage", { n: 3 })).toBe("Noch 3 Tage");
    expect(tn(1, "{n} Aufgabe", "{n} Aufgaben")).toBe("1 Aufgabe");
  });

  it("übersetzt auf Englisch, auch in Hilfsfunktionen", () => {
    setLocaleForTests("en");
    expect(t("Speichern")).toBe("Save");
    expect(formatDuration(90)).toBe("1 h 30 min");
  });

  it("teilt einen Satz um das eingebettete Element", () => {
    expect(tSplit("Modelle und Stimmen richtest du unter {link} ein.")).toEqual(["Modelle und Stimmen richtest du unter ", " ein."]);
    setLocaleForTests("en");
    expect(tSplit("Modelle und Stimmen richtest du unter {link} ein.")).toEqual(["You can set up models and voices under ", "."]);
  });

  it("bleibt bei unbekanntem Text beim Deutschen", () => {
    setLocaleForTests("en");
    expect(t("Ein Satz, den es nicht gibt")).toBe("Ein Satz, den es nicht gibt");
  });

  it("wählt die erste unterstützte Browsersprache, sonst Deutsch", () => {
    expect(pickLocale(["fr-FR", "en-US", "de"])).toBe("en");
    expect(pickLocale(["de-AT"])).toBe("de");
    expect(pickLocale(["fr"])).toBe("de");
  });
});
