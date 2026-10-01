import type { LucideIcon } from "lucide-react";

export function EmptyState({ icon: Icon, title, description }: { icon: LucideIcon; title: string; description: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-border px-5 py-8 text-center">
      <Icon className="mb-2.5 h-5 w-5 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
      <p className="text-[13px] font-medium">{title}</p>
      <p className="mt-1 max-w-xs text-xs leading-5 text-muted-foreground">{description}</p>
    </div>
  );
}
