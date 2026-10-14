"use client";

import { useEffect, useState } from "react";
import type { LandingContent, Locale } from "@/lib/content";
import { preferredLocale, rememberLocale, storedLocale } from "@/lib/locale";

/*
 * Sprachwahl ohne Server (GitHub Pages kennt kein `Accept-Language`).
 *
 * - Ein Klick auf einen Sprachlink (`data-sprache`) merkt sich die Wahl.
 * - Wer sich die *andere* Sprache gemerkt hat, wird dorthin weitergeleitet.
 * - Wer nichts gewaehlt hat und dessen Browser die andere Sprache bevorzugt,
 *   bekommt nur einen Hinweis – keine Umleitung, sonst saehen Suchmaschinen
 *   (die mit englischem Browser kommen) die deutsche Seite nie.
 */
export function LanguageHint({
  target,
  targetHref,
  texts,
}: {
  target: Locale;
  /** Mit `basePath` (`homeHref`). */
  targetHref: string;
  texts: LandingContent["languageHint"];
}) {
  const [show, setShow] = useState(false);

  useEffect(() => {
    function onClick(event: MouseEvent) {
      const link = (event.target as Element | null)?.closest?.("[data-sprache]");
      const locale = link?.getAttribute("data-sprache");
      if (locale === "de" || locale === "en") {
        rememberLocale(locale);
      }
    }
    document.addEventListener("click", onClick);

    const stored = storedLocale();
    if (stored === target) {
      window.location.replace(`${targetHref}${window.location.search}`);
    } else if (!stored && preferredLocale(navigator.languages) === target) {
      window.setTimeout(() => setShow(true), 0);
    }
    return () => document.removeEventListener("click", onClick);
  }, [target, targetHref]);

  if (!show) {
    return null;
  }
  return (
    <div lang={target} className="border-b border-border bg-muted/50">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2 text-[13px] sm:px-6">
        <p className="text-muted-foreground">{texts.text}</p>
        <div className="flex items-center gap-3">
          <a href={targetHref} hrefLang={target} data-sprache={target} className="font-medium underline-offset-4 hover:underline">
            {texts.action}
          </a>
          <button
            type="button"
            onClick={() => setShow(false)}
            aria-label={texts.dismiss}
            className="grid size-6 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <svg viewBox="0 0 12 12" className="size-3" aria-hidden>
              <path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.4" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
