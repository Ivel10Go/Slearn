import type { LucideIcon } from "lucide-react";

export function EmptyState({ icon: Icon, title, description }: { icon: LucideIcon; title: string; description: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-muted/30 px-5 py-10 text-center">
      <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-2xl bg-secondary text-secondary-foreground">
        <Icon className="h-5 w-5" aria-hidden="true" />
      </span>
      <p className="font-bold">{title}</p>
      <p className="mt-1 max-w-xs text-sm leading-6 text-muted-foreground">{description}</p>
    </div>
  );
}
