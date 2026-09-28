"use client";

import { useMemo, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
  groupLessonSlots,
  isSameDay,
  pickSlotLesson,
  startOfDay,
  startOfWeek,
  toIsoDate,
} from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { useParallelChoice, useScheduleRange } from "@/lib/use-schedule-range";
import type { DashboardLesson } from "@/lib/types";
import { cn } from "@/lib/utils";

type RangeId = "today" | "threeDays" | "week";

interface RangeOption {
  id: RangeId;
  label: string;
  /** So viele Tage umfasst der Zeitraum, und so weit springen die Pfeile. */
  days: number;
}

const RANGE_OPTIONS: RangeOption[] = [
  { id: "today", label: "Heute", days: 1 },
  { id: "threeDays", label: "3 Tage", days: 3 },
  { id: "week", label: "1 Woche", days: 7 },
];

function isWeekend(day: Date) {
  return day.getDay() === 0 || day.getDay() === 6;
}

function columnClasses(days: number) {
  if (days <= 1) {
    return "grid-cols-1";
  }
  if (days === 3) {
    return "grid-cols-1 sm:grid-cols-3";
  }
  // Sieben Tage: auf einem Tablet im Querformat ist die Sidebar weg, es ist also
  // mehr Platz als die 280px-Schale am Desktop vermuten lassen.
  return "grid-cols-1 sm:grid-cols-2 md:grid-cols-4 xl:grid-cols-7";
}

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
 * Eine Stunde als eine Zeile: Uhrzeit, Kuerzel und Raum.
 *
 * Ausgeschriebener Fachname, Titel und Lehrkraft stehen im Tooltip. Das haelt
 * die Zeile auf einer Zeile, was bei sieben Tagen den Unterschied ausmacht.
 */
function LessonTile({ lesson, reference }: { lesson: DashboardLesson; reference: Date }) {
  const state = getLessonState(lesson, reference);
  const isCurrent = state === "current";
  return (
    <div
      className={cn(
        "flex items-baseline gap-1.5 border-l-2 px-1.5 py-1 leading-tight transition-colors hover:bg-muted/50",
        isCurrent ? "border-foreground bg-muted" : "border-border/70",
        state === "completed" && !isCurrent && "opacity-55",
        lesson.isCancelled && "opacity-70",
      )}
      title={`${lesson.subject}${lesson.title ? ` · ${lesson.title}` : ""} · ${formatTime(lesson.startTime)} – ${formatTime(lesson.endTime)} Uhr · ${lesson.room} · ${lesson.teacher}${lesson.isCancelled ? " · Entfallen" : ""}`}
    >
      <span
        className={cn(
          "shrink-0 text-[11px] font-bold tabular-nums",
          isCurrent ? "text-primary" : "text-muted-foreground",
        )}
      >
        {formatTime(lesson.startTime)}
      </span>
      <span
        className={cn(
          "min-w-0 flex-1 truncate text-xs font-extrabold",
          lesson.isCancelled && "line-through",
        )}
      >
        {lesson.subjectShort}
      </span>
      {lesson.isCancelled ? (
        <span className="shrink-0 text-[10px] font-bold uppercase tracking-wide text-destructive">Entfall</span>
      ) : null}
      <span className="shrink-0 truncate text-[11px] tabular-nums text-muted-foreground">{lesson.room}</span>
    </div>
  );
}

/** Auswahlleiste "F | L" fuer eine Lage mit parallelen Faechern. */
function SlotPicker({
  lessons,
  selectedId,
  onChoose,
}: {
  lessons: DashboardLesson[];
  selectedId: string;
  onChoose: (id: string) => void;
}) {
  return (
    <div className="flex items-center gap-1" role="group" aria-label="Paralleles Fach wählen">
      {lessons.map((lesson) => {
        const aktiv = lesson.id === selectedId;
        return (
          <button
            key={lesson.id}
            type="button"
            onClick={() => onChoose(lesson.id)}
            aria-pressed={aktiv}
            title={`${lesson.subject} · ${lesson.room}`}
            className={cn(
              "border px-1 py-px text-[10px] font-extrabold leading-none transition-colors active:opacity-80",
              aktiv
                ? "border-foreground bg-foreground text-background"
                : "border-border/70 text-muted-foreground hover:border-foreground hover:text-foreground",
              lesson.isCancelled && "line-through",
            )}
          >
            {lesson.subjectShort}
          </button>
        );
      })}
    </div>
  );
}

