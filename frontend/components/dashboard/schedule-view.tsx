"use client";

import { useMemo, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  addDays,
  eachDay,
  formatDate,
  formatDateRange,
  formatTime,
  formatWeekNumber,
  getLessonState,
  isSameDay,
  startOfDay,
  startOfWeek,
  toDate,
  toIsoDate,
} from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { useScheduleRange } from "@/lib/use-schedule-range";
import type { DashboardLesson } from "@/lib/types";
import { cn } from "@/lib/utils";
import { t, tn } from "@/lib/i18n";

type RangeId = "today" | "threeDays" | "week";

interface RangeOption {
  id: RangeId;
  label: string;
  /** So viele Tage umfasst der Zeitraum, und so weit springen die Pfeile. */
  days: number;
}

const RANGE_OPTIONS: RangeOption[] = [
  { id: "today", label: t("Heute"), days: 1 },
  { id: "threeDays", label: t("3 Tage"), days: 3 },
  { id: "week", label: t("1 Woche"), days: 7 },
];

/** Breite der Zeitspalte links: "07:45" plus Luft. */
const TIME_GUTTER = "4.25rem";
/**
 * Mindestbreite einer Tagesspalte.
 *
 * Der Raster waechst mit: unterhalb dieser Breite wuerde ein Kuerzel plus Raum
 * umbrechen, also scrollt der Plan auf dem Telefon waagerecht, statt die
 * Information zu zerhacken. 6rem reicht, seit eine Stunde in der Uebersicht
 * einzeilig ist ("Eap 409"), und laesst die Woche in die Hauptspalte des
 * Dashboards passen.
 */
const DAY_MIN_WIDTH = "6rem";

