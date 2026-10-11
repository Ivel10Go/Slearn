"use client";

import { useState } from "react";
import { Check, LoaderCircle, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";

interface RowActionsProps {
  /** Bezeichnung des Eintrags, fuer die Screenreader-Beschriftung der Knoepfe. */
  label: string;
  /** Ohne `onEdit` bleibt nur der Loesch-Knopf - etwa bei Lernplaenen. */
  onEdit?: () => void;
  onDelete: () => void | Promise<unknown>;
  /** `true`, wenn gerade ein Schreibvorgang auf dieser Zeile laeuft. */
  busy?: boolean;
}

/**
 * Bearbeiten und Loeschen fuer eine Tabellenzeile.
 *
 * Das Loeschen ist zweistufig, genau wie bei den Integrationen: ein
 * "Entfernen"-Knopf allein genuegt nicht, weil der Fehler nicht reversibel
 * ist. "Abbrechen" ist dabei kein sonderner Knopf, sondern derselbe Knopf mit
 * geaenderter Beschriftung - so bleibt die Zeile gleich breit.
 */
export function RowActions({ label, onEdit, onDelete, busy }: RowActionsProps) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  async function handleDelete() {
    setDeleting(true);
    try {
      await onDelete();
    } finally {
      setDeleting(false);
      setConfirming(false);
    }
  }

  return (
    <div className="flex shrink-0 items-center gap-1.5">
      {onEdit ? (
        <Button type="button" size="icon" variant="ghost" onClick={onEdit} disabled={busy || deleting} aria-label={t("{titel} bearbeiten", { titel: label })}>
          <Pencil aria-hidden="true" />
        </Button>
      ) : null}
      {confirming ? (
        <>
          <Button type="button" size="sm" variant="destructive" onClick={() => void handleDelete()} disabled={deleting}>
            {deleting ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Check aria-hidden="true" />}
            {t("Wirklich loeschen")}
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={deleting}>
            {t("Abbrechen")}
          </Button>
        </>
      ) : (
        <Button type="button" size="icon" variant="ghost" onClick={() => setConfirming(true)} disabled={busy || deleting} aria-label={t("{titel} löschen", { titel: label })}>
          <Trash2 aria-hidden="true" />
        </Button>
      )}
    </div>
  );
}

interface SectionHeaderProps {
  title: string;
  /** Kurze Zahl rechts, etwa "2 offen". Bewusst Text, kein Badge. */
  count: string;
  loading: boolean;
  onReload: () => void;
  formOpen: boolean;
  onToggleForm: () => void;
  /** Beschriftung des Plus-Knopfs fuer Screenreader und Tooltip. */
  newLabel: string;
}

/**
 * Der Kopf der Dashboard-Listen (Hausaufgaben, Pruefungen, Termine).
 *
 * Frueher hatte jede Karte ein farbiges Badge und einen gefuellten
 * "Neu"-Knopf - drei weisse Knoepfe nebeneinander waren das Lauteste auf der
 * Seite, obwohl Anlegen die seltenste Handlung ist. Jetzt: Zahl als grauer
 * Text wie bei "Heute lernen", Neuladen und Anlegen als ruhige Symbolknoepfe.
 */
export function SectionHeader({ title, count, loading, onReload, formOpen, onToggleForm, newLabel }: SectionHeaderProps) {
  return (
    <CardHeader className="flex-row items-center justify-between gap-3 space-y-0 py-3">
      <CardTitle>{title}</CardTitle>
      <div className="-mr-1.5 flex items-center gap-0.5">
        <span className="mr-1.5 text-xs tabular-nums text-muted-foreground">{count}</span>
        <Button type="button" size="icon" variant="ghost" onClick={onReload} aria-label={t("{titel} neu laden", { titel: title })} title={t("Neu laden")} disabled={loading}>
          <RefreshCw className={cn(loading && "animate-spin")} aria-hidden="true" />
        </Button>
        <Button
          type="button"
          size="icon"
          variant={formOpen ? "secondary" : "ghost"}
          onClick={onToggleForm}
          aria-expanded={formOpen}
          aria-label={formOpen ? t("Formular schließen") : newLabel}
          title={formOpen ? t("Schließen") : newLabel}
        >
          {formOpen ? <X aria-hidden="true" /> : <Plus aria-hidden="true" />}
        </Button>
      </div>
    </CardHeader>
  );
}

interface SectionBannerProps {
  error: string | null;
  actionError: string | null;
  onRetry?: () => void;
}

/**
 * Fehlerbanner einer CRUD-Sektion.
 *
 * Ladefehler und Aktionsfehler bekommen getrennte Kaesten: der erste bedeutet
 * "die Liste ist alt oder leer", der zweite "dein Klick hat nicht
 * geklappt". Zusammengelegt wuesste niemand, was er wiederholen soll.
 */
export function SectionBanner({ error, actionError, onRetry }: SectionBannerProps) {
  return (
    <>
      {error ? (
        <div
          className="flex flex-col gap-3 rounded-md border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive sm:flex-row sm:items-center sm:justify-between"
          role="alert"
        >
          <span>{error}</span>
          {onRetry ? (
            <Button type="button" size="sm" variant="outline" onClick={onRetry}>
              {t("Erneut versuchen")}
            </Button>
          ) : null}
        </div>
      ) : null}
      {actionError && !error ? (
        <p className="rounded-md border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm font-medium text-destructive" role="alert">
          {actionError}
        </p>
      ) : null}
    </>
  );
}
