import { t, translateServerText } from "./i18n";
/*
 * Die Regeln der lokalen KI in der Oberflaeche - ohne React, ohne `fetch`.
 *
 * Warum eine eigene Datei: `vitest.config.mts` fuehrt ausschliesslich Tests
 * unter `lib/` mit `environment: "node"` aus. Komponenten- und DOM-Tests
 * laufen dort **stillschweigend nicht**. Alles hier ist deshalb eine reine
 * Funktion ueber die Antwort von `GET /api/v1/ai-runtime` und ueber die
 * Konfiguration einer `ai_provider`-Integration.
 *
 * Handgeschrieben und nicht ueber ein Zod-Schema aus `lib/schemas.ts`: die
 * beiden Dateien tragen eine gepflegte `any`-Ausnahme mit CI-Obergrenze
 * (`lib/schemas.ts` 7, `lib/types.ts` 15). Ein weiteres `any` dort wuerde eine
 * Zahl verschieben, die der CI-Job als Regression fallen laesst. Die Engstelle
 * ist deshalb hier, mit `unknown` statt `any` - dieselbe Entscheidung wie in
 * `lib/models.ts`.
 */

/** Der Host, unter dem Docker den Rechner der Lernenden erreicht. */
export const CONTAINER_HOST = "host.docker.internal";

/** Adressen, die aus dem Container **nicht** auf den Rechner zeigen. */
export const CONTAINER_LOOPBACK: readonly string[] = ["0.0.0.0", "::", "::1", "localhost"];

/**
 * Die Herkunft einer Antwort, ueber die die drei Services hinweg gleich
 * benannt wird: `local` (Server auf diesem Rechner), `provider` (Online) und
 * `fallback` (aus den Daten, ohne Modell).
 *
 * `ai` ist der **gespeicherte Altwert** des Tagescaches und bedeutet
 * inzwischen `provider`. Ein alter Eintrag, der als `fallback` erschiene,
 * waere eine Datenluege in genau dem Feld, das die Datenluege verhindern soll.
 */
export type AiSource = "local" | "provider" | "fallback";

/** `none` kommt nur bei der Dokumentenfrage vor: kein Treffer, keine Frage. */
export type AnswerSource = AiSource | "none";

export interface LocalRuntimePayload {
  kind: string;
  label: string;
  port: number;
  base_url: string;
  native_base_url: string;
  default_model: string;
  needs_api_key: boolean;
  note: string;
}

export interface AiRuntimePayload {
  runtimes: LocalRuntimePayload[];
  runtime_values: string[];
  runtime_labels: Record<string, string>;
  container_host: string;
  container_loopback_hosts: string[];
  container_trap_hint: string;
  stages: string[];
  local_first: boolean;
}

const UNKNOWN = "unbekannt";

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function asText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asBoolean(value: unknown): boolean {
  return value === true;
}

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.flatMap((eintrag) => (typeof eintrag === "string" ? [eintrag] : [])) : [];
}

function asLabels(value: unknown): Record<string, string> {
  const row = asRecord(value);
  if (!row) {
    return {};
  }
  return Object.fromEntries(
    Object.entries(row).flatMap(([schluessel, eintrag]) => {
      const text = asText(eintrag);
      return text ? [[schluessel, text] as [string, string]] : [];
    }),
  );
}

function asRuntimes(value: unknown): LocalRuntimePayload[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((eintrag): LocalRuntimePayload[] => {
    const row = asRecord(eintrag);
    const kind = asText(row?.kind);
    // Ohne `kind` kann die Karte den Knopf keinem Preset zuordnen - dann
    // lieber weglassen als eine halbe Zeile anzeigen.
    if (!kind) {
      return [];
    }
    return [
      {
        kind,
        label: asText(row?.label) ?? kind,
        port: asNumber(row?.port) ?? 0,
        base_url: asText(row?.base_url) ?? "",
        native_base_url: asText(row?.native_base_url) ?? "",
        default_model: asText(row?.default_model) ?? "",
        needs_api_key: asBoolean(row?.needs_api_key),
        note: asText(row?.note) ?? "",
      },
    ];
  });
}

