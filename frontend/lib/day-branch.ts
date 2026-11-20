import type { DashboardLesson, DashboardStudyTask } from "@/lib/types";

/**
 * Der Tagesast der Uebersicht: Stunden und Lernbloecke als Abschnitte auf
 * einer Zeitachse, jeder so lang wie seine echte Dauer. Rein rechnerisch,
 * damit der Test ohne DOM auskommt.
 */
export interface BranchItem {
  id: string;
  kind: "lesson" | "study";
  label: string;
  detail: string;
  start: Date;
  end: Date;
  cancelled: boolean;
  state: "past" | "current" | "upcoming";
  /** Lage auf dem Ast in Prozent. */
  left: number;
  width: number;
}

export interface DayBranch {
  startHour: number;
  endHour: number;
  items: BranchItem[];
  /** Position von "jetzt" in Prozent, auf 0 bis 100 geklemmt. */
  now: number;
  /** Jetzt liegt vor dem ersten bzw. nach dem letzten Eintrag des Astes. */
  outside: "before" | "after" | null;
}

const DEFAULT_START = 8;
const DEFAULT_END = 16;

function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function buildDayBranch(lessons: DashboardLesson[], tasks: DashboardStudyTask[], now: Date): DayBranch {
  const raw: Omit<BranchItem, "state" | "left" | "width">[] = [];
  for (const lesson of lessons) {
    const start = new Date(lesson.startTime);
    const end = new Date(lesson.endTime);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start || !sameDay(start, now)) {
      continue;
    }
    raw.push({ id: `l${lesson.id}`, kind: "lesson", label: lesson.subjectShort || lesson.subject, detail: lesson.room, start, end, cancelled: lesson.isCancelled });
  }
  for (const task of tasks) {
    if (!task.scheduledFor || task.status === "completed") {
      continue;
    }
    const start = new Date(task.scheduledFor);
    if (Number.isNaN(start.getTime()) || !sameDay(start, now)) {
      continue;
    }
    const end = new Date(start.getTime() + Math.max(5, task.durationMinutes) * 60_000);
    raw.push({ id: `s${task.id}`, kind: "study", label: task.subject || task.title, detail: task.title, start, end, cancelled: false });
  }
  raw.sort((a, b) => a.start.getTime() - b.start.getTime());

  const hours = raw.flatMap((item) => [item.start.getHours(), item.end.getHours() + (item.end.getMinutes() > 0 ? 1 : 0)]);
  const startHour = hours.length ? Math.min(...hours) : DEFAULT_START;
  const endHour = Math.max(startHour + 1, hours.length ? Math.max(...hours) : DEFAULT_END);

  const dayStart = new Date(now);
  dayStart.setHours(startHour, 0, 0, 0);
  const span = (endHour - startHour) * 3_600_000;
  const percent = (date: Date) => ((date.getTime() - dayStart.getTime()) / span) * 100;

  const items = raw.map((item) => ({
    ...item,
    state: now >= item.end ? "past" : now >= item.start ? "current" : "upcoming",
    left: percent(item.start),
    width: percent(item.end) - percent(item.start),
  })) satisfies BranchItem[];

  const position = percent(now);
  return {
    startHour,
    endHour,
    items,
    now: Math.min(100, Math.max(0, position)),
    outside: position < 0 ? "before" : position > 100 ? "after" : null,
  };
}
