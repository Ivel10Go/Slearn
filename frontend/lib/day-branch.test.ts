import { describe, expect, it } from "vitest";
import { buildDayBranch } from "./day-branch";
import type { DashboardLesson, DashboardStudyTask } from "./types";

function lesson(id: string, start: [number, number], end: [number, number], extra: Partial<DashboardLesson> = {}): DashboardLesson {
  return {
    id,
    subject: "Mathematik",
    subjectShort: "M",
    title: "",
    teacher: "",
    room: "204",
    startTime: new Date(2026, 10, 19, ...start).toISOString(),
    endTime: new Date(2026, 10, 19, ...end).toISOString(),
    color: "",
    isCancelled: false,
    ...extra,
  };
}

describe("Tagesast", () => {
  it("legt Stunden in ihrer echten Länge auf volle Stunden", () => {
    const now = new Date(2026, 10, 19, 9, 0);
    const ast = buildDayBranch([lesson("1", [8, 0], [9, 30]), lesson("2", [10, 0], [11, 0], { isCancelled: true })], [], now);
    expect(ast.startHour).toBe(8);
    expect(ast.endHour).toBe(11);
    expect(ast.items[0]).toMatchObject({ left: 0, width: 50, state: "current", label: "M" });
    expect(ast.items[1]).toMatchObject({ cancelled: true, state: "upcoming" });
    expect(ast.now).toBeCloseTo(33.33, 1);
    expect(ast.outside).toBeNull();
  });

  it("nimmt geplante Lernblöcke mit, erledigte nicht", () => {
    const now = new Date(2026, 10, 19, 18, 0);
    const task = (id: string, status: DashboardStudyTask["status"]) =>
      ({ id, subject: "Deutsch", title: "Gedicht", topic: "", durationMinutes: 30, status, progress: 0, scheduledFor: new Date(2026, 10, 19, 16, 0).toISOString(), dueDate: null, priority: 0, blockedBy: [], isSeries: false, startedAt: null, session: null }) satisfies DashboardStudyTask;
    const ast = buildDayBranch([], [task("a", "todo"), task("b", "completed")], now);
    expect(ast.items).toHaveLength(1);
    expect(ast.items[0]).toMatchObject({ kind: "study", state: "past" });
    expect(ast.outside).toBe("after");
    expect(ast.now).toBe(100);
  });

  it("zeigt ohne Einträge einen leeren Schultag", () => {
    const ast = buildDayBranch([], [], new Date(2026, 10, 19, 6, 0));
    expect(ast.items).toHaveLength(0);
    expect(ast.outside).toBe("before");
  });
});
