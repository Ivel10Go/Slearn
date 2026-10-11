"use client";

/**
 * Die Benachrichtigungen im Browser - ohne React und ohne DOM.
 *
 * Alles, was sich hier entscheiden laesst, steht in dieser Datei und nicht in
 * der Oberflaeche: die Zeiten, ab denen geprueft wird, die Umrechnung des
 * base64url-Schluessels, der Anker, zu dem eine Erinnerung gehoert, und die
 * Frage, ob eine Erinnerung ueberhaupt angezeigt werden darf. Damit ist der
 * Teil testbar, der sonst nur im Browser laeuft - und `vitest` laeuft hier mit
 * `environment: "node"`, also **ohne** DOM. Komponenten-Tests wuerden still
 * nicht ausgefuehrt; diese Datei ist deshalb bewusst frei davon.
 *
 * Zwei Wege, beide wichtig:
 *
 * * **Push** kommt ueber den Service Worker (`public/sw.js`). Die Seite holt
 *   dafuer den oeffentlichen Schluessel und meldet ein Abonnement an.
 * * **Lokal** holt die Seite ihre Erinnerungen selbst ueber
 *   `GET /v1/notifications` und zeigt sie ueber die `Notification`-API. Das ist
 *   der Weg fuer Browser ohne Push-Berechtigung, und er braucht keinen Dienst.
 *
 * Die Typen kommen aus `./types`, wie bei `progress.ts` und `grades.ts`: hier
 * stehen die Entscheidungen, dort die Form der Daten.
 */
import type {
  LernraumNotification,
  NotificationKind,
  NotificationSettings,
  QuietHours,
} from "./types";
import { t } from "./i18n";

export type { LernraumNotification, NotificationKind, NotificationSettings, QuietHours };

/** Die Nachrichtentypen, die zwischen Seite und Service Worker laufen. */
export const NACHRICHT_PUSH = "LERNRAUM_PUSH";
export const NACHRICHT_PUSH_KEY = "LERNRAUM_PUSH_KEY";
export const NACHRICHT_FRAGE_PUSH_KEY = "LERNRAUM_FRAGE_PUSH_KEY";
export const NACHRICHT_ANZEIGEN = "LERNRAUM_ANZEIGEN";

/**
 * Wie oft die offene Seite nach neuen Erinnerungen sieht.
 *
 * Der Unterschied ist der Takt, den ein Browser im Hintergrund zulässt: ein
 * sichtbarer Tab wird im Sekundentakt gedrosselt, und das ist genau der Fall,
 * in dem der lokale Weg sonst zu spaet waere. Im Hintergrund fragt die Seite
 * nur alle fuenf Minuten - das ist der Takt, den ein versteckter Tab bekommen
 * darf, und mehr kostet nur Akku.
 */
export const INTERVALL_SICHTBAR_MS = 30_000;
export const INTERVALL_HINTERGRUND_MS = 300_000;

/** Wie viele Erinnerungen ein Abruf holt. Deckt sich mit dem Server. */
export const MAX_ABRUF = 20;

/**
 * Die Ruhezeit, mit der die Seite rechnet.
 *
 * Der Server entscheidet, **ob** eine Erinnerung rausgeht - das ist die einzige
 * Stelle, an der das entschieden werden darf. Diese Kopie dient nur dazu, die
 * Anzeige zu erklaeren ("Ruhezeit 22:00 bis 07:00") und nicht zwei Zahlen im
 * Kopf zu haben.
 */

/**
 * Aus einem base64url-Wert die Bytes fuer `applicationServerKey`.
 *
 * `pushManager.subscribe` will ein `Uint8Array` und **keinen** base64url-String.
 * Die Umrechnung ist der Teil, der bei falscher Polsterung still ein
 * falsches Abo erzeugt - deshalb wird sie hier einmal gemacht und getestet.
 */
export function base64urlToBytes(value: string): Uint8Array {
  const normalisiert = value.trim().replace(/-/g, "+").replace(/_/g, "/");
  const mitPolster = normalisiert + "=".repeat((4 - (normalisiert.length % 4)) % 4);
  const binaer = typeof atob === "function" ? atob(mitPolster) : Buffer.from(mitPolster, "base64").toString("binary");
  const bytes = new Uint8Array(binaer.length);
  for (let index = 0; index < binaer.length; index += 1) {
    bytes[index] = binaer.charCodeAt(index);
  }
  return bytes;
}

