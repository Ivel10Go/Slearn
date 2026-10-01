import type { LucideIcon } from "lucide-react";

/**
 * Ein leerer Zustand als ruhige Zeile, nicht als Kasten.
 *
 * Auf dem Dashboard sind oft mehrere Karten gleichzeitig leer. Als gestrichelte
 * Kaesten mit Symbol, Ueberschrift und zwei Zeilen Erklaerung sah die Seite
 * dann voller aus als mit Inhalt - die Leere war das Lauteste darauf. Jetzt ist
 * es ein Satz in Grau: man sieht, dass nichts da ist, und liest weiter.
 */
export function EmptyState({ icon: Icon, title, description }: { icon: LucideIcon; title: string; description: string }) {
  return (
    <div className="flex items-start gap-2 py-1 text-[13px] leading-5 text-muted-foreground">
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
      <p>
        <span className="font-medium text-foreground/80">{title}.</span> {description}
      </p>
    </div>
  );
}
