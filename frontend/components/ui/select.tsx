import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Ein natives `<select>`, ueber die Klassenliste gestylt.
 *
 * Bewusst keine Listbox aus eigenem Code: Pfeil, Tastaturauswahl und der
 * Screenreader-Umriss des Browsers sind genau das, was man nicht nachbauen
 * sollte - und `appearance: none` nimmt dem Geraet weg, was es von selbst
 * richtig macht.
 */
const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        "flex h-9 w-full appearance-none rounded-md border border-input bg-transparent px-3 py-1.5 text-[13px] text-foreground transition-colors focus-visible:border-foreground focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 aria-[invalid=true]:border-foreground aria-[invalid=true]:ring-1",
        className,
      )}
      {...props}
    />
  ),
);
Select.displayName = "Select";

export { Select };
