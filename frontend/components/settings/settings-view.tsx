"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTheme } from "next-themes";
import {
  AlertTriangle,
  CheckCircle2,
  Cloud,
  Download,
  FolderTree,
  KeyRound,
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate, formatDateTime, getInitials } from "@/lib/format";
import type {
  IntegrationCatalog,
  IntegrationRead,
  IntegrationSyncResult,
  IntegrationTestResult,
  UserResponse,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import type { IntegrationWrite } from "@/lib/api";

type BadgeVariant = "default" | "secondary" | "outline" | "success" | "warning" | "info" | "accent";

const FIELD_LABELS: Record<string, string> = {
  base_url: "Basis-URL",
  school: "Schule",
  username: "Benutzername",
  password: "Passwort",
  model: "Modell",
  api_key: "API-Schlüssel",
  path: "Ablageort",
};

const FIELD_HINTS: Record<string, string> = {
  base_url: "Zum Beispiel https://schule.example.org",
  school: "Kürzel oder Name deiner Schule",
  username: "Deine Zugangsdaten aus dem Schulportal",
  model: "Zum Beispiel llama3.2",
  api_key: "Leer lassen, wenn der Dienst keinen Schlüssel braucht",
  path: "Relativ zum konfigurierten Dokumentordner",
};

const SECRET_FIELDS = new Set(["password", "api_key"]);

const KIND_ICONS: Record<string, typeof Plug> = {
  webuntis: Cloud,
  ai_provider: Sparkles,
  documents: FolderTree,
};

const STATUS_META: Record<string, { label: string; variant: BadgeVariant }> = {
  configured: { label: "Eingerichtet", variant: "success" },
  pending: { label: "Ausstehend", variant: "info" },
  error: { label: "Fehler", variant: "warning" },
  disabled: { label: "Deaktiviert", variant: "outline" },
};

const THEME_OPTIONS = [
  { value: "light", label: "Hell", icon: Sun },
  { value: "dark", label: "Dunkel", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const;

function fieldLabel(field: string) {
  return FIELD_LABELS[field] ?? field;
}

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
          <Skeleton className="h-56 rounded-2xl" />
          <Skeleton className="h-40 rounded-2xl" />
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
                  {isLoggingOut ? "Wird abgemeldet …" : "Jetzt abmelden"}
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
        <CardDescription>Wähle, ob dein Lernraum hell, dunkel oder wie das System erscheint.</CardDescription>
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
  onToggle: (enabled: boolean) => void;
  onTest: () => void;
  onSync: () => void;
  onDelete: () => void;
}

function IntegrationRow({ integration, testResult, syncResult, pendingAction, onToggle, onTest, onSync, onDelete }: IntegrationRowProps) {
  const [confirming, setConfirming] = useState(false);
  const isTesting = pendingAction === `test:${integration.id}`;
  const isSyncing = pendingAction === `sync:${integration.id}`;
  const isUpdating = pendingAction === `update:${integration.id}`;
  const isDeleting = pendingAction === `delete:${integration.id}`;
  const isBusy = isTesting || isSyncing || isUpdating || isDeleting;
  const Icon = KIND_ICONS[integration.kind] ?? Plug;
  const status = STATUS_META[integration.status] ?? { label: integration.status, variant: "secondary" as BadgeVariant };
  const result = testResult ?? syncResult;
  const entries = configEntries(integration);
  const incomplete = integration.missing_fields.length > 0;
  const canSync = ["webuntis", "documents"].includes(integration.kind) && integration.enabled && !incomplete && !isBusy;
  const syncLabel = integration.kind === "documents" ? "Dokumente indexieren" : "Stundenplan aktualisieren";

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
              {incomplete ? <Badge variant="warning">Unvollständig</Badge> : null}
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
        {["webuntis", "documents"].includes(integration.kind) ? (
          <Button type="button" size="sm" onClick={onSync} disabled={!canSync}>
            {isSyncing ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Download aria-hidden="true" />}
            {isSyncing ? "Wird importiert" : syncLabel}
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
  isSubmitting: boolean;
  onSubmit: (input: IntegrationWrite) => Promise<boolean>;
  onCancel: () => void;
}

function IntegrationForm({ catalog, isSubmitting, onSubmit, onCancel }: IntegrationFormProps) {
  const [kind, setKind] = useState(catalog[0]?.kind ?? "");
  const [name, setName] = useState(catalog[0]?.label ?? "");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  /** Wird nach einem Fehlversuch gesetzt, damit die Felder `aria-invalid` bekommen. */
  const [invalidFields, setInvalidFields] = useState<Set<string>>(new Set());

  const spec = useMemo(() => catalog.find((entry) => entry.kind === kind), [catalog, kind]);
  const credentialFields = spec ? [...spec.required_credentials, ...spec.optional_credentials] : [];
  const requiredFields = spec ? [...spec.required_config, ...spec.required_credentials] : [];
  const formErrorId = "integration-form-error";

  function handleKindChange(nextKind: string) {
    const nextSpec = catalog.find((entry) => entry.kind === nextKind);
    setKind(nextKind);
    setName(nextSpec?.label ?? "");
    setConfig({});
    setCredentials({});
    setFormError(null);
    setInvalidFields(new Set());
  }

  function collect(values: Record<string, string>, fields: string[]) {
    return fields.reduce<Record<string, string>>((result, field) => {
      const value = values[field]?.trim();
      if (value) {
        result[field] = value;
      }
      return result;
    }, {});
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!spec) {
      return;
    }

    const combined = { ...config, ...credentials };
    const missing = requiredFields.filter((field) => !combined[field]?.trim());
    if (missing.length > 0) {
      setInvalidFields(new Set(missing));
      setFormError(`Diese Angaben fehlen noch: ${missing.map(fieldLabel).join(", ")}.`);
      return;
    }

    setFormError(null);
    setInvalidFields(new Set());
    const created = await onSubmit({
      kind: spec.kind,
      name: name.trim() || spec.label,
      config: collect(config, spec.required_config),
      credentials: collect(credentials, credentialFields),
    });
    if (created) {
      setConfig({});
      setCredentials({});
      setInvalidFields(new Set());
    }
  }

  function renderField(field: string, bucket: "config" | "credentials") {
    const isRequired = requiredFields.includes(field);
    const value = bucket === "config" ? config[field] ?? "" : credentials[field] ?? "";
    const setter = bucket === "config" ? setConfig : setCredentials;
    const fieldId = `${bucket}-${kind}-${field}`;
    const invalid = invalidFields.has(field);
    const showError = invalid && formError !== null;
    return (
      <div key={field} className="space-y-2">
        <label className="text-sm font-bold" htmlFor={fieldId}>
          {fieldLabel(field)}
          {isRequired ? <span className="ml-1 text-destructive">*</span> : <span className="ml-1 text-xs font-semibold text-muted-foreground">(optional)</span>}
        </label>
        <Input
          id={fieldId}
          name={field}
          type={SECRET_FIELDS.has(field) ? "password" : "text"}
          autoComplete={SECRET_FIELDS.has(field) ? "new-password" : "off"}
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
          <p className="mt-1 text-xs text-muted-foreground">Zugangsdaten werden verschlüsselt gespeichert und nie im Browser abgelegt.</p>
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

      {spec.required_config.map((field) => renderField(field, "config"))}
      {credentialFields.map((field) => renderField(field, "credentials"))}

      {formError ? (
        <p id={formErrorId} className="rounded-xl border border-destructive/20 bg-destructive/10 px-3.5 py-3 text-sm font-medium text-destructive" role="alert">
          {formError}
        </p>
      ) : null}

      <Button type="submit" disabled={isSubmitting}>
        {isSubmitting ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Plus aria-hidden="true" />}
        {isSubmitting ? "Wird gespeichert …" : "Verbindung speichern"}
      </Button>
    </form>
  );
}

interface IntegrationsCardProps {
  integrations: IntegrationRead[];
  catalog: IntegrationCatalog[];
  pendingAction: string | null;
  testResults: Record<number, IntegrationTestResult>;
  syncResults: Record<number, IntegrationSyncResult>;
  isRefreshing: boolean;
  onRefresh: () => void;
  onAdd: (input: IntegrationWrite) => Promise<boolean>;
  onToggle: (integration: IntegrationRead, enabled: boolean) => void;
  onTest: (integration: IntegrationRead) => void;
  onSync: (integration: IntegrationRead) => void;
  onDelete: (integration: IntegrationRead) => void;
}

function IntegrationsCard({
  integrations,
  catalog,
  pendingAction,
  testResults,
  syncResults,
  isRefreshing,
  onRefresh,
  onAdd,
  onToggle,
  onTest,
  onSync,
  onDelete,
}: IntegrationsCardProps) {
  const [formOpen, setFormOpen] = useState(false);
  const isSubmitting = pendingAction === "create";

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>Integrationen</CardTitle>
          <CardDescription className="mt-1">Verbinde dein Schulportal, einen KI-Dienst oder einen Dokumentordner.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" size="sm" variant="outline" onClick={onRefresh} disabled={isRefreshing}>
            <RefreshCw className={cn(isRefreshing && "animate-spin")} aria-hidden="true" />
            Aktualisieren
          </Button>
          <Button type="button" size="sm" onClick={() => setFormOpen((open) => !open)} disabled={isSubmitting} aria-expanded={formOpen}>
            {formOpen ? <X aria-hidden="true" /> : <Plus aria-hidden="true" />}
            {formOpen ? "Schließen" : "Hinzufügen"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {formOpen && catalog.length > 0 ? (
          <IntegrationForm
            catalog={catalog}
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
              Stundenplan und Aufgaben kannst du auch ohne Integration manuell pflegen. Verbundene Dienste liefern später automatisch deine Lerninhalte.
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
                onToggle={(enabled) => onToggle(integration, enabled)}
                onTest={() => onTest(integration)}
                onSync={() => onSync(integration)}
                onDelete={() => onDelete(integration)}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export interface SettingsViewProps {
  user: UserResponse | null;
  integrations: IntegrationRead[];
  catalog: IntegrationCatalog[];
  error: string | null;
  actionError: string | null;
  status: "loading" | "ready" | "error";
  pendingAction: string | null;
  testResults: Record<number, IntegrationTestResult>;
  syncResults: Record<number, IntegrationSyncResult>;
  onRetry: () => void;
  onAdd: (input: IntegrationWrite) => Promise<boolean>;
  onToggle: (integration: IntegrationRead, enabled: boolean) => void;
  onTest: (integration: IntegrationRead) => void;
  onSync: (integration: IntegrationRead) => void;
  onDelete: (integration: IntegrationRead) => void;
  onLogout: () => void;
  isLoggingOut: boolean;
}

export function SettingsView({
  user,
  integrations,
  catalog,
  error,
  actionError,
  status,
  pendingAction,
  testResults,
  syncResults,
  onRetry,
  onAdd,
  onToggle,
  onTest,
  onSync,
  onDelete,
  onLogout,
  isLoggingOut,
}: SettingsViewProps) {
  if (status === "loading" && integrations.length === 0 && catalog.length === 0) {
    return <SettingsSkeleton />;
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="mb-2 text-sm font-bold uppercase tracking-[0.18em] text-primary">Dein Lernraum</p>
        <h1 className="text-3xl font-extrabold tracking-[-0.035em] sm:text-4xl">Einstellungen</h1>
        <p className="mt-2 text-sm font-semibold text-muted-foreground">Konto, Erscheinungsbild und deine verbundenen Dienste an einem Ort.</p>
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

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.85fr)]">
        <IntegrationsCard
          integrations={integrations}
          catalog={catalog}
          pendingAction={pendingAction}
          testResults={testResults}
          syncResults={syncResults}
          isRefreshing={status === "loading"}
          onRefresh={onRetry}
          onAdd={onAdd}
          onToggle={onToggle}
          onTest={onTest}
          onSync={onSync}
          onDelete={onDelete}
        />
        <div className="space-y-6">
          <AccountCard user={user} onLogout={onLogout} isLoggingOut={isLoggingOut} />
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


