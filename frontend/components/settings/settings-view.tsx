"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTheme } from "next-themes";
import {
  AlertTriangle,
  CalendarDays,
  CheckCircle2,
  Cloud,
  Copy,
  Download,
  FolderTree,
  Info,
  KeyRound,
  Link2,
  LoaderCircle,
  LogOut,
  Monitor,
  Moon,
  Plug,
  Plus,
  RefreshCw,
  Sparkles,
  Sun,
  Trash2,
  X,
} from "lucide-react";
import { BackupStatusCard } from "@/components/settings/backup-status-card";
import { ModelCatalogCard } from "@/components/settings/model-catalog-card";
import { NotificationSettingsCard } from "@/components/settings/notification-settings-card";
import { VoiceCatalogCard } from "@/components/settings/voice-catalog-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate, formatDateTime, getInitials } from "@/lib/format";
import {
  buildIntegrationWrite,
  fieldLabel,
  integrationConfigFields,
  isReadOnlyCredential,
  isSecretField,
  isSyncableKind,
  isWritingBlocked,
  needsAuthorization,
  syncLabelFor,
  validateIntegrationInput,
} from "@/lib/calendar-integrations";
import {
  containerTrapNote,
  describeStages,
  isLocalServer,
  presetFields,
  presetFor,
  runtimeOptions,
  type AiRuntimePayload,
} from "@/lib/ai-runtime";
import type {
  CalendarFeed,
  CalendarWriting,
  IntegrationCatalog,
  IntegrationRead,
  IntegrationSyncResult,
  IntegrationTestResult,
  UserResponse,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import type { IntegrationWrite } from "@/lib/api";
import type { NotificationApi, NotificationState } from "@/lib/use-notifications";

type BadgeVariant = "default" | "secondary" | "outline" | "success" | "warning" | "info" | "accent";

const KIND_ICONS: Record<string, typeof Plug> = {
  webuntis: Cloud,
  ai_provider: Sparkles,
  documents: FolderTree,
  ical: CalendarDays,
  google_calendar: CalendarDays,
  caldav: CalendarDays,
};

const STATUS_META: Record<string, { label: string; variant: BadgeVariant }> = {
  configured: { label: "Eingerichtet", variant: "success" },
  pending: { label: "Wartet auf Einwilligung", variant: "info" },
  error: { label: "Fehler", variant: "warning" },
  disabled: { label: "Deaktiviert", variant: "outline" },
};

const THEME_OPTIONS = [
  { value: "light", label: "Hell", icon: Sun },
  { value: "dark", label: "Dunkel", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const;

const FIELD_HINTS: Record<string, string> = {
  base_url: "Zum Beispiel https://schule.example.org",
  school: "KÃ¼rzel oder Name deiner Schule",
  username: "Deine Zugangsdaten aus dem Schulportal",
  password: "Bei iCloud das app-spezifische Passwort, nicht dein Apple-ID-Passwort",
  model: "Zum Beispiel llama3.2",
  api_key: "Leer lassen, wenn der Dienst keinen SchlÃ¼ssel braucht",
  path: "Relativ zum konfigurierten Dokumentordner",
  url: "Die abonnierte Adresse, zum Beispiel https://schule.example.org/kalender.ics",
  client_id: "Aus der Google-Konsole unter â€žZugangsdatenâ€œ",
  client_secret: "Bleibt verschlÃ¼sselt und verlÃ¤sst den Server nur bei Google",
  calendar_id: "â€žprimaryâ€œ oder die Id aus der Google-Konsole",
  redirect_uri:
    "Bei Google hinterlegen und hier genau so eintragen, zum Beispiel https://lernraum.example.org/api/backend/v1/integrations/1/oauth/callback",
  refresh_token: "Wird nach der Einwilligung automatisch gesetzt",
  token_scope: "Wird nach der Einwilligung automatisch gesetzt",
};

function configEntries(integration: IntegrationRead) {
  return Object.entries(integration.config).map(([key, value]) => ({
    key,
    label: fieldLabel(key),
    value: value === null || value === undefined ? "" : String(value),
  }));
}

function SettingsSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-4 w-96 max-w-full" />
      </div>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.85fr)]">
        <Skeleton className="h-[28rem] rounded-2xl" />
<div className="space-y-6">
            <Skeleton className="h-56 w-full rounded-2xl" />
            <Skeleton className="h-64 w-full rounded-2xl" />
            <Skeleton className="h-40 w-full rounded-2xl" />
          </div>
      </div>
    </div>
  );
}

interface AccountCardProps {
  user: UserResponse | null;
  onLogout: () => void;
  isLoggingOut: boolean;
}

