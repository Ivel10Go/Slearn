import type { DashboardLesson, LessonState } from "./types";

export function toDate(value: string | Date | null | undefined) {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  if (!value) {
    return null;
  }
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00` : value;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDate(
  value: string | Date | null | undefined,
  options: Intl.DateTimeFormatOptions = { weekday: "long", day: "2-digit", month: "long", year: "numeric" },
) {
  const date = toDate(value);
  return date ? new Intl.DateTimeFormat("de-DE", options).format(date) : "—";
}

export function formatTime(value: string | Date | null | undefined, includeSeconds = false) {
  const date = toDate(value);
  if (!date) {
    return "—";
  }
  return new Intl.DateTimeFormat("de-DE", {
    hour: "2-digit",
    minute: "2-digit",
    ...(includeSeconds ? { second: "2-digit" } : {}),
  }).format(date);
}

export function formatDateTime(value: string | Date | null | undefined) {
  const date = toDate(value);
  if (!date) {
    return "—";
  }
  return `${formatDate(value, { weekday: "short", day: "2-digit", month: "short" })}, ${formatTime(date)}`;
}

export function formatDuration(minutes: number) {
  if (minutes <= 0) {
    return "Ohne Dauer";
  }
  if (minutes < 60) {
    return `${minutes} Min.`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours} Std. ${remainingMinutes} Min.` : `${hours} Std.`;
}

export function getCountdown(target: string | Date, now = new Date()) {
  const targetDate = toDate(target);
  if (!targetDate) {
    return { days: 0, hours: 0, minutes: 0, totalMs: 0, isOverdue: false, label: "Kein Termin" };
  }
  const totalMs = targetDate.getTime() - now.getTime();
  const absoluteMs = Math.abs(totalMs);
  const days = Math.floor(absoluteMs / 86_400_000);
  const hours = Math.floor((absoluteMs % 86_400_000) / 3_600_000);
  const minutes = Math.floor((absoluteMs % 3_600_000) / 60_000);
  const isOverdue = totalMs < 0;
  const dayLabel = days === 1 ? "Tag" : "Tagen";
  const label = isOverdue
    ? days > 0
      ? `Vor ${days} ${dayLabel}`
      : "Termin vorbei"
    : days > 0
      ? `in ${days} ${dayLabel}`
      : hours > 0
        ? `in ${hours} ${hours === 1 ? "Stunde" : "Stunden"}`
        : `in ${Math.max(1, minutes)} Min.`;
  return { days, hours, minutes, totalMs, isOverdue, label };
}

export function getLessonState(lesson: Pick<DashboardLesson, "startTime" | "endTime">, now = new Date()): LessonState {
  const start = toDate(lesson.startTime)?.getTime() ?? Number.POSITIVE_INFINITY;
  const end = toDate(lesson.endTime)?.getTime() ?? Number.NEGATIVE_INFINITY;
  const current = now.getTime();
  if (current >= end) {
    return "completed";
  }
  if (current >= start) {
    return "current";
  }
  return "upcoming";
}

export function getDayProgress(lessons: Array<Pick<DashboardLesson, "startTime" | "endTime">>, now = new Date()) {
  const timestamps = lessons
    .map((lesson) => ({ start: toDate(lesson.startTime)?.getTime(), end: toDate(lesson.endTime)?.getTime() }))
    .filter((range): range is { start: number; end: number } => range.start !== undefined && range.end !== undefined);
  if (timestamps.length === 0) {
    return 0;
  }
  const start = Math.min(...timestamps.map((range) => range.start));
  const end = Math.max(...timestamps.map((range) => range.end));
  if (end <= start) {
    return 100;
  }
  return Math.min(100, Math.max(0, Math.round(((now.getTime() - start) / (end - start)) * 100)));
}

export function formatDueDate(value: string | Date, now = new Date()) {
  const date = toDate(value);
  if (!date) {
    return "Kein Datum";
  }
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const startOfDate = new Date(date);
  startOfDate.setHours(0, 0, 0, 0);
  const days = Math.round((startOfDate.getTime() - startOfToday.getTime()) / 86_400_000);
  if (days < 0) {
    return "Überfällig";
  }
  if (days === 0) {
    return "Heute fällig";
  }
  if (days === 1) {
    return "Morgen fällig";
  }
  return formatDate(date, { weekday: "short", day: "2-digit", month: "short" });
}

export function getInitials(value: string) {
  const initials = value
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
  return initials || "L";
}

/** Kopiert statt zu übernehmen: ``toDate`` liefert eine ``Date``-Eingabe unverändert zurück. */
function copyDate(value: string | Date) {
  const source = toDate(value);
  return source ? new Date(source.getTime()) : new Date();
}

export function startOfDay(value: string | Date) {
  const date = copyDate(value);
  date.setHours(0, 0, 0, 0);
  return date;
}

/** Montag als erster Tag der Woche, wie es der WebUntis-Import auch rechnet. */
export function startOfWeek(value: string | Date) {
  const date = startOfDay(value);
  const offset = (date.getDay() + 6) % 7;
  date.setDate(date.getDate() - offset);
  return date;
}

export function addDays(value: string | Date, days: number) {
  const date = copyDate(value);
  date.setDate(date.getDate() + days);
  return date;
}