function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: Array<{ id: T; label: string }>;
  value: T;
  onChange: (next: T) => void;
  label: string;
}) {
  return (
    <div className="flex border border-border" role="group" aria-label={label}>
      {options.map((option, index) => {
        const active = option.id === value;
        return (
          <button
            key={option.id}
            type="button"
            onClick={() => onChange(option.id)}
            aria-pressed={active}
            className={cn(
              "h-7 px-2.5 text-xs font-semibold transition-colors active:opacity-80",
              index > 0 && "border-l border-border",
              active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * Eine Stunde als Zelle im Raster.
 *
 * Im Uebersichtsmodus - mehr als ein Tag - reichen Kuerzel und Raum; Fachname,
 * Titel und Lehrkraft stehen im Tooltip. In der Tagesansicht ist Platz fuer
 * Fach, Raum und Lehrkraft - aber ebenfalls in einer Zeile.
 */
function LessonCell({ lesson, reference, detailed }: { lesson: DashboardLesson; reference: Date; detailed: boolean }) {
  const state = getLessonState(lesson, reference);
  const isCurrent = state === "current";
  return (
    <div
      className={cn(
        "flex border-l-2 leading-tight transition-colors hover:bg-muted/60",
        // Immer eine Zeile: sonst stapeln sich parallele Kurse zu Tuermen.
        detailed ? "items-baseline gap-3 px-2.5 py-1.5" : "items-baseline gap-1.5 px-1.5 py-1",
        isCurrent ? "border-foreground bg-muted" : "border-border/70",
        state === "completed" && !isCurrent && "opacity-50",
        lesson.isCancelled && "opacity-70",
      )}
      // Die Fachfarbe traegt nur die Randlinie; die laufende Stunde bleibt schwarz markiert.
      style={isCurrent ? undefined : { borderLeftColor: lesson.color }}
      title={`${lesson.subject}${lesson.title ? ` · ${lesson.title}` : ""} · ${formatTime(lesson.startTime)} – ${formatTime(lesson.endTime)} Uhr · ${lesson.room} · ${lesson.teacher}${lesson.isCancelled ? " · Entfallen" : ""}`}
    >
      {detailed ? (
        <>
          {/*
            Eine Zeile je Stunde: die Uhrzeit steht schon in der Zeitspalte, und
            der Titel nur, wenn er mehr sagt als das Fach - WebUntis liefert oft
            beides gleich.
          */}
          <span className={cn("min-w-0 flex-1 truncate text-[13px] font-semibold", lesson.isCancelled && "line-through")}>
            {lesson.subject}
            {lesson.title && lesson.title !== lesson.subject ? <span className="font-normal text-muted-foreground"> · {lesson.title}</span> : null}
          </span>
          <span className="shrink-0 truncate text-xs text-muted-foreground">{[lesson.room, lesson.teacher].filter(Boolean).join(" · ")}</span>
          {isCurrent ? <span className="shrink-0 text-[11px] font-medium text-foreground">{t("Jetzt")}</span> : null}
          {lesson.isCancelled ? <span className="shrink-0 text-[11px] font-medium text-destructive">{t("Entfallen")}</span> : null}
        </>
      ) : (
        <>
          <span className={cn("truncate text-xs font-semibold", lesson.isCancelled && "line-through")}>
            {lesson.subjectShort ?? lesson.subject}
          </span>
          <span className="truncate text-[10px] text-muted-foreground">{lesson.room}</span>
          {lesson.isCancelled ? <span className="sr-only">{t("Entfallen")}</span> : null}
        </>
      )}
    </div>
  );
}

/** Spaltenbreiten als Inline-Style, weil die Tageszahl zur Laufzeit feststeht. */
function gridTemplate(dayCount: number) {
  const column = dayCount <= 1 ? "minmax(0, 1fr)" : `minmax(${DAY_MIN_WIDTH}, 1fr)`;
  return `${TIME_GUTTER} repeat(${dayCount}, ${column})`;
}

interface GridRow {
  /** Uhrzeit in Ortszeit, "HH:MM" - gemeinsam fuer alle Tage. */
  startTime: string;
  label: string;
  /** Zellen je Tag; parallele Faecher stehen als Liste in derselben Zelle. */
  cells: Array<DashboardLesson[]>;
  isNow: boolean;
}

export function ScheduleCard() {
  const now = useNow();
  // "heute" aus dem Sekundentakt ableiten: kein zweiter State, und der Wechsel über Mitternacht passiert mit.
  const today = now ? startOfDay(now) : null;
  // Ohne useMemo wäre `reference` ein neuer Wert je Render und das Raster
  // darunter würde bei jedem Ticken des Sekundentakts neu gebaut.
  const reference = useMemo(() => now ?? new Date(), [now]);

  // Startet mit "Heute": das Dashboard beantwortet, was heute ansteht. Die
  // Woche ist einen Klick entfernt und haette den Rest der Seite verdraengt.
  const [rangeId, setRangeId] = useState<RangeId>("today");
  const [offset, setOffset] = useState(0);

  const option = RANGE_OPTIONS.find((entry) => entry.id === rangeId) ?? RANGE_OPTIONS[RANGE_OPTIONS.length - 1];

  // "1 Woche" meint die laufende Kalenderwoche ab Montag, nicht die nächsten sieben Tage.
  const { start, end } = useMemo(() => {
    if (!today) {
      return { start: null, end: null };
    }
    const base = option.id === "week" ? startOfWeek(today) : today;
    const from = addDays(base, offset * option.days);
    return { start: from, end: addDays(from, option.days - 1) };
  }, [option, offset, today]);

  const startIso = start ? toIsoDate(start) : "";
  const endIso = end ? toIsoDate(end) : "";

  const { lessons, error, status, reload } = useScheduleRange(startIso, endIso);
  const isLoading = status === "loading";

  function selectRange(next: RangeId) {
    setRangeId(next);
    setOffset(0);
  }

  const visibleDays = useMemo(() => (start && end ? eachDay(start, end) : []), [start, end]);

  /**
   * Das Raster.
   *
   * Zeilen sind die *unterschiedlichen Startzeiten im ganzen Zeitraum*, nicht die
   * Stunden eines einzelnen Tages. Nur so liegen 08:00 des Montags und 08:00 des
   * Dienstags in derselben Zeile - ein Stundenplan ist daran erkennbar, dass man
   * eine Zeile quer lesen kann. Zellen ohne Stunde bleiben als Haarlinie stehen,
   * sonst zerfällt das Raster.
   */
  const grid = useMemo(() => {
    const proTag = new Map<string, Map<string, DashboardLesson[]>>(visibleDays.map((day) => [toIsoDate(day), new Map()]));
    const startZeiten = new Set<string>();

    for (const lesson of lessons ?? []) {
      const dayKey = toIsoDate(toDate(lesson.startTime) ?? new Date());
      const spalten = proTag.get(dayKey);
      if (!spalten) {
        // Ausserhalb des sichtbaren Zeitraums - ein Reload nach einem
        // Zeitsprung kann eine Randstunde mitbringen.
        continue;
      }
      // Der Zeilenschluessel ist die **Uhrzeit** in der Ortszeit ("07:45"),
      // nicht der volle Zeitstempel: der traegt das Datum mit, und dann bekam
      // jeder Tag eigene Zeilen - die Donnerstagsstunden standen unter allen
      // Mittwochszeilen, und die Donnerstagsspalte sah oben leer aus.
      // "HH:MM" sortiert als Text richtig.
      const zeit = formatTime(lesson.startTime);
      startZeiten.add(zeit);
      const vorhanden = spalten.get(zeit);
      if (vorhanden) {
        vorhanden.push(lesson);
      } else {
        spalten.set(zeit, [lesson]);
      }
    }

    const rows: GridRow[] = [...startZeiten]
      .sort()
      .map((startTime) => ({
        startTime,
        label: startTime,
        cells: visibleDays.map((day) => {
          const treffer = proTag.get(toIsoDate(day))?.get(startTime);
          if (!treffer) {
            return [];
          }
          // Parallele Faecher alphabetisch nach Kuerzel, damit die Reihenfolge
          // nicht davon abhaengt, in welcher Reihenfolge das Portal antwortet.
          return treffer.slice().sort((a, b) => (a.subjectShort ?? a.subject).localeCompare(b.subjectShort ?? b.subject, "de"));
        }),
        isNow: visibleDays.some(
          (day) =>
            proTag.get(toIsoDate(day))?.get(startTime)?.some((lesson) => getLessonState(lesson, reference) === "current") ?? false,
        ),
      }));

    const proTagAnzahl = new Map<string, number>();
    for (const [dayKey, spalten] of proTag) {
      let anzahl = 0;
      for (const zelle of spalten.values()) {
        anzahl += zelle.length;
      }
      proTagAnzahl.set(dayKey, anzahl);
    }

    return { rows, proTagAnzahl };
  }, [lessons, visibleDays, reference]);

  const total = lessons?.length ?? 0;
  const detailed = visibleDays.length === 1;

  return (
    <Card id="schedule" className="scroll-mt-24">
      <CardHeader className="gap-2.5 pb-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <CardTitle>{t("Stundenplan")}</CardTitle>
          <span className="text-xs font-semibold tabular-nums text-muted-foreground">
            {start && end ? `${formatWeekNumber(start)} · ${formatDateRange(start, end)}` : t("lädt …")}
          </span>
          <Badge variant="secondary">{total}</Badge>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SegmentedControl
            options={RANGE_OPTIONS}
            value={rangeId}
            onChange={selectRange}
            label={t("Zeitraum wählen")}
          />
          <div className="flex items-center border border-border">
            <button
              type="button"
              onClick={() => setOffset((current) => current - 1)}
              className="flex h-7 w-7 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:opacity-80"
              aria-label={option.days === 1 ? t("Ein Tag zurück") : t("Um {n} Tage zurück", { n: option.days })}
            >
              <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => setOffset(0)}
              disabled={offset === 0}
              className="h-7 border-x border-border px-2 text-xs font-semibold text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:opacity-80 disabled:pointer-events-none disabled:opacity-40"
            >
              {offset === 0 ? (option.days === 1 ? t("Heute") : t("Aktuell")) : `${offset > 0 ? "+" : ""}${t("{n}T", { n: offset * option.days })}`}
            </button>
            <button
              type="button"
              onClick={() => setOffset((current) => current + 1)}
              className="flex h-7 w-7 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:opacity-80"
              aria-label={option.days === 1 ? t("Ein Tag vor") : t("Um {n} Tage vor", { n: option.days })}
            >
              <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
          <Button type="button" variant="ghost" size="icon" onClick={reload} disabled={isLoading} aria-label={t("Stundenplan neu laden")}>
            <RefreshCw className={cn("h-3.5 w-3.5", isLoading && "animate-spin")} aria-hidden="true" />
          </Button>
        </div>
      </CardHeader>

      <CardContent>
        {error ? (
          <p className="mb-4 border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        {!start || !end ? (
          <Skeleton className="h-48 w-full" />
        ) : isLoading ? (
          <div className="grid gap-2" style={{ gridTemplateColumns: gridTemplate(visibleDays.length) }}>
            {visibleDays.map((day) => (
              <Skeleton key={toIsoDate(day)} className="h-48 w-full" />
            ))}
          </div>
        ) : total === 0 && !error ? (
          <EmptyState
            icon={CalendarDays}
            title={t("Keine Stunden in diesem Zeitraum")}
            description={t("Für die gewählten Tage ist nichts eingetragen. Mit „Stundenplan aktualisieren“ in den Einstellungen holst du den Plan aus deinem Schulportal.")}
          />
        ) : (
          <div className="-mx-1 overflow-x-auto px-1">
            <div className="min-w-max">
              {/*
                `grid` definiert die Spalten, jede Zeile ist ein `subgrid` darueber.
                Das haelt die Tages-Spalten ueber alle Zeilen exakt auf derselben
                Position - mit `display: contents` statt `subgrid` faellt die
                Ausrichtung genau in dem Browser weg, der `role="row"` aus dem
                A11y-Baum entfernt.
              */}
              <div
                role="grid"
                aria-label={t("Stundenplan nach Tagen")}
                style={{ gridTemplateColumns: gridTemplate(visibleDays.length) }}
                className="grid"
              >
                <div role="row" className="col-span-full grid grid-cols-subgrid">
                  <div aria-hidden="true" className="sticky left-0 z-10 border-b border-border bg-card" />
                  {visibleDays.map((day) => {
                    const dayKey = toIsoDate(day);
                    const istHeute = today ? isSameDay(day, today) : false;
                    const anzahl = grid.proTagAnzahl.get(dayKey) ?? 0;
                    return (
                      <div
                        key={dayKey}
                        role="columnheader"
                        aria-label={formatDate(day, { weekday: "long", day: "2-digit", month: "long" })}
                        className={cn(
                          "border-b border-l border-border px-2 pb-1.5 pt-1",
                          istHeute ? "bg-muted" : "bg-card",
                        )}
                      >
                        <div className="flex items-baseline justify-between gap-1">
                          <span
                            className={cn(
                              "text-[11px] font-medium",
                              istHeute ? "text-foreground underline underline-offset-[5px]" : "text-muted-foreground",
                            )}
                          >
                            {formatDate(day, { weekday: "short" })}
                          </span>
                          <span className={cn("text-sm font-semibold tabular-nums", istHeute ? "text-foreground" : "text-muted-foreground")}>
                            {formatDate(day, { day: "2-digit" })}
                          </span>
                        </div>
                        <p className="mt-0.5 truncate text-[10px] font-medium text-muted-foreground/70">
                          {anzahl === 0
                            ? day.getDay() === 0 || day.getDay() === 6
                              ? t("Wochenende")
                              : t("Frei")
                            : tn(anzahl, "{n} Stunde", "{n} Stunden")}
                        </p>
                      </div>
                    );
                  })}
                </div>

                {grid.rows.map((row) => (
                  <div key={row.startTime} role="row" className="col-span-full grid grid-cols-subgrid">
                    <div
                      className={cn(
                        "sticky left-0 z-10 flex flex-col items-end border-l border-t border-border px-2 pb-1.5 pt-1.5",
                        row.isNow ? "bg-muted" : "bg-card",
                      )}
                    >
                      <span
                        className={cn(
                          "text-[11px] font-semibold tabular-nums",
                          row.isNow ? "text-primary underline underline-offset-[3px]" : "text-muted-foreground",
                        )}
                      >
                        {row.label}
                      </span>
                    </div>                    {row.cells.map((cellLessons, dayIndex) => {
                      const day = visibleDays[dayIndex];
                      const istHeute = today ? isSameDay(day, today) : false;
                      return (
                        <div
                          key={toIsoDate(day)}
                          role="gridcell"
                          className={cn(
                            "border-l border-t border-border",
                            istHeute ? "bg-muted/40" : "bg-card",
                          )}
                        >
                          {cellLessons.length === 0 ? null : (
                            <div className="flex flex-col">
                              {cellLessons.map((lesson) => (
                                <LessonCell key={lesson.id} lesson={lesson} reference={reference} detailed={detailed} />
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
