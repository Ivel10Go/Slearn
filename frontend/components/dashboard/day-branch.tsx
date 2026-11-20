"use client";

import { useEffect, useRef } from "react";
import { Faultier } from "@/components/assistant/faultier";
import { buildDayBranch, type BranchItem } from "@/lib/day-branch";
import { formatTime, getCountdown } from "@/lib/format";
import type { DashboardData } from "@/lib/types";
import { cn } from "@/lib/utils";
import { t, tn } from "@/lib/i18n";

/*
 * Der Tagesast: das Leitmotiv der Uebersicht (siehe .impeccable/surfaces).
 * Stunden und Lernbloecke liegen in ihrer echten Laenge auf dem Ast, das
 * vorhandene Faultier haengt bei "jetzt". Er ersetzt die fruehere Leiste aus
 * vier Zahlenkacheln - die Zahlen stehen jetzt als ein Satz darunter.
 *
 * Zustand nur ueber Fuellung und Strich, wie im Rest der App: laufend = Tinte,
 * vorbei = gedaempft, ausgefallen = schraffiert, Lernblock = gestrichelt.
 */

const SLOTH_WIDTH = 52;
// Der Ast im Faultier-SVG liegt bei y=10 von 136 (`KOERPER_BOX`).
const SLOTH_BRANCH_OFFSET = Math.round((SLOTH_WIDTH * 136) / 120 * (10 / 136));

function blockClass(item: BranchItem) {
  if (item.cancelled) {
    return "border-border text-muted-foreground line-through [background:repeating-linear-gradient(135deg,var(--muted)_0_5px,var(--card)_5px_10px)]";
  }
  if (item.state === "current") {
    return "border-foreground bg-foreground text-background";
  }
  if (item.kind === "study") {
    return cn("border-dashed border-foreground/60 bg-card", item.state === "past" && "text-muted-foreground");
  }
  return cn("border-input bg-card", item.state === "past" ? "text-muted-foreground" : "text-foreground");
}

function nextItem(items: BranchItem[]) {
  return items.find((item) => !item.cancelled && item.state === "current") ?? items.find((item) => !item.cancelled && item.state === "upcoming");
}

