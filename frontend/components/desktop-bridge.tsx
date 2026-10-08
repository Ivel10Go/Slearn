"use client";

import { useEffect } from "react";
import { useTheme } from "next-themes";
import { getDesktopBridge, toDesktopTheme } from "@/lib/desktop";
import { getLocale } from "@/lib/i18n";

/**
 * Haelt das Desktop-Fenster mit der Seite im Gleichschritt.
 *
 * `data-desktop` und `data-platform` setzt der Preload schon vor dem ersten
 * Rendern (sonst springt die Seitenleiste auf dem Mac nach dem Laden um die
 * Ampelknoepfe nach unten). Hier wird nur nachgezogen, falls die Seite ohne
 * Preload-Lauf neu aufgebaut wurde - und das Design an den Fensterrahmen
 * gemeldet, damit die Titelleiste unter Windows nicht hell bleibt, waehrend die
 * Seite dunkel ist.
 */
export function DesktopBridge() {
  const { theme } = useTheme();

  useEffect(() => {
    const bridge = getDesktopBridge();
    if (!bridge) {
      return;
    }
    const root = document.documentElement;
    root.dataset.desktop = "";
    root.dataset.platform = bridge.platform;
    // Die Sprache aendert sich nur mit einem Neuladen, deshalb genuegt es einmal.
    bridge.setLanguage?.(getLocale());
  }, []);

  useEffect(() => {
    getDesktopBridge()?.setTheme(toDesktopTheme(theme));
  }, [theme]);

  return null;
}
