"use client";

import { Select } from "@/components/ui/select";
import { changeLocale, getLocale, isLocale, LOCALE_NAMES, LOCALES, t } from "@/lib/i18n";

/**
 * Die Wahl der Oberflaechensprache.
 *
 * Jede Sprache steht in ihrer eigenen Schreibweise ("English", nicht
 * "Englisch") - wer die aktuelle Sprache nicht liest, findet seine trotzdem.
 * Ein Wechsel laedt die Seite neu (`changeLocale`); dabei gehen nur ungespeicherte
 * Eingaben verloren, nichts, was schon beim Server liegt.
 */
export function LanguageSelect({ id, className }: { id?: string; className?: string }) {
  return (
    <Select
      id={id}
      className={className}
      value={getLocale()}
      aria-label={t("Sprache")}
      onChange={(event) => {
        const gewaehlt = event.target.value;
        if (isLocale(gewaehlt) && gewaehlt !== getLocale()) {
          changeLocale(gewaehlt);
        }
      }}
    >
      {LOCALES.map((locale) => (
        <option key={locale} value={locale} lang={locale}>
          {LOCALE_NAMES[locale]}
        </option>
      ))}
    </Select>
  );
}
