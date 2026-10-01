"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Clock3, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { apiRequest } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/*
 * Der Backup-Stand kommt aus `GET /api/v1/backups/status`, das liest die
 * Statusdatei des Workers. Der Aufruf laeuft ueber `apiRequest` aus
 * `@/lib/api`, damit Praefix, Sitzungs-Cookie und Fehlermeldungen nicht zum
 * zweiten Mal erfunden werden. Der Endpunkt ist read-only: hier gibt es
 * bewusst keinen Knopf, der ein Backup ausloest, weil der Zeitplan im Worker
 * laeuft und ein zweiter Weg dorthin ein zweiter Zustand waere.
 *
 * Zwei Regeln fuer die Anzeige: Es wird nichts erfunden. Was der Worker nicht
 * weiss, steht als "unbekannt" da, und ein nicht erreichbarer Status ist eine
 * erklaerte Luecke mit Klartext - keine gruene Ampel und kein Datum aus dem
 * Naeherungswert.
 */

interface BackupRun {
  enabled: boolean;
  runnerAvailable: boolean;
  schedule: string | null;
  timezone: string | null;
  retentionDays: number | null;
  directoryWritable: boolean | null;
  pgDump: string | null;
  lastSuccessfulAt: string | null;
  nextDueAt: string | null;
  lastFile: string | null;
  lastSizeBytes: number | null;
  keptFiles: number | null;
  lastError: string | null;
}