/** Umgekehrt: Bytes als base64url ohne Polster, wie es der Server erwartet. */
export function bytesToBase64url(bytes: Uint8Array): string {
  let binaer = "";
  for (const byte of bytes) {
    binaer += String.fromCharCode(byte);
  }
  const normalisiert = typeof btoa === "function" ? btoa(binaer) : Buffer.from(binaer, "binary").toString("base64");
  return normalisiert.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Darf eine Erinnerung jetzt angezeigt werden?
 *
 * Drei Bedingungen, und alle drei sind noetig: der Browser muss gefragt haben,
 * die Benachrichtigungen muessen erlaubt sein, und der Tab muss sichtbar sein.
 * Der letzte Punkt ist der unangenehme: im Hintergrund zeigen die meisten
 * Browser eine Benachrichtigung gar nicht erst an, und `document.hidden` sagt
 * vorher, dass es sinnlos waere. Deshalb entscheidet das **hier** und nicht
 * erst im `Notification`-Aufruf.
 */
export function darfLokalAnzeigen(permission: string | undefined, verborgen: boolean): boolean {
  return permission === "granted" && !verborgen;
}

/**
 * Welcher Anker gehoert zu welcher Art.
 *
 * Die Navigation ist eine Seite mit Ankern; die Karten an der Seite bekommen
 * keinen neuen Anker. Faellt eine neue Art hinzu, ist das hier eine Zeile.
 */
export function ankerFuer(art: NotificationKind): string {
  return art === "exam_due" ? "/#calendar" : "/#study-tasks";
}

/** Wie eine Art auf Deutsch heisst. Fuer die Einstellungen und das Log. */
export function bezeichnung(art: NotificationKind): string {
  return art === "exam_due" ? t("Prüfung") : t("Lernaufgabe");
}

/**
 * Steht in diesem Browser ueberhaupt Push zur Verfuegung?
 *
 * Bewusst die **drei** Voraussetzungen getrennt geprueft: `serviceWorker` (der
 * bekommt die Nachricht), `PushManager` (das Abo) und `Notification` (die
 * Anzeige). In Firefox ohne aktivierten Push fehlt der `PushManager`, und das
 * ist dann kein Fehler, sondern der Fall, fuer den es den lokalen Weg gibt.
 */
export function pushVerfuegbar(
  faehigkeiten: { serviceWorker?: unknown; PushManager?: unknown; Notification?: unknown } | undefined,
): boolean {
  if (!faehigkeiten) {
    return false;
  }
  return Boolean(faehigkeiten.serviceWorker && faehigkeiten.PushManager && faehigkeiten.Notification);
}

/**
 * Erklaert die Ruhezeit in einem Satz.
 *
 * „Ueber Mitternacht" steht drin, weil `22:00 bis 07:00` sonst wie ein
 * Rechenfehler gelesen wird.
 */
export function beschreibeRuhezeit(ruhe: QuietHours): string {
  if (!ruhe.enabled) {
    return t("aus");
  }
  return t("{von} bis {bis}", { von: ruhe.start, bis: ruhe.end }) + (ruhe.spans_midnight ? ` ${t("(über Mitternacht)")}` : "");
}

/**
 * Was der Knopf in den Einstellungen anklicken soll.
 *
 * Ohne freigegebenen Kanal gibt es nichts zu pruefen - und der Knopf waere
 * eine Zusage, die das Backend nicht halten kann.
 */
export function pruefknopfMoeglich(einstellungen: NotificationSettings | null): boolean {
  if (!einstellungen) {
    return false;
  }
  if (!einstellungen.enabled) {
    return false;
  }
  return Object.values(einstellungen.channels).some(Boolean);
}

/** Die Kanäle, die gerade eingeschaltet sind - in der Reihenfolge des Servers. */
export function aktiveKanaele(einstellungen: NotificationSettings | null): string[] {
  if (!einstellungen) {
    return [];
  }
  return einstellungen.channels_available.filter((kanal) => einstellungen.channels[kanal]);
}
