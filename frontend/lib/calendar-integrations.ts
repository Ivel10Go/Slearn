/**
 * Die Entscheidungen der Integrationsverwaltung - ohne React.
 *
 * Warum eine eigene Datei: Die Regeln, die eine Verbindungszeile und das Formular
 * treffen, sind Logik, keine Darstellung. Als Teil einer Komponente wären sie nur
 * im Browser prüfbar, und `vitest.config.mts` führt ausschließlich Tests unter
 * `lib/` mit `environment: "node"` aus. Komponenten- und DOM-Tests laufen dort
 * **stillschweigend nicht** - sie zu schreiben und sich auf sie zu verlassen wäre
 * schlimmer, als die Regeln nicht zu prüfen.
 *
 * Jede Funktion hier ist eine deutsche Frage mit einer deutschen Antwort:
 * Was fehlt noch? Was darf man drücken? Was sagt man dem Nutzer?
 */

import type { IntegrationCatalog, IntegrationRead } from "./types";
import type { IntegrationWrite } from "./api";
import { t } from "./i18n";

/** Wozu der Knopf in dieser Art Integration tatsächlich abruft. */
export const SYNC_LABELS: Record<string, string> = {
  webuntis: t("Stundenplan aktualisieren"),
  documents: t("Dokumente indexieren"),
  ical: t("Termine holen"),
  google_calendar: t("Termine abgleichen"),
  caldav: t("Termine abgleichen"),
};

/**
 * Welche Arten überhaupt einen Abruf haben.
 *
 * „Stundenplan aktualisieren" wäre bei einem Kalender eine Lüge, und ein Knopf,
 * der immer sichtbar ist und dann nichts tut, ist schlechter als keiner.
 */
export const SYNC_KINDS: readonly string[] = ["webuntis", "documents", "ical", "google_calendar", "caldav"];

export function syncLabelFor(kind: string): string {
  return SYNC_LABELS[kind] ?? "Aktualisieren";
}

export function isSyncableKind(kind: string): boolean {
  return SYNC_KINDS.includes(kind);
}

/**
 * Felder, die der Nutzer niemals selbst eintippt.
 *
 * `refresh_token` und `token_scope` entstehen aus dem Einwilligungslauf. Ein
 * Eingabefeld dafür wäre eine Einladung, den Zugriff von Hand zu setzen und
 * damit genau die Zustimmung zu umgehen, um die es hier geht. Sie stehen in
 * den technischen Angaben der Zeile, damit sichtbar bleibt, dass sie existieren.
 */
export const READ_ONLY_CREDENTIALS: readonly string[] = ["refresh_token", "token_scope"];

export function isReadOnlyCredential(field: string): boolean {
  return READ_ONLY_CREDENTIALS.includes(field);
}

/** Felder, die niemals im Klartext nebenbei stehen sollten. */
export const SECRET_FIELDS: readonly string[] = ["password", "api_key", "client_secret", "refresh_token"];

export function isSecretField(field: string): boolean {
  return SECRET_FIELDS.includes(field);
}

const FIELD_LABELS: Record<string, string> = {
  base_url: t("Basis-URL"),
  school: t("Schule"),
  username: t("Benutzername"),
  password: t("Passwort"),
  model: t("Modell"),
  api_key: t("API-Schlüssel"),
  path: t("Ablageort"),
  url: t("ICS-Adresse"),
  client_id: t("Client-ID"),
  client_secret: t("Client-Schlüssel"),
  calendar_id: t("Kalender-ID"),
  redirect_uri: t("Weiterleitungs-Adresse"),
  refresh_token: t("Zugriff von Google"),
  token_scope: t("Erteilte Rechte"),
  runtime: "Laufzeit",
};

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field;
}

/**
 * Fehlt Google die Einwilligung?
 *
 * Das Merkmal ist der `refresh_token` unter den Credential-Schlüsseln: er
 * erscheint erst, nachdem der Rückweg von Google durchgelaufen ist. Das ist
 * robuster als ein eigener Status, weil derselbe Zustand auch dann stimmt, wenn
 * jemand die Zeile zwischenzeitlich umbenannt hat.
 */
export function needsAuthorization(integration: IntegrationRead): boolean {
  return integration.kind === "google_calendar" && !integration.credential_keys.includes("refresh_token");
}