interface BackupStatus {
  available: boolean;
  message: string;
  backup: BackupRun | null;
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

function asBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

/**
 * Engt die Antwort auf das, was angezeigt wird.
 *
 * Bewusst handgeschrieben statt ueber ein Zod-Schema: `lib/` gehoert in dieser
 * Welle einem anderen Abschnitt. Fehlt ein Feld, ist es `null` und damit
 * "unbekannt" - niemals ein geratener Wert.
 */
function parseBackupStatus(payload: unknown): BackupStatus | null {
  const root = asRecord(payload);
  if (!root) {
    return null;
  }
  const rawBackup = asRecord(root.backup);
  const message = asText(root.message) ?? "Der Worker hat nichts zum Backup gemeldet.";
  if (!rawBackup) {
    return { available: asBoolean(root.available) ?? false, message, backup: null };
  }
  return {
    available: asBoolean(root.available) ?? false,
    message,
    backup: {
      enabled: asBoolean(rawBackup.enabled) ?? false,
      runnerAvailable: asBoolean(rawBackup.runner_available) ?? false,
      schedule: asText(rawBackup.schedule),
      timezone: asText(rawBackup.timezone),
      retentionDays: asNumber(rawBackup.retention_days),
      directoryWritable: asBoolean(rawBackup.directory_writable),
      pgDump: asText(rawBackup.pg_dump),
      lastSuccessfulAt: asText(rawBackup.last_successful_at),
      nextDueAt: asText(rawBackup.next_due_at),
      lastFile: asText(rawBackup.last_file),
      lastSizeBytes: asNumber(rawBackup.last_size_bytes),
      keptFiles: asNumber(rawBackup.kept_files),
      lastError: asText(rawBackup.last_error),
    },
  };
}

function formatBytes(bytes: number | null) {
  if (bytes === null || bytes <= 0) {
    return UNKNOWN;
  }
  if (bytes < 1024) {
    return `${bytes} Byte`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1).replace(".", ",")} KiB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MiB`;
}

function describeError(cause: unknown) {
  if (cause instanceof Error && cause.message) {
    return cause.message;
  }
  return "Der Backup-Status konnte nicht geladen werden.";
}

export function BackupStatusCard() {
  const requestId = useRef(0);
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const load = useCallback(async () => {
    const currentRequest = ++requestId.current;
    setIsLoading((current) => current || true);
    try {
      const payload = parseBackupStatus(await apiRequest("/v1/backups/status"));
      if (currentRequest !== requestId.current) {
        return;
      }
      setStatus(payload);
      setError(payload ? null : "Die Antwort des Lernraums war nicht lesbar.");
    } catch (cause) {
      if (currentRequest !== requestId.current) {
        return;
      }
      setStatus(null);
      setError(describeError(cause));
    } finally {
      if (currentRequest === requestId.current) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    // `react-hooks/set-state-in-effect` (Fehlerregel in eslint-config-next) fasst
    // jeden `setState` im Effektkoerper als kaskadierendes Rendern auf. Der erste
    // Abruf laeuft deshalb einen Tick spaeter - dasselbe Muster wie in
    // `lib/use-now.ts`.
    const initial = window.setTimeout(() => void load(), 0);
    return () => {
      window.clearTimeout(initial);
      requestId.current += 1;
    };
  }, [load]);

  const backup = status?.backup ?? null;
  const running = Boolean(backup?.enabled && backup.runnerAvailable);

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>Sicherung</CardTitle>
          <CardDescription className="mt-1">
            Der Worker legt regelmäßig einen Dump der Datenbank ab. Ein Backup sichert nicht die hochgeladenen Dateien.
          </CardDescription>
        </div>
        <Button type="button" size="sm" variant="outline" onClick={() => void load()} disabled={isLoading}>
          <RefreshCw className={cn(isLoading && "animate-spin")} aria-hidden="true" />
          Aktualisieren
        </Button>
      </CardHeader>

      <CardContent className="space-y-4">
        {error ? (
          <div className="rounded-md border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm font-medium text-destructive" role="alert">
            <span className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              {error}
            </span>
          </div>
        ) : null}

        {!error && isLoading && !status ? (
          <div className="space-y-3">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : null}

        {!error && status && !status.available ? (
          <p className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-800 dark:border-amber-900/70 dark:bg-amber-950/50 dark:text-amber-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            {status.message}
          </p>
        ) : null}

        {!error && backup ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              {running ? (
                <Badge variant="success">
                  <CheckCircle2 className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                  Läuft
                </Badge>
              ) : (
                <Badge variant="outline">Nicht aktiv</Badge>
              )}
              {backup.directoryWritable === false ? <Badge variant="warning">Ablage nicht beschreibbar</Badge> : null}
            </div>

            <dl className="grid gap-2 sm:grid-cols-2">
              <Detail label="Zeitplan" value={backup.schedule ? `${backup.schedule}${backup.timezone ? ` (${backup.timezone})` : ""}` : UNKNOWN} />
              <Detail label="Aufbewahrung" value={backup.retentionDays === null ? UNKNOWN : `${backup.retentionDays} Tage`} />
              <Detail label="Letzte Sicherung" value={formatDateTime(backup.lastSuccessfulAt)} />
              <Detail
                label="Nächster Termin"
                value={
                  <span className="inline-flex items-center gap-1.5">
                    <Clock3 className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                    {formatDateTime(backup.nextDueAt)}
                  </span>
                }
              />
              <Detail label="Letzte Datei" value={backup.lastFile ?? UNKNOWN} />
              <Detail label="Größe" value={formatBytes(backup.lastSizeBytes)} />
              <Detail
                label="Sicherungen auf dem Datenträger"
                value={backup.keptFiles === null ? UNKNOWN : `${backup.keptFiles} Dateien`}
              />
              <Detail label="Werkzeug" value={backup.pgDump ?? UNKNOWN} />
            </dl>

            {backup.directoryWritable === false ? (
              <p className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-xs font-semibold text-amber-800 dark:border-amber-900/70 dark:bg-amber-950/50 dark:text-amber-200">
                Der Worker kann den Sicherungsordner nicht beschreiben. Im Container muss der Backup-Pfad read/write
                eingebunden sein, sonst schlägt jeder Lauf fehl.
              </p>
            ) : null}

            {backup.lastError ? (
              <p className="rounded-md border border-destructive/20 bg-destructive/10 px-4 py-3 text-xs font-medium text-destructive">
                Letzter Lauf fehlgeschlagen: {backup.lastError}
              </p>
            ) : null}
          </>
        ) : null}

        {!error && !backup && !isLoading && !status ? (
          <p className="text-sm text-muted-foreground">Es liegen keine Angaben zum Backup vor.</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

interface DetailProps {
  label: string;
  value: string | ReactNode;
}

function Detail({ label, value }: DetailProps) {
  return (
    <div className="rounded-md border border-border/70 bg-muted/40 px-3.5 py-3">
      <dt className="text-[11px] font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-1 truncate text-sm font-semibold">{value}</dd>
    </div>
  );
}
