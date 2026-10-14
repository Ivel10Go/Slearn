import type { Locale } from "./content";

/*
 * Gemerkte und bevorzugte Sprache der Landingpage. Derselbe Schluessel wie in
 * der App (`frontend/lib/i18n.ts`); geteilt wird er trotzdem nicht, weil die
 * Seiten verschiedene Herkuenfte haben.
 */
export const LOCALE_STORAGE_KEY = "lernraum.sprache";

export function storedLocale(): Locale | null {
  try {
    const value = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return value === "de" || value === "en" ? value : null;
  } catch {
    return null;
  }
}

export function rememberLocale(locale: Locale): void {
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Privater Modus o. ae.: die Wahl gilt dann nur fuer diesen Seitenaufruf.
  }
}

/** Erste unterstuetzte Sprache aus `navigator.languages`; `null`, wenn keine passt. */
export function preferredLocale(languages: readonly string[]): Locale | null {
  for (const language of languages) {
    const base = language.toLowerCase().split("-")[0];
    if (base === "de" || base === "en") {
      return base;
    }
  }
  return null;
}
