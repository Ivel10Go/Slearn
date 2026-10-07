"use client";

import { z } from "zod";
import { apiRequest } from "./api";

/*
 * Der automatische Lernplan (`POST /study/plans/generate`).
 *
 * Eigene Datei wie `assistant.ts`: ein abgeschlossener Bereich, der `api.ts`
 * und `schemas.ts` nicht weiter wachsen laesst.
 */

export type AutoPlanKind = "homework" | "exam" | "review" | "task";

const itemSchema = z.object({
  kind: z.enum(["homework", "exam", "review", "task"]),
  title: z.string(),
  description: z.string().nullable().optional().default(null),
  duration_minutes: z.number().int(),
  starts_at: z.string(),
  due_on: z.string(),
  priority: z.number().int(),
  exam_id: z.number().int().nullable().optional().default(null),
  topic_id: z.number().int().nullable().optional().default(null),
  homework_id: z.number().int().nullable().optional().default(null),
  task_id: z.number().int().nullable().optional().default(null),
});

const daySchema = z.object({
  day: z.string(),
  starts_at: z.string(),
  capacity_minutes: z.number().int(),
  planned_minutes: z.number().int(),
  overbooked: z.boolean(),
});

const responseSchema = z.object({
  applied: z.boolean(),
  study_plan_id: z.number().int().nullable().optional().default(null),
  items: z.array(itemSchema),
  days: z.array(daySchema),
  skipped: z.number().int(),
});

export type AutoPlanItem = z.infer<typeof itemSchema>;
export type AutoPlanDay = z.infer<typeof daySchema>;
export type AutoPlan = z.infer<typeof responseSchema>;

export interface AutoPlanOptions {
  days: number;
  dailyMinutes: number;
  apply: boolean;
}

export function parseAutoPlan(payload: unknown): AutoPlan {
  return responseSchema.parse(payload);
}

export async function generateAutoPlan({ days, dailyMinutes, apply }: AutoPlanOptions): Promise<AutoPlan> {
  return parseAutoPlan(
    await apiRequest("/v1/study/plans/generate", {
      method: "POST",
      body: JSON.stringify({ days, daily_minutes: dailyMinutes, apply }),
    }),
  );
}

export const KIND_LABEL: Record<AutoPlanKind, string> = {
  homework: "Hausaufgabe",
  exam: "Klausur",
  review: "Wiederholung",
  task: "Aufgabe",
};

export interface AutoPlanDayGroup {
  day: AutoPlanDay;
  items: AutoPlanItem[];
}

/** Die Einheiten je Tag; Tage ohne Einheit fallen weg, die Reihenfolge bleibt die des Backends. */
export function groupByDay(plan: AutoPlan): AutoPlanDayGroup[] {
  const groups = new Map<string, AutoPlanDayGroup>();
  for (const day of plan.days) {
    groups.set(day.day, { day, items: [] });
  }
  for (const item of plan.items) {
    const key = localDayKey(item.starts_at, plan.days);
    groups.get(key)?.items.push(item);
  }
  return [...groups.values()].filter((group) => group.items.length > 0);
}

/**
 * Welcher Plantag zu einem Zeitpunkt gehoert: der letzte, der nicht spaeter
 * beginnt. Ueber die Startzeiten des Backends statt ueber die Zeitzone des
 * Browsers - die Tage rechnet das Backend in der Zeitzone des Kontos.
 */
function localDayKey(startsAt: string, days: AutoPlanDay[]): string {
  const time = Date.parse(startsAt);
  let key = days[0]?.day ?? "";
  for (const day of days) {
    if (Date.parse(day.starts_at) <= time) {
      key = day.day;
    }
  }
  return key;
}
