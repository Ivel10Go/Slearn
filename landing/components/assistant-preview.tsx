import { Faultier } from "@/components/faultier";
import type { LandingContent } from "@/lib/content";
import { cn } from "@/lib/utils";

/* Ein Gespraech mit dem Lern-Assistenten, nachgebaut wie die App es zeigt. */
export function AssistantPreview({ texts, className }: { texts: LandingContent["assistantPreview"]; className?: string }) {
  return (
    <div
      role="img"
      aria-label={texts.label}
      className={cn("rounded-xl border border-border bg-card p-4 text-left select-none sm:p-5", className)}
    >
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-lg bg-foreground px-3 py-2 text-[12.5px] text-background">
          {texts.question}
        </p>
      </div>

      <div className="mt-4 flex gap-2.5">
        <Faultier zustand="freut" groesse={28} still className="mt-0.5" />
        <div className="min-w-0 flex-1 space-y-2.5">
          <p className="text-[12.5px] leading-relaxed">
            {texts.answer}
          </p>

          <div className="rounded-md border border-border">
            <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
              <p className="text-[11px] text-muted-foreground">{texts.suggestion}</p>
              <p className="text-[11px] text-muted-foreground">{texts.kind}</p>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5">
              <div>
                <p className="text-[12.5px] font-medium">{texts.entryTitle}</p>
                <p className="text-[11px] text-muted-foreground">{texts.entryDate}</p>
              </div>
              <div className="flex gap-1.5">
                <span className="rounded-md border border-border px-2.5 py-1 text-[11px] text-muted-foreground">{texts.discard}</span>
                <span className="rounded-md border border-foreground bg-foreground px-2.5 py-1 text-[11px] font-medium text-background">
                  {texts.add}
                </span>
              </div>
            </div>
          </div>

          <p className="text-[12.5px] leading-relaxed">
            {texts.followUp}
          </p>

          <div className="flex flex-wrap gap-1.5 pt-0.5">
            <span className="rounded-sm border border-border px-1.5 py-px text-[10px] text-muted-foreground">
              {texts.excerpt}
            </span>
            <span className="rounded-sm border border-border px-1.5 py-px text-[10px] text-muted-foreground">{texts.source}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