/** Ist der Abruf freigegeben - oder nur lesend? */
export function isWritingBlocked(kind: string, catalog: IntegrationCatalog[], allowed: boolean): boolean {
  const spec = catalog.find((entry) => entry.kind === kind);
  return Boolean(spec?.supports_writing) && !allowed;
}

export interface FormValidation {
  message: string;
  /** Die Felder, die `aria-invalid` bekommen sollen. */
  invalid: string[];
}

/**
 * Prüft Pflichtfelder **und** Alternativen und liefert einen deutschen Satz.
 *
 * Die Alternativen (`required_one_of`, etwa `{url, path}` für ein iCalendar-Abo)
 * stehen bewusst **nicht** in den Pflichtfeldern. Ein Feld aus einer Gruppe ist
 * nicht "fehlend", wenn das andere gefüllt ist - sonst könnte man keine Adresse
 * eintragen und speichern.
 *
 * Eine Gruppe mit **zwei** ausgefüllten Feldern ist ein eigener Fehler und kein
 * fehlendes Feld: Es geht darum, dass beide Angaben gespeichert würden und die
 * Quelle dann unklar bleibt, welche gilt.
 */
export function validateIntegrationInput(
  spec: IntegrationCatalog,
  config: Record<string, string>,
  credentials: Record<string, string>,
): FormValidation | null {
  const combined = { ...config, ...credentials };
  const fehlend = [...spec.required_config, ...spec.required_credentials].filter((field) => !combined[field]?.trim());
  const beschriftungen = fehlend.map(fieldLabel);

  for (const gruppe of spec.required_one_of) {
    const felder = gruppe.filter(Boolean);
    const gewaehlt = felder.filter((field) => combined[field]?.trim());
    if (gewaehlt.length > 1) {
      return {
        message: t("Trage nur eine Angabe ein: {liste}.", { liste: felder.map(fieldLabel).join(` ${t("oder")} `) }),
        invalid: gewaehlt,
      };
    }
    if (gewaehlt.length === 0) {
      beschriftungen.push(felder.map(fieldLabel).join(` ${t("oder")} `));
    }
  }

  if (beschriftungen.length === 0) {
    return null;
  }
  return { message: t("Diese Angaben fehlen noch: {liste}.", { liste: beschriftungen.join(", ") }), invalid: fehlend };
}

/**
 * Baut den Schreibvorgang für das Formular.
 *
 * Drei Dinge passieren hier bewusst: die Alternativfelder werden **mit**
 * geschickt (sonst ginge `path` verloren, weil es kein Pflichtfeld ist), die
 * optionalen Felder ebenfalls (`runtime` beim KI-Server), und die
 * schreibgeschützten Felder werden **weggelassen** (sonst schickte das Formular
 * leere Platzhalter mit, die den vom Server gespeicherten Refresh-Token
 * überschreiben würden).
 */
export function buildIntegrationWrite(
  spec: IntegrationCatalog,
  name: string,
  config: Record<string, string>,
  credentials: Record<string, string>,
): IntegrationWrite {
  const configFelder = [
    ...spec.required_config,
    ...(spec.optional_config ?? []),
    ...spec.required_one_of.flat().filter(Boolean),
  ];
  const credentialFelder = [...spec.required_credentials, ...spec.optional_credentials].filter(
    (field) => !isReadOnlyCredential(field),
  );
  return {
    kind: spec.kind,
    name: name.trim() || spec.label,
    config: collectFilled(config, configFelder),
    credentials: collectFilled(credentials, credentialFelder),
  };
}

/**
 * Die Felder, die das Formular in dieser Art anzeigt.
 *
 * Pflichtfelder zuerst, danach die optionalen. Die Reihenfolge ist die Anzeige-
 * reihenfolge, und deshalb eine Funktion statt zweier Stellen im Formular -
 * sonst hängt die Pflichtmarkierung von der Liste ab, die gerade iteriert wird.
 */
export function integrationConfigFields(spec: IntegrationCatalog): string[] {
  return [...spec.required_config, ...(spec.optional_config ?? [])];
}


function collectFilled(values: Record<string, string>, fields: string[]): Record<string, string> {
  return fields.reduce<Record<string, string>>((result, field) => {
    const value = values[field]?.trim();
    if (value) {
      result[field] = value;
    }
    return result;
  }, {});
}