function AccountCard({ user, onLogout, isLoggingOut }: AccountCardProps) {
  const [abmeldeOffen, setAbmeldeOffen] = useState(false);
  const displayName = user?.username ?? "Lernende:r";
  const details = [
    { label: "E-Mail", value: user?.email || "Nicht hinterlegt" },
    { label: "Dabei seit", value: formatDate(user?.created_at) },
    { label: "Letzte Anmeldung", value: formatDateTime(user?.last_login_at) },
    { label: "Zeitzone", value: user?.timezone || "Nicht gesetzt" },
  ];

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader className="flex flex-row items-start gap-3.5">
        <span className="flex h-12 w-12 shrink-0 items-center justify-center border border-border bg-primary text-sm font-extrabold text-primary-foreground">
          {getInitials(displayName)}
        </span>
        <div className="min-w-0 flex-1">
          <CardTitle className="truncate text-lg">{displayName}</CardTitle>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge variant={user?.is_admin ? "accent" : "secondary"}>{user?.is_admin ? "Administration" : "Lernkonto"}</Badge>
            {user?.is_active === false ? <Badge variant="warning">Deaktiviert</Badge> : null}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <dl className="grid gap-3 sm:grid-cols-2">
          {details.map((detail) => (
            <div key={detail.label} className="rounded-xl border border-border/70 bg-muted/40 px-3.5 py-3">
              <dt className="text-[11px] font-bold uppercase tracking-[0.14em] text-muted-foreground">{detail.label}</dt>
              <dd className="mt-1 truncate text-sm font-semibold">{detail.value}</dd>
            </div>
          ))}
        </dl>

        {/*
          `POST /v1/auth/logout` loescht nur das Session-Cookie. Die Daten
          bleiben, deshalb ist der Knopf erst nach einer Bestaetigung aktiv -
          sonst waere ein Fehlklick der ganze Abmeldevorgang.
        */}
        {user ? (
          <div className="flex flex-wrap items-center gap-2 border-t border-border/70 pt-4">
            {abmeldeOffen ? (
              <>
                <Button type="button" size="sm" variant="destructive" onClick={onLogout} disabled={isLoggingOut}>
                  {isLoggingOut ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <LogOut aria-hidden="true" />}
                  {isLoggingOut ? "Wird abgemeldet â€¦" : "Jetzt abmelden"}
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={() => setAbmeldeOffen(false)} disabled={isLoggingOut}>
                  Abbrechen
                </Button>
              </>
            ) : (
              <Button type="button" size="sm" variant="outline" onClick={() => setAbmeldeOffen(true)} aria-expanded={abmeldeOffen}>
                <LogOut aria-hidden="true" />
                Abmelden
              </Button>
            )}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ThemeCard() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setMounted(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader>
        <CardTitle>Darstellung</CardTitle>
        <CardDescription>WÃ¤hle, ob dein Lernraum hell, dunkel oder wie das System erscheint.</CardDescription>
      </CardHeader>
      <CardContent>
        {mounted ? (
          <div className="grid grid-cols-3 gap-2" role="group" aria-label="Farbschema">
            {THEME_OPTIONS.map((option) => {
              const Icon = option.icon;
              const active = theme === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setTheme(option.value)}
                  aria-pressed={active}
                  className={cn(
                    "flex min-h-20 flex-col items-center justify-center gap-1.5 rounded-xl border text-xs font-bold transition-colors active:opacity-80",
                    active
                      ? "border-primary/40 bg-primary/10 text-primary"
                      : "border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  <Icon className="h-5 w-5" aria-hidden="true" />
                  {option.label}
                </button>
              );
            })}
          </div>
        ) : (
          <Skeleton className="h-20 w-full" />
        )}
      </CardContent>
    </Card>
  );
}

interface IntegrationRowProps {
  integration: IntegrationRead;
  testResult: IntegrationTestResult | undefined;
  syncResult: IntegrationSyncResult | undefined;
  pendingAction: string | null;
  /** Läuft gerade die Einwilligung von Google? */
  canWriteRemotely: boolean;
  /** Der Katalog - die Quelle der Angabe, ob in dieser Art geschrieben werden kann. */
  catalog: IntegrationCatalog[];
  /** Die bekannten lokalen Laufzeiten, fuer den Hinweis an einer KI-Verbindung. */
  runtime: AiRuntimePayload | null;
  onToggle: (enabled: boolean) => void;
  onTest: () => void;
  onSync: () => void;
  onAuthorize: () => void;
  onDelete: () => void;
}

function IntegrationRow({
  integration,
  testResult,
  syncResult,
  pendingAction,
  canWriteRemotely,
  catalog,
  runtime,
  onToggle,
  onTest,
  onSync,
  onAuthorize,
  onDelete,
}: IntegrationRowProps) {

  const [confirming, setConfirming] = useState(false);
  const isTesting = pendingAction === `test:${integration.id}`;
  const isSyncing = pendingAction === `sync:${integration.id}`;
  const isAuthorizing = pendingAction === `authorize:${integration.id}`;
  const isUpdating = pendingAction === `update:${integration.id}`;
  const isDeleting = pendingAction === `delete:${integration.id}`;
  const isBusy = isTesting || isSyncing || isAuthorizing || isUpdating || isDeleting;
  const Icon = KIND_ICONS[integration.kind] ?? Plug;
  const status = STATUS_META[integration.status] ?? { label: integration.status, variant: "secondary" as BadgeVariant };
  const result = testResult ?? syncResult;
  const entries = configEntries(integration);
  const incomplete = integration.missing_fields.length > 0;
  const canSync = isSyncableKind(integration.kind) && integration.enabled && !incomplete && !isBusy;
  const syncLabel = syncLabelFor(integration.kind);
  /*
    Google braucht zwei Schritte: die Integration anlegen und sich dann bei
    Google einwilligen. `refresh_token` in den technischen Angaben ist das
    Merkmal dafÃ¼r - er erscheint erst, nachdem der RÃ¼ckweg durchlaufen ist. Ohne
    die Freigabe zum Schreiben startet der Server den Lauf gar nicht, deshalb
    der zweite Hinweis.
  */
  const awaitingConsent = needsAuthorization(integration);
  const writingBlocked = isWritingBlocked(integration.kind, catalog, canWriteRemotely);
  const conflicts = syncResult?.conflicts ?? 0;

  /*
    Zwei Hinweise an einer KI-Zeile, und beide stehen dort, wo man sie braucht:

    * `lokal` sagt, **wo** der Server laeuft. Das ist keine Kosmetik: davon
      haengt ab, ob eine Anfrage den Rechner verlaesst.
    * Der Container-Hinweis greift bei `localhost` in einer gespeicherten
      Adresse. Wer das einmal gespeichert hat, sieht ihn auch ohne Tippen -
      nicht erst, wenn der Verbindungstest rot wird.
  */
  const kiAdresse = integration.kind === "ai_provider" ? String(integration.config.base_url ?? "") : "";
  const kiLokal = kiAdresse ? isLocalServer(kiAdresse) : false;
  const kiTrap = containerTrapNote(kiAdresse);
  const kiStufen = runtime && kiAdresse ? describeStages(runtime) : [];

  return (
    <li className="rounded-2xl border border-border/70 bg-card/60 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Icon className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-bold">{integration.name}</p>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <Badge variant="outline">{integration.kind}</Badge>
              <Badge variant={status.variant}>{status.label}</Badge>
              {incomplete ? <Badge variant="warning">UnvollstÃ¤ndig</Badge> : null}
              {/*
                Das Badge sagt, **wo** der Server laeuft. Bei einem Online-Dienst
                ist das die entscheidende Information: dann verlaesst eine Frage
                den Rechner.
              */}
              {kiLokal ? <Badge variant="accent">Lokal</Badge> : null}
              {kiAdresse && !kiLokal ? <Badge variant="outline">Online</Badge> : null}
            </div>
          </div>
        </div>

        <button
          type="button"
          role="switch"
          aria-checked={integration.enabled}
          aria-label={`${integration.name} ${integration.enabled ? "deaktivieren" : "aktivieren"}`}
          disabled={isBusy}
          onClick={() => onToggle(!integration.enabled)}
          className={cn(
            "relative h-6 w-11 shrink-0 rounded-full transition-colors duration-fast disabled:opacity-50 active:opacity-80",
            integration.enabled ? "bg-primary" : "bg-muted",
          )}
        >
          <span
            className={cn(
              "absolute top-0.5 h-5 w-5 rounded-full border border-border bg-card transition-[left] duration-fast",
              integration.enabled ? "left-[22px]" : "left-0.5",
            )}
          />
        </button>
      </div>

      {entries.length > 0 ? (
        <dl className="mt-4 grid gap-2 sm:grid-cols-2">
          {entries.map((entry) => (
            <div key={entry.key} className="flex items-baseline justify-between gap-3 rounded-lg bg-muted/40 px-3 py-2">
              <dt className="text-xs font-bold uppercase tracking-[0.1em] text-muted-foreground">{entry.label}</dt>
              <dd className="truncate text-xs font-semibold">{entry.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
          {integration.has_credentials ? `Zugangsdaten gespeichert (${integration.credential_keys.join(", ")})` : "Keine Zugangsdaten"}
        </span>
        <span>Zuletzt getestet: {formatDateTime(integration.last_tested_at)}</span>
        {integration.last_sync_at ? <span>Zuletzt synchronisiert: {formatDateTime(integration.last_sync_at)}</span> : null}
      </div>

      {incomplete ? (
        <p className="mt-3 flex items-start gap-2 text-xs font-semibold text-amber-700 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          Es fehlen noch: {integration.missing_fields.map(fieldLabel).join(", ")}
        </p>
      ) : null}

      {kiTrap ? (
        <p className="mt-3 flex items-start gap-2 border border-foreground/25 bg-foreground/10 px-3 py-2 text-xs font-semibold leading-5">
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {kiTrap}
        </p>
      ) : null}

      {kiStufen.length > 0 ? (
        <ol className="mt-3 space-y-1 border-l-2 border-border pl-3 text-[11px] leading-4 text-muted-foreground">
          {kiStufen.map((stufe) => (
            <li key={stufe}>{stufe}</li>
          ))}
        </ol>
      ) : null}

      {/*
        Ein Konflikt ist der eine Fall, in dem ein Abruf **nichts** entschieden
        hat: die Lernende und die Gegenseite haben denselben Termin geÃ¤ndert.
        Er wird hier ausdrÃ¼cklich benannt, weil ein stillschweigend
        Ã¼bergangener Konflikt wie eine verlorene Ã„nderung aussieht.
      */}
      {conflicts > 0 ? (
        <p className="mt-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800 dark:border-amber-900/70 dark:bg-amber-950/50 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {conflicts === 1 ? "Ein Termin wurde" : `${conflicts} Termine wurden`} an beiden Seiten geÃ¤ndert und sind
          offen geblieben. Deine Fassung steht hier; entscheide sie in deinem Kalender neu.
        </p>
      ) : null}

      {writingBlocked && !awaitingConsent ? (
        <p className="mt-3 flex items-start gap-2 text-xs font-medium text-muted-foreground">
          <KeyRound className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          Gelesen wird ohne weiteres. Geschrieben wird erst mit der Zustimmung oben im Schalter.
        </p>
      ) : null}

      {integration.last_error ? (
        <p className="mt-3 rounded-xl border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive">
          {integration.last_error}
        </p>
      ) : null}

      {/*
        Der Container steht immer im DOM, nur sein Inhalt wechselt. Ein
        `role="status"`, das erst zusammen mit dem Text eingefuegt wird, wird
        von Screenreadern nicht angesagt - die Live-Region muss vorher da sein.
        Test- und Sync-Ergebnis teilen sich die Box, weil beide dieselbe
        Frage beantworten: hat es geklappt?
      */}
      <div className="mt-3" role="status" aria-live="polite">
        {result ? (
          <p
            className={cn(
              "flex items-start gap-2 rounded-xl border px-3 py-2 text-xs font-medium",
              result.ok
                ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/70 dark:bg-emerald-950/50 dark:text-emerald-300"
                : "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/70 dark:bg-amber-950/50 dark:text-amber-300",
            )}
          >
            {result.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />}
            {result.message}
          </p>
        ) : null}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" variant="outline" onClick={onTest} disabled={isBusy}>
          {isTesting ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <RefreshCw aria-hidden="true" />}
          Verbindung testen
        </Button>
        {awaitingConsent ? (
          <Button type="button" size="sm" onClick={onAuthorize} disabled={isBusy || incomplete}>
            {isAuthorizing ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Link2 aria-hidden="true" />}
            {isAuthorizing ? "Weiterleitung lÃ¤uft" : writingBlocked ? "Zustimmung zeigen, dann verbinden" : "Mit Google verbinden"}
          </Button>
        ) : null}
        {isSyncableKind(integration.kind) ? (
          <Button type="button" size="sm" onClick={onSync} disabled={!canSync}>
            {isSyncing ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Download aria-hidden="true" />}
            {isSyncing ? "LÃ¤uft" : syncLabel}
          </Button>
        ) : null}
        {confirming ? (
          <>
            <Button type="button" size="sm" variant="destructive" onClick={onDelete} disabled={isBusy}>
              {isDeleting ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Trash2 aria-hidden="true" />}
              Wirklich entfernen
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={isBusy}>
              Abbrechen
            </Button>
          </>
        ) : (
          <Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(true)} disabled={isBusy}>
            <Trash2 aria-hidden="true" />
            Entfernen
          </Button>
        )}
      </div>
    </li>
  );
}

interface IntegrationFormProps {
  catalog: IntegrationCatalog[];
  /** Die bekannten lokalen Laufzeiten. `null`, solange nichts geladen ist. */
  runtime: AiRuntimePayload | null;
  isSubmitting: boolean;
  onSubmit: (input: IntegrationWrite) => Promise<boolean>;
  onCancel: () => void;
}

function IntegrationForm({ catalog, runtime, isSubmitting, onSubmit, onCancel }: IntegrationFormProps) {
  const [kind, setKind] = useState(catalog[0]?.kind ?? "");
  const [name, setName] = useState(catalog[0]?.label ?? "");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  /** Wird nach einem Fehlversuch gesetzt, damit die Felder `aria-invalid` bekommen. */
  const [invalidFields, setInvalidFields] = useState<Set<string>>(new Set());

  const spec = useMemo(() => catalog.find((entry) => entry.kind === kind), [catalog, kind]);
  const credentialFields = spec ? [...spec.required_credentials, ...spec.optional_credentials] : [];
  /*
    Aus einer Gruppe wie `{url, path}` wird **ein** alternatives Feld-Set, nicht
    zwei Pflichtfelder. Zwei bedeutungslose Pflichtfelder, von denen eines
    reicht, wÃ¤ren eine Frage, die das Formular nicht beantworten kann - und
    `required_one_of` ist genau dafÃ¼r da. Die PrÃ¼fung selbst liegt in
    `validateIntegrationInput`, damit sie ohne Browser testbar ist.
  */
  const alternativeSets = useMemo(() => (spec?.required_one_of ?? []).map((group) => group.filter(Boolean)), [spec]);
  const requiredFields = spec ? [...spec.required_config, ...spec.required_credentials] : [];
  const configFields = spec ? integrationConfigFields(spec) : [];
  const formErrorId = "integration-form-error";

  /*
    Der KI-Server ist die einzige Art mit Vorschlaegen. Sie stehen **im**
    Formular und nicht in einem Hilfetext daneben: die Wahl zwischen
    `host.docker.internal` und `127.0.0.1` ist genau die Wahl, die man nicht
    treffen kann, wenn man sie sich nicht erklaeren laesst.
  */
  const istKiServer = kind === "ai_provider";
  const kiVorschlaege = runtime?.runtimes ?? [];
  const kiAuswahl = runtime ? runtimeOptions(runtime) : [];
  const trapHinweis = istKiServer ? containerTrapNote(config.base_url ?? "") : null;

  function handleKindChange(nextKind: string) {
    const nextSpec = catalog.find((entry) => entry.kind === nextKind);
    setKind(nextKind);
    setName(nextSpec?.label ?? "");
    setConfig({});
    setCredentials({});
    setFormError(null);
    setInvalidFields(new Set());
  }

  /** Uebernimmt die Felder eines Vorschlags - beide Adressen, je nach Bauart. */
  function uebernehmen(presetKind: string, nativ: boolean) {
    if (!runtime) {
      return;
    }
    const preset = presetFor(runtime, presetKind);
    if (!preset) {
      return;
    }
    const felder = presetFields(preset, nativ);
    setConfig((aktuell) => ({ ...aktuell, ...felder }));
    setName(preset.label);
    setFormError(null);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!spec) {
      return;
    }

    const fehler = validateIntegrationInput(spec, config, credentials);
    if (fehler) {
      setInvalidFields(new Set(fehler.invalid));
      setFormError(fehler.message);
      return;
    }

    setFormError(null);
    setInvalidFields(new Set());
    const created = await onSubmit(buildIntegrationWrite(spec, name, config, credentials));
    if (created) {
      setConfig({});
      setCredentials({});
      setInvalidFields(new Set());
    }
  }

  function renderField(field: string, bucket: "config" | "credentials") {
    const isRequired = requiredFields.includes(field);
    const isAlternative = alternativeSets.some((gruppe) => gruppe.includes(field));
    const value = bucket === "config" ? config[field] ?? "" : credentials[field] ?? "";
    const setter = bucket === "config" ? setConfig : setCredentials;
    const fieldId = `${bucket}-${kind}-${field}`;
    const invalid = invalidFields.has(field);
    const showError = invalid && formError !== null;
    /*
      `runtime` bekommt ein eigenes Auswahlfeld statt eines Texteingabefelds:
      die Werte sind eine geschlossene Liste aus dem Backend, und ein
      Tippfehler darin wuerde erst beim Speichern auffallen. Die Liste kommt
      aus `runtimeOptions`, nicht aus einer zweiten Liste hier.
    */
    if (istKiServer && field === "runtime") {
      return (
        <div key={field} className="space-y-2">
          <label className="text-sm font-bold" htmlFor={fieldId}>
            {fieldLabel(field)}
            <span className="ml-1 text-xs font-semibold text-muted-foreground">(optional)</span>
          </label>
          <select
            id={fieldId}
            name={field}
            value={value || "auto"}
            onChange={(event) => setConfig((aktuell) => ({ ...aktuell, runtime: event.target.value }))}
            disabled={isSubmitting}
            aria-describedby={`${fieldId}-hinweis`}
            className="flex h-11 w-full border border-input bg-card px-3.5 py-2 text-sm text-foreground transition-colors focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {kiAuswahl.map((auswahl) => (
              <option key={auswahl.value} value={auswahl.value}>
                {auswahl.label}
              </option>
            ))}
          </select>
          <p id={`${fieldId}-hinweis`} className="text-xs leading-5 text-muted-foreground">
            Leer lassen genügt: Dann erkennt der Lernraum die Art an der Adresse.
          </p>
        </div>
      );
    }
    return (
      <div key={field} className="space-y-2">
        <label className="text-sm font-bold" htmlFor={fieldId}>
          {fieldLabel(field)}
          {isRequired ? (
            <span className="ml-1 text-destructive">*</span>
          ) : (
            <span className="ml-1 text-xs font-semibold text-muted-foreground">
              {isAlternative ? "(alternativ)" : "(optional)"}
            </span>
          )}
        </label>
        <Input
          id={fieldId}
          name={field}
          type={isSecretField(field) ? "password" : "text"}
          autoComplete={isSecretField(field) ? "new-password" : "off"}
          placeholder={FIELD_HINTS[field] ?? ""}
          value={value}
          onChange={(event) => setter((current) => ({ ...current, [field]: event.target.value }))}
          required={isRequired}
          disabled={isSubmitting}
          aria-invalid={invalid || undefined}
          aria-describedby={showError ? formErrorId : undefined}
        />
      </div>
    );
  }

  if (!spec) {
    return null;
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="space-y-5 rounded-2xl border border-primary/20 bg-primary/5 p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-bold">Neue Verbindung</p>
          <p className="mt-1 text-xs text-muted-foreground">Zugangsdaten werden verschlÃ¼sselt gespeichert und nie im Browser abgelegt.</p>
        </div>
        <Button type="button" size="icon" variant="ghost" onClick={onCancel} disabled={isSubmitting} aria-label="Abbrechen">
          <X aria-hidden="true" />
        </Button>
      </div>

      <div className="space-y-2">
        <label className="text-sm font-bold" htmlFor="integration-kind">Dienst</label>
        <select
          id="integration-kind"
          name="kind"
          value={kind}
          onChange={(event) => handleKindChange(event.target.value)}
          disabled={isSubmitting}
          className="flex h-11 w-full rounded-xl border border-input bg-card px-3.5 py-2 text-sm text-foreground shadow-sm transition-colors focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {catalog.map((entry) => (
            <option key={entry.kind} value={entry.kind}>
              {entry.label}
            </option>
          ))}
        </select>
        <p className="text-xs leading-5 text-muted-foreground">{spec.description}</p>
      </div>

      {/*
        Die Vorschlaege stehen ueber den Feldern, nicht darunter: wer sie
        nicht sieht, tippt `localhost` und wartet auf einen roten Knopf.
      */}
      {istKiServer ? (
        <section className="space-y-3 border border-border/70 bg-muted/40 p-3.5" aria-label="Lokale KI-Server">
          <p className="text-xs font-bold uppercase tracking-[0.14em] text-muted-foreground">Läuft der Server auf deinem Rechner?</p>
          {kiVorschlaege.length > 0 ? (
            <>
              <ul className="space-y-2">
                {kiVorschlaege.map((preset) => (
                  <li key={preset.kind} className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-bold">{preset.label}</p>
                      <p className="text-[11px] leading-4 text-muted-foreground">{preset.note}</p>
                    </div>
                    <div className="flex shrink-0 gap-1.5">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => uebernehmen(preset.kind, false)}
                        disabled={isSubmitting}
                      >
                        Docker
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => uebernehmen(preset.kind, true)}
                        disabled={isSubmitting}
                      >
                        Ohne Docker
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
              <p className="text-[11px] leading-4 text-muted-foreground">
                „Docker“ trägt {runtime?.container_host ?? "host.docker.internal"} ein, „ohne Docker“ die lokale Adresse. Beides ist dieselbe
                Verbindung, nur aus einer anderen Bauart heraus.
              </p>
            </>
          ) : (
            <p className="text-xs leading-5 text-muted-foreground">
              Die Vorschläge konnten nicht geladen werden. Trage Adresse und Modell von Hand ein.
            </p>
          )}
          {runtime?.container_trap_hint ? (
            <p className="flex items-start gap-2 border-l-2 border-foreground/30 pl-3 text-[11px] leading-4 font-semibold">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {runtime.container_trap_hint}
            </p>
          ) : null}
        </section>
      ) : null}

      <div className="space-y-2">
        <label className="text-sm font-bold" htmlFor="integration-name">Name</label>
        <Input
          id="integration-name"
          name="name"
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          disabled={isSubmitting}
        />
      </div>

      {configFields.map((field) => renderField(field, "config"))}
      {alternativeSets.map((gruppe) => (
        <div key={gruppe.join("|")} className="space-y-2 border-l-2 border-border pl-3">
          <p className="text-xs font-semibold text-muted-foreground">Genau eine Angabe: {gruppe.map(fieldLabel).join(" oder ")}</p>
          {gruppe.map((field) => renderField(field, "config"))}
        </div>
      ))}
      {credentialFields.filter((field) => !isReadOnlyCredential(field)).map((field) => renderField(field, "credentials"))}

      {/*
        Der Hinweis erscheint, **während** getippt wird. Er steht nicht erst in
        der Fehlermeldung des Verbindungstests, weil dann schon jemand auf
        „Verbindung testen" geklickt hat und die richtige Antwort nicht mehr
        sieht - sondern nur, dass es nicht ging.
      */}
      {trapHinweis ? (
        <p className="flex items-start gap-2 border border-foreground/25 bg-foreground/10 px-3.5 py-3 text-xs font-semibold leading-5" role="status">
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {trapHinweis}
        </p>
      ) : null}

      {formError ? (
        <p id={formErrorId} className="rounded-xl border border-destructive/20 bg-destructive/10 px-3.5 py-3 text-sm font-medium text-destructive" role="alert">
          {formError}
        </p>
      ) : null}

      <Button type="submit" disabled={isSubmitting}>
        {isSubmitting ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Plus aria-hidden="true" />}
        {isSubmitting ? "Wird gespeichert â€¦" : "Verbindung speichern"}
      </Button>
    </form>
  );
}

interface IntegrationsCardProps {
  integrations: IntegrationRead[];
  catalog: IntegrationCatalog[];
  /** Die bekannten lokalen Laufzeiten aus `GET /v1/ai-runtime`. */
  runtime: AiRuntimePayload | null;
  pendingAction: string | null;
  testResults: Record<number, IntegrationTestResult>;
  syncResults: Record<number, IntegrationSyncResult>;
  isRefreshing: boolean;
  /** Darf in einer Kalenderquelle geschrieben werden? */
  canWriteRemotely: boolean;
  onRefresh: () => void;
  onAdd: (input: IntegrationWrite) => Promise<boolean>;
  onToggle: (integration: IntegrationRead, enabled: boolean) => void;
  onTest: (integration: IntegrationRead) => void;
  onSync: (integration: IntegrationRead) => void;
  onAuthorize: (integration: IntegrationRead) => void;
  onDelete: (integration: IntegrationRead) => void;
}

function IntegrationsCard({
  integrations,
  catalog,
  runtime,
  pendingAction,
  testResults,
  syncResults,
  isRefreshing,
  canWriteRemotely,
  onRefresh,
  onAdd,
  onToggle,
  onTest,
  onSync,
  onAuthorize,
  onDelete,
}: IntegrationsCardProps) {
  const [formOpen, setFormOpen] = useState(false);
  const isSubmitting = pendingAction === "create";

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>Integrationen</CardTitle>
          <CardDescription className="mt-1">
            Verbinde dein Schulportal, einen KI-Dienst, einen Dokumentordner oder einen Kalender.
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" size="sm" variant="outline" onClick={onRefresh} disabled={isRefreshing}>
            <RefreshCw className={cn(isRefreshing && "animate-spin")} aria-hidden="true" />
            Aktualisieren
          </Button>
          <Button type="button" size="sm" onClick={() => setFormOpen((open) => !open)} disabled={isSubmitting} aria-expanded={formOpen}>
            {formOpen ? <X aria-hidden="true" /> : <Plus aria-hidden="true" />}
            {formOpen ? "SchlieÃŸen" : "HinzufÃ¼gen"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {formOpen && catalog.length > 0 ? (
          <IntegrationForm
            catalog={catalog}
            runtime={runtime}
            isSubmitting={isSubmitting}
            onSubmit={async (input) => {
              const created = await onAdd(input);
              if (created) {
                setFormOpen(false);
              }
              return created;
            }}
            onCancel={() => setFormOpen(false)}
          />
        ) : null}

        {integrations.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border px-5 py-10 text-center">
            <Plug className="mx-auto h-8 w-8 text-muted-foreground" aria-hidden="true" />
            <p className="mt-3 text-sm font-bold">Noch nichts verbunden</p>
            <p className="mx-auto mt-1 max-w-sm text-xs leading-5 text-muted-foreground">
              Stundenplan, Aufgaben und Termine kannst du auch ohne Integration manuell pflegen. Verbundene Dienste
              liefern spÃ¤ter automatisch deine Lerninhalte.
            </p>
          </div>
        ) : (
          <ul className="space-y-4">
            {integrations.map((integration) => (
              <IntegrationRow
                key={integration.id}
                integration={integration}
                testResult={testResults[integration.id]}
                syncResult={syncResults[integration.id]}
                pendingAction={pendingAction}
                canWriteRemotely={canWriteRemotely}
                catalog={catalog}
                runtime={runtime}
                onToggle={(enabled) => onToggle(integration, enabled)}
                onTest={() => onTest(integration)}
                onSync={() => onSync(integration)}
                onAuthorize={() => onAuthorize(integration)}
                onDelete={() => onDelete(integration)}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Die Zustimmung, Termine in verbundene Kalender zu **schreiben**.
 *
 * Sie steht hier und nicht in der Zeile einer Integration, weil sie nicht an
 * einer hÃ¤ngt, sondern an der Frage, ob der Lernraum das Ã¼berhaupt darf. Und
 * sie ist **aus**, bis jemand sie einschaltet: Lesen genÃ¼gt, sobald eine Quelle
 * verbunden ist - die hat die Lernende ja selbst angegeben. Schreiben ist der
 * Fall, der eine ausdrÃ¼ckliche Entscheidung braucht.
 */
function CalendarWritingCard({
  writing,
  isSaving,
  onToggle,
}: {
  writing: CalendarWriting | null;
  isSaving: boolean;
  onToggle: (enabled: boolean) => void;
}) {
  const enabled = writing?.share_calendar_for_writing ?? false;

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader>
        <CardTitle>Termine in verbundene Kalender schreiben</CardTitle>
        <CardDescription className="mt-1">
          Ohne diese Zustimmung werden Kalender nur gelesen. Ein Abgleich in beide Richtungen braucht sie.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <label className="flex cursor-pointer items-start gap-3">
          <input
            type="checkbox"
            className="mt-1 h-4 w-4 shrink-0 accent-foreground"
            checked={enabled}
            disabled={isSaving || writing === null}
            onChange={(event) => onToggle(event.target.checked)}
          />
          <span className="min-w-0">
            <span className="block text-sm font-bold">
              {enabled ? "Freigegeben" : "Nicht freigegeben"}
            </span>
            <span className="mt-1 block text-xs text-muted-foreground">
              Gespeichert als <code className="font-mono">{writing?.preference_key ?? "share_calendar_for_writing"}</code>.
              Ein fehlender oder kaputter Eintrag bedeutet immer â€žausâ€œ.
            </span>
          </span>
        </label>

        {writing && writing.uses.length > 0 ? (
          <div className="space-y-1.5 border-t border-border/70 pt-4">
            <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-muted-foreground">Damit wird</p>
            <ul className="space-y-1">
              {writing.uses.map((use) => (
                <li key={use} className="text-xs text-muted-foreground">
                  {use}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/**
 * Die abonnierbare ICS-Adresse des Lernraums - der Weg nach drauÃŸen.
 *
 * Der SchlÃ¼ssel ist im Klartext ein Vollzugriff auf **diese** Kalenderdatei,
 * deshalb gibt es hier ausdrÃ¼cklich einen Knopf zum Erzeugen: Wer ihn drÃ¼ckt,
 * weiÃŸ, dass eine alte Adresse mitgeht. Ein still erneuertes Geheimnis wÃ¤re
 * keines, und der Knopf kostet nur einen Klick.
 */
function CalendarFeedCard({
  feed,
  isLoading,
  isRotating,
  onCreate,
}: {
  feed: CalendarFeed | null;
  isLoading: boolean;
  isRotating: boolean;
  onCreate: () => void;
}) {
  const [kopiert, setKopiert] = useState(false);

  function adresse() {
    if (!feed?.url_path || typeof window === "undefined") {
      return "";
    }
    return `${window.location.origin}${feed.url_path}`;
  }

  function kopieren() {
    const ziel = adresse();
    if (!ziel) {
      return;
    }
    void navigator.clipboard?.writeText(ziel).then(() => {
      setKopiert(true);
      window.setTimeout(() => setKopiert(false), 2000);
    });
  }

  if (isLoading) {
    return (
      <Card className="border-border/80 bg-card/90">
        <CardHeader>
          <CardTitle>Kalender abonnieren</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-16 w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader>
        <CardTitle>Kalender abonnieren</CardTitle>
        <CardDescription className="mt-1">
          Trage diese Adresse in Google Kalender, Apple Kalender oder Outlook ein, damit deine Termine dort erscheinen.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {feed?.active && adresse() ? (
          <>
            <p className="break-all border border-border bg-muted/40 px-3 py-2 font-mono text-xs">{adresse()}</p>
            <p className="text-xs text-muted-foreground">
              {feed.message} Ein neuer SchlÃ¼ssel macht diese Adresse sofort ungÃ¼ltig.
            </p>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">{feed?.message ?? "Es ist noch keine Adresse eingerichtet."}</p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" onClick={onCreate} disabled={isRotating}>
            {isRotating ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Plus aria-hidden="true" />}
            {feed?.active ? "Neuen SchlÃ¼ssel erzeugen" : "Adresse einrichten"}
          </Button>
          {feed?.active ? (
            <Button type="button" size="sm" variant="outline" onClick={kopieren}>
              {kopiert ? <CheckCircle2 aria-hidden="true" /> : <Copy aria-hidden="true" />}
              {kopiert ? "Kopiert" : "Adresse kopieren"}
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

export interface SettingsViewProps {
  user: UserResponse | null;
  integrations: IntegrationRead[];
  catalog: IntegrationCatalog[];
  /** Die bekannten lokalen KI-Laufzeiten. `null`, wenn die Abfrage ausfiel. */
  runtime: AiRuntimePayload | null;
  error: string | null;
  actionError: string | null;
  status: "loading" | "ready" | "error";
  pendingAction: string | null;
  testResults: Record<number, IntegrationTestResult>;
  syncResults: Record<number, IntegrationSyncResult>;
  /** Die Zustimmung zum Schreiben in verbundene Kalender. `null`, solange nichts geladen ist. */
  writing: CalendarWriting | null;
  /** Die abonnierbare ICS-Adresse. `null`, solange nichts geladen ist. */
  feed: CalendarFeed | null;
  notifications: NotificationApi & NotificationState;
  onRetry: () => void;
  onAdd: (input: IntegrationWrite) => Promise<boolean>;
  onToggle: (integration: IntegrationRead, enabled: boolean) => void;
  onTest: (integration: IntegrationRead) => void;
  onSync: (integration: IntegrationRead) => void;
  onAuthorize: (integration: IntegrationRead) => void;
  onDelete: (integration: IntegrationRead) => void;
  onToggleWriting: (enabled: boolean) => void;
  onCreateFeed: () => void;
  onLogout: () => void;
  isLoggingOut: boolean;
}

export function SettingsView({
  user,
  integrations,
  catalog,
  runtime,
  error,
  actionError,
  status,
  pendingAction,
  testResults,
  syncResults,
writing,
  feed,
  notifications,
  onRetry,
  onAdd,
  onToggle,
  onTest,
  onSync,
  onAuthorize,
  onDelete,
  onToggleWriting,
  onCreateFeed,
  onLogout,
  isLoggingOut,
}: SettingsViewProps) {
  if (status === "loading" && integrations.length === 0 && catalog.length === 0) {
    return <SettingsSkeleton />;
  }

  const canWriteRemotely = writing?.share_calendar_for_writing ?? false;

  return (
    <div className="space-y-6">
      <header>
        <p className="mb-2 text-sm font-bold uppercase tracking-[0.18em] text-primary">Dein Lernraum</p>
        <h1 className="text-3xl font-extrabold tracking-[-0.035em] sm:text-4xl">Einstellungen</h1>
        <p className="mt-2 text-sm font-semibold text-muted-foreground">Konto, Erscheinungsbild, Sicherung und deine verbundenen Dienste an einem Ort.</p>
      </header>

      {error ? (
        <div className="flex flex-col gap-3 rounded-2xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive sm:flex-row sm:items-center sm:justify-between" role="alert">
          <span>{error}</span>
          <Button type="button" size="sm" variant="outline" onClick={onRetry}>
            <RefreshCw aria-hidden="true" />
            Erneut versuchen
          </Button>
        </div>
      ) : null}

      {actionError ? (
        <p className="rounded-2xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm font-medium text-destructive" role="alert">
          {actionError}
        </p>
      ) : null}

      {/*
        Die Zustimmung steht **Ã¼ber** der Integrationsliste und nicht als Zeile
        darin: sie haengt an keiner einzelnen Verbindung. Wer sie sucht, soll sie
        finden, ohne erst eine Kalenderzeile auswaehlen zu muessen.
      */}
      <CalendarWritingCard writing={writing} isSaving={pendingAction === "writing"} onToggle={onToggleWriting} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.85fr)]">
        <IntegrationsCard
          integrations={integrations}
          catalog={catalog}
          runtime={runtime}
          pendingAction={pendingAction}
          testResults={testResults}
          syncResults={syncResults}
          isRefreshing={status === "loading"}
          canWriteRemotely={canWriteRemotely}
          onRefresh={onRetry}
          onAdd={onAdd}
          onToggle={onToggle}
          onTest={onTest}
          onSync={onSync}
          onAuthorize={onAuthorize}
          onDelete={onDelete}
        />
        <div className="space-y-6">
          <ModelCatalogCard />
          {/*
            Die Stimmen stehen **unter** dem Modellkatalog und nicht darin: eine
            Stimme ist eine Stimme *in einer* Sprache, ein Whisper-Modell hat
            nur eine Sprache. Zusammengelegt waere genau die Entscheidung unsichtbar,
            die man hier treffen muss.
          */}
          <VoiceCatalogCard />
          <AccountCard user={user} onLogout={onLogout} isLoggingOut={isLoggingOut} />
          <CalendarFeedCard
            feed={feed}
            isLoading={feed === null}
            isRotating={pendingAction === "feed"}
            onCreate={onCreateFeed}
          />
          <NotificationSettingsCard
            settings={notifications.settings}
            vapid={notifications.vapid}
            subscriptions={notifications.subscriptions}
            permission={notifications.permission}
            pushMoeglich={notifications.pushMoeglich}
            status={notifications.status}
            error={notifications.error}
            actionError={notifications.actionError}
            pending={notifications.pending}
            bericht={notifications.pruefbericht}
            zuletzt={notifications.zuletzt}
            onSpeichern={notifications.speichern}
            onErlaubnis={notifications.erlaubnisAnfragen}
            onPushAnmelden={notifications.pushAnmelden}
            onPushAbmelden={notifications.pushAbmelden}
            onPruefen={notifications.pruefen}
            onQuittieren={notifications.quittieren}
          />
          <BackupStatusCard />
          <ThemeCard />
        </div>
      </div>

      <footer className="flex flex-col items-center justify-between gap-2 border-t border-border/70 pt-5 text-center text-xs text-muted-foreground sm:flex-row sm:text-left">
        <span>Deine Angaben bleiben in deinem Lernraum.</span>
        <span className="inline-flex items-center gap-1.5">
          <Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
          Lernraum
        </span>
      </footer>
    </div>
  );
}


