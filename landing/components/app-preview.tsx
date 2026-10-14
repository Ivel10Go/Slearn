import type { LandingContent } from "@/lib/content";
import { cn } from "@/lib/utils";

/*
 * Nachbau des Dashboards in reinem HTML statt eines Screenshots: scharf in
 * jeder Groesse, im Dunkelmodus automatisch dunkel und ohne Bilddatei. Die
 * Inhalte sind ausgedacht, der Aufbau folgt `components/dashboard/dashboard-view.tsx`.
 */

export function AppPreview({ texts, className }: { texts: LandingContent["appPreview"]; className?: string }) {
  return (
    <div
      aria-label={texts.label}
      role="img"
      className={cn("overflow-hidden rounded-xl border border-border bg-card text-left select-none", className)}
    >
      <div className="flex h-9 items-center gap-1.5 border-b border-border px-3">
        <span className="size-2.5 rounded-full border border-border bg-muted" />
        <span className="size-2.5 rounded-full border border-border bg-muted" />
        <span className="size-2.5 rounded-full border border-border bg-muted" />
        <span className="ml-3 text-[11px] text-muted-foreground">Lernraum</span>
      </div>
      <div className="flex">
        <aside className="hidden w-40 shrink-0 border-r border-border bg-muted/50 px-2.5 py-3 md:block">
          {texts.nav.map((bereich) => (
            <div key={bereich.group} className="mb-3">
              <p className="px-2 pb-1 text-[10.5px] text-muted-foreground">{bereich.group}</p>
              {bereich.items.map((punkt) => (
                <p
                  key={punkt}
                  className={cn(
                    "rounded-sm px-2 py-[3px] text-[11.5px]",
                    punkt === texts.active ? "bg-accent font-medium" : "text-muted-foreground",
                  )}
                >
                  {punkt}
                </p>
              ))}
            </div>
          ))}
        </aside>

        <div className="min-w-0 flex-1 p-4 sm:p-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <p className="text-[16px] font-semibold tracking-tight">{texts.greeting}</p>
              <p className="mt-0.5 text-[11.5px] text-muted-foreground">
                {texts.summary}
              </p>
            </div>
            <span className="rounded-sm border border-border px-1.5 py-px text-[10px] text-muted-foreground">{texts.source}</span>
          </div>

          <div className="mt-4 grid grid-cols-2 overflow-hidden rounded-lg border border-border sm:grid-cols-4">
            {texts.stats.map((zahl, index) => (
              <div
                key={zahl.title}
                className={cn(
                  "px-3 py-2.5",
                  index % 2 === 1 && "border-l border-border",
                  index >= 2 && "border-t border-border sm:border-t-0",
                  index === 2 && "sm:border-l",
                )}
              >
                <p className="text-[10.5px] text-muted-foreground">{zahl.title}</p>
                <p className="mt-0.5 text-[15px] font-semibold tabular-nums">{zahl.value}</p>
                <p className="truncate text-[10.5px] text-muted-foreground">{zahl.note}</p>
              </div>
            ))}
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-[1.25fr_1fr]">
            <div className="rounded-lg border border-border">
              <p className="border-b border-border px-3 py-2 text-[12px] font-medium">{texts.today}</p>
              <ul>
                {texts.lessons.map((stunde) => (
                  <li
                    key={stunde.time}
                    className={cn(
                      "flex items-center gap-3 border-b border-border px-3 py-[7px] text-[11.5px] last:border-b-0",
                      stunde.now && "bg-muted",
                    )}
                  >
                    <span className="w-9 font-mono text-[10.5px] text-muted-foreground tabular-nums">{stunde.time}</span>
                    <span className="w-7 font-medium">{stunde.short}</span>
                    <span className={cn("min-w-0 flex-1 truncate", stunde.cancelled && "text-muted-foreground line-through")}>
                      {stunde.name}
                    </span>
                    <span className="text-[10.5px] text-muted-foreground">{stunde.cancelled ? texts.cancelled : stunde.room}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="rounded-lg border border-border">
              <p className="border-b border-border px-3 py-2 text-[12px] font-medium">{texts.homework}</p>
              <ul>
                {texts.tasks.map((aufgabe) => (
                  <li key={aufgabe.title} className="flex items-center gap-2.5 border-b border-border px-3 py-[7px] text-[11.5px] last:border-b-0">
                    <span
                      className={cn(
                        "grid size-3.5 shrink-0 place-items-center rounded-[3px] border border-foreground/60",
                        aufgabe.done && "bg-foreground",
                      )}
                    >
                      {aufgabe.done ? (
                        <svg viewBox="0 0 10 10" className="size-2.5 text-background" aria-hidden>
                          <path d="M2 5.2 4.1 7.3 8 3" fill="none" stroke="currentColor" strokeWidth="1.6" />
                        </svg>
                      ) : null}
                    </span>
                    <span className={cn("min-w-0 flex-1 truncate", aufgabe.done && "text-muted-foreground line-through")}>
                      {aufgabe.title}
                    </span>
                    <span className="text-[10.5px] text-muted-foreground">
                      {aufgabe.subject} · {aufgabe.due}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
