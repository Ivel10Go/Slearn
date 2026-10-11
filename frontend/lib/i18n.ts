/**
 * Mehrsprachigkeit der Oberflaeche.
 *
 * Der deutsche Text ist selbst der Schluessel (wie bei gettext): `t("Speichern")`
 * liefert auf Deutsch "Speichern" und auf Englisch den Eintrag aus `i18n/en.ts`.
 * Fehlt ein Eintrag, bleibt der deutsche Text stehen - lieber ein deutsches Wort
 * als ein leerer Knopf. `lib/i18n.test.ts` prueft, dass jeder `t(<Text>)`-Aufruf im
 * Quelltext eine Uebersetzung hat, und faellt sonst den Testlauf.
 *
 * Die Sprache ist ein Modulzustand und kein React-Kontext: auch Hilfsfunktionen
 * ausserhalb von Komponenten (`formatDuration`, Fehlermeldungen in `api.ts`)
 * uebersetzen. Ein Wechsel laedt die Seite neu. Das geht, weil `SetupGate` auf
 * dem Server nichts rendert - jeder sichtbare Text entsteht im Browser, nachdem
 * die gespeicherte Sprache gelesen ist, und es gibt keinen Hydrationsunterschied.
 */
import { en } from "./i18n/en";

export const LOCALES = ["de", "en"] as const;
export type Locale = (typeof LOCALES)[number];

/** Jede Sprache in ihrer eigenen Schreibweise - so findet man sie auch, wenn man die aktuelle nicht liest. */
export const LOCALE_NAMES: Record<Locale, string> = { de: "Deutsch", en: "English" };

/** Fuer `Intl`: Englisch als en-GB, damit Tag vor Monat und 24 Stunden bleiben. */
const INTL_LOCALES: Record<Locale, string> = { de: "de-DE", en: "en-GB" };

const DICTIONARIES: Record<Locale, Record<string, string> | null> = { de: null, en };

export const LOCALE_STORAGE_KEY = "lernraum.sprache";

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

/** Aus `navigator.languages` die erste unterstuetzte Sprache, sonst Deutsch. */
export function pickLocale(preferred: readonly string[]): Locale {
  for (const tag of preferred) {
    const base = tag.toLowerCase().split("-")[0];
    if (isLocale(base)) {
      return base;
    }
  }
  return "de";
}

function detectLocale(): Locale {
  if (typeof window === "undefined") {
    return "de";
  }
  try {
    const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    if (isLocale(stored)) {
      return stored;
    }
  } catch {
    // Gesperrter Speicher: dann entscheidet der Browser.
  }
  return pickLocale(window.navigator.languages ?? [window.navigator.language]);
}

let current: Locale = detectLocale();

export function getLocale(): Locale {
  return current;
}

/** Nur fuer Tests: setzt die Sprache ohne Speicher und ohne Neuladen. */
export function setLocaleForTests(locale: Locale) {
  current = locale;
}

/** Merkt die Sprache und laedt neu, damit jeder Text - auch die vorberechneten - neu entsteht. */
export function changeLocale(locale: Locale) {
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Ohne Speicher gilt die Wahl nur bis zum naechsten Start.
  }
  current = locale;
  window.location.reload();
}

export function intlLocale(): string {
  return INTL_LOCALES[current];
}

export type TranslationValues = Record<string, string | number>;

function interpolate(text: string, values?: TranslationValues) {
  if (!values) {
    return text;
  }
  return text.replace(/\{(\w+)\}/g, (match, name: string) => (name in values ? String(values[name]) : match));
}

/** Uebersetzt einen deutschen Text; `{name}` wird aus `values` ersetzt. */
export function t(source: string, values?: TranslationValues): string {
  const dictionary = DICTIONARIES[current];
  return interpolate(dictionary?.[source] ?? source, values);
}

/**
 * Eine Meldung, die so vom Backend kommt. Ohne Platzhalter-Ersetzung: ein `{`
 * in einer Servermeldung ist Text, kein Platzhalter.
 */
export function translateServerText(text: string): string {
  return DICTIONARIES[current]?.[text] ?? text;
}

/**
 * Ein Satz mit einem Element mittendrin (Link, Hervorhebung): liefert den Text
 * vor und nach `{link}`. So bleibt der Satz ein Schluessel, und jede Sprache
 * stellt das Element an die Stelle, an die es in ihrem Satzbau gehoert.
 */
export function tSplit(source: string, values?: TranslationValues): [string, string] {
  const text = t(source, values);
  const index = text.indexOf("{link}");
  return index < 0 ? [text, ""] : [text.slice(0, index), text.slice(index + "{link}".length)];
}

/** Einzahl oder Mehrzahl nach `count`; `{n}` steht fuer die Zahl. */
export function tn(count: number, one: string, other: string, values?: TranslationValues): string {
  return t(count === 1 ? one : other, { n: count, ...values });
}