export function DayBranch({ data, reference }: { data: DashboardData; reference: Date }) {
  const ast = buildDayBranch(data.lessons, data.studyTasks, reference);
  const hours = Array.from({ length: ast.endHour - ast.startHour + 1 }, (_, index) => ast.startHour + index);
  const next = nextItem(ast.items);
  const evening = reference.getHours() >= 21 || reference.getHours() < 6;
  const { summary } = data;
  const scroller = useRef<HTMLDivElement | null>(null);
  const nowPercent = ast.now;

  // Auf schmalen Bildschirmen beim Oeffnen zu "jetzt" springen, nicht bei 7 Uhr stehen.
  useEffect(() => {
    const element = scroller.current;
    if (element && element.scrollWidth > element.clientWidth) {
      element.scrollLeft = (element.scrollWidth * nowPercent) / 100 - element.clientWidth / 2;
    }
  }, [nowPercent]);

  return (
    <section aria-label={t("Dein Tag")} className="pb-2 pt-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-[17px] font-semibold tracking-tight">{t("Dein Tag")}</h2>
        <p className="text-[15px] text-muted-foreground">
          {[
            tn(summary.lessonsTotal, "{fertig} von {n} Stunde", "{fertig} von {n} Stunden", { fertig: summary.lessonsCompleted }),
            tn(summary.tasksTotal, "{fertig} von {n} Lernblock", "{fertig} von {n} Lernblöcken", { fertig: summary.tasksCompleted }),
            tn(summary.homeworkPending, "{n} Hausaufgabe offen", "{n} Hausaufgaben offen"),
          ].join(" · ")}
        </p>
      </div>

      {/* Unter 640px laeuft der Ast seitlich weiter, statt Bloecke unlesbar zu stauchen. */}
      <div ref={scroller} className="-mx-4 mt-3 overflow-x-auto px-4">
        <div className="relative h-[142px] min-w-[600px]">
          <div className="absolute inset-x-0 top-0 h-4 text-[13px] tabular-nums text-muted-foreground" aria-hidden="true">
            {hours.map((hour, index) => (
              <span
                key={hour}
                className="absolute -translate-x-1/2 first:translate-x-0 last:-translate-x-full"
                style={{ left: `${(index / (hours.length - 1)) * 100}%` }}
              >
                {hour}
              </span>
            ))}
          </div>

          <ol className="absolute inset-x-0 top-8 h-12" aria-label={t("Stunden und Lernblöcke heute")}>
            {ast.items.map((item) => (
              <li
                key={item.id}
                className={cn("absolute top-0 flex h-12 min-w-0 flex-col justify-center overflow-hidden rounded-t-md border border-b-0 px-2 text-[15px] leading-4", blockClass(item))}
                style={{ left: `calc(${item.left}% + 1px)`, width: `calc(${item.width}% - 2px)` }}
                title={`${formatTime(item.start)}–${formatTime(item.end)} · ${item.kind === "study" ? item.detail : item.label}`}
              >
                <span className="truncate font-medium">{item.label}</span>
                <span className={cn("truncate text-[13px]", item.state === "current" ? "text-background/80" : "text-muted-foreground")}>
                  {item.cancelled ? t("fällt aus") : item.kind === "study" ? t("Lernblock") : item.detail || formatTime(item.start)}
                </span>
              </li>
            ))}
          </ol>

          {ast.outside === null ? (
            <div className="absolute top-6 h-14 w-px bg-foreground" style={{ left: `${ast.now}%` }} aria-hidden="true" />
          ) : null}

          <div className="absolute inset-x-0 top-[80px] h-[3px] rounded-full bg-foreground" aria-hidden="true" />
          {/* Zwei Blaetter am Astende, gezeichnet wie `f-blatt` an der Figur. */}
          <svg className="absolute -right-1 top-[66px] h-6 w-8 fill-accent stroke-foreground" viewBox="0 0 32 24" strokeWidth={1.5} strokeLinejoin="round" aria-hidden="true" focusable="false">
            <path d="M6 15C11 5 22 3 29 7C24 14 14 17 6 15Z" />
            <path d="M10 16C15 22 24 23 29 20C24 15 16 14 10 16Z" />
          </svg>

          <div
            className="absolute"
            style={{ left: `clamp(0px, calc(${ast.now}% - ${SLOTH_WIDTH / 2}px), calc(100% - ${SLOTH_WIDTH}px))`, top: `${81 - SLOTH_BRANCH_OFFSET}px` }}
          >
            <Faultier koerper groesse={SLOTH_WIDTH} zustand={evening ? "schlaeft" : "ruhe"} beschriftung={t("Jetzt, {uhrzeit}", { uhrzeit: formatTime(reference) })} />
          </div>
        </div>
      </div>

      <p className="mt-1 text-[15px]">
        {next ? (
          <>
            <span className="text-muted-foreground">{next.state === "current" ? t("Läuft gerade") : t("Als Nächstes")}: </span>
            <span className="font-medium">{next.kind === "study" ? next.detail : next.label}</span>
            <span className="text-muted-foreground">
              {" · "}
              {formatTime(next.start)}
              {next.kind === "lesson" && next.detail ? ` · ${t("Raum {raum}", { raum: next.detail })}` : null}
              {next.state === "upcoming" ? ` · ${getCountdown(next.start, reference).label}` : null}
            </span>
          </>
        ) : (
          <span className="text-muted-foreground">
            {ast.items.length ? t("Für heute ist alles vorbei.") : t("Heute stehen keine Stunden und Lernblöcke im Plan.")}
          </span>
        )}
      </p>
    </section>
  );
}
