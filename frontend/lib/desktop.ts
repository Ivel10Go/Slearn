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
  /**
   * Ab Version 1.1. Eine aeltere App hat die folgenden Funktionen nicht, deshalb
   * optional - die Karte blendet die Schalter dann aus.
   */
  getSettings?: () => Promise<DesktopSettings>;
  setOpenAtLogin?: (enabled: boolean) => Promise<{ openAtLogin: boolean }>;
  setCloseToTray?: (enabled: boolean) => Promise<{ closeToTray: boolean }>;
  checkForUpdates?: () => Promise<DesktopUpdateStatus>;
  getUpdateStatus?: () => Promise<DesktopUpdateStatus>;
}

export interface DesktopSettings {
  openAtLogin: boolean;
  closeToTray: boolean;
  updatesSupported: boolean;
}

export interface DesktopUpdateStatus {
  state: "idle" | "checking" | "available" | "downloaded" | "current" | "error";
  supported: boolean;
  version?: string;
  appVersion: string;
  message?: string;
}

/** Ein Satz zum Update-Stand fuer die Einstellungen. */
export function describeUpdateStatus(status: DesktopUpdateStatus | null): string {
  if (!status || !status.supported) {
    return "";
  }
  switch (status.state) {
    case "checking":
      return "Suche nach Updates …";
    case "available":
      return `Version ${status.version ?? ""} wird geladen …`.replace("  ", " ");
    case "downloaded":
      return `Version ${status.version ?? ""} ist bereit und wird beim nächsten Neustart installiert.`.replace("  ", " ");
    case "current":
      return "Lernraum ist aktuell.";
    case "error":
      return status.message ?? "Die Suche nach Updates ist fehlgeschlagen.";
    default:
      return "Updates werden automatisch gesucht.";
  }
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
