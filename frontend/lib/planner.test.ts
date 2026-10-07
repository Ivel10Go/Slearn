import { describe, expect, it } from "vitest";
import {
  DEFAULT_PLANNER_OPTIONS,
  isNewBlock,
  mergeOptions,
  plannerResultSchema,
  summarizePlan,
  validateOptions,
  type PlannerBlock,
} from "./planner";

function block(overrides: Partial<PlannerBlock>): PlannerBlock {
  return {
    kind: "task",
    title: "Vokabeln",
    start: "2026-10-05T14:00:00Z",
    end: "2026-10-05T14:45:00Z",
    duration_minutes: 45,
    priority: 2,
    deadline: null,
    late: false,
    task_id: 1,
    homework_id: null,
    exam_id: null,
    topic_id: null,
    subject_name: null,
    ...overrides,
  };
}

describe("Wochenplaner", () => {
  it("erkennt Blöcke, die erst beim Übernehmen entstehen", () => {
    expect(isNewBlock(block({}))).toBe(false);
    expect(isNewBlock(block({ kind: "homework", task_id: null }))).toBe(true);
    expect(isNewBlock(block({ kind: "exam_topic", task_id: 9 }))).toBe(false);
  });

  it("fasst den Vorschlag zusammen", () => {
    const result = plannerResultSchema.parse({
      applied: false,
      window_start: "2026-10-05",
      window_end: "2026-10-11",
      days: [
        {
          date: "2026-10-05",
          budget_minutes: 120,
          planned_minutes: 75,
          blocks: [block({}), block({ kind: "homework", task_id: null, duration_minutes: 30, late: true })],
          busy: [],
        },
      ],
      unplaced: [{ kind: "task", title: "Zu viel", duration_minutes: 60, reason: "Kein freier Platz im Zeitraum" }],
      planned_count: 2,
    });

    expect(summarizePlan(result)).toEqual({ blocks: 2, minutes: 75, newBlocks: 1, late: 1, unplaced: 1 });
    expect(result.created_count).toBe(0);
  });

  it("lehnt ein Ende vor dem Beginn ab", () => {
    expect(validateOptions(DEFAULT_PLANNER_OPTIONS)).toBeNull();
    expect(validateOptions({ ...DEFAULT_PLANNER_OPTIONS, latest: "13:00" })).toMatch(/Ende/);
    expect(validateOptions({ ...DEFAULT_PLANNER_OPTIONS, earliest: "25:00" })).toMatch(/HH:MM/);
    expect(validateOptions({ ...DEFAULT_PLANNER_OPTIONS, max_minutes_per_day: 5 })).toMatch(/Minuten/);
  });

  it("übernimmt aus dem Speicher nur bekannte Werte mit passendem Typ", () => {
    const merged = mergeOptions({ days: 14, earliest: 15, fremd: true, include_weekends: false });
    expect(merged.days).toBe(14);
    expect(merged.earliest).toBe(DEFAULT_PLANNER_OPTIONS.earliest);
    expect(merged.include_weekends).toBe(false);
    expect("fremd" in merged).toBe(false);
    expect(mergeOptions(null)).toEqual(DEFAULT_PLANNER_OPTIONS);
  });
});