export function addWeeks(value: string | Date, weeks: number) {
  return addDays(value, weeks * 7);
}

/** Jeder Kalendertag zwischen beiden Grenzen, beide einschließlich. */
export function eachDay(start: string | Date, end: string | Date) {
  const days: Date[] = [];
  const last = startOfDay(end).getTime();
  for (let cursor = startOfDay(start); cursor.getTime() <= last; cursor = addDays(cursor, 1)) {
    days.push(cursor);
  }
  return days;
}

export function isSameDay(left: string | Date | null | undefined, right: string | Date | null | undefined) {
  const first = toDate(left);
  const second = toDate(right);
  if (!first || !second) {
    return false;
  }
  return (
    first.getFullYear() === second.getFullYear() &&
    first.getMonth() === second.getMonth() &&
    first.getDate() === second.getDate()
  );
}

/** ``YYYY-MM-DD`` in lokaler Zeit - das Format, das die API für ``start``/``end`` erwartet. */
export function toIsoDate(value: string | Date) {
  const date = copyDate(value);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/** ISO-8601-Kalenderwoche, wie sie in Deutschland üblich ist. */
export function formatWeekNumber(value: string | Date) {
  const date = toDate(value);
  if (!date) {
    return "—";
  }
  // Die Woche gehört zu dem Jahr, in dem ihr Donnerstag liegt.
  const thursday = addDays(date, 3 - ((date.getDay() + 6) % 7));
  const january4 = new Date(thursday.getFullYear(), 0, 4);
  const firstThursday = addDays(january4, 3 - ((january4.getDay() + 6) % 7));
  return `KW ${Math.round((thursday.getTime() - firstThursday.getTime()) / 604_800_000) + 1}`;
}

/** Überschrift über einem Zeitraum, etwa "15. – 21. September". */
export function formatDateRange(start: string | Date, end: string | Date) {
  const first = toDate(start);
  const last = toDate(end);
  if (!first || !last) {
    return "—";
  }
  if (isSameDay(first, last)) {
    return formatDate(first, { day: "2-digit", month: "long", year: "numeric" });
  }
  if (first.getFullYear() === last.getFullYear() && first.getMonth() === last.getMonth()) {
    return `${formatDate(first, { day: "2-digit" })} – ${formatDate(last, { day: "2-digit", month: "long" })}`;
  }
  if (first.getFullYear() === last.getFullYear()) {
    return `${formatDate(first, { day: "2-digit", month: "short" })} – ${formatDate(last, {
      day: "2-digit",
      month: "long",
    })}`;
  }
  return `${formatDate(first, { day: "2-digit", month: "short", year: "numeric" })} – ${formatDate(last, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  })}`;
}

export interface LessonSlot<T> {
  /** Schluessel der Lage, stabil ueber Tage hinweg. */
  key: string;
  lessons: T[];
}

/**
 * Gruppiert Stunden zu parallelen Lagen.
 *
 * WebUntis liefert Faecher, die zur selben Zeit in verschiedenen Raeumen
 * stattfinden, als eigene Eintraege. Ohne Gruppierung stuende dann im Plan
 * zweimal dieselbe Uhrzeit. Lagen kommen nach Startzeit sortiert, die
 * Alternativen darin alphabetisch nach Kuerzel - beides, damit die Reihenfolge
 * nicht davon abhaengt, in welcher Reihenfolge das Portal antwortet.
 */
export function groupLessonSlots<T extends { startTime: string; endTime: string; subjectShort?: string | null }>(
  lessons: T[],
): LessonSlot<T>[] {
  const nachKey = new Map<string, T[]>();
  for (const lesson of lessons) {
    const key = `${lesson.startTime}|${lesson.endTime}`;
    const vorhanden = nachKey.get(key);
    if (vorhanden) {
      vorhanden.push(lesson);
    } else {
      nachKey.set(key, [lesson]);
    }
  }

  return [...nachKey.entries()]
    .sort(([ersterA], [ersterB]) => (ersterA < ersterB ? -1 : ersterA > ersterB ? 1 : 0))
    .map(([key, gruppe]) => ({
      key,
      lessons: gruppe
        .slice()
        .sort((a, b) => (a.subjectShort ?? "").localeCompare(b.subjectShort ?? "", "de")),
    }));
}

/** Das Kuerzel einer Lage, fuer die Auswahlleiste "F | L". */
export function slotLabel<T extends { subjectShort: string; room: string }>(lessons: T[]) {
  return lessons.map((lesson) => lesson.subjectShort).join(" | ");
}

/**
 * Waehlt aus einer Lage aus, ohne sie zu verstecken.
 *
 * Faellt die gespeicherte Auswahl weg, etwa weil sich der Plan geaendert hat,
 * gewinnt die erste Alternative. Eine Lektion, die gar nicht zur Lage passt,
 * wird nie gewaehlt - dann bliebe der Slot leer und der Tag wirkte falsch.
 */
export function pickSlotLesson<T extends { id: string }>(lessons: T[], selectedId: string | null): T {
  const passend = selectedId ? lessons.find((lesson) => lesson.id === selectedId) : undefined;
  return passend ?? lessons[0];
}