/**
 * Engt `GET /api/v1/ai-runtime` auf das, was angezeigt wird.
 *
 * Fehlt ein Preset ganz, ist die Liste leer - und kein gerateter Ersatz. Die
 * Oberflaeche sagt dann "keine Vorschlaege", statt eine erfundene Adresse in
 * ein Formular zu schreiben.
 */
export function parseAiRuntime(payload: unknown): AiRuntimePayload | null {
  const root = asRecord(payload);
  if (!root) {
    return null;
  }
  return {
    runtimes: asRuntimes(root.runtimes),
    runtime_values: asStrings(root.runtime_values),
    runtime_labels: asLabels(root.runtime_labels),
    container_host: asText(root.container_host) ?? CONTAINER_HOST,
    container_loopback_hosts: asStrings(root.container_loopback_hosts),
    container_trap_hint: asText(root.container_trap_hint) ?? "",
    stages: asStrings(root.stages),
    local_first: asBoolean(root.local_first),
  };
}

/**
 * Bringt einen gelieferten `source` auf die heutige Bedeutung.
 *
 * Unbekanntes wird `fallback` und nicht `local`: eine erfundene *lokale*
 * Herkunft waere die schlimmere Luege, weil sie eine Datenschutzentscheidung
 * behauptet, die nicht stattgefunden hat.
 */
export function normalizeSource(value: unknown): AnswerSource {
  const text = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (text === "none") {
    return "none";
  }
  if (text === "ai") {
    return "provider";
  }
  if (text === "local" || text === "provider" || text === "fallback") {
    return text;
  }
  return "fallback";
}

export interface SourceLabel {
  /** Das deutsche Wort im Badge. */
  text: string;
  /** Zeigt das Badge, dass ein Modell geantwortet hat? */
  fromModel: boolean;
}

/**
 * Das Badge fuer eine Herkunft. Monochrom und ohne Farbcode: der Zustand steht
 * als Wort da, genau wie bei den uebrigen Karten.
 */
export function describeSource(source: AnswerSource, model?: string | null): SourceLabel {
  switch (source) {
    case "local":
      return { text: model ? `Lokales Modell · ${model}` : "Lokales Modell", fromModel: true };
    case "provider":
      return { text: model ? `KI-Antwort · ${model}` : "KI-Antwort", fromModel: true };
    case "none":
      return { text: t("Keine Treffer"), fromModel: false };
    default:
      return { text: t("Aus deinen Daten"), fromModel: false };
  }
}

/** Der Host aus einer URL, kleingeschrieben und ohne IPv6-Klammern. */
export function hostOf(url: string): string {
  const ohneLeer = url.trim();
  const mitProtokoll = /^[a-z][a-z0-9+.-]*:\/\//i.test(ohneLeer) ? ohneLeer : `http://${ohneLeer}`;
  try {
    const parsed = new URL(mitProtokoll);
    return parsed.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  } catch {
    return "";
  }
}

/** Zeigt diese Adresse aus dem Docker-Container auf den Container selbst? */
export function isContainerLoopback(url: string): boolean {
  const host = hostOf(url);
  if (!host) {
    return false;
  }
  return CONTAINER_LOOPBACK.includes(host) || host.startsWith("127.");
}

/** Ist der Server auf diesem Rechner oder in dessen Netz? */
export function isLocalServer(url: string): boolean {
  const host = hostOf(url);
  if (!host) {
    return false;
  }
  if (isContainerLoopback(url) || host === CONTAINER_HOST || host === "host.containers.internal") {
    return true;
  }
  if (host.endsWith(".local")) {
    return true;
  }
  return isPrivateAddress(host);
}

function isPrivateAddress(host: string): boolean {
  // Genau die Netze, in denen `ipaddress.is_private` auf der Python-Seite
  // ebenfalls greift. Eine erkennen statt sie aufzuzaehlen ist hier richtig,
  // weil IPv4-Bereiche in `String`-Vergleichen nicht zu schreiben sind.
  const v4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (v4) {
    const [, a, b] = v4.map(Number) as unknown as number[];
    if (a === 10) {
      return true;
    }
    if (a === 192 && b === 168) {
      return true;
    }
    if (a === 172 && b >= 16 && b <= 31) {
      return true;
    }
    if (a === 169 && b === 254) {
      return true;
    }
    return false;
  }
  return host.startsWith("fd") && host.includes(":");
}