function DayColumn({
  day,
  lessons,
  reference,
  isToday,
  choices,
  onChoose,
}: {
  day: Date;
  lessons: DashboardLesson[];
  reference: Date;
  isToday: boolean;
  choices: Record<string, string>;
  onChoose: (slotKey: string, lessonId: string) => void;
}) {
  const weekend = isWeekend(day);
  const slots = useMemo(() => groupLessonSlots(lessons), [lessons]);
  return (
    <section
      className="min-w-0 border-t-2 border-border pt-1"
      aria-label={formatDate(day, { weekday: "long", day: "2-digit", month: "long" })}
    >
      <div className="mb-1 flex items-baseline justify-between gap-1">
        <span
          className={cn(
            "text-[11px] font-bold uppercase tracking-[0.12em]",
            isToday ? "text-foreground underline underline-offset-4" : "text-muted-foreground",
          )}
        >
          {formatDate(day, { weekday: "short" })}
        </span>
        <span className={cn("text-xs font-extrabold tabular-nums", weekend && "text-muted-foreground")}>
          {formatDate(day, { day: "2-digit" })}
        </span>
      </div>
      {slots.length === 0 ? (
        <p
          className={cn(
            "border border-dashed border-border/70 px-1 py-3 text-center text-[10px] leading-4",
            weekend ? "text-border" : "text-muted-foreground",
          )}
        >
          {weekend ? "Wochenende" : "Frei"}
        </p>
      ) : (
        <div className="space-y-0.5">
          {slots.map((slot) => {
            const gewaehlt = pickSlotLesson(slot.lessons, choices[slot.key] ?? null);
            return (
              <div key={slot.key} className="space-y-0.5">
                {slot.lessons.length > 1 ? (
                  <SlotPicker
                    lessons={slot.lessons}
                    selectedId={gewaehlt.id}
                    onChoose={(id) => onChoose(slot.key, id)}
                  />
                ) : null}
                <LessonTile lesson={gewaehlt} reference={reference} />
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

export function ScheduleCard() {
  const now = useNow();
  // "heute" aus dem Sekundentakt ableiten: kein zweiter State, und der Wechsel über Mitternacht passiert mit.
  const today = now ? startOfDay(now) : null;
  const reference = now ?? new Date();

  const [rangeId, setRangeId] = useState<RangeId>("week");
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
  const { choices, choose } = useParallelChoice();
  const isLoading = status === "loading";

  function selectRange(next: RangeId) {
    setRangeId(next);
    setOffset(0);
  }

  const visibleDays = start && end ? eachDay(start, end) : [];
  const lessonsByDay = useMemo(() => {
    const grouped = new Map<string, DashboardLesson[]>();
    for (const lesson of lessons ?? []) {
      const key = toIsoDate(lesson.startTime);
      grouped.set(key, [...(grouped.get(key) ?? []), lesson]);
    }
    return grouped;
  }, [lessons]);

  const total = lessons?.length ?? 0;

  return (
    <Card id="schedule" className="scroll-mt-24">
      {/* Zwei Zeilen statt einer: Titel oben, Bedienelemente darunter. Mit einer
          Zeile laufen sieben Tage plus Segmente, Pfeile und Reload auf
          schmalen Fenstern aus der Karte. */}
      <CardHeader className="gap-3 pb-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <CardTitle className="flex items-center gap-1.5 text-sm font-extrabold">
            <CalendarDays className="h-4 w-4 text-primary" aria-hidden="true" />
            Stundenplan
          </CardTitle>
          <CardDescription className="text-xs tabular-nums">
            {start && end ? `${formatWeekNumber(start)} · ${formatDateRange(start, end)}` : "lädt …"}
          </CardDescription>
          <Badge variant="secondary">{total}</Badge>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SegmentedControl
            options={RANGE_OPTIONS}
            value={rangeId}
            onChange={selectRange}
            label="Zeitraum wählen"
          />
          <div className="flex items-center border border-border">
            <button
              type="button"
              onClick={() => setOffset((current) => current - 1)}
              className="flex h-7 w-7 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:opacity-80"
              aria-label={option.days === 1 ? "Ein Tag zurück" : `Um ${option.days} Tage zurück`}
            >
              <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => setOffset(0)}
              disabled={offset === 0}
              className="h-7 border-x border-border px-2 text-xs font-semibold text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:opacity-80 disabled:pointer-events-none disabled:opacity-40"
            >
              {offset === 0 ? "Heute" : `${offset > 0 ? "+" : ""}${offset * option.days}T`}
            </button>
            <button
              type="button"
              onClick={() => setOffset((current) => current + 1)}
              className="flex h-7 w-7 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:opacity-80"
              aria-label={option.days === 1 ? "Ein Tag vor" : `Um ${option.days} Tage vor`}
            >
              <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
          <Button type="button" variant="ghost" size="icon" onClick={reload} disabled={isLoading} aria-label="Stundenplan neu laden">
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
          <Skeleton className="h-32 w-full" />
        ) : isLoading ? (
          <div className={cn("grid gap-2", columnClasses(visibleDays.length))}>
            {visibleDays.map((day) => (
              <Skeleton key={toIsoDate(day)} className="h-32 w-full" />
            ))}
          </div>
        ) : total === 0 && !error ? (
          <EmptyState
            icon={CalendarDays}
            title="Keine Stunden in diesem Zeitraum"
            description="Für die gewählten Tage ist nichts eingetragen. Mit „Stundenplan aktualisieren“ in den Einstellungen holst du den Plan aus deinem Schulportal."
          />
        ) : (
          <div className={cn("grid gap-2", columnClasses(visibleDays.length))}>
            {visibleDays.map((day) => (
              <DayColumn
                key={toIsoDate(day)}
                day={day}
                lessons={lessonsByDay.get(toIsoDate(day)) ?? []}
                reference={reference}
                isToday={today ? isSameDay(day, today) : false}
                choices={choices}
                onChoose={choose}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
