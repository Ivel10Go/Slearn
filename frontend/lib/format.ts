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
