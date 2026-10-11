import { formatNumber } from "./format";
import { t } from "./i18n";
/*
 * Reine Helfer fuer den Modellkatalog und den Download-Manager.
 *
 * Alles hier ist eine reine Funktion ueber die Antwort aus
 * `GET /api/v1/models` - kein React, kein `fetch`. Das hat zwei Gruende: Der
 * Teil ist damit unter `lib/**` testbar (Vitest laeuft nur dort, siehe
 * `vitest.config.mts`), und die Karte enthaelt keine Normalisierungslogik,
 * die ein zweites Mal erfunden werden koennte.
 *
 * Warum handgeschrieben und nicht ueber ein Zod-Schema aus `lib/schemas.ts`:
 * Die Dateien `lib/schemas.ts` und `lib/types.ts` tragen in dieser Welle eine
 * bewusst gepflegte Ausnahme (`no-explicit-any`, siehe `.github/ci/known-failures.mjs`).
 * Ein weiteres `any` dort wuerde eine Zahl verschieben, die der CI-Job als
 * Regression faellt. Die Engstelle ist deshalb hier, mit `unknown` statt `any`.
 */

export type ModelStatus = "not_downloaded" | "downloading" | "installed" | "interrupted" | "failed";

export interface ModelStatusPayload {
  status: ModelStatus | string;
  bytes_downloaded: number;
  bytes_total: number;
  progress_percent: number;
  speed_bytes_per_second: number | null;
  current_file: string | null;
  verified: boolean;
  message: string;
}

export interface ModelFilePayload {
  name: string;
  size_bytes: number;
  sha256: string;
}

export interface ModelPayload {
  id: string;
  label: string;
  task: string;
  family: string;
  description: string;
  license: string;
  source_repository: string;
  source_page: string;
  parameter_count: number | null;
  quantization: string | null;
  context_length: number | null;
  size_bytes: number;
  ram_estimate_bytes: number;
  ram_estimate_gib: number;
  ram_is_estimate: boolean;
  ram_basis: string;
  files: ModelFilePayload[];
  status: ModelStatusPayload;
}

export interface ModelStoragePayload {
  available: boolean;
  writable: boolean;
  free_bytes: number | null;
  message: string;
}

export interface ModelCatalogPayload {
  models: ModelPayload[];
  storage: ModelStoragePayload;
  shared_installation: boolean;
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

function asFiles(value: unknown): ModelFilePayload[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((entry) => {
    const row = asRecord(entry);
    const name = asText(row?.name);
    const size = asNumber(row?.size_bytes);
    const sha256 = asText(row?.sha256);
    return name && size !== null && sha256 ? [{ name, size_bytes: size, sha256 }] : [];
  });
}

function asStatus(value: unknown): ModelStatusPayload {
  const row = asRecord(value);
  const status = asText(row?.status) ?? "not_downloaded";
  return {
    status,
    bytes_downloaded: asNumber(row?.bytes_downloaded) ?? 0,
    bytes_total: asNumber(row?.bytes_total) ?? 0,
    progress_percent: asNumber(row?.progress_percent) ?? 0,
    speed_bytes_per_second: asNumber(row?.speed_bytes_per_second),
    current_file: asText(row?.current_file),
    verified: asBoolean(row?.verified),
    message: asText(row?.message) ?? "",
  };
}

/**
 * Engt die Antwort auf das, was angezeigt wird.
 *
 * Fehlt ein Feld, ist es `null` oder 0 und damit "unbekannt" - niemals ein
 * geratener Wert. Ein Katalogeintrag ohne `id` wird verworfen, weil die Karte
 * ihn sonst nicht eindeutig einem Knopf zuordnen koennte.
 */
export function parseModelCatalog(payload: unknown): ModelCatalogPayload | null {
  const root = asRecord(payload);
  if (!root) {
    return null;
  }
  const rawModels = Array.isArray(root.models) ? root.models : [];
  const models = rawModels.flatMap((entry): ModelPayload[] => {
    const row = asRecord(entry);
    const id = asText(row?.id);
    if (!id) {
      return [];
    }
    return [
      {
        id,
        label: asText(row?.label) ?? id,
        task: asText(row?.task) ?? UNKNOWN,
        family: asText(row?.family) ?? UNKNOWN,
        description: asText(row?.description) ?? "",
        license: asText(row?.license) ?? UNKNOWN,
        source_repository: asText(row?.source_repository) ?? UNKNOWN,
        source_page: asText(row?.source_page) ?? "",
        parameter_count: asNumber(row?.parameter_count),
        quantization: asText(row?.quantization),
        context_length: asNumber(row?.context_length),
        size_bytes: asNumber(row?.size_bytes) ?? 0,
        ram_estimate_bytes: asNumber(row?.ram_estimate_bytes) ?? 0,
        ram_estimate_gib: asNumber(row?.ram_estimate_gib) ?? 0,
        ram_is_estimate: asBoolean(row?.ram_is_estimate),
        ram_basis: asText(row?.ram_basis) ?? "",
        files: asFiles(row?.files),
        status: asStatus(row?.status),
      },
    ];
  });
  const rawStorage = asRecord(root.storage);
  const storage = {
    available: asBoolean(rawStorage?.available),
    writable: asBoolean(rawStorage?.writable),
    free_bytes: asNumber(rawStorage?.free_bytes),
    message: asText(rawStorage?.message) ?? "",
  };
  return { models, storage, shared_installation: asBoolean(root.shared_installation) };
}

/** Bytes in einer deutschen Einheit. Nicht gerundet, sondern abgeschnitten. */
export function formatModelBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes <= 0) {
    return UNKNOWN;
  }
  if (bytes < 1024) {
    return `${Math.round(bytes)} Byte`;
  }
  const mib = bytes / (1024 * 1024);
  if (mib < 1024) {
    return `${formatNumber(mib, 1)} MiB`;
  }
  return `${formatNumber(mib / 1024, 1)} GiB`;
}

