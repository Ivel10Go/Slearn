import { ApiError } from "./api";
import { t } from "./i18n";
import type { UserResponse } from "./types";

/** Die Anmeldeseite. Deutsch wie `/lernen` und `/fortschritt`. */
export const LOGIN_PATH = "/anmelden";

/** Der Parameter, unter dem die Anmeldeseite das Ziel nach der Anmeldung bekommt. */
export const NEXT_PARAM = "weiter";

/**
 * Was `/v1/setup` ueber den Betrieb verraet.
 *
 * - `needed`: lokaler Modus ohne Konto (erste Einrichtung).
 * - `done`: lokaler Modus mit Konto - eine Anmeldung gibt es dort nicht.
 * - `auth`: der Endpunkt antwortet 404, also `AUTH_ENABLED=true`.
 */
export type SetupMode = "needed" | "done" | "auth";

export type AccessState = "setup" | "login" | "ready";

export interface Access {
  state: AccessState;
  /** Nur mit Anmeldung gibt es etwas abzumelden. */
  serverMode: boolean;
}

interface AccessProbes {
  setupMode: () => Promise<SetupMode>;
  session: () => Promise<UserResponse | null>;
}

/**
 * Entscheidet, was vor den Seiten steht: Einrichtung, Anmeldung oder nichts.
 *
 * Die Sitzung wird **nur im Server-Modus** gefragt. Im lokalen Modus antwortet
 * `/auth/me` nie mit 401, und eine zusaetzliche Anfrage vor jeder Seite haette
 * dort keinen Zweck. Scheitert die Sitzungsabfrage mit etwas anderem als 401
 * (Server weg, 503), bleibt die Schranke offen: die Seiten zeigen ihren
 * eigenen Fehler mit "Erneut versuchen", die Anmeldeseite koennte daran
 * nichts aendern.
 */
export async function resolveAccess(probes: AccessProbes): Promise<Access> {
  const mode = await probes.setupMode();
  if (mode === "needed") {
    return { state: "setup", serverMode: false };
  }
  if (mode === "done") {
    return { state: "ready", serverMode: false };
  }
  try {
    const user = await probes.session();
    return { state: user ? "ready" : "login", serverMode: true };
  } catch {
    return { state: "ready", serverMode: true };
  }
}

/**
 * Das Ziel nach der Anmeldung, nur innerhalb von Slearn.
 *
 * Der Wert kommt aus der Adresszeile und damit von jedem, der einen Link
 * verschickt. `//boese.example` und `/\boese.example` sind fuer den Browser
 * fremde Herkuenfte - ohne diese Pruefung waere die Anmeldeseite eine offene
 * Weiterleitung mit Slearn-Absender.
 */
export function safeNextPath(raw: string | null | undefined): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) {
    return "/";
  }
  if (raw === LOGIN_PATH || raw.startsWith(`${LOGIN_PATH}?`) || raw.startsWith(`${LOGIN_PATH}/`)) {
    return "/";
  }
  return raw;
}

/** Die Anmeldeseite mit dem Ziel, zu dem sie danach zurueckfuehrt. */
export function loginHref(next?: string | null): string {
  const target = safeNextPath(next);
  return target === "/" ? LOGIN_PATH : `${LOGIN_PATH}?${NEXT_PARAM}=${encodeURIComponent(target)}`;
}

/**
 * Die Meldung unter dem Anmeldeformular.
 *
 * Das Backend antwortet hier englisch ("Invalid credentials", "Too many login
 * attempts"); die Oberflaeche nimmt deshalb den Statuscode, nicht den Text.
 */
export function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return t("Benutzername oder Passwort stimmt nicht.");
    }
    if (error.status === 429) {
      return t("Zu viele Anmeldeversuche. Warte ein paar Minuten und versuche es dann noch einmal.");
    }
    if (error.status === 422) {
      return t("Bitte gib Benutzername und Passwort ein.");
    }
    if (error.status === 0) {
      return error.message;
    }
  }
  return t("Die Anmeldung hat nicht geklappt. Bitte versuche es noch einmal.");
}
