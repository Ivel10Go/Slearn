"use client";

import { useState } from "react";
import { Check, LoaderCircle, Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";

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
        <Button type="button" size="icon" variant="ghost" onClick={onEdit} disabled={busy || deleting} aria-label={`${label} bearbeiten`}>
          <Pencil aria-hidden="true" />
        </Button>
      ) : null}
      {confirming ? (
        <>
          <Button type="button" size="sm" variant="destructive" onClick={() => void handleDelete()} disabled={deleting}>
            {deleting ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Check aria-hidden="true" />}
            Wirklich loeschen
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={deleting}>
            Abbrechen
          </Button>
        </>
      ) : (
        <Button type="button" size="icon" variant="ghost" onClick={() => setConfirming(true)} disabled={busy || deleting} aria-label={`${label} loeschen`}>
          <Trash2 aria-hidden="true" />
        </Button>
      )}
    </div>
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
          className="flex flex-col gap-3 rounded-2xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive sm:flex-row sm:items-center sm:justify-between"
          role="alert"
        >
          <span>{error}</span>
          {onRetry ? (
            <Button type="button" size="sm" variant="outline" onClick={onRetry}>
              Erneut versuchen
            </Button>
          ) : null}
        </div>
      ) : null}
      {actionError && !error ? (
        <p className="rounded-2xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm font-medium text-destructive" role="alert">
          {actionError}
        </p>
      ) : null}
    </>
  );
}
