import { cn } from "@/lib/utils";

/*
 * Nachbau des Dashboards in reinem HTML statt eines Screenshots: scharf in
 * jeder Groesse, im Dunkelmodus automatisch dunkel und ohne Bilddatei. Die
 * Inhalte sind ausgedacht, der Aufbau folgt `components/dashboard/dashboard-view.tsx`.
 */

const NAV = [
  { gruppe: "Heute", punkte: ["Übersicht", "Stundenplan", "Hausaufgaben", "Prüfungen", "Kalender"] },
  { gruppe: "Bibliothek", punkte: ["Lernen", "Fächer", "Dokumente", "Fortschritt"] },
  { gruppe: "Werkzeuge", punkte: ["Assistent", "KI & Modelle"] },
];

const KENNZAHLEN = [
  { titel: "Nächste Stunde", wert: "09:35", zusatz: "Mathe · R 204" },
  { titel: "Hausaufgaben", wert: "3", zusatz: "1 bis morgen" },
  { titel: "Prüfungen", wert: "Bio", zusatz: "in 9 Tagen" },
  { titel: "Lernzeit", wert: "2:40 h", zusatz: "von 5 h Wochenziel" },
];

const STUNDEN = [
  { zeit: "07:45", fach: "D", name: "Deutsch", raum: "R 112" },
  { zeit: "08:35", fach: "E", name: "Englisch", raum: "R 112" },
  { zeit: "09:35", fach: "M", name: "Mathematik", raum: "R 204", jetzt: true },
  { zeit: "10:25", fach: "Bio", name: "Biologie", raum: "Bio 2" },
  { zeit: "11:30", fach: "F", name: "Französisch", raum: "R 017", entfaellt: true },
];

const AUFGABEN = [
  { titel: "Arbeitsblatt Ableitungen", fach: "M", faellig: "morgen", erledigt: false },
  { titel: "Vokabeln Unit 4", fach: "E", faellig: "Do", erledigt: false },
  { titel: "Gedichtanalyse – Entwurf", fach: "D", faellig: "Fr", erledigt: true },
];

export function AppPreview({ className }: { className?: string }) {
  return (
    <div
      aria-label="Vorschau des Lernraum-Dashboards"
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
          {NAV.map((bereich) => (
            <div key={bereich.gruppe} className="mb-3">
              <p className="px-2 pb-1 text-[10.5px] text-muted-foreground">{bereich.gruppe}</p>
              {bereich.punkte.map((punkt) => (
                <p
                  key={punkt}
                  className={cn(
                    "rounded-sm px-2 py-[3px] text-[11.5px]",
                    punkt === "Übersicht" ? "bg-accent font-medium" : "text-muted-foreground",
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
              <p className="text-[16px] font-semibold tracking-tight">Guten Morgen, Mia</p>
              <p className="mt-0.5 text-[11.5px] text-muted-foreground">
                Mathe um 09:35, danach Bio. Die Klausur ist in neun Tagen.
              </p>
            </div>
            <span className="rounded-sm border border-border px-1.5 py-px text-[10px] text-muted-foreground">lokal</span>
          </div>

          <div className="mt-4 grid grid-cols-2 overflow-hidden rounded-lg border border-border sm:grid-cols-4">
            {KENNZAHLEN.map((zahl, index) => (
              <div
                key={zahl.titel}
                className={cn(
                  "px-3 py-2.5",
                  index % 2 === 1 && "border-l border-border",
                  index >= 2 && "border-t border-border sm:border-t-0",
                  index === 2 && "sm:border-l",
                )}
              >
                <p className="text-[10.5px] text-muted-foreground">{zahl.titel}</p>
                <p className="mt-0.5 text-[15px] font-semibold tabular-nums">{zahl.wert}</p>
                <p className="truncate text-[10.5px] text-muted-foreground">{zahl.zusatz}</p>
              </div>
            ))}
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-[1.25fr_1fr]">
            <div className="rounded-lg border border-border">
              <p className="border-b border-border px-3 py-2 text-[12px] font-medium">Heute</p>
              <ul>
                {STUNDEN.map((stunde) => (
                  <li
                    key={stunde.zeit}
                    className={cn(
                      "flex items-center gap-3 border-b border-border px-3 py-[7px] text-[11.5px] last:border-b-0",
                      stunde.jetzt && "bg-muted",
                    )}
                  >
                    <span className="w-9 font-mono text-[10.5px] text-muted-foreground tabular-nums">{stunde.zeit}</span>
                    <span className="w-7 font-medium">{stunde.fach}</span>
                    <span className={cn("min-w-0 flex-1 truncate", stunde.entfaellt && "text-muted-foreground line-through")}>
                      {stunde.name}
                    </span>
                    <span className="text-[10.5px] text-muted-foreground">{stunde.entfaellt ? "entfällt" : stunde.raum}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="rounded-lg border border-border">
              <p className="border-b border-border px-3 py-2 text-[12px] font-medium">Hausaufgaben</p>
              <ul>
                {AUFGABEN.map((aufgabe) => (
                  <li key={aufgabe.titel} className="flex items-center gap-2.5 border-b border-border px-3 py-[7px] text-[11.5px] last:border-b-0">
                    <span
                      className={cn(
                        "grid size-3.5 shrink-0 place-items-center rounded-[3px] border border-foreground/60",
                        aufgabe.erledigt && "bg-foreground",
                      )}
                    >
                      {aufgabe.erledigt ? (
                        <svg viewBox="0 0 10 10" className="size-2.5 text-background" aria-hidden>
                          <path d="M2 5.2 4.1 7.3 8 3" fill="none" stroke="currentColor" strokeWidth="1.6" />
                        </svg>
                      ) : null}
                    </span>
                    <span className={cn("min-w-0 flex-1 truncate", aufgabe.erledigt && "text-muted-foreground line-through")}>
                      {aufgabe.titel}
                    </span>
                    <span className="text-[10.5px] text-muted-foreground">
                      {aufgabe.fach} · {aufgabe.faellig}
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