/**
 * Die Container-taugliche Fassung einer lokalen Adresse, sonst `null`.
 *
 * Dieselbe Funktion wie `dockerHostFor` im Backend. Sie steht hier, weil die
 * Oberflaeche den Hinweis **vor** dem Speichern zeigen soll - nicht erst, wenn
 * der Verbindungstest rot war.
 */
export function dockerHostFor(url: string): string | null {
  if (!isContainerLoopback(url)) {
    return null;
  }
  try {
    const parsed = new URL(url.trim().startsWith("http") ? url.trim() : `http://${url.trim()}`);
    // Ohne Port gibt es nichts zu uebertragen - der Aufrufer soll nicht raten.
    if (!parsed.port) {
      return null;
    }
    return `${parsed.protocol}//${CONTAINER_HOST}:${parsed.port}`;
  } catch {
    return null;
  }
}

/**
 * Der Satz, der bei `localhost` im Formular steht - oder `null`, wenn die
 * Adresse richtig ist. Der haeufigste Konfigurationsfehler dieser ganzen Welle.
 */
export function containerTrapNote(url: string): string | null {
  const replacement = dockerHostFor(url);
  if (!replacement) {
    return null;
  }
  return t("Läuft der Lernraum in Docker, zeigt „{url}“ auf den Container, nicht auf deinen Rechner. Nimm {ersatz}.", { url: url.trim(), ersatz: replacement });
}

export interface RuntimeOption {
  value: string;
  label: string;
}

/** Der Wert, den der Backend immer akzeptiert. */
export const RUNTIME_AUTO = "auto";

/**
 * Die Auswahl „Laufzeit" für das Formular.
 *
 * Ein Wert pro erlaubtem ``runtime``, in der Reihenfolge des Servers. Fehlt ein
 * Label, wird der Wert selbst benannt - sichtbar genug, um den Tippfehler zu
 * sehen, statt ihn zu überspielen.
 *
 * `auto` ist ein **garantierter** Boden: antwortet ein Server ohne die Liste
 * (etwa ein älterer Stand), gibt es trotzdem mindestens eine Auswahl. Ein leeres
 * Auswahlfeld neben einem Pflichtfeld wäre eine Frage, die niemand beantworten
 * kann, und `auto` ist genau der Wert, den der Backend in jedem Fall annimmt.
 */
export function runtimeOptions(runtime: AiRuntimePayload): RuntimeOption[] {
  const werte = runtime.runtime_values.length > 0 ? runtime.runtime_values : [RUNTIME_AUTO];
  return werte.map((value) => ({
    value,
    label: runtime.runtime_labels[value] ?? value,
  }));
}

/** Das Preset zu einer Laufzeit, oder `null` fuer ``auto``. */
export function presetFor(runtime: AiRuntimePayload, kind: string): LocalRuntimePayload | null {
  return runtime.runtimes.find((preset) => preset.kind === kind) ?? null;
}

/**
 * Was beim Anwaehlen eines Presets in das Formular geschrieben wird.
 *
 * Beide Adressen kommen mit, weil die Oberflaeche nicht weiss, ob der
 * Lernraum gerade in Docker laeuft. `native` waehlt der Mensch - deshalb steht
 * es als eigener Knopf daneben und nicht als versteckter Default.
 */
export function presetFields(preset: LocalRuntimePayload, native = false): Record<string, string> {
  const base = native ? preset.native_base_url : preset.base_url;
  return {
    base_url: base,
    model: preset.default_model,
    runtime: preset.kind,
  };
}

/** Die Reihenfolge der Wege als Text fuer die Oberflaeche. */
export function describeStages(runtime: AiRuntimePayload): string[] {
  return runtime.stages.length > 0 ? runtime.stages.map(translateServerText) : [t("1. Kein KI-Server eingetragen.")];
}

/** Der Port als deutsche Anzeige, oder `null` wenn unbekannt. */
export function formatRuntimePort(port: number): string | null {
  return port > 0 ? String(port) : null;
}

export { UNKNOWN };
