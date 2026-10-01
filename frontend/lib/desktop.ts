/**
 * Die Bruecke zur Desktop-App (Electron).
 *
 * `desktop/preload.js` legt sie als `window.lernraumDesktop` an. Im Browser
 * fehlt sie, und jede Stelle, die sie nutzt, muss ohne sie funktionieren - die
 * Web-Fassung ist dieselbe Oberflaeche.
 */
export type DesktopTheme = "light" | "dark" | "system";

export interface DesktopBridge {
  platform: "win32" | "darwin" | "linux";
  version: string;
  /** Faerbt Fensterrahmen und Titelleiste passend zum gewaehlten Design. */
  setTheme: (theme: DesktopTheme) => void;
  /** Oeffnet den Ordner mit Datenbank, Dokumenten und Modellen im Dateimanager. */
  openDataFolder: () => Promise<void>;
  /** Pfad des Datenordners, nur zur Anzeige. */
  dataFolder: string;
}

export function getDesktopBridge(): DesktopBridge | null {
  if (typeof window === "undefined") {
    return null;
  }
  return (window as unknown as { lernraumDesktop?: DesktopBridge }).lernraumDesktop ?? null;
}

/** Die Theme-Einstellung von `next-themes` auf die drei Werte des Fensters abbilden. */
export function toDesktopTheme(theme: string | undefined): DesktopTheme {
  return theme === "light" || theme === "dark" ? theme : "system";
}
