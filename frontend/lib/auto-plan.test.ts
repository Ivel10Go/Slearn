import { describe, expect, it } from "vitest";
import { groupByDay, parseAutoPlan } from "./auto-plan";

const plan = parseAutoPlan({
  applied: false,
  study_plan_id: null,
  skipped: 0,
  days: [
    { day: "2026-10-12", starts_at: "2026-10-12T13:30:00Z", capacity_minutes: 90, planned_minutes: 75, overbooked: false },
    { day: "2026-10-13", starts_at: "2026-10-13T08:00:00Z", capacity_minutes: 90, planned_minutes: 0, overbooked: false },
    { day: "2026-10-14", starts_at: "2026-10-14T12:00:00Z", capacity_minutes: 90, planned_minutes: 120, overbooked: true },
  ],
  items: [
    { kind: "homework", title: "Hausaufgabe: Blatt 3", duration_minutes: 30, starts_at: "2026-10-12T13:30:00Z", due_on: "2026-10-13", priority: 5 },
    { kind: "exam", title: "Ableitungen lernen", duration_minutes: 45, starts_at: "2026-10-12T14:00:00Z", due_on: "2026-10-15", priority: 4 },
    { kind: "review", title: "Vokabeln wiederholen", duration_minutes: 120, starts_at: "2026-10-14T12:00:00Z", due_on: "2026-10-18", priority: 2 },
  ],
});

describe("Automatischer Lernplan", () => {
  it("ergaenzt fehlende Verweise als null", () => {
    expect(plan.items[0].exam_id).toBeNull();
    expect(plan.items[0].homework_id).toBeNull();
  });

  it("gruppiert nach Plantag und laesst leere Tage weg", () => {
    const gruppen = groupByDay(plan);
    expect(gruppen.map((gruppe) => gruppe.day.day)).toEqual(["2026-10-12", "2026-10-14"]);
    expect(gruppen[0].items.map((item) => item.title)).toEqual(["Hausaufgabe: Blatt 3", "Ableitungen lernen"]);
    expect(gruppen[1].day.overbooked).toBe(true);
  });

  it("lehnt eine unbekannte Art ab", () => {
    expect(() =>
      parseAutoPlan({ applied: false, skipped: 0, days: [], items: [{ kind: "party", title: "x", duration_minutes: 1, starts_at: "", due_on: "", priority: 0 }] }),
    ).toThrow();
  });
});