/** Der Zustand als deutsche Beschriftung. Kein Farbcode, nur ein Wort. */
export function describeModelStatus(status: string): string {
  switch (status) {
    case "installed":
      return t("Geladen");
    case "downloading":
      return t("Wird geladen");
    case "interrupted":
      return t("Unterbrochen");
    case "failed":
      return t("Fehlgeschlagen");
    default:
      return t("Nicht geladen");
  }
}

/** Wie viel des Modells schon da ist, immer zwischen 0 und 100. */
export function modelProgress(status: Pick<ModelStatusPayload, "bytes_downloaded" | "bytes_total">): number {
  if (!Number.isFinite(status.bytes_total) || status.bytes_total <= 0) {
    return 0;
  }
  const anteil = (status.bytes_downloaded / status.bytes_total) * 100;
  if (!Number.isFinite(anteil)) {
    return 0;
  }
  return Math.min(100, Math.max(0, Math.round(anteil * 10) / 10));
}

/** Ob der Knopf „herunterladen" gerade etwas ausloest. */
export function canStartModelDownload(status: ModelStatusPayload): boolean {
  return status.status !== "downloading";
}

/** Ob „abbrechen" etwas zu tun hat. */
export function canCancelModelDownload(status: ModelStatusPayload): boolean {
  return status.status === "downloading";
}

/** Ob „entfernen" etwas zu tun hat - auch ein Teilstand loescht. */
export function canDeleteModel(status: ModelStatusPayload): boolean {
  return status.status !== "downloading" && (status.status !== "not_downloaded" || status.bytes_downloaded > 0);
}

/** Die Aufgabenbezeichnung im deutschen Satz. */
export function describeModelTask(task: string): string {
  switch (task) {
    case "text_generation":
      return t("Text");
    case "speech_to_text":
      return t("Sprache zu Text");
    case "text_to_speech":
      return t("Text zu Sprache");
    case "embedding":
      return t("Einbettungen");
    case "image_to_text":
      return t("Bild zu Text");
    default:
      return task;
  }
}

/** Passt das Modell auf den freien Speicher? Die RAM-Zahl ist eine Schaetzung. */
export function modelFitsFreeSpace(bytes: number, freeBytes: number | null): boolean | null {
  if (freeBytes === null || freeBytes === undefined || freeBytes <= 0) {
    return null;
  }
  return bytes <= freeBytes;
}
