import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Beschriftung, Bedienelement und Fehlermeldung als eine Einheit.
 *
 * Die Verknuepfung laeuft ueber `htmlFor`/`id` und `aria-describedby`. Ohne die
 * `aria-describedby`-Verknuepfung sagt ein Screenreader nur "Falsch" und nicht,
 * was fehlt - bei einer Lernenden, die eine Hausaufgabe anlegt, ist genau das
 * der Unterschied zwischen einem Korrekturversuch und fuenf.
 */
export function useFieldIds(id: string, hasHint: boolean, hasError: boolean) {
  return {
    controlId: id,
    hintId: hasHint ? `${id}-hint` : undefined,
    errorId: hasError ? `${id}-error` : undefined,
  };
}

export interface FieldProps {
  id: string;
  label: string;
  hint?: string;
  error?: string | null;
  required?: boolean;
  className?: string;
  children: (props: {
    id: string;
    "aria-invalid": true | undefined;
    "aria-describedby": string | undefined;
    required: true | undefined;
  }) => React.ReactNode;
}

export function Field({ id, label, hint, error, required, className, children }: FieldProps) {
  const { controlId, hintId, errorId } = useFieldIds(id, Boolean(hint), Boolean(error));
  // Beide Texte duerfen gleichzeitig genannt werden: der Hinweis sagt, was
  // einzugeben ist, der Fehler, was fehlt.
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={controlId} className="block text-[13px] font-medium">
        {label}
        {required ? (
          <span className="ml-1 text-destructive" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      {children({ id: controlId, "aria-invalid": error ? true : undefined, "aria-describedby": describedBy, required: required || undefined })}
      {hint ? (
        <p id={hintId} className="text-xs leading-5 text-muted-foreground">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-xs font-medium text-foreground underline decoration-foreground/40 underline-offset-2">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Meldung am Ende eines Formulars.
 *
 * `role="alert"` bringt den Text vor, sobald er erscheint - bei einer
 * fehlgeschlagenen Speicherung ist das die wichtigste Information der Seite.
 */
export function FormError({ id, message }: { id?: string; message: string | null }) {
  if (!message) {
    return null;
  }
  return (
    <p id={id} className="rounded-md border border-foreground/30 bg-muted px-3 py-2.5 text-[13px] font-medium text-foreground" role="alert">
      {message}
    </p>
  );
}
